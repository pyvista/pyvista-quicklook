import Darwin
import Foundation
import os

/// Log destination for the app and its Quick Look extension.
let osLog = Logger(subsystem: "org.pyvista.PyVistaQuickLook", category: "preview")

/// Seconds since this process was started, which covers the time before it could log.
func processAge() -> Double {
    var info = kinfo_proc()
    var size = MemoryLayout<kinfo_proc>.stride
    var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
    guard sysctl(&mib, 4, &info, &size, nil, 0) == 0 else {
        return 0
    }
    let started = info.kp_proc.p_starttime
    let seconds = Double(started.tv_sec) + Double(started.tv_usec) / 1_000_000
    return Date().timeIntervalSince1970 - seconds
}

private let logStamp: DateFormatter = {
    let formatter = DateFormatter()
    formatter.dateFormat = "HH:mm:ss.SSS"
    return formatter
}()

/// Appends a line to a debug log inside the extension's own temporary directory.
func pvqlLog(_ message: String) {
    osLog.notice("\(message, privacy: .public)")
    let path = (NSTemporaryDirectory() as NSString).appendingPathComponent("pvql-extension.log")
    if let size = (try? FileManager.default.attributesOfItem(atPath: path))?[.size] as? Int,
       size > 1_000_000 {
        try? FileManager.default.removeItem(atPath: path)
    }
    let age = String(format: "%6.2fs", processAge())
    let line = "\(logStamp.string(from: Date())) [pid \(getpid()) +\(age)] \(message)\n"
    if let handle = FileHandle(forWritingAtPath: path) {
        handle.seekToEndOfFile()
        handle.write(Data(line.utf8))
        try? handle.close()
    } else {
        try? line.write(toFile: path, atomically: true, encoding: .utf8)
    }
}

/// Absolute path of the user's home directory, even when the process is sandboxed.
func realHome() -> String {
    if let entry = getpwuid(getuid()), let directory = entry.pointee.pw_dir {
        return String(cString: directory)
    }
    return NSHomeDirectory()
}

/// Talks to the render service, and runs the `pvql` helper for diagnostics.
enum Helper {
    struct Failure: Error {
        let message: String
        /// Whether the file is above the size limit, so the reader may preview it anyway.
        var isTooLarge = false
        /// What the file's header says, sent with a refusal so the panel still has it.
        var info: [String: Any]?
        /// Whether the installation is at fault rather than the file being previewed.
        var isSetup = false
    }

    /// What the render service returned: a scene to explore, or a rendered image.
    enum Payload {
        case scene(URL)
        case image(Data)
    }

    /// A preview and the service's description of the dataset, when it sent one.
    struct Reply {
        let payload: Payload
        let info: [String: Any]?
    }

    static let hardTimeout: TimeInterval = 120
    static let requestSuffix = ".pvqlreq"
    static let replySuffix = ".pvqlrep"
    static let earlySuffix = ".pvqlpre"

    static let serviceMissingMessage = """
    The PyVista Quick Look render service is not answering.

    Start it with:

        pvql service --install

    Then check it with:

        pvql doctor
    """

    static let missingHelperMessage = """
    PyVista Quick Look cannot find the "pvql" helper.

    Install it, then record where it lives:

        uv tool install pyvista-quicklook
        pvql config --init
    """

