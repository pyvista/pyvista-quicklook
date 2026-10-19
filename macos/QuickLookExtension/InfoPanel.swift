import AppKit

/// What the render service says about a dataset, already formatted for display.
struct MeshInfo {
    let type: String
    let facts: [(String, String)]
    /// One axis name, minimum, and maximum per row.
    let bounds: [[String]]
    let file: [(String, String)]
    let arrays: [[String: Any]]
    let blocks: [[String: Any]]
    /// The PyVista user dict, one line of JSON per entry.
    let userDict: [String]
    /// How many arrays there are, including any the list leaves out.
    let arrayCount: Int
    /// How many blocks there are, including any the list leaves out.
    let blockCount: Int
    /// Whether this is what the header alone says, without the values.
    let fromHeader: Bool

    init?(_ json: [String: Any]?) {
        guard let json, let type = json["type"] as? String else {
            return nil
        }
        func pairs(_ key: String) -> [(String, String)] {
            (json[key] as? [[Any]] ?? []).compactMap { row in
                guard row.count == 2, let label = row[0] as? String else {
                    return nil
                }
                if let items = row[1] as? [String] {
                    return (label, items.joined(separator: listSeparator))
                }
                guard let value = row[1] as? String else {
                    return nil
                }
                return (label, value)
            }
        }
        self.type = type
        facts = pairs("facts")
        let bounds = (json["facts"] as? [[Any]] ?? []).first { $0.first as? String == "Bounds" }
        self.bounds = (bounds?.last as? [[String]] ?? []).filter { $0.count == 3 }
        file = pairs("file")
        arrays = json["arrays"] as? [[String: Any]] ?? []
        blocks = json["blocks"] as? [[String: Any]] ?? []
        userDict = json["user_dict"] as? [String] ?? []
        arrayCount = json["array_count"] as? Int ?? arrays.filter { $0["more"] == nil }.count
        blockCount = json["block_count"] as? Int ?? blocks.filter { $0["more"] == nil }.count
        fromHeader = json["from_header"] as? Bool ?? false
    }

    /// Whether there is nothing to show beyond the type.
    var isEmpty: Bool {
        facts.isEmpty && file.isEmpty && arrays.isEmpty && blocks.isEmpty && userDict.isEmpty
    }

    /// Returns a fact's value by its label.
    func fact(_ label: String) -> String? {
        facts.first { $0.0 == label }?.1
    }

    /// The type and its size in short fields, for when there is room for nothing else.
    var summaryParts: [String] {
        var parts = [type]
        if let blocks = fact("Blocks") {
            parts.append("\(blocks) blocks")
        }
        if let dimensions = fact("Dimensions") {
            parts.append(dimensions)
        } else {
            if let points = fact("Points") { parts.append("\(points) points") }
            if let cells = fact("Cells") { parts.append("\(cells) cells") }
        }
        if arrayCount > 0 {
            parts.append("\(arrayCount) \(arrayCount == 1 ? "array" : "arrays")")
        }
        return parts
    }
}

/// Separates the fields of the summary that share a line.
let summarySeparator = "\u{00A0}· "

/// Room the label's own padding takes from the width a line is measured against.
let summaryPadding: CGFloat = 8

/// Returns fields packed onto as few lines as fit the width, never splitting a field.
func packed(_ parts: [String], width: CGFloat, font: NSFont) -> [String] {
    let attributes: [NSAttributedString.Key: Any] = [.font: font]
    var lines: [String] = []
    // A field holds no breakable space, so the label never wraps inside one.
    for part in parts.map({ $0.replacingOccurrences(of: " ", with: "\u{00A0}") }) {
        if let last = lines.last {
            let joined = last + summarySeparator + part
            if (joined as NSString).size(withAttributes: attributes).width <= width - summaryPadding {
                lines[lines.count - 1] = joined
                continue
            }
        }
        lines.append(part)
    }
    return lines
}

/// How the info sits beside the render.
enum InfoArrangement: String {
    case automatic, below, side, compact, hidden
}

/// Builds a label that can be selected and copied, as Finder's own info can.
@MainActor
func infoLabel(
    _ text: String,
    size: CGFloat = 12,
    weight: NSFont.Weight = .regular,
    color: NSColor = .labelColor,
    monospacedDigits: Bool = false
) -> NSTextField {
    let label = NSTextField(labelWithString: text)
    label.font = monospacedDigits
        ? NSFont.monospacedDigitSystemFont(ofSize: size, weight: weight)
        : NSFont.systemFont(ofSize: size, weight: weight)
    label.textColor = color
    label.isSelectable = true
    label.lineBreakMode = .byTruncatingTail
    label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    return label
}

