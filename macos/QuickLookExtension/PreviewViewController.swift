import AppKit
import Foundation
import QuickLookUI
import SceneKit
import UniformTypeIdentifiers

/// What to put in the panel once the render service has answered.
enum Preview {
    case scene(SCNScene, MeshInfo?)
    case image(Data, MeshInfo?)
    case text(String)
    case details(URL)
    case message(String)
    /// A file above the size limit, with what its header says.
    case tooLarge(String, MeshInfo?)
}

/// How much of a text file is shown.
let textPreviewLimit = 2_000_000

/// Returns the file's contents when it reads as text, which a claimed file may turn out to be.
func textContents(of url: URL) -> String? {
    guard let handle = try? FileHandle(forReadingFrom: url) else {
        return nil
    }
    defer { try? handle.close() }
    let data = handle.readData(ofLength: textPreviewLimit)
    guard !data.isEmpty, !data.contains(0) else {
        return nil
    }
    let controls = data.filter { $0 < 0x20 && $0 != 0x09 && $0 != 0x0A && $0 != 0x0D }.count
    guard controls * 100 < data.count else {
        return nil
    }
    guard let text = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) else {
        return nil
    }
    let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? data.count
    return size > data.count ? text + "\n\n[first \(data.count / 1_000_000) MB of \(size / 1_000_000) MB]" : text
}

/// Builds a view that lets the reader turn the mesh with the mouse.
@MainActor
func sceneView(for scene: SCNScene) -> SCNView {
    let view = SCNView()
    view.scene = scene
    view.allowsCameraControl = true
    view.autoenablesDefaultLighting = true
    view.antialiasingMode = .multisampling4X
    view.backgroundColor = .clear

    view.pointOfView = previewCamera(for: scene)
    return view
}

/// Builds a scrolling view of a file's text, for claimed files that are not meshes.
@MainActor
func textView(_ text: String) -> NSView {
    let view = NSTextView()
    view.string = text
    view.isEditable = false
    view.isSelectable = true
    view.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
    view.textContainerInset = NSSize(width: 12, height: 12)
    view.autoresizingMask = [.width]
    view.isVerticallyResizable = true
    view.isHorizontallyResizable = false
    view.textContainer?.widthTracksTextView = true

    let scroll = NSScrollView()
    scroll.hasVerticalScroller = true
    scroll.documentView = view
    return scroll
}

/// Returns the opening of a failure, which is all the details view has room for.
func briefly(_ reason: String, limit: Int = 200) -> String {
    let joined = reason.split(whereSeparator: \.isNewline).map(String.init).prefix(2)
        .joined(separator: " ")
    return joined.count > limit ? String(joined.prefix(limit)) + "…" : joined
}

/// How many entries of a folder are examined when looking for a DICOM series.
let dicomSampleSize = 8

/// Returns whether a folder holds DICOM slices, the one kind of folder that is a dataset.
/// Every DICOM file carries "DICM" after a 128 byte preamble, whatever it is named.
func holdsDicomFiles(_ url: URL) -> Bool {
    let manager = FileManager.default
    let entries = (try? manager.contentsOfDirectory(
        at: url,
        includingPropertiesForKeys: [.isRegularFileKey],
        options: [.skipsHiddenFiles, .skipsSubdirectoryDescendants]
    )) ?? []
    for entry in entries.sorted(by: { $0.lastPathComponent < $1.lastPathComponent })
        .prefix(dicomSampleSize) {
        guard let handle = try? FileHandle(forReadingFrom: entry) else {
            continue
        }
        defer { try? handle.close() }
        let head = handle.readData(ofLength: 132)
        if head.count == 132, head.suffix(4) == Data("DICM".utf8) {
            return true
        }
    }
    return false
}

let utiPrefix = "org.pyvista.pyvista-quicklook"

/// Whether the file's type is one this app exported. A type macOS owns, a gzip archive or
/// a folder, has a preview of its own, which is better than any imitation of it here.
func isOwnType(_ url: URL) -> Bool {
    let type = (try? url.resourceValues(forKeys: [.contentTypeKey]))?.contentType
    return type?.identifier.hasPrefix(utiPrefix) ?? false
}

/// How large the file's icon is drawn beside its details.
let iconSide: CGFloat = 128

