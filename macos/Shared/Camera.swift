import AppKit
import Foundation
import SceneKit

/// PyVista's default view direction, from the scene toward the camera, with +z up.
let viewDirection = simd_normalize(SIMD3<Float>(1, 1, 1))
/// PyVista's default view angle, in degrees.
let viewAngle: Float = 30
/// Space left around the scene's outline, as a share of its side, as pyvista-zstd leaves around its pictures.
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

/// A camera moved as VTK's trackball camera moves PyVista's: turned about a focal point,
/// panned with it, and brought toward it.
struct Trackball {
    var position: SIMD3<Float>
    var target: SIMD3<Float>
    var up: SIMD3<Float>

    /// VTK's `MotionFactor`, which scales every mouse motion.
    static let motionFactor: Float = 10
    /// The base VTK raises to the motion to bring the camera in.
    static let dollyBase: Float = 1.1
    /// VTK's `MouseWheelMotionFactor`, a wheel notch's share of the motion factor.
    static let wheelMotionFactor: Float = 0.2

    /// From the camera toward the focal point.
    var forward: SIMD3<Float> {
        simd_normalize(target - position)
    }

    /// The camera's right, as VTK's cross of the direction of projection and the view up.
    var right: SIMD3<Float> {
        simd_normalize(simd_cross(forward, up))
    }

    /// Turns the camera about the view up, through the focal point; VTK's `Azimuth`.
    mutating func azimuth(_ degrees: Float) {
        turn(about: simd_normalize(up), by: degrees)
    }

    /// Lifts or lowers the camera about its right, through the focal point; VTK's `Elevation`.
    mutating func elevation(_ degrees: Float) {
        turn(about: right, by: -degrees)
        up = simd_normalize(simd_cross(right, forward))
    }

    /// Rolls the camera about its line of sight; VTK's `Roll`.
    mutating func roll(_ degrees: Float) {
        up = simd_act(simd_quatf(angle: degrees * .pi / 180, axis: forward), up)
    }

    /// Moves the camera and the focal point together across the view.
    mutating func translate(right dx: Float, up dy: Float) {
        let move = right * dx + simd_normalize(up) * dy
        position += move
        target += move
    }

    /// Brings the camera toward the focal point by a factor, or away for one below 1; VTK's `Dolly`.
    mutating func dolly(_ factor: Float) {
        position = target + (position - target) / factor
    }

    private mutating func turn(about axis: SIMD3<Float>, by degrees: Float) {
        let rotation = simd_quatf(angle: degrees * .pi / 180, axis: axis)
        position = target + simd_act(rotation, position - target)
        up = simd_act(rotation, up)
    }

    // What VTK's `vtkInteractorStyleTrackballCamera` makes of the mouse, with dx and dy the
    // mouse's motion in points, y upward, and the view's size in points.

    /// A drag: VTK's `Rotate`, 20 degrees times the motion factor across the view.
    mutating func rotate(dx: Float, dy: Float, viewport: SIMD2<Float>) {
        azimuth(dx * -20 / viewport.x * Self.motionFactor)
        elevation(dy * -20 / viewport.y * Self.motionFactor)
    }

    /// A drag with control held: VTK's `Spin`, rolling by the angle swept about the view's centre.
    mutating func spin(from: SIMD2<Float>, to: SIMD2<Float>, viewport: SIMD2<Float>) {
        let centre = viewport / 2
        let was = atan2(from.y - centre.y, from.x - centre.x)
        let now = atan2(to.y - centre.y, to.x - centre.x)
        roll((now - was) * 180 / .pi)
    }

    /// A drag with shift held, or with the middle button: VTK's `Pan`, keeping the point
    /// under the mouse under it, at the focal point's depth.
    mutating func pan(dx: Float, dy: Float, viewport: SIMD2<Float>, fieldOfView: Float, vertical: Bool) {
        let depth = simd_distance(position, target)
        let span = 2 * depth * tan(fieldOfView / 2 * .pi / 180)
        let perPoint = span / (vertical ? viewport.y : viewport.x)
        translate(right: -dx * perPoint, up: -dy * perPoint)
    }

    /// A drag with the right button: VTK's `Dolly`, 1.1 to the motion factor over half the view.
    mutating func dolly(dy: Float, viewport: SIMD2<Float>) {
        dolly(pow(Self.dollyBase, Self.motionFactor * dy / (viewport.y / 2)))
    }

    /// A wheel notch, or a share of one: forward brings the camera in, as VTK's `OnMouseWheelForward`.
    mutating func wheel(_ notches: Float) {
        dolly(pow(Self.dollyBase, Self.motionFactor * Self.wheelMotionFactor * notches))
    }
}

/// How a scene's theme shades and lights it; `_scene_export.py`'s STYLE_COMMENT writes it.
struct SceneStyle: Decodable {
    var shading = "flat"
    var lighting = true
    var ambient: CGFloat = 0
    var diffuse: CGFloat = 1
    var specular: CGFloat = 0
    var specularPower: CGFloat = 100
    var primitive = "faces"
    var pointSize: CGFloat = 5

    enum CodingKeys: String, CodingKey {
        case shading, lighting, ambient, diffuse, specular, primitive
        case specularPower = "specular_power"
        case pointSize = "point_size"
    }

