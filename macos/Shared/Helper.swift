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

/// The debug log file, kept open between lines and started afresh once it grows too large.
private final class DebugLog {
    static let shared = DebugLog()
    static let maximumBytes: UInt64 = 1_000_000
    /// How many lines go by between looks at the file's size.
    static let linesPerCheck = 100

    let path = (NSTemporaryDirectory() as NSString).appendingPathComponent("pvql-extension.log")
    private let queue = DispatchQueue(label: "org.pyvista.PyVistaQuickLook.log")
    private var handle: FileHandle?
    private var linesSinceCheck = DebugLog.linesPerCheck

    /// Appends a line, reopening the file when it is missing or has outgrown its limit.
    func append(_ line: String) {
        queue.sync {
            linesSinceCheck += 1
            if linesSinceCheck >= Self.linesPerCheck {
                linesSinceCheck = 0
                reopen()
            }
            handle?.write(Data(line.utf8))
        }
    }

    /// Opens the file for appending, emptied first when it is above the limit.
    private func reopen() {
        try? handle?.close()
        handle = nil
        let manager = FileManager.default
        if let size = (try? manager.attributesOfItem(atPath: path))?[.size] as? UInt64, size > Self.maximumBytes {
            try? manager.removeItem(atPath: path)
        }
        if !manager.fileExists(atPath: path) {
            manager.createFile(atPath: path, contents: nil)
        }
        handle = FileHandle(forWritingAtPath: path)
        handle?.seekToEndOfFile()
    }
}