/// Returns a folder's total size and how many items it holds, as the Finder reports them.
func folderFacts(_ url: URL) -> String {
    let manager = FileManager.default
    let entries = (try? manager.contentsOfDirectory(
        atPath: url.path
    )) ?? []
    var bytes: Int64 = 0
    if let walk = manager.enumerator(
        at: url,
        includingPropertiesForKeys: [.totalFileAllocatedSizeKey, .fileSizeKey],
        options: []
    ) {
        for case let item as URL in walk {
            let values = try? item.resourceValues(forKeys: [.totalFileAllocatedSizeKey, .fileSizeKey])
            bytes += Int64(values?.totalFileAllocatedSize ?? values?.fileSize ?? 0)
        }
    }
    let size = ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    return "\(size), \(entries.count) \(entries.count == 1 ? "item" : "items")"
}

/// Builds what Quick Look shows for a file it cannot open: the icon on the left, and the
/// name, size, and date beside it. Shown for anything claimed that holds no mesh, since
/// an extension cannot hand a file back to Quick Look to preview in its own way.
@MainActor
func detailsView(_ url: URL) -> NSView {
    // A file icon is drawn at 1024 points unless it is asked to be smaller, which is
    // wider than the panel; the size has to be set on the image, not on the view.
    let image = NSWorkspace.shared.icon(forFile: url.path)
    image.size = NSSize(width: iconSide, height: iconSide)
    let icon = NSImageView(image: image)
    icon.imageScaling = .scaleProportionallyUpOrDown
    icon.translatesAutoresizingMaskIntoConstraints = false
    icon.setContentHuggingPriority(.required, for: .horizontal)
    icon.setContentHuggingPriority(.required, for: .vertical)

    let name = NSTextField(labelWithString: url.lastPathComponent)
    name.font = NSFont.systemFont(ofSize: 28, weight: .bold)
    name.lineBreakMode = .byTruncatingMiddle
    name.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

    let values = try? url.resourceValues(forKeys: [
        .fileSizeKey, .contentModificationDateKey, .isDirectoryKey,
    ])
    var lines: [String] = []
    if values?.isDirectory == true {
        lines.append(folderFacts(url))
    } else if let bytes = values?.fileSize {
        lines.append(ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file))
    }
    if let modified = values?.contentModificationDate {
        let stamp = DateFormatter.localizedString(
            from: modified, dateStyle: .medium, timeStyle: .medium
        )
        lines.append("Last modified \(stamp)")
    }
    let facts = NSTextField(labelWithString: lines.joined(separator: "\n"))
    facts.font = NSFont.systemFont(ofSize: 16)
    facts.textColor = .secondaryLabelColor

    let text = NSStackView(views: [name, facts])
    text.orientation = .vertical
    text.alignment = .leading
    text.spacing = 8

    let row = NSStackView(views: [icon, text])
    row.orientation = .horizontal
    row.alignment = .centerY
    row.spacing = 24
    row.translatesAutoresizingMaskIntoConstraints = false

    let container = NSView()
    container.addSubview(row)
    NSLayoutConstraint.activate([
        icon.widthAnchor.constraint(equalToConstant: iconSide),
        icon.heightAnchor.constraint(equalToConstant: iconSide),
        row.centerXAnchor.constraint(equalTo: container.centerXAnchor),
        row.centerYAnchor.constraint(equalTo: container.centerYAnchor),
        row.leadingAnchor.constraint(greaterThanOrEqualTo: container.leadingAnchor, constant: 24),
        row.trailingAnchor.constraint(lessThanOrEqualTo: container.trailingAnchor, constant: -24),
    ])
    return container
}

/// Returns the render with the dataset's info beside it, or the render alone without info.
@MainActor
func withInfo(_ render: NSView, _ info: MeshInfo?) -> NSView {
    guard let info, !info.isEmpty else {
        return render
    }
    let arrangement = (Helper.configuration()?["info_panel"] as? String).flatMap(InfoArrangement.init)
    return MeshPreviewView(render: render, info: info, arrangement: arrangement ?? .automatic)
}

/// Builds a view showing text, used when a mesh cannot be previewed.
@MainActor
func messageView(_ text: String) -> NSView {
    let field = NSTextField(wrappingLabelWithString: text)
    field.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
    field.isSelectable = true
    field.translatesAutoresizingMaskIntoConstraints = false

    let container = NSView()
    container.addSubview(field)
    NSLayoutConstraint.activate([
        field.topAnchor.constraint(equalTo: container.topAnchor, constant: 16),
        field.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 16),
        field.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -16),
        field.bottomAnchor.constraint(lessThanOrEqualTo: container.bottomAnchor, constant: -16),
    ])
    return container
}

/// A button that runs a closure when pressed.
final class ActionButton: NSButton {
    private var onPress: (() -> Void)?