/// Lets a label be shortened to fit, keeping its full text in a tooltip.
@MainActor
func shortened(_ label: NSTextField, _ mode: NSLineBreakMode = .byTruncatingTail) -> NSTextField {
    label.lineBreakMode = mode
    label.toolTip = label.stringValue
    return label
}

/// Returns a count with spaces between thousands that never break across lines, as the facts show it.
func grouped(_ count: Int) -> String {
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    formatter.groupingSeparator = "\u{00A0}"
    return formatter.string(from: NSNumber(value: count)) ?? String(count)
}

/// Builds a small capitalised section heading.
@MainActor
func infoHeading(_ text: String) -> NSTextField {
    let label = infoLabel(text.uppercased(), size: 11, weight: .semibold, color: .controlAccentColor)
    label.isSelectable = false
    return label
}

/// Width of the label column, shared so every group of facts lines up.
let factLabelWidth: CGFloat = 96

/// The colour of a line that stands for content left out, so it is not read as content.
let truncationColor = NSColor.secondaryLabelColor

/// Whether a line stands for content left out, as "… 5 more keys" does.
func isTruncation(_ line: String) -> Bool {
    line.trimmingCharacters(in: .whitespaces).hasPrefix("\u{2026} ")
}

/// Separates the items of a fact that arrives as a list.
let listSeparator = "\u{1F}"

/// The most lines a fact value fills before the rest is summarised.
let maxFactLines = 4

/// Breaks a fact value into lines that fit the value column, whole items or words at a time.
func factLines(_ value: String, width: CGFloat, font: NSFont, noun: String) -> [String] {
    let attributes: [NSAttributedString.Key: Any] = [.font: font]
    let isList = value.contains(listSeparator)
    let items = isList ? value.components(separatedBy: listSeparator) : value.components(separatedBy: " ")
    let joiner = isList ? ", " : " "
    var lines: [[String]] = []
    for item in items {
        if var last = lines.last {
            last.append(item)
            let text = last.joined(separator: joiner) + (isList ? "," : "")
            if (text as NSString).size(withAttributes: attributes).width <= width {
                lines[lines.count - 1] = last
                continue
            }
        }
        lines.append([item])
    }
    if lines.count > maxFactLines {
        let hidden = lines[maxFactLines...].reduce(0) { $0 + $1.count }
        lines = Array(lines[..<maxFactLines])
        let text = lines.enumerated().map { index, line in
            line.joined(separator: joiner) + (isList && index < maxFactLines - 1 ? "," : "")
        }
        if !isList {
            return text.dropLast() + [text[maxFactLines - 1] + "\u{2026}"]
        }
        return text + ["\u{2026} \(hidden) more \(noun)"]
    }
    return lines.enumerated().map { index, line in
        line.joined(separator: joiner) + (isList && index < lines.count - 1 ? "," : "")
    }
}

/// Builds a two-column grid of labels and values.
@MainActor
func factsGrid(_ rows: [(String, String)], size: CGFloat, width: CGFloat, bounds: [[String]] = []) -> NSGridView {
    let columnSpacing: CGFloat = 12
    let valueWidth = max(width - factLabelWidth - columnSpacing, 40)
    var views: [[NSView]] = []
    for (label, value) in rows {
        let title = infoLabel(label, size: size, color: .secondaryLabelColor)
        // Each line gets a row, since a grid cell sizes a wrapping label as one line.
        let font = infoLabel("", size: size, monospacedDigits: true).font ?? .systemFont(ofSize: size)
        let split = value.split(separator: "\n").flatMap {
            factLines(String($0), width: valueWidth, font: font, noun: label.lowercased())
        }
        let lines = split.isEmpty ? [""] : split
        for (index, line) in lines.enumerated() {
            let text = infoLabel(line, size: size, monospacedDigits: true)
            if isTruncation(line) {
                text.textColor = truncationColor
            } else {
                // A word wider than the column is the one thing packing cannot fit.
                _ = shortened(text)
                text.toolTip = value.replacingOccurrences(of: listSeparator, with: ", ")
                text.widthAnchor.constraint(lessThanOrEqualToConstant: valueWidth).isActive = true
            }
            views.append([index == 0 ? title : NSGridCell.emptyContentView, text])
        }
    }
    if !bounds.isEmpty {
        views.append([infoLabel("Bounds", size: size, color: .secondaryLabelColor), boundsGrid(bounds, size: size)])
    }
    let grid = NSGridView(views: views)
    grid.rowSpacing = 4
    grid.columnSpacing = columnSpacing
    grid.column(at: 0).xPlacement = .leading
    grid.column(at: 0).width = factLabelWidth
    grid.setContentHuggingPriority(.required, for: .vertical)
    return grid
}

