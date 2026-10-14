import Foundation
import ModelIO
import simd

/// Writes each mesh of a Model I/O file as a world-space PLY, with a manifest naming them.
@main
enum ModelIOExport {
    static func main() {
        let arguments = CommandLine.arguments
        guard arguments.count == 3 else {
            fail("usage: pvql-modelio <model file> <output folder>", status: 2)
        }
        let source = URL(fileURLWithPath: arguments[1])
        let folder = URL(fileURLWithPath: arguments[2], isDirectory: true)
        guard MDLAsset.canImportFileExtension(source.pathExtension) else {
            fail("Model I/O does not read .\(source.pathExtension) files", status: 2)
        }
        let asset = MDLAsset(url: source)
        // Model I/O only logs what went wrong; a file it could not read yields no objects.
        guard asset.count > 0 else {
            fail("pvql-modelio: Model I/O could not read \(source.lastPathComponent)", status: 1)
        }
        var manifest: [[String: String]] = []
        for index in 0..<asset.count {
            walk(asset.object(at: index), path: "", into: &manifest, folder: folder)
        }
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: manifest, options: [.sortedKeys])
            try data.write(to: folder.appendingPathComponent("manifest.json"))
        } catch {
            fail("could not write \(folder.path): \(error)", status: 1)
        }
    }

    static func fail(_ message: String, status: Int32) -> Never {
        FileHandle.standardError.write(Data("\(message)\n".utf8))
        exit(status)
    }

    /// Writes the meshes under an object, naming each by the path of object names above it.
    static func walk(_ object: MDLObject, path: String, into manifest: inout [[String: String]], folder: URL) {
        let name = object.name.isEmpty ? "object" : object.name
        let here = path.isEmpty ? name : "\(path)/\(name)"
        if let mesh = object as? MDLMesh {
            let file = "mesh-\(manifest.count).ply"
            let transform = MDLTransform.globalTransform(with: object, atTime: 0)
            if let ply = PLY(mesh: mesh, transform: transform) {
                do {
                    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                    try ply.data.write(to: folder.appendingPathComponent(file))
                    manifest.append(["name": here, "file": file])
                } catch {
                    fail("could not write \(file): \(error)", status: 1)
                }
            }
        }
        for child in object.children.objects {
            walk(child, path: here, into: &manifest, folder: folder)
        }
    }
}

/// A binary little-endian PLY of one mesh: positions, any normals and colours, and faces.
struct PLY {
    var data = Data()
    var faceCount = 0

    init?(mesh: MDLMesh, transform: float4x4) {
        guard let positions = mesh.vertexAttributeData(forAttributeNamed: MDLVertexAttributePosition, as: .float3) else {
            return nil
        }
        var normals = mesh.vertexAttributeData(forAttributeNamed: MDLVertexAttributeNormal, as: .float3)
        let colors = mesh.vertexAttributeData(forAttributeNamed: MDLVertexAttributeColor, as: .float3)
        let count = mesh.vertexCount
        guard positions.bufferSize >= count * positions.stride else { return nil }
        // Normals turn with the mesh but do not stretch with it; a flattened mesh has none.
        let linear = simd_float3x3(columns: (
            simd_make_float3(transform.columns.0),
            simd_make_float3(transform.columns.1),
            simd_make_float3(transform.columns.2)
        ))
        if linear.determinant == 0 { normals = nil }
        let rotation = linear.inverse.transpose

        var faces = Data()
        for case let submesh as MDLSubmesh in mesh.submeshes ?? [] {
            append(submesh, to: &faces)
        }
        guard faceCount > 0 else { return nil }

        var header = "ply\nformat binary_little_endian 1.0\n"
        header += "comment pvql modelio export\n"
        header += "element vertex \(count)\nproperty float x\nproperty float y\nproperty float z\n"
        if normals != nil { header += "property float nx\nproperty float ny\nproperty float nz\n" }
        if colors != nil { header += "property uchar red\nproperty uchar green\nproperty uchar blue\n" }
        header += "element face \(faceCount)\nproperty list uchar int vertex_indices\nend_header\n"
        data.append(Data(header.utf8))
        for index in 0..<count {
            let point = transform * SIMD4(vector(positions, at: index), 1)
            appendFloats([point.x, point.y, point.z])
            if let normals {
                let normal = simd_normalize(rotation * vector(normals, at: index))
                appendFloats([normal.x, normal.y, normal.z])
            }
            if let colors {
                let color = simd_clamp(vector(colors, at: index), SIMD3(repeating: 0), SIMD3(repeating: 1))
                data.append(contentsOf: [UInt8(color.x * 255 + 0.5), UInt8(color.y * 255 + 0.5), UInt8(color.z * 255 + 0.5)])
            }
        }
        data.append(faces)
    }

    private func vector(_ attribute: MDLVertexAttributeData, at index: Int) -> SIMD3<Float> {
        let start = attribute.dataStart.advanced(by: index * attribute.stride)
        let floats = start.assumingMemoryBound(to: Float.self)
        return SIMD3(floats[0], floats[1], floats[2])
    }

    private mutating func appendFloats(_ values: [Float]) {
        for value in values {
            var little = value.bitPattern.littleEndian
            data.append(Data(bytes: &little, count: 4))
        }
    }

    /// Appends a submesh's triangles, quads and strips as faces; lines and points are not faces.
    private mutating func append(_ submesh: MDLSubmesh, to faces: inout Data) {
        let indices = submesh.indices
        func face(_ ids: [UInt32]) {
            faces.append(UInt8(ids.count))
            for id in ids {
                var little = id.littleEndian
                faces.append(Data(bytes: &little, count: 4))
            }
            faceCount += 1
        }
        switch submesh.geometryType {
        case .triangles:
            for start in stride(from: 0, to: indices.count - 2, by: 3) {
                face(Array(indices[start..<start + 3]))
            }
        case .quads:
            for start in stride(from: 0, to: indices.count - 3, by: 4) {
                face(Array(indices[start..<start + 4]))
            }
        case .triangleStrips:
            for start in 0..<max(indices.count - 2, 0) {
                let (a, b, c) = (indices[start], indices[start + 1], indices[start + 2])
                face(start % 2 == 0 ? [a, b, c] : [b, a, c])
            }
        default:
            break
        }
    }
}

extension MDLSubmesh {
    /// The submesh's indices widened to 32 bits, whatever width the file stored them at.
    var indices: [UInt32] {
        let map = indexBuffer.map()
        let count = indexCount
        switch indexType {
        case .uInt8:
            let bytes = map.bytes.assumingMemoryBound(to: UInt8.self)
            return (0..<count).map { UInt32(bytes[$0]) }
        case .uInt16:
            let shorts = map.bytes.assumingMemoryBound(to: UInt16.self)
            return (0..<count).map { UInt32(shorts[$0]) }
        case .uInt32:
            let ints = map.bytes.assumingMemoryBound(to: UInt32.self)
            return (0..<count).map { ints[$0] }
        case .invalid:
            return []
        @unknown default:
            return []
        }
    }
}
