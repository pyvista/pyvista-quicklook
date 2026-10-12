import AppKit
import Foundation

/// Lays out the info panel off screen and prints, as JSON, the arrangement and every label shown.
@main
enum InfoProbe {
    @MainActor
    static func main() throws {
        _ = NSApplication.shared
        let arguments = CommandLine.arguments
        guard arguments.count == 5,
              let width = Double(arguments[2]),
              let height = Double(arguments[3]),
              let arrangement = InfoArrangement(rawValue: arguments[4])
        else {
            FileHandle.standardError.write(Data("usage: InfoProbe <info.json> <width> <height> <arrangement>\n".utf8))
            exit(2)
        }
        let json = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: arguments[1])))
        guard let info = MeshInfo(json as? [String: Any]) else {
            FileHandle.standardError.write(Data("not an info file\n".utf8))
            exit(3)
        }
        let view = MeshPreviewView(render: NSView(), info: info, arrangement: arrangement)
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.contentView = view
        view.layoutSubtreeIfNeeded()
        view.layout()
        view.layoutSubtreeIfNeeded()

        let colorNames: [(NSColor, String)] = [
            (.labelColor, "label"),
            (.secondaryLabelColor, "secondary"),
            (.tertiaryLabelColor, "tertiary"),
            (.controlAccentColor, "accent"),
        ]
        var labels: [[String: Any]] = []
        func walk(_ node: NSView) {
            guard !node.isHidden else {
                return
            }
            if let label = node as? NSTextField, !label.stringValue.isEmpty {
                let wraps = label.cell?.wraps ?? false
                let fit = NSRect(x: 0, y: 0, width: wraps ? label.bounds.width : .greatestFiniteMagnitude, height: .greatestFiniteMagnitude)
                let needed = label.cell?.cellSize(forBounds: fit) ?? .zero
                // Text past the frame is either shortened on purpose or cut off by mistake.
                let wider = !wraps && needed.width > label.bounds.width + 1
                let taller = needed.height > label.bounds.height + 0.5
                let limited = wraps && label.maximumNumberOfLines > 0
                // Content wider than its column runs past the edge the scroll view clips at.
                var overflows = false
                if let scroll = label.enclosingScrollView {
                    let visible = scroll.contentView.convert(scroll.contentView.bounds, to: view)
                    overflows = label.convert(label.bounds, to: view).maxX > visible.maxX + 0.5
                }
                labels.append([
                    "overflows": overflows,
                    "text": label.stringValue,
                    "color": colorNames.first { $0.0 == label.textColor }?.1 ?? "other",
                    "clipped": (taller && !limited) || (!wraps && taller),
                    "shortened": wider || (taller && limited),
                    "tooltip": label.toolTip ?? "",
                ])
            }
            node.subviews.forEach(walk)
        }
        walk(view)
        let report: [String: Any] = ["arrangement": view.resolved.rawValue, "empty": info.isEmpty, "labels": labels]
        let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
    }
}