    /// The style a scene file's header names, or PyVista's defaults when it names none.
    static func read(_ url: URL) -> SceneStyle {
        guard let handle = try? FileHandle(forReadingFrom: url) else {
            return SceneStyle()
        }
        defer { try? handle.close() }
        let head = (try? handle.read(upToCount: 2048)) ?? Data()
        let marker = Data("comment pvql style ".utf8)
        guard let start = head.range(of: marker)?.upperBound,
              let end = head[start...].firstIndex(of: UInt8(ascii: "\n")),
              let style = try? JSONDecoder().decode(SceneStyle.self, from: head[start..<end])
        else {
            return SceneStyle()
        }
        return style
    }

    /// Gives every material the theme's shading and its share of each kind of light.
    func apply(to scene: SCNScene) {
        if primitive != "faces" {
            // Points and lines are drawn in their own colour, unlit, as PyVista draws them.
            scene.rootNode.enumerateHierarchy { node, _ in
                guard let geometry = node.geometry else { return }
                node.geometry = primitive == "points" ? points(of: geometry) : lines(of: geometry)
                node.geometry?.materials.forEach { $0.lightingModel = .constant }
            }
            return
        }
        if lighting && ambient > 0 {
            // VTK's ambient light is white; the theme's ambient share sets how much reaches a face.
            let light = SCNLight()
            light.type = .ambient
            light.intensity = unitLightIntensity * ambient
            light.color = NSColor.white
            let node = SCNNode()
            node.light = light
            scene.rootNode.addChildNode(node)
        }
        // The view-space position's change across the pixel gives the face's normal toward the eye.
        let flat = "_surface.normal = normalize(cross(dfdy(_surface.position), dfdx(_surface.position)));"
        // A back face keeps its vertex normal, which points away from the eye; turn it round.
        let smooth = "if (dot(_surface.normal, _surface.view) < 0.0) { _surface.normal = -_surface.normal; }"
        scene.rootNode.enumerateHierarchy { node, _ in
            node.geometry?.materials.forEach { material in
                // PyVista culls nothing by default, so a surface shows from either side.
                material.isDoubleSided = true
                material.shaderModifiers = [.surface: shading == "flat" ? flat : smooth]
                material.lightingModel = !lighting ? .constant : specular > 0 ? .blinn : .lambert
                material.diffuse.intensity = lighting ? diffuse : 1
                material.locksAmbientWithDiffuse = true
                material.specular.contents = NSColor(white: 1, alpha: 1)
                material.specular.intensity = specular
                material.shininess = specularPower
            }
        }
    }

    /// The geometry's vertices as round points of the theme's size in pixels.
    private func points(of geometry: SCNGeometry) -> SCNGeometry {
        let count = geometry.sources(for: .vertex).first?.vectorCount ?? 0
        let element = SCNGeometryElement(
            data: nil, primitiveType: .point, primitiveCount: count, bytesPerIndex: 4
        )
        // SceneKit's "radius" comes out as roughly the point's diameter in pixels.
        element.pointSize = pointSize
        element.minimumPointScreenSpaceRadius = pointSize
        element.maximumPointScreenSpaceRadius = pointSize
        return rebuilt(geometry, with: [element])
    }

    /// Each folded-back triangle (a, b, a) the exporter wrote, as the line segment a-b.
    private func lines(of geometry: SCNGeometry) -> SCNGeometry {
        let elements = geometry.elements.compactMap { element -> SCNGeometryElement? in
            guard element.primitiveType == .triangles else { return nil }
            var ends: [UInt32] = []
            ends.reserveCapacity(element.primitiveCount * 2)
            element.data.withUnsafeBytes { raw in
                for triangle in 0..<element.primitiveCount {
                    let base = triangle * 3
                    if element.bytesPerIndex == 2 {
                        let ids = raw.bindMemory(to: UInt16.self)
                        ends += [UInt32(ids[base]), UInt32(ids[base + 1])]
                    } else {
                        let ids = raw.bindMemory(to: UInt32.self)
                        ends += [ids[base], ids[base + 1]]
                    }
                }
            }
            return SCNGeometryElement(indices: ends, primitiveType: .line)
        }
        return rebuilt(geometry, with: elements)
    }

    private func rebuilt(_ geometry: SCNGeometry, with elements: [SCNGeometryElement]) -> SCNGeometry {
        let sources = geometry.sources.filter { $0.semantic != .normal }
        let made = SCNGeometry(sources: sources, elements: elements)
        made.materials = geometry.materials
        return made
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

/// Lights a scene as PyVista does, returning the node that holds the lights.
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
    return rig
}

/// Turns the lights with the camera before each frame. Camera control draws through a camera
/// of its own once the reader turns the scene, so lights placed under the first camera would
/// stay behind.
final class LightsFollowCamera: NSObject, SCNSceneRendererDelegate {
    let rig: SCNNode
    /// Called on the main thread once the first few frames have been drawn.
    var onReady: (() -> Void)?
    /// Frames drawn so far; the first can come out before the scene is ready.
    private var framesDrawn = 0

    init(_ rig: SCNNode) {
        self.rig = rig
    }

    func renderer(_ renderer: SCNSceneRenderer, updateAtTime time: TimeInterval) {
        if let camera = renderer.pointOfView {
            rig.simdWorldOrientation = camera.simdWorldOrientation
        }
    }

    func renderer(_ renderer: SCNSceneRenderer, didRenderScene scene: SCNScene, atTime time: TimeInterval) {
        framesDrawn += 1
        guard framesDrawn >= 3, let ready = onReady else {
            return
        }
        onReady = nil
        DispatchQueue.main.async(execute: ready)
    }
}