    convenience init(title: String, onPress: @escaping () -> Void) {
        self.init(frame: .zero)
        self.title = title
        bezelStyle = .rounded
        self.onPress = onPress
        target = self
        action = #selector(pressed)
    }

    @objc private func pressed() {
        onPress?()
    }
}

/// Centres a column of views in an otherwise empty view.
@MainActor
func centred(_ views: [NSView]) -> NSView {
    let stack = NSStackView(views: views)
    stack.orientation = .vertical
    stack.alignment = .centerX
    stack.spacing = 12
    stack.translatesAutoresizingMaskIntoConstraints = false
    let container = NSView()
    container.addSubview(stack)
    NSLayoutConstraint.activate([
        stack.centerXAnchor.constraint(equalTo: container.centerXAnchor),
        stack.centerYAnchor.constraint(equalTo: container.centerYAnchor),
        stack.widthAnchor.constraint(lessThanOrEqualTo: container.widthAnchor, constant: -32),
    ])
    return container
}

/// Builds the stand-in for a render that is still being made.
@MainActor
func progressView(_ text: String) -> NSView {
    let spinner = NSProgressIndicator()
    spinner.style = .spinning
    spinner.startAnimation(nil)
    return centred([spinner, infoLabel(text, color: .secondaryLabelColor)])
}

/// Builds the stand-in for a render of a file above the size limit, with a way to preview it anyway.
@MainActor
func largeFileView(_ message: String, onPreview: @escaping () -> Void) -> NSView {
    // The service indents a command it suggests, which is set apart as code.
    let lines = message.components(separatedBy: "\n")
    let prose = lines.filter { !$0.hasPrefix("    ") }.joined(separator: "\n")
    let label = NSTextField(wrappingLabelWithString: prose.trimmingCharacters(in: .whitespacesAndNewlines))
    label.font = NSFont.systemFont(ofSize: 12)
    label.alignment = .center
    label.isSelectable = true
    label.preferredMaxLayoutWidth = 420
    let commands = lines.filter { $0.hasPrefix("    ") }.map { codeBlock($0.trimmingCharacters(in: .whitespaces)) }
    return centred([label] + commands + [ActionButton(title: "Preview Anyway", onPress: onPreview)])
}

/// A rounded, tinted panel behind a command, as a code block is shown.
final class CodeBlockView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        NSColor.quaternaryLabelColor.setFill()
        NSBezierPath(roundedRect: bounds, xRadius: 6, yRadius: 6).fill()
    }
}

/// Builds a command set in a monospaced font on a tinted panel.
@MainActor
func codeBlock(_ text: String) -> NSView {
    let label = NSTextField(labelWithString: text)
    label.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
    label.isSelectable = true
    label.translatesAutoresizingMaskIntoConstraints = false
    label.setContentHuggingPriority(.required, for: .horizontal)
    let block = CodeBlockView()
    block.setContentHuggingPriority(.required, for: .horizontal)
    block.addSubview(label)
    NSLayoutConstraint.activate([
        label.topAnchor.constraint(equalTo: block.topAnchor, constant: 6),
        label.bottomAnchor.constraint(equalTo: block.bottomAnchor, constant: -6),
        label.leadingAnchor.constraint(equalTo: block.leadingAnchor, constant: 10),
        label.trailingAnchor.constraint(equalTo: block.trailingAnchor, constant: -10),
    ])
    return block
}

/// Calls Quick Look's completion handler once, however many times the preview changes.
@MainActor
final class Completion {
    private var handler: ((Error?) -> Void)?

    init(_ handler: ((Error?) -> Void)?) {
        self.handler = handler
    }

    /// Whether Quick Look is still waiting to hear whether there is a preview.
    var isPending: Bool { handler != nil }

    func finish(_ error: Error?) {
        handler?(error)
        handler = nil
    }
}

/// Shows PyVista-readable mesh files in the Quick Look panel.
@objc(PVQLPreviewViewController)
final class PVQLPreviewViewController: NSViewController, QLPreviewingController {
    /// Handed back for a file this extension has nothing to add to. Quick Look then draws
    /// the preview it would have drawn anyway; the description is empty because Quick Look
    /// puts it above that preview, where there is nothing worth saying.
    static let noPreview = NSError(
        domain: utiPrefix,
        code: 1,
        userInfo: [NSLocalizedDescriptionKey: ""]
    )

    override func loadView() {
        view = NSView(frame: NSRect(x: 0, y: 0, width: 800, height: 600))
    }

