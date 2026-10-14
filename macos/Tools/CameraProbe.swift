import Foundation

/// Moves a trackball camera as told and prints, as JSON, where it ends up.
@main
enum CameraProbe {
    static func main() throws {
        let arguments = CommandLine.arguments.dropFirst()
        let numbers = arguments.prefix(9).compactMap(Float.init)
        guard numbers.count == 9 else {
            let usage = """
            usage: CameraProbe px py pz tx ty tz ux uy uz [move ...], each move one of azimuth=deg,
            elevation=deg, roll=deg, dolly=factor, wheel=notches, rotate=dx,dy,w,h, spin=x0,y0,x1,y1,w,h,
            pan=dx,dy,w,h,fov,vertical, drag=dy,w,h
            """
            FileHandle.standardError.write(Data("\(usage)\n".utf8))
            exit(2)
        }
        var camera = Trackball(
            position: SIMD3(numbers[0], numbers[1], numbers[2]),
            target: SIMD3(numbers[3], numbers[4], numbers[5]),
            up: SIMD3(numbers[6], numbers[7], numbers[8])
        )
        for move in arguments.dropFirst(9) {
            let parts = move.split(separator: "=", maxSplits: 1).map(String.init)
            let values = parts.count == 2 ? parts[1].split(separator: ",").compactMap { Float($0) } : []
            switch (parts[0], values.count) {
            case ("azimuth", 1): camera.azimuth(values[0])
            case ("elevation", 1): camera.elevation(values[0])
            case ("roll", 1): camera.roll(values[0])
            case ("dolly", 1): camera.dolly(values[0])
            case ("wheel", 1): camera.wheel(values[0])
            case ("rotate", 4): camera.rotate(dx: values[0], dy: values[1], viewport: SIMD2(values[2], values[3]))
            case ("spin", 6):
                camera.spin(
                    from: SIMD2(values[0], values[1]),
                    to: SIMD2(values[2], values[3]),
                    viewport: SIMD2(values[4], values[5])
                )
            case ("pan", 6):
                camera.pan(
                    dx: values[0],
                    dy: values[1],
                    viewport: SIMD2(values[2], values[3]),
                    fieldOfView: values[4],
                    vertical: values[5] != 0
                )
            case ("drag", 3): camera.dolly(dy: values[0], viewport: SIMD2(values[1], values[2]))
            default:
                FileHandle.standardError.write(Data("bad move \(move)\n".utf8))
                exit(2)
            }
        }
        let report: [String: [Float]] = [
            "position": [camera.position.x, camera.position.y, camera.position.z],
            "target": [camera.target.x, camera.target.y, camera.target.z],
            "up": [camera.up.x, camera.up.y, camera.up.z],
        ]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]))
    }
}
