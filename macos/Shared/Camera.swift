import AppKit
import Foundation
import SceneKit

/// PyVista's default view direction, from the scene toward the camera, with +z up.
let viewDirection = simd_normalize(SIMD3<Float>(1, 1, 1))
/// PyVista's default view angle, in degrees.
let viewAngle: Float = 30
/// Space left around the scene's outline, as a share of its side; `_still.py`'s MARGIN matches.
let frameMargin: Float = 0.05

/// A camera framing a scene, and the point it turns about.
typealias Framing = (node: SCNNode, target: SIMD3<Float>)

/// Returns every vertex in the scene, in world coordinates.
func worldVertices(_ scene: SCNScene) -> [SIMD3<Float>] {
    var points: [SIMD3<Float>] = []
    scene.rootNode.enumerateHierarchy { node, _ in
        guard let geometry = node.geometry else {
            return
        }
        let transform = node.simdWorldTransform
        for source in geometry.sources(for: .vertex)
        where source.usesFloatComponents && source.componentsPerVector >= 3 {
            let wide = source.bytesPerComponent == 8
            source.data.withUnsafeBytes { raw in
                for index in 0..<source.vectorCount {
                    let start = source.dataOffset + index * source.dataStride
                    var point = SIMD4<Float>(0, 0, 0, 1)
                    for axis in 0..<3 {
                        let at = start + axis * source.bytesPerComponent
                        point[axis] = wide
                            ? Float(raw.loadUnaligned(fromByteOffset: at, as: Double.self))
                            : raw.loadUnaligned(fromByteOffset: at, as: Float.self)
                    }
                    let world = transform * point
                    points.append(SIMD3(world.x, world.y, world.z))
                }
            }
        }
    }
    return points
}

/// Returns a camera where PyVista puts its own, and the point it turns about.
///
/// The camera is aimed through the middle of the scene's outline and its view angle narrowed
/// until the outline fills it less a margin, as a still is cropped to the dataset.
func previewCamera(for scene: SCNScene) -> Framing {
    let (center, radius) = scene.rootNode.boundingSphere
    let middle = SIMD3<Float>(Float(center.x), Float(center.y), Float(center.z))
    let halfAngle = viewAngle / 2 * .pi / 180
    // VTK backs away until the bounding sphere fills its view angle.
    let distance = Float(radius) / sin(halfAngle)

    let camera = SCNCamera()
    camera.zNear = 0.01
    camera.zFar = Double(distance + Float(radius)) * 10
    let node = SCNNode()
    node.camera = camera
    node.simdPosition = middle + viewDirection * distance
    scene.rootNode.addChildNode(node)

    let up = SIMD3<Float>(0, 0, 1)
    var target = middle
    var span = 2 * tan(halfAngle)
    let points = worldVertices(scene)
    // Aiming moves the outline a little, so it is measured again from the new aim.
    for _ in 0..<3 {
        node.simdLook(at: target, up: up, localFront: SIMD3(0, 0, -1))
        let toCamera = node.simdWorldTransform.inverse
        var low = SIMD2<Float>(repeating: .infinity)
        var high = -low
        for point in points {
            let seen = toCamera * SIMD4(point, 1)
            guard seen.z < 0 else {
                continue
            }
            let slope = SIMD2(seen.x, seen.y) / -seen.z
            low = simd_min(low, slope)
            high = simd_max(high, slope)
        }
        guard low.x <= high.x else {
            break
        }
        let aim = (low + high) / 2
        let depth = simd_distance(node.simdPosition, middle)
        let world = node.simdWorldTransform * SIMD4(aim.x * depth, aim.y * depth, -depth, 1)
        target = SIMD3(world.x, world.y, world.z)
        span = max(high.x - low.x, high.y - low.y) * (1 + 2 * frameMargin)
    }
    node.simdLook(at: target, up: up, localFront: SIMD3(0, 0, -1))
    camera.fieldOfView = CGFloat(2 * atan(span / 2) * 180 / .pi)
    return (node, target)
}

/// Whether a scene file asks for its faces drawn flat, as PyVista's theme draws them by default.
func drawsFlat(_ url: URL) -> Bool {
    guard let handle = try? FileHandle(forReadingFrom: url) else {
        return false
    }
    defer { try? handle.close() }
    let head = (try? handle.read(upToCount: 1024)) ?? Data()
    return head.range(of: Data("comment pvql shading flat\n".utf8)) != nil
}

/// Shades every face with its own slope, as VTK's flat interpolation does.
func drawFlat(_ scene: SCNScene) {
    // The view-space position's change across the pixel gives the face's normal toward the eye.
    let modifier = "_surface.normal = normalize(cross(dfdy(_surface.position), dfdx(_surface.position)));"
    scene.rootNode.enumerateHierarchy { node, _ in
        node.geometry?.materials.forEach { $0.shaderModifiers = [.surface: modifier] }
    }
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
