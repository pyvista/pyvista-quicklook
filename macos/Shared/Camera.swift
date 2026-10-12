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

/// SceneKit's intensity for a light of unit strength, in lumens.
let unitLightIntensity: CGFloat = 1000

/// PyVista's default lights, the defaults of VTK's `vtkLightKit` (key light 0.75 at elevation
/// 50 and azimuth 10, fill, back and head lights at ratios 3, 3.5 and 3), as
/// `pv.Plotter().renderer.lights` reports them: the direction toward each light in camera
/// space (+x right, +y up, +z toward the viewer), its intensity and its colour.
let lightKit: [(direction: SIMD3<Float>, intensity: CGFloat, color: (CGFloat, CGFloat, CGFloat))] = [
    (SIMD3(0, 0, 1), 0.25, (1, 1, 1)),
    (SIMD3(0.112, 0.766, 0.633), 0.75, (1, 0.9725, 0.902)),
    (SIMD3(-0.045, -0.966, 0.255), 0.25, (0.9098, 0.9333, 1)),
    (SIMD3(0.94, 0, -0.342), 0.2143, (1, 1, 1)),
    (SIMD3(-0.94, 0, -0.342), 0.2143, (1, 1, 1)),
]

/// Lights and shades a scene as PyVista does, returning the node that holds the lights.
///
/// The lights are given in camera space, so the returned node must take the orientation of
/// whatever camera draws the scene; `LightsFollowCamera` keeps it so as the reader turns it.
func lightLikePyVista(_ scene: SCNScene) -> SCNNode {
    let rig = SCNNode()
    for light in lightKit {
        let source = SCNLight()
        source.type = .directional
        source.intensity = unitLightIntensity * light.intensity
        source.color = NSColor(srgbRed: light.color.0, green: light.color.1, blue: light.color.2, alpha: 1)
        let node = SCNNode()
        node.light = source
        // A directional light shines along its node's -z, from the light toward the scene.
        node.simdOrientation = simd_quatf(from: SIMD3(0, 0, -1), to: -simd_normalize(light.direction))
        rig.addChildNode(node)
    }
    scene.rootNode.addChildNode(rig)
    scene.rootNode.enumerateHierarchy { node, _ in
        node.geometry?.materials.forEach { $0.lightingModel = .lambert }
    }
    return rig
}

/// Turns the lights with the camera before each frame. Camera control draws through a camera
/// of its own once the reader turns the scene, so lights placed under the first camera would
/// stay behind.
final class LightsFollowCamera: NSObject, SCNSceneRendererDelegate {
    let rig: SCNNode

    init(_ rig: SCNNode) {
        self.rig = rig
    }

    func renderer(_ renderer: SCNSceneRenderer, updateAtTime time: TimeInterval) {
        if let camera = renderer.pointOfView {
            rig.simdWorldOrientation = camera.simdWorldOrientation
        }
    }
}