/// Builds the user dict as lines of JSON, each cut short rather than wrapped.
@MainActor
func userDictView(_ lines: [String], size: CGFloat, width: CGFloat) -> NSStackView {
    let font = NSFont.monospacedSystemFont(ofSize: size - 1, weight: .regular)
    let views: [NSView] = lines.map { line in
        let label = shortened(infoLabel(line, size: size - 1, color: isTruncation(line) ? truncationColor : .labelColor))
        label.font = font
        label.widthAnchor.constraint(lessThanOrEqualToConstant: width).isActive = true
        return label
    }
    let stack = NSStackView(views: views)
    stack.orientation = .vertical
    stack.alignment = .leading
    stack.spacing = 2
    return stack
}

/// Builds the bounds as one row per axis, minimum and maximum aligned on the decimal point.
@MainActor
func boundsGrid(_ rows: [[String]], size: CGFloat) -> NSGridView {
    /// Splits a number at its decimal point, keeping the point with the fraction.
    func halves(_ number: String) -> (String, String) {
        guard let dot = number.firstIndex(of: ".") else {
            return (number, "")
        }
        return (String(number[..<dot]), String(number[dot...]))
    }
    /// Builds an axis name, drawn quieter than the numbers.
    func mark(_ text: String) -> NSTextField {
        infoLabel(text, size: size, color: .secondaryLabelColor)
    }
    let views: [[NSView]] = rows.map { row in
        let (lowWhole, lowFraction) = halves(row[1])
        let (highWhole, highFraction) = halves(row[2])
        return [
            mark(row[0]),
            infoLabel(lowWhole, size: size, monospacedDigits: true),
            infoLabel(lowFraction, size: size, monospacedDigits: true),
            infoLabel(highWhole, size: size, monospacedDigits: true),
            infoLabel(highFraction, size: size, monospacedDigits: true),
        ]
    }
    let grid = NSGridView(views: views)
    grid.rowSpacing = 2
    grid.columnSpacing = 0
    let placements: [NSGridCell.Placement] = [.leading, .trailing, .leading, .trailing, .leading]
    for (index, placement) in placements.enumerated() {
        grid.column(at: index).xPlacement = placement
    }
    grid.column(at: 0).trailingPadding = 8
    grid.column(at: 2).trailingPadding = 16
    return grid
}

/// Builds a thin horizontal rule.
@MainActor
func rule() -> NSView {
    let box = NSBox()
    box.boxType = .separator
    return box
}

/// The table columns: key, heading, whether right aligned.
let arrayColumns: [(String, String, Bool)] = [
    ("name", "Name", false),
    ("association", "Assoc.", false),
    ("dtype", "Type", false),
    ("components", "Comp.", true),
    ("range", "Range", false),
    ("active", "Active", false),
]

/// Widest an array name is drawn before it is shortened in the middle.
let maxNameWidth: CGFloat = 200

/// Returns a cell of an array row as text.
func arrayCell(_ row: [String: Any], _ key: String) -> String {
    switch row[key] {
    case let roles as [String]: return roles.joined(separator: ", ")
    case let number as NSNumber: return number.stringValue
    case let text as String: return text
    default: return ""
    }
}

/// Builds the arrays as a table with a column per property, for a wide panel.
@MainActor
func arraysTable(_ rows: [[String: Any]], size: CGFloat, blocks: Bool) -> NSGridView {
    var columns = arrayColumns
    if blocks {
        columns.insert(("blocks", "Blocks", false), at: 3)
    }
    let header = columns.map { _, title, _ in
        infoLabel(title, size: size, weight: .semibold, color: .secondaryLabelColor) as NSView
    }
    var views: [[NSView]] = [header]
    for row in rows {
        if row["more"] != nil {
            views.append([infoLabel(arrayCell(row, "name"), size: size, color: truncationColor)]
                + columns.dropFirst().map { _ in NSGridCell.emptyContentView })
            continue
        }
        views.append(columns.map { key, _, _ in
            let label = infoLabel(arrayCell(row, key), size: size, monospacedDigits: key != "name")
            if key == "name" {
                label.font = NSFont.systemFont(ofSize: size, weight: .medium)
                _ = shortened(label, .byTruncatingMiddle)
                label.widthAnchor.constraint(lessThanOrEqualToConstant: maxNameWidth).isActive = true
            }
            if key == "active" || key == "association" {
                label.textColor = .secondaryLabelColor
            }
            return label
        })
    }
    let grid = NSGridView(views: views)
    mergeTruncationRows(grid, rows: rows)
    grid.rowSpacing = 5
    grid.columnSpacing = 14
    for (index, column) in columns.enumerated() {
        grid.column(at: index).xPlacement = column.2 ? .trailing : .leading
    }
    grid.row(at: 0).bottomPadding = 2
    return grid
}

