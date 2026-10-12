import AppKit
import Foundation
import Metal
import SceneKit

/// Renders a scene file off screen with the same camera the panel uses.
func render(_ input: URL, to output: URL, size: CGFloat) throws {
    let scene = try SCNScene(url: input, options: nil)

    // SCNScene reports an unreadable file by handing back an empty scene.
    var geometries = 0
    scene.rootNode.enumerateHierarchy { node, _ in
        if node.geometry != nil {
            geometries += 1
        }
    }
    guard geometries > 0 else {
        throw NSError(
            domain: "RenderScene",
            code: 3,
            userInfo: [NSLocalizedDescriptionKey: "\(input.path) holds no geometry"]
        )
    }

    guard let device = MTLCreateSystemDefaultDevice() else {
        throw NSError(
            domain: "RenderScene",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "no Metal device is available"]
        )
    }

    let renderer = SCNRenderer(device: device, options: nil)
    renderer.scene = scene
    let camera = previewCamera(for: scene)
    lightLikePyVista(scene, camera: camera)
    renderer.pointOfView = camera

    let image = renderer.snapshot(
        atTime: 0,
        with: CGSize(width: size, height: size),
        antialiasingMode: .multisampling4X
    )
    guard let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let png = bitmap.representation(using: .png, properties: [:])
    else {
        throw NSError(
            domain: "RenderScene",
            code: 2,
            userInfo: [NSLocalizedDescriptionKey: "the render could not be encoded"]
        )
    }
    try png.write(to: output)
}

@main
enum RenderScene {
    static func main() {
        let arguments = CommandLine.arguments
        guard arguments.count >= 3 else {
            FileHandle.standardError.write(Data("usage: RenderScene <scene> <png> [size]\n".utf8))
            exit(2)
        }
        let side = arguments.count > 3 ? CGFloat(Double(arguments[3]) ?? 512) : 512
        do {
            try render(
                URL(fileURLWithPath: arguments[1]),
                to: URL(fileURLWithPath: arguments[2]),
                size: side
            )
        } catch {
            FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }
}
