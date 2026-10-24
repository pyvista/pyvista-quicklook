import AppKit
import Foundation
import QuickLookUI
import SceneKit
import UniformTypeIdentifiers

/// What to put in the panel once the render service has answered.
enum Preview {
    case scene(SCNScene, Framing, MeshInfo?)
    case image(Data, MeshInfo?)
    case text(String)
    /// A file shown by its icon and facts, which are gathered off the main thread.
    case details(URL, String)
    case message(String)
    /// A file handed back to Quick Look, with the message shown when there is no handing back.
    case declined(String)
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
    let size = Helper.fileFacts(url)?.size ?? data.count
    guard size > data.count else {
        return text
    }
    let shown = String(format: "%.1f", Double(data.count) / 1_000_000)
    let whole = String(format: "%.1f", Double(size) / 1_000_000)
    return text + "\n\n[first \(shown) MB of \(whole) MB]"
}

/// A scene view that keeps the lights turning with its camera.
final class LitSceneView: SCNView {
    /// Held here, since a view only keeps a weak reference to its delegate.
    var lights: LightsFollowCamera?
    /// The point the camera turns about and moves toward, as VTK's focal point.
    var target = SIMD3<Float>(0, 0, 0)
    /// Whether the reader has moved the camera from where the scene opened.
    private var moved = false
    private var lastMouse = NSPoint.zero

    override func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        // The still fits the shorter side, so the view angle spans that side until the scene is moved.
        if !moved {
            pointOfView?.camera?.projectionDirection = newSize.width < newSize.height ? .horizontal : .vertical
        }
    }

    /// Reads the camera, moves it, and writes it back; the lights follow it at the next frame.
    private func move(_ change: (inout Trackball, SIMD2<Float>) -> Void) {
        guard let node = pointOfView else {
            return
        }
        let worldUp = simd_act(node.simdWorldOrientation, SIMD3<Float>(0, 1, 0))
        var camera = Trackball(position: node.simdWorldPosition, target: target, up: worldUp)
        change(&camera, SIMD2(Float(bounds.width), Float(bounds.height)))
        node.simdWorldPosition = camera.position
        node.simdLook(at: camera.target, up: camera.up, localFront: SIMD3(0, 0, -1))
        target = camera.target
        moved = true
    }

    private func point(_ event: NSEvent) -> NSPoint {
        convert(event.locationInWindow, from: nil)
    }

    override func mouseDown(with event: NSEvent) {
        lastMouse = point(event)
    }

    override func rightMouseDown(with event: NSEvent) {
        lastMouse = point(event)
    }

    override func otherMouseDown(with event: NSEvent) {
        lastMouse = point(event)
    }

    override func mouseDragged(with event: NSEvent) {
        let now = point(event)
        let dx = Float(now.x - lastMouse.x)
        let dy = Float(now.y - lastMouse.y)
        let was = SIMD2(Float(lastMouse.x), Float(lastMouse.y))
        lastMouse = now
        let flags = event.modifierFlags
        move { camera, viewport in
            if flags.contains(.shift), flags.contains(.control) {
                camera.dolly(dy: dy, viewport: viewport)
            } else if flags.contains(.shift) {
                self.pan(&camera, dx: dx, dy: dy, viewport: viewport)
            } else if flags.contains(.control) {
                camera.spin(from: was, to: SIMD2(Float(now.x), Float(now.y)), viewport: viewport)
            } else {
                camera.rotate(dx: dx, dy: dy, viewport: viewport)
            }
        }
    }

    override func rightMouseDragged(with event: NSEvent) {
        let now = point(event)
        let dy = Float(now.y - lastMouse.y)
        lastMouse = now
        move { camera, viewport in camera.dolly(dy: dy, viewport: viewport) }
    }

    override func otherMouseDragged(with event: NSEvent) {
        let now = point(event)
        let dx = Float(now.x - lastMouse.x)
        let dy = Float(now.y - lastMouse.y)
        lastMouse = now
        move { camera, viewport in self.pan(&camera, dx: dx, dy: dy, viewport: viewport) }
    }

    override func scrollWheel(with event: NSEvent) {
        // A wheel counts in notches; a trackpad in points, of which this many make a notch.
        let notches = Float(event.scrollingDeltaY) / (event.hasPreciseScrollingDeltas ? 30 : 1)
        move { camera, _ in camera.wheel(notches) }
    }

    override func magnify(with event: NSEvent) {
        let factor = 1 + Float(event.magnification)
        move { camera, _ in camera.dolly(max(factor, 0.1)) }
    }

    private func pan(_ camera: inout Trackball, dx: Float, dy: Float, viewport: SIMD2<Float>) {
        guard let lens = pointOfView?.camera else {
            return
        }
        camera.pan(
            dx: dx,
            dy: dy,
            viewport: viewport,
            fieldOfView: Float(lens.fieldOfView),
            vertical: lens.projectionDirection == .vertical
        )
    }
}