/// Builds the arrays as a list, one entry of two lines each, for a narrow panel.
@MainActor
func arraysList(_ rows: [[String: Any]], size: CGFloat, width: CGFloat) -> NSStackView {
    let entries: [NSView] = rows.map { row in
        if row["more"] != nil {
            return infoLabel(arrayCell(row, "name"), size: size, color: truncationColor)
        }
        let name = shortened(infoLabel(arrayCell(row, "name"), size: size, weight: .medium), .byTruncatingMiddle)
        let roles = arrayCell(row, "active")
        let title = NSStackView(views: [name])
        if !roles.isEmpty {
            let tag = infoLabel(roles, size: size - 1, color: .secondaryLabelColor)
            tag.setContentCompressionResistancePriority(.required, for: .horizontal)
            title.addArrangedSubview(tag)
        }
        title.widthAnchor.constraint(lessThanOrEqualToConstant: width).isActive = true
        title.spacing = 6
        var details = [arrayCell(row, "association"), arrayCell(row, "dtype")]
        let components = arrayCell(row, "components")
        if components != "1" {
            details[1] += " × \(components)"
        }
        if let blocks = row["blocks"] as? String {
            details.append("\(blocks) blocks")
        }
        let range = arrayCell(row, "range")
        if !range.isEmpty {
            details.append(range)
        }
        let line = detailLine(details, size: size, width: width)
        let lines: [NSView] = [title, line]
        let entry = NSStackView(views: lines)
        entry.orientation = .vertical
        entry.alignment = .leading
        entry.spacing = 1
        return entry
    }
    let list = NSStackView(views: entries)
    list.orientation = .vertical
    list.alignment = .leading
    list.spacing = 8
    return list
}

/// Builds the detail line under a list entry, wrapping onto a second line before it is shortened.
@MainActor
func detailLine(_ details: [String], size: CGFloat, width: CGFloat) -> NSTextField {
    let line = shortened(infoLabel(details.joined(separator: " · "), size: size - 1, color: .secondaryLabelColor, monospacedDigits: true))
    line.cell?.wraps = true
    line.cell?.isScrollable = false
    line.cell?.truncatesLastVisibleLine = true
    line.usesSingleLineMode = false
    line.maximumNumberOfLines = 2
    line.preferredMaxLayoutWidth = width
    line.widthAnchor.constraint(lessThanOrEqualToConstant: width).isActive = true
    return line
}

/// Builds the blocks of a composite dataset as a list, two lines each, for a narrow panel.
@MainActor
func blocksList(_ rows: [[String: Any]], size: CGFloat, width: CGFloat) -> NSStackView {
    let entries: [NSView] = rows.map { row in
        let name = shortened(infoLabel(arrayCell(row, "name"), size: size, weight: .medium), .byTruncatingMiddle)
        name.widthAnchor.constraint(lessThanOrEqualToConstant: width).isActive = true
        if row["more"] != nil {
            name.textColor = truncationColor
            return name
        }
        let details = [
            arrayCell(row, "type"),
            arrayCell(row, "dimensions"),
            arrayCell(row, "points").isEmpty ? "" : "\(arrayCell(row, "points")) points",
            arrayCell(row, "cells").isEmpty ? "" : "\(arrayCell(row, "cells")) cells",
        ].filter { !$0.isEmpty }
        let line = detailLine(details, size: size, width: width)
        let entry = NSStackView(views: [name, line])
        entry.orientation = .vertical
        entry.alignment = .leading
        entry.spacing = 1
        return entry
    }
    let list = NSStackView(views: entries)
    list.orientation = .vertical
    list.alignment = .leading
    list.spacing = 8
    return list
}

