import AppKit
import QuickLookUI

/// The diagnostics window, or the Quick Look panel for files opened with the app.
final class StatusController: NSObject, NSApplicationDelegate {
    let window = NSWindow(
        contentRect: NSRect(x: 0, y: 0, width: 720, height: 520),
        styleMask: [.titled, .closable, .miniaturizable, .resizable],
        backing: .buffered,
        defer: false
    )
    let textView = NSTextView()
    let runButton = NSButton(title: "Run Diagnostics", target: nil, action: nil)
    /// Files the app was asked to open, each shown in a window.
    var opened: [URL] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        // Opened for a file, the app is just the panel; closing it quits.
        guard opened.isEmpty else {
            return
        }
        buildWindow()
        runDiagnostics()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    /// Shows each file opened with the app in a window of its own, previewed as Quick Look would.
    func application(_ application: NSApplication, open urls: [URL]) {
        opened += urls
        for url in urls {
            let controller = PVQLPreviewViewController()
            let window = NSWindow(contentViewController: controller)
            window.title = url.lastPathComponent
            window.setContentSize(NSSize(width: 1100, height: 720))
            window.center()
            window.isReleasedWhenClosed = false
            window.makeKeyAndOrderFront(nil)
            controller.preparePreviewOfFile(at: url) { error in
                guard error != nil else {
                    return
                }
                // Declined, as a file that is not a mesh is; show its details instead.
                DispatchQueue.main.async { controller.show(detailsView(url)) }
            }
            pvqlLog("opened \(url.lastPathComponent) in a window")
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Installs a minimal main menu so the standard shortcuts work.
    func buildMenu() {
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit PyVista Quick Look", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        mainMenu.addItem(appItem)
        NSApp.mainMenu = mainMenu
    }

    /// Lays out the diagnostics window.
    func buildWindow() {
        window.title = "PyVista Quick Look"
        window.center()

        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.documentView = textView
        textView.isEditable = false
        textView.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        textView.textContainerInset = NSSize(width: 8, height: 8)
        textView.autoresizingMask = [.width]

        runButton.target = self
        runButton.action = #selector(runDiagnostics)
        let configButton = NSButton(title: "Open Config File", target: self, action: #selector(openConfig))
        let cacheButton = NSButton(title: "Reveal Cache", target: self, action: #selector(revealCache))
        let uninstallButton = NSButton(title: "Uninstall…", target: self, action: #selector(uninstall))

        let buttons = NSStackView(views: [runButton, configButton, cacheButton, uninstallButton])
        buttons.orientation = .horizontal
        buttons.spacing = 8

        let stack = NSStackView(views: [scroll, buttons])
        stack.orientation = .vertical
        stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        stack.translatesAutoresizingMaskIntoConstraints = false
        scroll.setContentHuggingPriority(.defaultLow, for: .vertical)

        window.contentView = NSView()
        window.contentView?.addSubview(stack)
        if let content = window.contentView {
            NSLayoutConstraint.activate([
                stack.topAnchor.constraint(equalTo: content.topAnchor),
                stack.bottomAnchor.constraint(equalTo: content.bottomAnchor),
                stack.leadingAnchor.constraint(equalTo: content.leadingAnchor),
                stack.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            ])
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Runs `pvql doctor` and shows its report.
    @objc func runDiagnostics() {
        runButton.isEnabled = false
        textView.string = "Running diagnostics…\n"
        DispatchQueue.global(qos: .userInitiated).async {
            let report: String
            do {
                let result = try Helper.run(["doctor"])
                report = [result.output, result.errors].filter { !$0.isEmpty }.joined(separator: "\n")
            } catch {
                report = (error as? Helper.Failure)?.message ?? error.localizedDescription
            }
            DispatchQueue.main.async {
                self.textView.string = report
                self.runButton.isEnabled = true
            }
        }
    }

    /// Opens the configuration file, creating it first when it does not exist.
    @objc func openConfig() {
        let path = URL(fileURLWithPath: realHome())
            .appendingPathComponent("Library/Application Support/PyVistaQuickLook/config.json")
        if !FileManager.default.fileExists(atPath: path.path) {
            _ = try? Helper.run(["config", "--init"])
        }
        NSWorkspace.shared.open(path)
    }

    /// Asks, then removes the app, the render service, the PyVista environment, and `pvql`.
    @objc func uninstall() {
        let alert = NSAlert()
        alert.messageText = "Uninstall PyVista Quick Look?"
        alert.informativeText = "This removes the app, its Quick Look extension, the render service, the PyVista environment, and the cache. The configuration file is kept."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Uninstall")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else {
            return
        }
        runButton.isEnabled = false
        textView.string = "Uninstalling…\n"
        DispatchQueue.global(qos: .userInitiated).async {
            let report: String
            do {
                let result = try Helper.run(["uninstall", "--yes"])
                report = [result.output, result.errors].filter { !$0.isEmpty }.joined(separator: "\n")
            } catch {
                report = (error as? Helper.Failure)?.message ?? error.localizedDescription
            }
            DispatchQueue.main.async {
                self.textView.string = report + "\n\nYou can quit now."
            }
        }
    }

    /// Reveals the preview cache in the Finder.
    @objc func revealCache() {
        let path = URL(fileURLWithPath: realHome())
            .appendingPathComponent("Library/Caches/PyVistaQuickLook")
        try? FileManager.default.createDirectory(at: path, withIntermediateDirectories: true)
        NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: path.path)
    }
}

let controller = StatusController()
let application = NSApplication.shared
application.delegate = controller
application.setActivationPolicy(.regular)
application.run()
