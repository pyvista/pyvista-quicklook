import AppKit
import Foundation

/// Lays out the info panel off screen and prints, as JSON, the arrangement and every label shown.
@main
enum InfoProbe {
    @MainActor
    static func main() throws {
        _ = NSApplication.shared
        let arguments = CommandLine.arguments
        guard arguments.count >= 5,
              let width = Double(arguments[2]),
              let height = Double(arguments[3]),
              let arrangement = InfoArrangement(rawValue: arguments[4])
        else {
            FileHandle.standardError.write(Data("usage: InfoProbe <info.json> <width> <height> <arrangement> [key=value ...]\n".utf8))
            exit(2)
        }
        // What the reader last left the panel at, kept in memory, never in a preferences file.
        let suite = "org.pyvista.PyVistaQuickLook.InfoProbe"
        let memory = UserDefaults(suiteName: suite)!
        memory.removePersistentDomain(forName: suite)
        memory.setVolatileDomain([:], forName: suite)
        InfoPanelMemory.defaults = memory
        // Drags are applied after the first layout, as a reader's would be.
        var drags: [Double] = []
        for setting in arguments.dropFirst(5) {
            let parts = setting.split(separator: "=", maxSplits: 1).map(String.init)
            guard parts.count == 2, let value = Double(parts[1]) else {
                FileHandle.standardError.write(Data("bad setting \(setting)\n".utf8))
                exit(2)
            }
            switch parts[0] {
            case "collapsed": InfoPanelMemory.collapsed = value != 0
            case "side_width": InfoPanelMemory.sideWidth = value
            case "below_height": InfoPanelMemory.belowHeight = value
            case "drag": drags.append(value)
            default:
                FileHandle.standardError.write(Data("unknown setting \(parts[0])\n".utf8))
                exit(2)
            }
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
        if !drags.isEmpty {
            for drag in drags {
                view.dragged(CGFloat(drag))
            }
            view.dragEnded()
            view.layout()
            view.layoutSubtreeIfNeeded()
        }

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
        let report: [String: Any] = [
            "arrangement": view.resolved.rawValue,
            "empty": info.isEmpty,
            "labels": labels,
            "folded": view.isFolded,
            "info_width": view.infoFrame.width,
            "info_height": view.infoFrame.height,
            "divider": [
                view.dividerFrame.minX, view.dividerFrame.minY,
                view.dividerFrame.width, view.dividerFrame.height,
            ],
            "remembered": [
                "side_width": InfoPanelMemory.sideWidth.map { Double($0) as Any } ?? NSNull(),
                "below_height": InfoPanelMemory.belowHeight.map { Double($0) as Any } ?? NSNull(),
                "collapsed": InfoPanelMemory.collapsed,
            ],
        ]
        let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
    }
}