/// Builds the blocks of a composite dataset as a table.
@MainActor
func blocksTable(_ rows: [[String: Any]], size: CGFloat) -> NSGridView {
    var columns: [(String, String, Bool)] = [
        ("name", "Name", false), ("type", "Type", false), ("points", "Points", true), ("cells", "Cells", true),
    ]
    if rows.contains(where: { !arrayCell($0, "dimensions").isEmpty }) {
        columns.insert(("dimensions", "Dimensions", false), at: 2)
    }
    var views: [[NSView]] = [columns.map { _, title, _ in
        infoLabel(title, size: size, weight: .semibold, color: .secondaryLabelColor)
    }]
    for row in rows {
        if row["more"] != nil {
            views.append([infoLabel(arrayCell(row, "name"), size: size, color: truncationColor)]
                + columns.dropFirst().map { _ in NSGridCell.emptyContentView })
            continue
        }
        views.append(columns.map { key, _, _ in
            let label = infoLabel(arrayCell(row, key), size: size, monospacedDigits: key != "name")
            if key == "name" {
                _ = shortened(label, .byTruncatingMiddle)
                label.widthAnchor.constraint(lessThanOrEqualToConstant: maxNameWidth).isActive = true
            }
            return label
        })
    }
    let grid = NSGridView(views: views)
    mergeTruncationRows(grid, rows: rows)
    grid.rowSpacing = 5
    grid.columnSpacing = 14
    for (index, column) in columns.enumerated() {
        grid.column(at: index).xPlacement = column.2 ? .trailing : .leading
    }
    return grid
}

/// Lets a table's "… N more" row span every column, so it does not widen the first.
@MainActor
func mergeTruncationRows(_ grid: NSGridView, rows: [[String: Any]]) {
    for (index, row) in rows.enumerated() where row["more"] != nil && grid.numberOfColumns > 1 {
        grid.row(at: index + 1).mergeCells(in: NSRange(location: 0, length: grid.numberOfColumns))
    }
}

/// A view whose origin is at the top, so scrolled content starts at its beginning.
final class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

/// Builds a heading over a group of facts.
@MainActor
func factsSection(
    _ heading: String, _ rows: [(String, String)], size: CGFloat, width: CGFloat, bounds: [[String]] = []
) -> NSStackView {
    factsSectionView(heading, factsGrid(rows, size: size, width: width, bounds: bounds))
}

/// Builds the column of facts, file details, arrays, and blocks to fit the given width.
@MainActor
func infoContent(_ info: MeshInfo, wide: Bool, size: CGFloat, width: CGFloat) -> NSView {
    var sections: [NSView] = []
    var groups: [NSView] = []
    let groupSpacing: CGFloat = 40
    let paired = wide && !info.facts.isEmpty && !info.file.isEmpty
    let groupWidth = paired ? (width - groupSpacing) / 2 : width
    if !info.facts.isEmpty {
        let rows = [("Type", info.type)] + info.facts
        groups.append(factsSection("Dataset", rows, size: size, width: groupWidth, bounds: info.bounds))
    }
    if !info.file.isEmpty {
        groups.append(factsSection("File", info.file, size: size, width: groupWidth))
    }
    if paired {
        let row = NSStackView(views: groups)
        row.orientation = .horizontal
        row.alignment = .top
        row.spacing = groupSpacing
        sections.append(row)
    } else {
        sections += groups
    }
    if !info.userDict.isEmpty {
        sections.append(factsSectionView("User Dict", userDictView(info.userDict, size: size, width: width)))
    }
    if !info.arrays.isEmpty {
        let list = wide
            ? arraysTable(info.arrays, size: size, blocks: !info.blocks.isEmpty)
            : arraysList(info.arrays, size: size, width: width)
        sections.append(factsSectionView("Arrays (\(grouped(info.arrayCount)))", list))
    }
    if !info.blocks.isEmpty {
        let blocks = wide ? blocksTable(info.blocks, size: size) : blocksList(info.blocks, size: size, width: width)
        sections.append(factsSectionView("Blocks (\(grouped(info.blockCount)))", blocks))
    }
    let stack = NSStackView(views: sections)
    stack.orientation = .vertical
    stack.alignment = .leading
    stack.spacing = 18
    return stack
}

/// Builds a heading over any view.
@MainActor
func factsSectionView(_ heading: String, _ body: NSView) -> NSStackView {
    let section = NSStackView(views: [infoHeading(heading), body])
    section.orientation = .vertical
    section.alignment = .leading
    section.spacing = 6
    section.setHuggingPriority(.required, for: .vertical)
    return section
}

/// How the reader last left the info panel, kept across previews.
enum InfoPanelMemory {
    static var defaults = UserDefaults.standard
    static let sideWidthKey = "info_side_width"
    static let belowHeightKey = "info_below_height"
    static let collapsedKey = "info_collapsed"

    /// The width dragged to beside the render, if any.
    static var sideWidth: CGFloat? {
        get { stored(sideWidthKey) }
        set { store(sideWidthKey, newValue) }
    }

