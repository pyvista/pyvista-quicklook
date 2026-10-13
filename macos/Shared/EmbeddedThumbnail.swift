import Foundation
import zlib

/// Reads the picture a `.pv` file may carry of itself, without the service.
enum EmbeddedThumbnail {
    /// The bytes every `.pv` file with a thumbnail starts with: a raw zstd frame holding the
    /// `__thumbnail` array header, then the magic of the skippable frame that holds the picture.
    static let prefix: Data = {
        let frame: [UInt8] = [0x28, 0xB5, 0x2F, 0xFD, 0xA0, 0x2B, 0x00, 0x00, 0x00, 0x59, 0x01, 0x00, 0x0B, 0x00, 0x00, 0x00]
        let shape: [UInt8] = [0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        let padding = [UInt8](repeating: 0x20, count: 13)
        let magic: [UInt8] = [0x50, 0x2A, 0x4D, 0x18]
        var bytes = frame
        bytes += Array("__thumbnail".utf8)
        bytes += shape
        bytes += Array("|u1".utf8)
        bytes += padding
        bytes += magic
        return Data(bytes)
    }()
    /// Where the thumbnail record, and its descriptor, start.
    static let recordOffset = 63
    /// The length of the version 1 descriptor.
    static let descriptorBytes = 32
    /// The first bytes of every PNG.
    static let pngSignature = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    /// Thumbnails above this size are not read, so a damaged length never reads a whole file.
    static let maximumBytes = 8 * 1024 * 1024

    /// A picture a file carries of itself.
    struct Picture {
        let png: Data
        /// Whether the data changed after the picture was taken.
        let isStale: Bool
    }

    /// Returns the PNG a file carries of itself, or nil when it has none or only a stale one.
    static func current(_ url: URL) -> Data? {
        guard let picture = picture(url), !picture.isStale else {
            return nil
        }
        return picture.png
    }

    /// Returns the picture a file carries of itself, or nil when it has none.
    static func picture(_ url: URL) -> Picture? {
        guard url.pathExtension.lowercased() == "pv", let handle = try? FileHandle(forReadingFrom: url) else {
            return nil
        }
        defer { try? handle.close() }
        return locate(in: handle)
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
        guard header >= descriptorBytes, length <= maximumBytes, header + length == recordLength,
              (try? handle.seek(toOffset: UInt64(recordOffset + header))) != nil,
              let png = try? handle.read(upToCount: length), png.count == length,
              png.starts(with: pngSignature)
        else {
            return nil
        }
        return Picture(
            png: png,
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
        // The checksum covers the entries after the first two frames, so fewer leave nothing to check.
        guard frames < size / 16, 16 * frames > 32, size - 8 >= 16 * frames else {
            return false
        }
        let start = size - 8 - 16 * frames + 32
        let count = Int(size - 8 - start)
        guard (try? handle.seek(toOffset: start)) != nil,
              let entries = try? handle.read(upToCount: count),
              entries.count == count
        else {
            return false
        }
        let actual = entries.withUnsafeBytes { bytes in
            UInt32(zlib.crc32(0, bytes.bindMemory(to: Bytef.self).baseAddress, uInt(bytes.count)))
        }
        return actual == crc
    }

    /// Reads a little-endian `u32` at an offset into some bytes.
    static func uint32(_ data: Data, _ offset: Int) -> UInt32 {
        data.withUnsafeBytes { bytes in
            UInt32(littleEndian: bytes.loadUnaligned(fromByteOffset: offset - data.startIndex, as: UInt32.self))
        }
    }
}
