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
        /// Whether the service cannot read the file's folder and asks for a copy instead.
        var needsCopy = false
        /// Whether the file holds no mesh, so it may be shown as whatever else it is.
        var isNotMesh = false
        /// Whether the service took the request and is still working on it.
        var isUnderway = false
    }

    /// What the render service returned: a scene to explore, or a rendered image.
    enum Payload {
        case scene(URL)
        case image(Data)
    }

    /// A preview and the service's description of the dataset, when it sent one.
    struct Reply {
        /// Nil when only the description was asked for.
        let payload: Payload?
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

    /// Returns the settings: the copy the service keeps in the exchange folder, which the
    /// sandbox lets the extension read, else the file itself.
    static func configuration() -> [String: Any]? {
        let mirrored = URL(fileURLWithPath: exchangeDirectory()).appendingPathComponent("config.json")
        let original = URL(fileURLWithPath: realHome())
            .appendingPathComponent("Library/Application Support/PyVistaQuickLook/config.json")
        for url in [mirrored, original] {
            if let data = try? Data(contentsOf: url),
               let settings = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                return settings
            }
        }
        return nil
    }

    /// Returns the `pvql` path recorded in the configuration file, if present.
    static func configuredPath() -> String? {
        guard let path = configuration()?["pvql"] as? String else {
            return nil
        }
        return (path as NSString).expandingTildeInPath
    }

    /// Returns the identifier of the Quick Look preview extension in a folder of plug-ins.
    static func previewExtension(in plugIns: URL) -> String? {
        let embedded = (try? FileManager.default.contentsOfDirectory(at: plugIns, includingPropertiesForKeys: nil)) ?? []
        return embedded.lazy.compactMap(Bundle.init(url:)).first { bundle in
            let point = (bundle.object(forInfoDictionaryKey: "NSExtension") as? [String: Any])?["NSExtensionPointIdentifier"]
            return point as? String == "com.apple.quicklook.preview"
        }?.bundleIdentifier
    }

    /// The app's extensions live in an app of their own, nested here and never launched, since
    /// macOS re-registers an app without a Developer ID signature on every launch, and with it
    /// the extensions it embeds.
    static func plugInsFolder() -> URL {
        Bundle.main.bundleURL.appendingPathComponent("Contents/Library/PyVista Quick Look Extensions.app/Contents/PlugIns")
    }

    /// Returns the folder the service watches: the container of the preview extension the app ships.
    static func exchangeDirectory() -> String {
        guard ProcessInfo.processInfo.environment["APP_SANDBOX_CONTAINER_ID"] == nil,
              let identifier = previewExtension(in: plugInsFolder())
        else {
            return NSTemporaryDirectory()
        }
        let container = "\(realHome())/Library/Containers/\(identifier)/Data/tmp/"
        try? FileManager.default.createDirectory(atPath: container, withIntermediateDirectories: true)
        return container
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
            throw Failure(message: missingHelperMessage)
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
            throw Failure(message: "Could not run \(tool)\n\n\(error.localizedDescription)")
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
    /// header says when the service sends it ahead of a slow preview. The file is copied
    /// only when the service cannot read its folder.
    static func requestPreview(
        _ url: URL,
        force: Bool = false,
        scene: Bool? = nil,
        early: (([String: Any]) -> Void)? = nil
    ) throws -> Reply {
        do {
            return try exchange(url, force: force, copy: nil, early: early, scene: scene)
        } catch let failure as Failure where failure.needsCopy {
            let directory = exchangeDirectory() as NSString
            let stagingPath = directory.appendingPathComponent("\(UUID().uuidString).staging")
            guard let copy = stage(url, in: stagingPath, force: force) else {
                try? FileManager.default.removeItem(atPath: stagingPath)
                throw failure
            }
            pvqlLog("staged a copy the service can read")
            do {
                let reply = try exchange(url, force: force, copy: copy, early: early, scene: scene)
                try? FileManager.default.removeItem(atPath: stagingPath)
                return reply
            } catch let underway as Failure where underway.isUnderway {
                // The service still reads the copy, and removes it once it is stale.
                throw underway
            } catch {
                try? FileManager.default.removeItem(atPath: stagingPath)
                throw error
            }
        }
    }

    /// Returns the service's description of a file without converting it, or nil.
    static func requestDescription(_ url: URL) -> [String: Any]? {
        (try? exchange(url, force: false, copy: nil, early: nil, describe: true))?.info
    }

    /// Copies a file where the service can read it, unless it is a folder or above the limit.
    static func stage(_ url: URL, in stagingPath: String, force: Bool) -> String? {
        let manager = FileManager.default
        var isFolder: ObjCBool = false
        // A directory is never staged: its own size says nothing about what it holds.
        guard manager.fileExists(atPath: url.path, isDirectory: &isFolder), !isFolder.boolValue,
              let size = (try? manager.attributesOfItem(atPath: url.path))?[.size] as? Int,
              size > 0, force || size <= maximumFileBytes()
        else {
            return nil
        }
        let staged = (stagingPath as NSString).appendingPathComponent(url.lastPathComponent)
        do {
            try manager.createDirectory(atPath: stagingPath, withIntermediateDirectories: true)
            try manager.copyItem(atPath: url.path, toPath: staged)
        } catch {
            pvqlLog("could not stage a copy: \(error.localizedDescription)")
            return nil
        }
        return staged
    }

    /// Sends one request to the render service and waits for its reply.
    static func exchange(
        _ url: URL,
        force: Bool,
        copy: String?,
        early: (([String: Any]) -> Void)?,
        scene: Bool? = nil,
        describe: Bool = false
    ) throws -> Reply {
        let directory = exchangeDirectory() as NSString
        let token = UUID().uuidString
        let requestPath = directory.appendingPathComponent("\(token)\(requestSuffix)")
        let replyPath = directory.appendingPathComponent("\(token)\(replySuffix)")
        let earlyPath = directory.appendingPathComponent("\(token)\(earlySuffix)")
        let scratchPath = requestPath + ".tmp"
        let manager = FileManager.default

        var body: [String: Any] = ["path": url.path]
        if force {
            body["force"] = true
        }
        if let copy {
            body["copy"] = copy
        }
        // Sent when a tab chose the view; otherwise the settings choose.
        if let scene {
            body["scene"] = scene
        }
        if describe {
            body["describe"] = true
        }
        if let attributes = try? manager.attributesOfItem(atPath: url.path) {
            body["size"] = (attributes[.size] as? NSNumber)?.intValue ?? 0
            if let modified = attributes[.modificationDate] as? Date {
                body["mtime"] = Int(modified.timeIntervalSince1970)
            }
        }

        pvqlLog("requesting \(url.path)")
        do {
            let payload = try JSONSerialization.data(withJSONObject: body)
            try payload.write(to: URL(fileURLWithPath: scratchPath))
            try manager.moveItem(atPath: scratchPath, toPath: requestPath)
        } catch {
            throw Failure(message: "Could not reach the render service.\n\n\(error.localizedDescription)")
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
                throw Failure(message: "The render service sent a malformed reply.")
            }
            if json["ok"] as? Bool == true {
                let info = json["info"] as? [String: Any]
                if describe {
                    return Reply(payload: nil, info: info)
                }
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
                needsCopy: json["needs_copy"] as? Bool == true,
                isNotMesh: json["not_a_mesh"] as? Bool == true
            )
        }

        // A request the service took is still being worked on; one it never took means it is gone.
        if !manager.fileExists(atPath: requestPath) {
            pvqlLog("service is still working after \(Int(replyTimeout())) s")
            throw Failure(
                message: "The render service is still working on \(url.lastPathComponent) after \(Int(replyTimeout())) seconds.\n\nSelect the file again later to see its preview.",
                isUnderway: true
            )
        }
        try? manager.removeItem(atPath: requestPath)
        pvqlLog("service did not answer")
        throw Failure(message: serviceMissingMessage)
    }
}