    /// The height dragged to below the render, if any.
    static var belowHeight: CGFloat? {
        get { stored(belowHeightKey) }
        set { store(belowHeightKey, newValue) }
    }

    /// Whether the panel was folded away: to the line alone beside the render, or to the
    /// one-line summary below it.
    static var collapsed: Bool {
        get { defaults.bool(forKey: collapsedKey) }
        set { defaults.set(newValue, forKey: collapsedKey) }
    }

    private static func stored(_ key: String) -> CGFloat? {
        defaults.object(forKey: key) == nil ? nil : CGFloat(defaults.double(forKey: key))
    }

    private static func store(_ key: String, _ value: CGFloat?) {
        if let value {
            defaults.set(Double(value), forKey: key)
        } else {
            defaults.removeObject(forKey: key)
        }
    }
}

/// The line between the render and the info, which drags to resize and folds the info away.
final class InfoDivider: NSView {
    /// Whether the line stands beside the render rather than below it.
    var vertical = false
    /// Called with how far the mouse moved across the line, in points.
    var onDrag: ((CGFloat) -> Void)?
    var onDragEnd: (() -> Void)?
    var onToggle: (() -> Void)?
    /// How wide the grab area is around the one-point line; the button sits inside it.
    static let thickness: CGFloat = 23
    static let buttonSide: CGFloat = 21
    private var last = NSPoint.zero
    private let line = NSBox()
    let toggle = NSButton()

    override init(frame: NSRect) {
        super.init(frame: frame)
        line.boxType = .separator
        addSubview(line)
        toggle.bezelStyle = .circular
        toggle.imagePosition = .imageOnly
        toggle.imageScaling = .scaleProportionallyDown
        toggle.setButtonType(.momentaryPushIn)
        toggle.target = self
        toggle.action = #selector(toggled)
        addSubview(toggle)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) is not used")
    }

    private var chevron = ""

    /// Shows a chevron pointing where the info goes when the button is pressed.
    func showChevron(_ name: String, tip: String) {
        guard name != chevron else {
            return
        }
        chevron = name
        let symbol = NSImage(systemSymbolName: name, accessibilityDescription: tip)
        toggle.image = symbol?.withSymbolConfiguration(.init(pointSize: 9, weight: .bold))
        toggle.toolTip = tip
    }

    override func layout() {
        super.layout()
        let middle = (Self.thickness - 1) / 2
        let side = Self.buttonSide
        line.frame = vertical
            ? NSRect(x: middle, y: 0, width: 1, height: bounds.height)
            : NSRect(x: 0, y: middle, width: bounds.width, height: 1)
        // On the line, at its top or its right, and opaque, so the line stops under it.
        toggle.frame = vertical
            ? NSRect(x: (bounds.width - side) / 2, y: bounds.height - side - 2, width: side, height: side)
            : NSRect(x: bounds.width - side - 4, y: (bounds.height - side) / 2, width: side, height: side)
    }

    override func resetCursorRects() {
        addCursorRect(bounds, cursor: vertical ? .resizeLeftRight : .resizeUpDown)
        addCursorRect(toggle.frame, cursor: .arrow)
    }

    override func mouseDown(with event: NSEvent) {
        last = superview?.convert(event.locationInWindow, from: nil) ?? .zero
    }

    override func mouseDragged(with event: NSEvent) {
        let point = superview?.convert(event.locationInWindow, from: nil) ?? .zero
        onDrag?(vertical ? point.x - last.x : point.y - last.y)
        last = point
    }

    override func mouseUp(with event: NSEvent) {
        onDragEnd?()
    }

    @objc private func toggled() {
        onToggle?()
    }
}

/// Shows a render with the dataset's info beside it, below it, or summarised under it.
final class MeshPreviewView: NSView {
    let render: NSView
    let info: MeshInfo
    let arrangement: InfoArrangement
    private let scroll = NSScrollView()
    private let summary: NSTextField
    private let divider = InfoDivider()
    /// What the content was last built for, so it is rebuilt only when that changes.
    private var built: (wide: Bool, width: CGFloat)?

    /// Sizes below which only the short summary is shown.
    static let compactWidth: CGFloat = 420
    static let compactHeight: CGFloat = 340
    /// Widest the info column becomes beside the render.
    static let sideWidth: CGFloat = 340
    /// Narrowest the column beside the render may be; a narrower panel puts the info below.
    static let minSideWidth: CGFloat = 260
    /// Space around the info inside its scrolling column.
    static let inset: CGFloat = 16
    /// Dragged this far past the narrowest the column may be, the info folds away.
    static let foldPast: CGFloat = 60
    /// The shortest the row below the render may be dragged to; past it, the info folds away.
    static let foldHeight: CGFloat = 48
    /// The size a drag is taking the info to, unclamped, so it can pass the fold.
    private var dragging: CGFloat?

