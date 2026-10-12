import AppKit
import Foundation
import SceneKit

/// Returns a camera three quarters round and a little above the scene, with +z up.
func previewCamera(for scene: SCNScene) -> SCNNode {
    let (center, radius) = scene.rootNode.boundingSphere
    let camera = SCNCamera()
    camera.zNear = 0.01
    camera.zFar = Double(radius) * 100

    let node = SCNNode()
    node.camera = camera

    let middle = SCNVector3(CGFloat(center.x), CGFloat(center.y), CGFloat(center.z))
    let offset = SCNVector3(1, 1, 0.85)
    let length = sqrt(offset.x * offset.x + offset.y * offset.y + offset.z * offset.z)
    let distance = CGFloat(radius) * 2.6
    node.position = SCNVector3(
        middle.x + offset.x / length * distance,
        middle.y + offset.y / length * distance,
        middle.z + offset.z / length * distance
    )
    scene.rootNode.addChildNode(node)
    node.look(at: middle, up: SCNVector3(0, 0, 1), localFront: SCNVector3(0, 0, -1))
    return node
}

/// PyVista's default lights, VTK's light kit: direction toward the light from the camera's
/// point of view, intensity and colour.
let lightKit: [(direction: SIMD3<Float>, intensity: CGFloat, color: (CGFloat, CGFloat, CGFloat))] = [
    (SIMD3(0, 0, 1), 0.25, (1, 1, 1)),
    (SIMD3(0.112, 0.766, 0.633), 0.75, (1, 0.9725, 0.902)),
    (SIMD3(-0.045, -0.966, 0.255), 0.25, (0.9098, 0.9333, 1)),
    (SIMD3(0.94, 0, -0.342), 0.2143, (1, 1, 1)),
    (SIMD3(-0.94, 0, -0.342), 0.2143, (1, 1, 1)),
]

/// Lights and shades a scene as PyVista does: the light kit moving with the camera, on
/// purely diffuse surfaces.
func lightLikePyVista(_ scene: SCNScene, camera: SCNNode) {
    for light in lightKit {
        let source = SCNLight()
        source.type = .directional
        source.intensity = 1000 * light.intensity
        source.color = NSColor(
            calibratedRed: light.color.0, green: light.color.1, blue: light.color.2, alpha: 1
        )
        let node = SCNNode()
        node.light = source
        // A directional light shines along its node's -z.
        node.simdOrientation = simd_quatf(from: SIMD3(0, 0, -1), to: -simd_normalize(light.direction))
        camera.addChildNode(node)
    }
    scene.rootNode.enumerateHierarchy { node, _ in
        node.geometry?.materials.forEach { $0.lightingModel = .lambert }
    }
}