/// A description fetched on another thread, which any number of waits can ask for.
final class DescriptionWait: @unchecked Sendable {
    private let done = DispatchGroup()
    private var info: [String: Any]?

    init(_ url: URL, settings: Helper.Settings) {
        done.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            self.info = Helper.requestDescription(url, settings: settings)
            self.done.leave()
        }
    }

    /// Returns the description once it comes, or nil when there is none or it takes too long.
    func wait(seconds: Double = .infinity) -> [String: Any]? {
        let deadline: DispatchTime = seconds.isFinite ? .now() + seconds : .distantFuture
        guard done.wait(timeout: deadline) == .success else {
            return nil
        }
        return info
    }
}

/// Builds a view that lets the reader turn the mesh with the mouse.
@MainActor
func sceneView(for scene: SCNScene, framing: Framing) -> SCNView {
    let view = LitSceneView()
    view.scene = scene
    view.antialiasingMode = .multisampling4X
    view.backgroundColor = .clear

    view.pointOfView = framing.node
    view.target = framing.target
    // The camera comes as near as the reader likes without the scene cutting off; the
    // range previewCamera sets still serves the stills.
    framing.node.camera?.automaticallyAdjustsZRange = true
    view.lights = LightsFollowCamera(lightLikePyVista(scene))
    view.delegate = view.lights
    // Hidden, and drawn continuously, until a few frames are out: the first can show a blank.
    view.alphaValue = 0
    view.rendersContinuously = true
    view.lights?.onReady = { [weak view] in
        view?.alphaValue = 1
        view?.rendersContinuously = false
    }
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

/// Returns a file's size, or a folder's contents, and its date, one line each, walking the folder.
func fileFacts(_ url: URL) -> String {
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
    return lines.joined(separator: "\n")
}

/// Builds what Quick Look shows for a file it cannot open: the icon on the left, and the
/// name and `facts` beside it. Shown for anything claimed that holds no mesh, since
/// an extension cannot hand a file back to Quick Look to preview in its own way.
@MainActor
func detailsView(_ url: URL, facts: String) -> NSView {
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

    let factsLabel = NSTextField(labelWithString: facts)
    factsLabel.font = NSFont.systemFont(ofSize: 16)
    factsLabel.textColor = .secondaryLabelColor

    let text = NSStackView(views: [name, factsLabel])
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
func withInfo(_ render: NSView, _ info: MeshInfo?, _ settings: Helper.Settings) -> NSView {
    guard let info, !info.isEmpty else {
        return render
    }
    let arrangement = settings.infoPanel.flatMap(InfoArrangement.init)
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

/// Returns a view showing a picture scaled to fit, or nil when the bytes are not an image.
@MainActor
func pictureView<View: NSImageView>(_ png: Data?, as kind: View.Type = NSImageView.self) -> View? {
    guard let png, let image = NSImage(data: png) else {
        return nil
    }
    let imageView = kind.init(frame: .zero)
    imageView.imageScaling = .scaleProportionallyUpOrDown
    imageView.image = image
    // Without this the picture's own size wins over the panel's, and a small panel crops it.
    for orientation in [NSLayoutConstraint.Orientation.horizontal, .vertical] {
        imageView.setContentCompressionResistancePriority(.defaultLow, for: orientation)
        imageView.setContentHuggingPriority(.defaultLow, for: orientation)
    }
    return imageView
}

/// Lays a small view over the bottom right corner of a larger one.
@MainActor
func overlaid(_ base: NSView, corner: NSView) -> NSView {
    let container = NSView()
    for part in [base, corner] {
        part.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(part)
    }
    NSLayoutConstraint.activate([
        base.topAnchor.constraint(equalTo: container.topAnchor),
        base.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        base.leadingAnchor.constraint(equalTo: container.leadingAnchor),
        base.trailingAnchor.constraint(equalTo: container.trailingAnchor),
    ])
    NSLayoutConstraint.activate([
        corner.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -12),
        corner.bottomAnchor.constraint(equalTo: container.bottomAnchor, constant: -12),
        corner.leadingAnchor.constraint(greaterThanOrEqualTo: container.leadingAnchor, constant: 12),
    ])
    return container
}

/// A picture that calls `onTouch` the first time a mouse or trackpad gesture lands on it.
final class TouchablePicture: NSImageView {
    /// Called once, by the first press, scroll or gesture on the picture.
    var onTouch: (() -> Void)?

    /// Calls `onTouch` once.
    private func touched() {
        let action = onTouch
        onTouch = nil
        action?()
    }

    override func mouseDown(with event: NSEvent) {
        touched()
    }

    override func rightMouseDown(with event: NSEvent) {
        touched()
    }

    override func otherMouseDown(with event: NSEvent) {
        touched()
    }

    override func scrollWheel(with event: NSEvent) {
        touched()
    }

    override func magnify(with event: NSEvent) {
        touched()
    }

    override func rotate(with event: NSEvent) {
        touched()
    }
}

/// Returns a touchable picture, or nil when the bytes are not an image.
@MainActor
func touchablePicture(_ png: Data, onTouch: @escaping () -> Void) -> NSImageView? {
    let picture = pictureView(png, as: TouchablePicture.self)
    picture?.onTouch = onTouch
    return picture
}

/// What a mesh preview shows: a picture of it, or the surface to turn.
enum PreviewMode {
    case still
    case interactive
}

/// A file to preview, and the picture it carries of itself, read once per panel.
struct Request {
    let url: URL
    let force: Bool
    let carried: EmbeddedThumbnail.Picture?

    /// Whether the settings let a file's own picture open first; false in the app.
    let picturesFirst: Bool
    /// The settings as read when the panel opened.
    let settings: Helper.Settings

    var name: String { url.lastPathComponent }
    /// Whether the file opens on its own picture; a stale one is only a placeholder.
    var hasPicture: Bool { picturesFirst && carried?.isStale == false }
}

/// Builds a render placeholder: the picture dimmed under a spinner, or a spinner alone.
@MainActor
func placeholderView(_ name: String, _ thumbnail: Data?) -> NSView {
    guard let picture = pictureView(thumbnail) else {
        return progressView("Reading \(name)\u{2026}")
    }
    picture.alphaValue = 0.3
    let progress = progressView("Generating mesh preview\u{2026}")
    let container = NSView()
    for part in [picture, progress] {
        part.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(part)
        NSLayoutConstraint.activate([
            part.topAnchor.constraint(equalTo: container.topAnchor),
            part.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            part.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            part.trailingAnchor.constraint(equalTo: container.trailingAnchor),
        ])
    }
    return container
}

/// Builds the file's own picture with a note saying why it is all there is.
@MainActor
func pictureWithNote(_ picture: NSView, _ note: String) -> NSView {
    let label = NSTextField(wrappingLabelWithString: note)
    label.font = NSFont.systemFont(ofSize: 11)
    label.textColor = .secondaryLabelColor
    label.isSelectable = true
    label.preferredMaxLayoutWidth = 360
    return overlaid(picture, corner: label)
}

/// Builds the stand-in for a render of a file above the size limit, with a way to preview it anyway.
@MainActor
func largeFileView(_ message: String, thumbnail: Data? = nil, onPreview: @escaping () -> Void) -> NSView {
    // The service indents a command it suggests, which is set apart as code.
    let lines = message.components(separatedBy: "\n")
    let prose = lines.filter { !$0.hasPrefix("    ") }.joined(separator: "\n")
    let label = NSTextField(wrappingLabelWithString: prose.trimmingCharacters(in: .whitespacesAndNewlines))
    label.font = NSFont.systemFont(ofSize: 12)
    label.alignment = .center
    label.isSelectable = true
    label.preferredMaxLayoutWidth = 420
    let commands = lines.filter { $0.hasPrefix("    ") }.map { codeBlock($0.trimmingCharacters(in: .whitespaces)) }
    var views: [NSView] = [label] + commands + [ActionButton(title: "Preview Anyway", onPress: onPreview)]
    // The picture the file carries of itself is shown above, as it costs nothing to read.
    if let picture = pictureView(thumbnail) {
        picture.heightAnchor.constraint(lessThanOrEqualToConstant: 240).isActive = true
        picture.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        views.insert(picture, at: 0)
    }
    return centred(views)
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

    /// Whether a file's own picture is shown before its 3D view; false in the app.
    var prefersPicture = true
    /// Counts the loads asked for, so a slower earlier one does not replace a later one.
    var generation = 0
    /// What the info panel last showed and for which file, kept while the 3D view loads.
    var shownInfo: (url: URL, info: MeshInfo)?

    override func loadView() {
        view = NSView(frame: NSRect(x: 0, y: 0, width: 800, height: 600))
    }

    /// Adopts Quick Look's private protocol, through which Finder asks the questions below.
    static let adoptsPrivateProtocol: Bool = {
        guard let adopted = objc_getProtocol("QLPreviewingController_Private") else {
            return false
        }
        return class_addProtocol(PVQLPreviewViewController.self, adopted)
    }()

    override init(nibName: NSNib.Name?, bundle: Bundle?) {
        super.init(nibName: nibName, bundle: bundle)
        _ = Self.adoptsPrivateProtocol
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) is not used")
    }

    /// Whether Finder should frame the preview in its pane; the system's own 3D preview goes without.
    @objc func wantsBorder() -> Bool {
        false
    }

    /// Whether a press here turns the scene, wakes the picture, or resizes the info, rather than
    /// dragging the file out of Finder's pane.
    @objc(mustHandleDragAtLocation:)
    func mustHandleDrag(at point: NSPoint) -> Bool {
        var hit = view.hitTest(point)
        while let node = hit {
            if node is LitSceneView || node is InfoDivider || node is TouchablePicture {
                return true
            }
            hit = node.superview
        }
        return false
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

            // Read once per panel, so a hand-edited file is picked up by the next one.
            let settings = Helper.Settings.load()
            let request = Request(
                url: url,
                force: false,
                carried: EmbeddedThumbnail.picture(url),
                picturesFirst: self.prefersPicture && !settings.forceInteractive && !settings.themed,
                settings: settings
            )
            self.load(request, handler: handler)
        }
    }

    /// Shows a mesh preview beside its info, unless a newer load took the panel.
    func present(_ content: NSView, _ info: MeshInfo?, _ request: Request, generation: Int) {
        guard generation == self.generation else {
            return
        }
        let kept = shownInfo?.url == request.url ? shownInfo?.info : nil
        // What the header alone says has no bounds, so it never replaces what the values said.
        if let info, info.bounds.isEmpty == false || kept?.bounds.isEmpty != false {
            shownInfo = (request.url, info)
        } else if kept == nil {
            shownInfo = nil
        }
        show(withInfo(content, shownInfo?.info, request.settings))
    }

    /// Returns what a control in the panel calls to load the file again in a mode.
    func switching(_ request: Request) -> (PreviewMode) -> Void {
        { [weak self] mode in self?.switchTo(mode, request) }
    }

    /// Loads a preview again from a panel already showing, setting aside whatever the last load
    /// was still making.
    func switchTo(_ mode: PreviewMode, _ request: Request) {
        generation += 1
        present(placeholderView(request.name, request.carried?.png), nil, request, generation: generation)
        DispatchQueue.global(qos: .userInitiated).async {
            self.load(request, mode: mode, handler: nil)
        }
    }

    /// Fetches a preview and shows it, showing what the header says first when that comes early.
    ///
    /// `handler` is Quick Look's, called once the panel has something to show; it is nil when a
    /// file is loaded again from a panel already showing. `mode` is the view asked for; without
    /// one, a file opens on its own picture when it has one, else on what the settings say.
    nonisolated func load(_ request: Request, mode: PreviewMode? = nil, handler: ((Error?) -> Void)?) {
        let (completion, generation) = DispatchQueue.main.sync {
            (Completion(handler), self.generation)
        }
        let (url, name, thumbnail) = (request.url, request.name, request.carried?.png)
        let shown = mode ?? (request.hasPicture ? .still : .interactive)
        /// Shows a mesh preview beside its info, for this load only.
        let present: @MainActor (NSView, MeshInfo?) -> Void = { content, info in
            self.present(content, info, request, generation: generation)
        }
        let failed = { (message: String) in "Could not preview \(name)\n\n\(message)" }
        // A current picture the file carries of itself is the preview, without the service.
        if shown == .still, request.hasPicture, let png = thumbnail {
            // The description comes in under a second, and a picture shown without it would move
            // when the details arrive beside or below it.
            // Handling the picture loads the surface to turn in its place.
            let wake: @MainActor () -> Void = { [weak self] in self?.switchTo(.interactive, request) }
            let described = DescriptionWait(url, settings: request.settings)
            let early = described.wait(seconds: 2)
            DispatchQueue.main.async {
                let picture = touchablePicture(png, onTouch: wake) ?? messageView(failed("Its picture could not be read."))
                present(picture, early.flatMap(MeshInfo.init))
                completion.finish(nil)
                pvqlLog("showing the picture \(name) carries as its preview")
            }
            guard early == nil, let info = described.wait() else {
                return
            }
            DispatchQueue.main.async {
                present(touchablePicture(png, onTouch: wake) ?? NSImageView(), MeshInfo(info))
            }
            return
        }
        // A picture the file carries of itself is shown at once, before the service answers.
        if thumbnail != nil {
            pvqlLog("showing the picture \(name) carries")
            DispatchQueue.main.async {
                if completion.isPending {
                    present(placeholderView(name, thumbnail), nil)
                    completion.finish(nil)
                }
            }
        }
        let outcome: Preview
        do {
            // A file with a picture of its own asks for the 3D view; otherwise the settings choose.
            let reply = try Helper.requestPreview(
                url, force: request.force, scene: request.hasPicture ? true : nil, settings: request.settings
            ) { info in
                DispatchQueue.main.async {
                    present(placeholderView(name, thumbnail), MeshInfo(info))
                    completion.finish(nil)
                }
            }
            let info = MeshInfo(reply.info)
            switch reply.payload {
            case let .scene(sceneURL):
                defer { try? FileManager.default.removeItem(at: sceneURL) }
                let scene = try SCNScene(url: sceneURL, options: nil)
                SceneStyle.read(sceneURL).apply(to: scene)
                // Framing reads every vertex, so it is done here rather than on the main thread.
                outcome = .scene(scene, previewCamera(for: scene), info)
            case let .image(png):
                outcome = .image(png, info)
            case nil:
                throw Helper.Failure(message: "The render service sent no preview.")
            }
        } catch {
            let failure = error as? Helper.Failure
            let message = failure?.message ?? error.localizedDescription
            pvqlLog("no preview: \(message.prefix(120))")
            if failure?.isTooLarge == true {
                outcome = .tooLarge(message, MeshInfo(failure?.info))
            } else if failure?.isNotMesh != true {
                // The file may well be a mesh, so say what went wrong rather than show it as text.
                outcome = .message(failed(message))
                if let png = thumbnail {
                    // The file's own picture is still worth showing, with why it is all there is.
                    let what = shown == .still ? "A current picture" : "The interactive preview"
                    let note = "\(what) could not be made: \(briefly(message))"
                    DispatchQueue.main.async {
                        let picture = pictureView(png).map { pictureWithNote($0, note) }
                        present(picture ?? messageView(failed(message)), nil)
                        completion.finish(nil)
                    }
                    return
                }
            } else if !isOwnType(url) {
                // macOS has a preview of its own for this type; let it show that, if it still can.
                pvqlLog("declining, not a mesh: \(briefly(message))")
                outcome = .declined(failed(message))
            } else if let text = textContents(of: url) {
                outcome = .text(text)
            } else {
                outcome = .details(url, fileFacts(url))
            }
        }

        DispatchQueue.main.async {
            defer { completion.finish(nil) }
            guard generation == self.generation else {
                return
            }
            switch outcome {
            case let .scene(scene, framing, info):
                present(sceneView(for: scene, framing: framing), info)
                pvqlLog("showing an interactive scene in \(self.view.bounds.size)")
            case let .image(png, info):
                present(pictureView(png) ?? NSImageView(), info)
                pvqlLog("showing a rendered image in \(self.view.bounds.size)")
            case let .tooLarge(message, info):
                let anyway = self.switching(Request(
                    url: url, force: true, carried: request.carried, picturesFirst: request.picturesFirst,
                    settings: request.settings
                ))
                let notice = largeFileView(message, thumbnail: thumbnail) { anyway(shown) }
                present(notice, info)
                pvqlLog("showing a size notice")
            case let .text(text):
                self.show(textView(text))
                pvqlLog("showing text, \(text.count) characters")
            case let .details(fileURL, facts):
                self.show(detailsView(fileURL, facts: facts))
                pvqlLog("showing file details")
            case let .message(text):
                present(messageView(text), nil)
                pvqlLog("showing a message: \(text.prefix(120))")
            case let .declined(text):
                if completion.isPending {
                    completion.finish(Self.noPreview)
                } else {
                    self.show(messageView(text))
                }
            }
        }
    }
}