    init(render: NSView, info: MeshInfo, arrangement: InfoArrangement = .automatic) {
        self.render = render
        self.info = info
        self.arrangement = arrangement
        summary = infoLabel("", size: 11, color: .secondaryLabelColor, monospacedDigits: true)
        summary.alignment = .center
        summary.cell?.wraps = true
        summary.cell?.isScrollable = false
        summary.usesSingleLineMode = false
        summary.lineBreakMode = .byWordWrapping
        summary.maximumNumberOfLines = 0
        super.init(frame: .zero)
        divider.onDrag = { [weak self] delta in self?.dragged(delta) }
        divider.onDragEnd = { [weak self] in self?.dragEnded() }
        divider.onToggle = { [weak self] in self?.toggleCollapsed() }
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.scrollerStyle = .overlay
        for view in [render, scroll, summary, divider] {
            addSubview(view)
        }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) is not used")
    }

    /// Where the info is shown, or nothing while it is folded away or left out.
    var infoFrame: NSRect {
        scroll.isHidden ? .zero : scroll.frame
    }

    /// Where the line and its button are, or nothing when there is no line.
    var dividerFrame: NSRect {
        divider.isHidden ? .zero : divider.frame
    }

    /// Whether the reader folded the info away: beside the render that leaves only the
    /// line and its button, below it the one-line summary.
    var isFolded: Bool {
        arrangement != .hidden && InfoPanelMemory.collapsed
    }

    /// The arrangement for the current size, and for whether the info was folded away.
    var resolved: InfoArrangement {
        let fitted = fitting
        if isFolded, fitted != .side {
            return .compact
        }
        return fitted
    }

    /// The arrangement for the current size alone.
    var fitting: InfoArrangement {
        guard arrangement == .automatic else {
            return arrangement
        }
        let size = bounds.size
        if size.width < Self.compactWidth || size.height < Self.compactHeight {
            return .compact
        }
        let roomBeside = Self.sideColumn(size.width) >= Self.minSideWidth
        return size.width >= size.height * 1.25 && roomBeside ? .side : .below
    }

    /// The width of the column beside the render in a panel this wide.
    static func sideColumn(_ width: CGFloat) -> CGFloat {
        min(sideWidth, width * 0.42)
    }

    /// The column's width: as dragged to, within what the panel allows, else the usual share.
    private func columnWidth() -> CGFloat {
        let wanted = InfoPanelMemory.sideWidth ?? Self.sideColumn(bounds.width)
        return min(max(wanted, Self.minSideWidth), bounds.width * 0.6)
    }

    /// The info's height below the render: as dragged to, within what the panel allows, else
    /// what the content needs, so a short table does not take half the panel.
    private func rowHeight() -> CGFloat {
        let most = bounds.height * 0.7
        if let dragged = InfoPanelMemory.belowHeight {
            return min(max(dragged, Self.foldHeight), most)
        }
        let wanted = min(contentHeight(), bounds.height * 0.5)
        return max(wanted, min(120, bounds.height * 0.3))
    }

    /// Resizes the info by a drag across the divider, folding it away when dragged too small.
    func dragged(_ delta: CGFloat) {
        guard !isFolded else {
            return
        }
        switch resolved {
        case .side:
            let width = (dragging ?? columnWidth()) - delta
            dragging = width
            if width < Self.minSideWidth - Self.foldPast {
                InfoPanelMemory.sideWidth = nil
                fold()
            } else {
                InfoPanelMemory.sideWidth = width
            }
        case .below:
            let height = (dragging ?? rowHeight()) + delta
            dragging = height
            if height < Self.foldHeight {
                InfoPanelMemory.belowHeight = nil
                fold()
            } else {
                InfoPanelMemory.belowHeight = height
            }
        case .compact, .hidden, .automatic:
            return
        }
        needsLayout = true
    }

    /// Settles a dragged size on what the panel allowed, so the next preview starts there.
    func dragEnded() {
        dragging = nil
        guard !isFolded else {
            return
        }
        switch resolved {
        case .side: InfoPanelMemory.sideWidth = columnWidth()
        case .below: InfoPanelMemory.belowHeight = rowHeight()
        case .compact, .hidden, .automatic: break
        }
    }

    private func fold() {
        dragging = nil
        InfoPanelMemory.collapsed = true
    }

    /// Folds the info down to its summary, or opens it again.
    func toggleCollapsed() {
        InfoPanelMemory.collapsed.toggle()
        needsLayout = true
    }

    /// Builds the scrolling content for a column, as tables when asked and they fit, else as lists.
    private func fill(wide: Bool, column: CGFloat) {
        // Built a few points narrow at most, so live resizing does not rebuild on every step.
        let width = floor((column - 2 * Self.inset) / 8) * 8
        if let built, built.wide == wide, built.width == width {
            return
        }
        built = (wide, width)
        var content = infoContent(info, wide: wide, size: 12, width: width)
        if wide, content.fittingSize.width > width {
            content = infoContent(info, wide: false, size: 12, width: width)
        }
        content.translatesAutoresizingMaskIntoConstraints = false
        let document = FlippedView()
        document.translatesAutoresizingMaskIntoConstraints = false
        document.addSubview(content)
        scroll.documentView = document
        NSLayoutConstraint.activate([
            content.topAnchor.constraint(equalTo: document.topAnchor, constant: Self.inset),
            content.leadingAnchor.constraint(equalTo: document.leadingAnchor, constant: Self.inset),
            content.trailingAnchor.constraint(lessThanOrEqualTo: document.trailingAnchor, constant: -Self.inset),
            content.bottomAnchor.constraint(equalTo: document.bottomAnchor, constant: -Self.inset),
            document.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
        ])
    }

    /// The height the info needs at a given width, so a short table does not take half the panel.
    private func contentHeight() -> CGFloat {
        scroll.documentView?.layoutSubtreeIfNeeded()
        return scroll.documentView?.fittingSize.height ?? 0
    }

    override func layout() {
        super.layout()
        let bounds = self.bounds
        let mode = resolved
        let folded = isFolded
        // Compact for want of room has nothing to unfold, so it has no button.
        let unfoldable = mode == .compact && folded && fitting != .compact
        summary.isHidden = mode != .compact
        scroll.isHidden = mode == .compact || mode == .hidden || folded
        divider.isHidden = mode == .hidden || mode == .automatic || (mode == .compact && !unfoldable)
        let grab = InfoDivider.thickness
        let half = (grab - 1) / 2
        switch mode {
        case .hidden, .automatic:
            render.frame = bounds
        case .compact:
            let room = unfoldable ? InfoDivider.buttonSide + 10 : 0
            let width = bounds.width - 16 - 2 * room
            let font = summary.font ?? NSFont.systemFont(ofSize: 11)
            summary.stringValue = packed(info.summaryParts, width: width, font: font).joined(separator: "\n")
            let fit = NSRect(x: 0, y: 0, width: width, height: .greatestFiniteMagnitude)
            let height = ceil(summary.cell?.cellSize(forBounds: fit).height ?? 16)
            summary.frame = NSRect(x: 8 + room, y: 6, width: width, height: height)
            let below = height + 10
            if unfoldable {
                divider.vertical = false
                divider.showChevron("chevron.up", tip: "Show the details")
                divider.frame = NSRect(x: 0, y: below - half, width: bounds.width, height: grab)
            }
            render.frame = NSRect(x: 0, y: below, width: bounds.width, height: bounds.height - below)
        case .side:
            let width = folded ? 0 : columnWidth()
            if !folded {
                fill(wide: false, column: width)
                scroll.frame = NSRect(x: bounds.width - width, y: 0, width: width, height: bounds.height)
            }
            divider.vertical = true
            divider.showChevron(folded ? "chevron.left" : "chevron.right", tip: folded ? "Show the details" : "Hide the details")
            // Folded, the strip stands whole at the edge; open, the line is the column's edge.
            let edge = folded ? bounds.width - grab : bounds.width - width - 1 - half
            divider.frame = NSRect(x: edge, y: 0, width: grab, height: bounds.height)
            render.frame = NSRect(x: 0, y: 0, width: edge + half, height: bounds.height)
        case .below:
            fill(wide: true, column: bounds.width)
            scroll.frame = NSRect(x: 0, y: 0, width: bounds.width, height: bounds.height * 0.45)
            let height = rowHeight()
            scroll.frame = NSRect(x: 0, y: 0, width: bounds.width, height: height)
            divider.vertical = false
            divider.showChevron("chevron.down", tip: "Hide the details")
            divider.frame = NSRect(x: 0, y: height - half, width: bounds.width, height: grab)
            render.frame = NSRect(x: 0, y: height + 1, width: bounds.width, height: bounds.height - height - 1)
        }
        divider.needsLayout = true
        window?.invalidateCursorRects(for: divider)
    }
}
