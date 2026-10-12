import Compression
import Foundation
import zlib

/// Reads the picture a `.pv` or `.3mf` file may carry of itself, without the service.
enum EmbeddedThumbnail {
    /// The bytes every `.pv` file with a thumbnail starts with: a raw zstd frame holding the
    /// `__thumbnail` array header, then the magic of the skippable frame that holds the picture.
    static let prefix = Data(
        [0x28, 0xB5, 0x2F, 0xFD, 0xA0, 0x2B, 0x00, 0x00, 0x00, 0x59, 0x01, 0x00, 0x0B, 0x00, 0x00, 0x00]
            + Array("__thumbnail".utf8)
            + [0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
            + Array("|u1".utf8) + Array(repeating: 0x20, count: 13)
            + [0x50, 0x2A, 0x4D, 0x18]
    )
    /// Where the thumbnail record, and its descriptor, start.
    static let recordOffset = 63
    /// The length of the version 1 descriptor.
    static let descriptorBytes = 32
    /// The first bytes of every PNG.
    static let pngSignature = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    /// The first bytes of every JPEG, which a 3MF package may hold instead.
    static let jpegSignature = Data([0xFF, 0xD8, 0xFF])
    /// Thumbnails above this size are not read, so a damaged length never reads a whole file.
    static let maximumBytes = 8 * 1024 * 1024

    /// A picture a file carries of itself.
    struct Picture {
        let png: Data
        let width: Int
        let height: Int
        /// Whether the data changed after the picture was taken.
        let isStale: Bool
    }

    /// Returns the PNG a file carries of itself, or nil when it has none, or only a stale one
    /// and `allowStale` is false.
    static func read(_ url: URL, allowStale: Bool = true) -> Data? {
        guard let picture = picture(url), allowStale || !picture.isStale else {
            return nil
        }
        return picture.png
    }

    /// Returns the picture a file carries of itself, or nil when it has none.
    static func picture(_ url: URL) -> Picture? {
        let kind = url.pathExtension.lowercased()
        guard ["pv", "3mf"].contains(kind), let handle = try? FileHandle(forReadingFrom: url) else {
            return nil
        }
        defer { try? handle.close() }
        return kind == "pv" ? locate(in: handle) : packageThumbnail(in: handle)
    }

    /// Returns the thumbnail a 3MF package names in its relationships, or holds where it usually is.
    static func packageThumbnail(in handle: FileHandle) -> Picture? {
        guard let entries = zipEntries(handle) else {
            return nil
        }
        var path = "Metadata/thumbnail.png"
        if let rels = entries["_rels/.rels"].flatMap({ zipContents(handle, $0) }),
           let xml = String(data: rels, encoding: .utf8),
           let target = thumbnailTarget(xml) {
            path = target
        }
        guard let entry = entries[path] ?? entries.first(where: { $0.key.lowercased() == path.lowercased() })?.value,
              let image = zipContents(handle, entry),
              image.starts(with: pngSignature) || image.starts(with: jpegSignature)
        else {
            return nil
        }
        return Picture(png: image, width: 0, height: 0, isStale: false)
    }

    /// Returns the package path the root relationships give the thumbnail.
    static func thumbnailTarget(_ xml: String) -> String? {
        for tag in xml.components(separatedBy: "<").dropFirst() where tag.contains("/metadata/thumbnail\"") {
            guard let range = tag.range(of: #"Target="([^"]+)""#, options: .regularExpression) else {
                continue
            }
            let target = tag[range].dropFirst("Target=\"".count).dropLast()
            return String(target.hasPrefix("/") ? target.dropFirst() : target)
        }
        return nil
    }

    /// Where a file sits inside a zip archive and how it is stored.
    struct ZipEntry {
        let method: UInt16
        let compressedSize: Int
        let size: Int
        let headerOffset: UInt64
    }

    /// Returns the entries of a zip archive by path, read from its central directory.
    static func zipEntries(_ handle: FileHandle) -> [String: ZipEntry]? {
        guard let end = try? handle.seekToEnd(), end >= 22 else {
            return nil
        }
        // The end record sits in the last 22 bytes, or before a comment of up to 64 KiB.
        let tailLength = min(end, 22 + 65_535)
        guard (try? handle.seek(toOffset: end - tailLength)) != nil,
              let tail = try? handle.read(upToCount: Int(tailLength)),
              let marker = tail.lastRange(of: Data([0x50, 0x4B, 0x05, 0x06]))
        else {
            return nil
        }
        let record = tail[marker.lowerBound...]
        guard record.count >= 22 else {
            return nil
        }
        let count = Int(uint16(record, record.startIndex + 10))
        let directorySize = Int(uint32(record, record.startIndex + 12))
        let directoryOffset = UInt64(uint32(record, record.startIndex + 16))
        guard directorySize <= maximumBytes, (try? handle.seek(toOffset: directoryOffset)) != nil,
              let directory = try? handle.read(upToCount: directorySize), directory.count == directorySize
        else {
            return nil
        }
        var entries: [String: ZipEntry] = [:]
        var at = directory.startIndex
        for _ in 0..<count {
            guard at + 46 <= directory.endIndex, uint32(directory, at) == 0x0201_4B50 else {
                break
            }
            let nameLength = Int(uint16(directory, at + 28))
            let extraLength = Int(uint16(directory, at + 30))
            let commentLength = Int(uint16(directory, at + 32))
            guard at + 46 + nameLength <= directory.endIndex else {
                break
            }
            let name = String(decoding: directory[(at + 46)..<(at + 46 + nameLength)], as: UTF8.self)
            entries[name] = ZipEntry(
                method: uint16(directory, at + 10),
                compressedSize: Int(uint32(directory, at + 20)),
                size: Int(uint32(directory, at + 24)),
                headerOffset: UInt64(uint32(directory, at + 42))
            )
            at += 46 + nameLength + extraLength + commentLength
        }
        return entries
    }

    /// Returns the contents of one zip entry, stored or deflated, or nil.
    static func zipContents(_ handle: FileHandle, _ entry: ZipEntry) -> Data? {
        guard entry.size <= maximumBytes, entry.compressedSize <= maximumBytes,
              (try? handle.seek(toOffset: entry.headerOffset)) != nil,
              let header = try? handle.read(upToCount: 30), header.count == 30,
              uint32(header, header.startIndex) == 0x0403_4B50
        else {
            return nil
        }
        let skip = UInt64(30 + Int(uint16(header, header.startIndex + 26)) + Int(uint16(header, header.startIndex + 28)))
        guard (try? handle.seek(toOffset: entry.headerOffset + skip)) != nil,
              let packed = try? handle.read(upToCount: entry.compressedSize), packed.count == entry.compressedSize
        else {
            return nil
        }
        switch entry.method {
        case 0:
            return packed
        case 8:
            // Raw deflate, which is what the Compression framework calls zlib.
            var unpacked = Data(count: entry.size)
            let written = unpacked.withUnsafeMutableBytes { output in
                packed.withUnsafeBytes { input in
                    compression_decode_buffer(
                        output.bindMemory(to: UInt8.self).baseAddress!, entry.size,
                        input.bindMemory(to: UInt8.self).baseAddress!, packed.count,
                        nil, COMPRESSION_ZLIB
                    )
                }
            }
            return written == entry.size ? unpacked : nil
        default:
            return nil
        }
    }

    /// Returns the picture from where the format keeps it, at the start of the file.
    static func locate(in handle: FileHandle) -> Picture? {
        guard let head = try? handle.read(upToCount: recordOffset + descriptorBytes),
              head.count == recordOffset + descriptorBytes,
              head.prefix(prefix.count) == prefix,
              head[recordOffset..<recordOffset + 8] == Data("PVZTHUMB".utf8),
              uint32(head, recordOffset + 8) == 1
        else {
            return nil
        }
        let header = Int(uint32(head, recordOffset + 12))
        let length = Int(uint32(head, recordOffset + 24))
        let recordLength = Int(uint32(head, recordOffset - 4))
        guard header >= descriptorBytes, length <= maximumBytes, header + length <= recordLength,
              (try? handle.seek(toOffset: UInt64(recordOffset + header))) != nil,
              let png = try? handle.read(upToCount: length), png.count == length,
              png.starts(with: pngSignature)
        else {
            return nil
        }
        return Picture(
            png: png,
            width: Int(uint32(head, recordOffset + 16)),
            height: Int(uint32(head, recordOffset + 20)),
            isStale: !matchesData(handle, crc: uint32(head, recordOffset + 28))
        )
    }

    /// Returns whether the file's index still has the checksum the picture was taken with.
    static func matchesData(_ handle: FileHandle, crc: UInt32) -> Bool {
        guard let size = try? handle.seekToEnd(), size >= 8,
              (try? handle.seek(toOffset: size - 8)) != nil,
              let tail = try? handle.read(upToCount: 8), tail.count == 8
        else {
            return false
        }
        let frames = tail.withUnsafeBytes { UInt64(littleEndian: $0.loadUnaligned(as: UInt64.self)) }
        guard frames < size / 16, size - 8 >= 16 * frames + 32 else {
            return false
        }
        let start = size - 8 - 16 * frames + 32
        guard (try? handle.seek(toOffset: start)) != nil,
              let entries = try? handle.read(upToCount: Int(size - 8 - start)),
              entries.count == Int(size - 8 - start)
        else {
            return false
        }
        let actual = entries.withUnsafeBytes { bytes in
            UInt32(zlib.crc32(0, bytes.bindMemory(to: Bytef.self).baseAddress, uInt(bytes.count)))
        }
        return actual == crc
    }

    /// Reads a little-endian `u16` at an offset into some bytes.
    static func uint16(_ data: Data, _ offset: Int) -> UInt16 {
        data.withUnsafeBytes { bytes in
            UInt16(littleEndian: bytes.loadUnaligned(fromByteOffset: offset - data.startIndex, as: UInt16.self))
        }
    }

    /// Reads a little-endian `u32` at an offset into some bytes.
    static func uint32(_ data: Data, _ offset: Int) -> UInt32 {
        data.withUnsafeBytes { bytes in
            UInt32(littleEndian: bytes.loadUnaligned(fromByteOffset: offset - data.startIndex, as: UInt32.self))
        }
    }
}