/// Appends a line to a debug log inside the extension's own temporary directory.
func pvqlLog(_ message: String) {
    osLog.notice("\(message, privacy: .public)")
    let age = String(format: "%6.2fs", processAge())
    DebugLog.shared.append("\(logStamp.string(from: Date())) [pid \(getpid()) +\(age)] \(message)\n")
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
    /// Seconds a helper asked to stop gets before it is killed.
    static let killGrace: TimeInterval = 5
    static let requestSuffix = ".pvqlreq"
    static let replySuffix = ".pvqlrep"
    static let earlySuffix = ".pvqlpre"

    static let serviceMissingMessage = """
    The PyVista Quick Look render service is not answering.

    Check that PyVista Quick Look is switched on in System Settings, under
    General, Login Items & Extensions. Otherwise start it with:

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

    /// The settings one preview load reads once; the next load reads the file again.
    struct Settings {
        /// Whether a file opens in 3D rather than on the picture it carries.
        let forceInteractive: Bool
        /// Where the info panel goes, as the settings name it; nil leaves it to the panel.
        let infoPanel: String?
        /// Files above this many bytes are not staged; the service refuses them at the same size.
        let maximumFileBytes: Int
        /// Seconds to wait for the service: its own conversion timeout, and some to spare.
        let replyTimeout: TimeInterval

        init(_ configuration: [String: Any]?) {
            // False when unset, as config.py's DEFAULTS has it.
            forceInteractive = configuration?["force_interactive"] as? Bool ?? false
            infoPanel = configuration?["info_panel"] as? String
            let megabytes = (configuration?["max_file_size_mb"] as? NSNumber)?.intValue ?? 2048
            maximumFileBytes = megabytes > 0 ? megabytes * 1_000_000 : Int.max
            replyTimeout = ((configuration?["timeout"] as? NSNumber)?.doubleValue ?? 60) + 30
        }

        /// Reads the settings file as it is at this moment.
        static func load() -> Settings {
            Settings(configuration())
        }
    }

    /// Returns the settings: the copy the service keeps in the exchange folder, which the
    /// sandbox lets the extension read, else the file itself.
    static func configuration() -> [String: Any]? {
        let mirrored = URL(fileURLWithPath: exchangeDirectory).appendingPathComponent("config.json")
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
        Bundle.main.bundleURL.appendingPathComponent("Contents/Library/PyVista Quick Look.app/Contents/PlugIns")
    }

    /// The folder the service watches: the container of the preview extension the app ships.
    static let exchangeDirectory: String = {
        guard ProcessInfo.processInfo.environment["APP_SANDBOX_CONTAINER_ID"] == nil,
              let identifier = previewExtension(in: plugInsFolder())
        else {
            return NSTemporaryDirectory()
        }
        let container = "\(realHome())/Library/Containers/\(identifier)/Data/tmp/"
        try? FileManager.default.createDirectory(atPath: container, withIntermediateDirectories: true)
        return container
    }()

    /// Returns the size and modification date of a file, of what a link points at for a link.
    static func fileFacts(_ url: URL) -> (size: Int, modified: Date?)? {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.resolvingSymlinksInPath().path) else {
            return nil
        }
        return ((attributes[.size] as? NSNumber)?.intValue ?? 0, attributes[.modificationDate] as? Date)
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
            guard process.isRunning else {
                return
            }
            process.terminate()
            // A helper that does not act on the request is killed, so the wait below ends.
            DispatchQueue.global().asyncAfter(deadline: .now() + killGrace) {
                if process.isRunning {
                    kill(process.processIdentifier, SIGKILL)
                }
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

    /// Asks the render service for a preview of a mesh file.
    ///
    /// `force` previews a file above the size limit. `early` is handed what the file's
    /// header says when the service sends it ahead of a slow preview. The file is copied
    /// only when the service cannot read its folder.
    static func requestPreview(
        _ url: URL,
        force: Bool = false,
        scene: Bool? = nil,
        settings: Settings,
        early: (([String: Any]) -> Void)? = nil
    ) throws -> Reply {
        do {
            return try exchange(url, force: force, copy: nil, settings: settings, early: early, scene: scene)
        } catch let failure as Failure where failure.needsCopy {
            let directory = exchangeDirectory as NSString
            let stagingPath = directory.appendingPathComponent("\(UUID().uuidString).staging")
            guard let copy = stage(url, in: stagingPath, force: force, settings: settings) else {
                try? FileManager.default.removeItem(atPath: stagingPath)
                throw failure
            }
            pvqlLog("staged a copy the service can read")
            do {
                let reply = try exchange(url, force: force, copy: copy, settings: settings, early: early, scene: scene)
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
    static func requestDescription(_ url: URL, settings: Settings) -> [String: Any]? {
        (try? exchange(url, force: false, copy: nil, settings: settings, early: nil, describe: true))?.info
    }

    /// Copies a file where the service can read it, unless it is a folder or above the limit.
    static func stage(_ url: URL, in stagingPath: String, force: Bool, settings: Settings) -> String? {
        let manager = FileManager.default
        var isFolder: ObjCBool = false
        // A directory is never staged: its own size says nothing about what it holds.
        guard manager.fileExists(atPath: url.path, isDirectory: &isFolder), !isFolder.boolValue,
              let size = fileFacts(url)?.size,
              size > 0, force || size <= settings.maximumFileBytes
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
        settings: Settings,
        early: (([String: Any]) -> Void)?,
        scene: Bool? = nil,
        describe: Bool = false
    ) throws -> Reply {
        let directory = exchangeDirectory as NSString
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
        // The size and date of what a link points at, so a preview expires with the target.
        if let facts = fileFacts(url) {
            body["size"] = facts.size
            if let modified = facts.modified {
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

        let deadline = Date().addingTimeInterval(settings.replyTimeout)
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
            let waited = Int(settings.replyTimeout)
            pvqlLog("service is still working after \(waited) s")
            throw Failure(
                message: "The render service is still working on \(url.lastPathComponent) after \(waited) seconds.\n\nSelect the file again later to see its preview.",
                isUnderway: true
            )
        }
        try? manager.removeItem(atPath: requestPath)
        pvqlLog("service did not answer")
        throw Failure(message: serviceMissingMessage)
    }
}