    /// Returns the path to the `pvql` executable, or nil when it cannot be found.
    static func locate() -> String? {
        var candidates: [String] = []
        if let override = ProcessInfo.processInfo.environment["PVQL_HELPER"] {
            candidates.append(override)
        }
        if let configured = configuredPath() {
            candidates.append(configured)
        }
        if let declared = Bundle.main.object(forInfoDictionaryKey: "PVQLHelperPath") as? String {
            candidates.append(declared)
        }
        let home = realHome()
        candidates += [
            "\(home)/.local/bin/pvql",
            "/opt/homebrew/bin/pvql",
            "/usr/local/bin/pvql",
            "/usr/bin/pvql",
        ]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    /// Returns the contents of the configuration file, if present.
    static func configuration() -> [String: Any]? {
        let url = URL(fileURLWithPath: realHome())
            .appendingPathComponent("Library/Application Support/PyVistaQuickLook/config.json")
        guard let data = try? Data(contentsOf: url) else {
            return nil
        }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    /// Returns the `pvql` path recorded in the configuration file, if present.
    static func configuredPath() -> String? {
        guard let path = configuration()?["pvql"] as? String else {
            return nil
        }
        return (path as NSString).expandingTildeInPath
    }

    /// Files above this many bytes are not staged; the service refuses them at the same size.
    static func maximumFileBytes() -> Int {
        let megabytes = (configuration()?["max_file_size_mb"] as? NSNumber)?.intValue ?? 2048
        return megabytes > 0 ? megabytes * 1_000_000 : Int.max
    }

    /// Runs `pvql` with the given arguments and returns its exit status and output.
    static func run(_ arguments: [String]) throws -> (status: Int32, output: String, errors: String) {
        guard let tool = locate() else {
            pvqlLog("helper not found")
            throw Failure(message: missingHelperMessage, isSetup: true)
        }
        pvqlLog("running \(tool) \(arguments)")

        let process = Process()
        process.executableURL = URL(fileURLWithPath: tool)
        process.arguments = arguments

        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe

        var environment = ProcessInfo.processInfo.environment
        environment["HOME"] = realHome()
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"
        process.environment = environment

        do {
            try process.run()
        } catch {
            pvqlLog("spawn failed: \(error.localizedDescription)")
            throw Failure(message: "Could not run \(tool)\n\n\(error.localizedDescription)", isSetup: true)
        }

        var outData = Data()
        var errData = Data()
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global().async {
            outData = outPipe.fileHandleForReading.readDataToEndOfFile()
            group.leave()
        }
        group.enter()
        DispatchQueue.global().async {
            errData = errPipe.fileHandleForReading.readDataToEndOfFile()
            group.leave()
        }

        let watchdog = DispatchWorkItem {
            if process.isRunning {
                process.terminate()
            }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + hardTimeout, execute: watchdog)
        process.waitUntilExit()
        watchdog.cancel()
        group.wait()

        let trim = CharacterSet.whitespacesAndNewlines
        pvqlLog("pvql exited \(process.terminationStatus)")
        return (
            process.terminationStatus,
            String(decoding: outData, as: UTF8.self).trimmingCharacters(in: trim),
            String(decoding: errData, as: UTF8.self).trimmingCharacters(in: trim)
        )
    }

    /// Seconds to wait for the service: its own conversion timeout, and some to spare.
    static func replyTimeout() -> TimeInterval {
        let seconds = (configuration()?["timeout"] as? NSNumber)?.doubleValue ?? 60
        return seconds + 30
    }

    /// Asks the render service for a preview of a mesh file.
    ///
    /// `force` previews a file above the size limit. `early` is handed what the file's
    /// header says when the service sends it ahead of a slow preview.
    static func requestPreview(
        _ url: URL,
        force: Bool = false,
        early: (([String: Any]) -> Void)? = nil
    ) throws -> Reply {
        let directory = NSTemporaryDirectory() as NSString
        let token = UUID().uuidString
        let requestPath = directory.appendingPathComponent("\(token)\(requestSuffix)")
        let replyPath = directory.appendingPathComponent("\(token)\(replySuffix)")
        let earlyPath = directory.appendingPathComponent("\(token)\(earlySuffix)")
        let scratchPath = requestPath + ".tmp"
        let stagingPath = directory.appendingPathComponent("\(token).staging")
        let manager = FileManager.default
        defer { try? manager.removeItem(atPath: stagingPath) }

        var body: [String: Any] = ["path": url.path]
        if force {
            body["force"] = true
        }
        if let attributes = try? manager.attributesOfItem(atPath: url.path) {
            let size = (attributes[.size] as? NSNumber)?.intValue ?? 0
            body["size"] = size
            if let modified = attributes[.modificationDate] as? Date {
                body["mtime"] = Int(modified.timeIntervalSince1970)
            }
            // The service cannot read folders macOS keeps private, so leave it a copy.
            // A directory is never staged: its own size says nothing about what it holds.
            var isFolder: ObjCBool = false
            manager.fileExists(atPath: url.path, isDirectory: &isFolder)
            if !isFolder.boolValue, size > 0, force || size <= maximumFileBytes() {
                let staged = (stagingPath as NSString).appendingPathComponent(url.lastPathComponent)
                try? manager.createDirectory(atPath: stagingPath, withIntermediateDirectories: true)
                if (try? manager.copyItem(atPath: url.path, toPath: staged)) != nil {
                    body["copy"] = staged
                }
            }
        }

        pvqlLog("requesting \(url.path)")
        do {
            let payload = try JSONSerialization.data(withJSONObject: body)
            try payload.write(to: URL(fileURLWithPath: scratchPath))
            try manager.moveItem(atPath: scratchPath, toPath: requestPath)
        } catch {
            throw Failure(message: "Could not reach the render service.\n\n\(error.localizedDescription)", isSetup: true)
        }

        let deadline = Date().addingTimeInterval(replyTimeout())
        defer { try? manager.removeItem(atPath: earlyPath) }
        while Date() < deadline {
            if let data = manager.contents(atPath: earlyPath) {
                try? manager.removeItem(atPath: earlyPath)
                if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let info = json["info"] as? [String: Any] {
                    pvqlLog("received the header ahead of the preview")
                    early?(info)
                }
            }
            guard let data = manager.contents(atPath: replyPath) else {
                Thread.sleep(forTimeInterval: 0.05)
                continue
            }
            try? manager.removeItem(atPath: replyPath)
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw Failure(message: "The render service sent a malformed reply.", isSetup: true)
            }
            if json["ok"] as? Bool == true {
                let info = json["info"] as? [String: Any]
                if let scene = json["scene"] as? String {
                    pvqlLog("received a scene at \(scene)")
                    return Reply(payload: .scene(URL(fileURLWithPath: scene)), info: info)
                }
                if let png = json["png"] as? String {
                    defer { try? manager.removeItem(atPath: png) }
                    guard let bytes = manager.contents(atPath: png), !bytes.isEmpty else {
                        throw Failure(message: "The render service produced an empty preview.")
                    }
                    pvqlLog("received \(bytes.count) bytes")
                    return Reply(payload: .image(bytes), info: info)
                }
            }
            let reported = json["error"] as? String
            // The service marks what the reader can act on, such as a folder it may not read.
            throw Failure(
                message: reported ?? "The render service could not produce a preview.",
                isTooLarge: json["too_large"] as? Bool == true,
                info: json["info"] as? [String: Any],
                isSetup: json["actionable"] as? Bool == true
            )
        }

        // A request the service took is still being worked on; one it never took means it is gone.
        if !manager.fileExists(atPath: requestPath) {
            pvqlLog("service is still working after \(Int(replyTimeout())) s")
            throw Failure(message: "The render service is still converting \(url.lastPathComponent) after \(Int(replyTimeout())) seconds.\n\nIt is kept for the next time the file is selected.")
        }
        try? manager.removeItem(atPath: requestPath)
        pvqlLog("service did not answer")
        throw Failure(message: serviceMissingMessage, isSetup: true)
    }
}