    /// Fills the panel with a view, pinned to every edge so it resizes with it.
    @MainActor
    func show(_ replacement: NSView) {
        view.subviews.forEach { $0.removeFromSuperview() }
        replacement.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(replacement)
        NSLayoutConstraint.activate([
            replacement.topAnchor.constraint(equalTo: view.topAnchor),
            replacement.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            replacement.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            replacement.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
        view.layoutSubtreeIfNeeded()
    }

    func preparePreviewOfFile(
        at url: URL,
        completionHandler handler: @escaping (Error?) -> Void
    ) {
        pvqlLog("preview requested for \(url.path)")
        DispatchQueue.global(qos: .userInitiated).async {
            // Only file work here; AppKit views are built on the main thread below.
            let isFolder = (try? url.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory
            if isFolder == true, !holdsDicomFiles(url) {
                // Hand it straight back, without waiting on the service, so Quick Look
                // shows the folder preview it would have shown anyway.
                pvqlLog("declining an ordinary folder")
                DispatchQueue.main.async { handler(Self.noPreview) }
                return
            }

            self.load(url, force: false, handler: handler)
        }
    }

    /// Returns what pressing "Preview Anyway" does: load the file past its size limit.
    func previewAnyway(_ url: URL, _ info: MeshInfo?) -> () -> Void {
        { [weak self] in
            guard let self else {
                return
            }
            self.show(withInfo(progressView("Reading \(url.lastPathComponent)\u{2026}"), info))
            DispatchQueue.global(qos: .userInitiated).async {
                self.load(url, force: true, handler: nil)
            }
        }
    }

    /// Fetches a preview and shows it, showing what the header says first when that comes early.
    ///
    /// `handler` is Quick Look's, called once the panel has something to show; it is nil when a
    /// file is loaded again from a panel already showing.
    nonisolated func load(_ url: URL, force: Bool, handler: ((Error?) -> Void)?) {
        let completion = DispatchQueue.main.sync { Completion(handler) }
        let name = url.lastPathComponent
        let outcome: Preview
        do {
            let reply = try Helper.requestPreview(url, force: force) { info in
                DispatchQueue.main.async {
                    self.show(withInfo(progressView("Reading \(name)\u{2026}"), MeshInfo(info)))
                    completion.finish(nil)
                }
            }
            let info = MeshInfo(reply.info)
            switch reply.payload {
            case let .scene(sceneURL):
                defer { try? FileManager.default.removeItem(at: sceneURL) }
                outcome = .scene(try SCNScene(url: sceneURL, options: nil), info)
            case let .image(png):
                outcome = .image(png, info)
            }
        } catch {
            let failure = error as? Helper.Failure
            let message = failure?.message ?? error.localizedDescription
            pvqlLog("no preview: \(message.prefix(120))")
            if failure?.isTooLarge == true {
                outcome = .tooLarge(message, MeshInfo(failure?.info))
            } else if failure?.isNotMesh != true {
                // The file may well be a mesh, so say what went wrong rather than show it as text.
                outcome = .message("Could not preview \(name)\n\n\(message)")
            } else if !isOwnType(url) {
                // macOS has a preview of its own for this type; let it show that, if it still can.
                pvqlLog("declining, not a mesh: \(briefly(message))")
                DispatchQueue.main.async {
                    if completion.isPending {
                        completion.finish(Self.noPreview)
                    } else {
                        self.show(messageView("Could not preview \(name)\n\n\(message)"))
                    }
                }
                return
            } else if let text = textContents(of: url) {
                outcome = .text(text)
            } else {
                outcome = .details(url)
            }
        }

        DispatchQueue.main.async {
            switch outcome {
            case let .scene(scene, info):
                self.show(withInfo(sceneView(for: scene), info))
                pvqlLog("showing an interactive scene in \(self.view.bounds.size)")
            case let .image(png, info):
                let imageView = NSImageView()
                imageView.imageScaling = .scaleProportionallyUpOrDown
                imageView.image = NSImage(data: png)
                self.show(withInfo(imageView, info))
                pvqlLog("showing a rendered image in \(self.view.bounds.size)")
            case let .tooLarge(message, info):
                self.show(withInfo(largeFileView(message, onPreview: self.previewAnyway(url, info)), info))
                pvqlLog("showing a size notice")
            case let .text(text):
                self.show(textView(text))
                pvqlLog("showing text, \(text.count) characters")
            case let .details(fileURL):
                self.show(detailsView(fileURL))
                pvqlLog("showing file details")
            case let .message(text):
                self.show(messageView(text))
                pvqlLog("showing a message: \(text.prefix(120))")
            }
            completion.finish(nil)
        }
    }
}
