// Holds the extensions and the render service; never launched, only run to (un)register it.
import Foundation
import ServiceManagement

let service = SMAppService.agent(plistName: "org.pyvista.PyVistaQuickLook.Service.plist")

/// How System Settings has the service, in words `pvql doctor` matches on.
func describe(_ status: SMAppService.Status) -> String {
    switch status {
    case .enabled: return "enabled"
    case .requiresApproval: return "requires approval"
    case .notRegistered: return "not registered"
    case .notFound: return "not found"
    @unknown default: return "unknown"
    }
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("\(message)\n".utf8))
    exit(1)
}

switch CommandLine.arguments.dropFirst().first {
case "register":
    // Left alone when switched off in System Settings, which is the reader's choice.
    if service.status == .requiresApproval {
        print(describe(service.status))
        exit(0)
    }
    // Registered afresh, since macOS runs only the build of the program it registered.
    try? service.unregister()
    do {
        try service.register()
    } catch {
        fail("\(error.localizedDescription) (\(describe(service.status)))")
    }
    print(describe(service.status))
case "unregister":
    if service.status != .notRegistered {
        do {
            try service.unregister()
        } catch {
            fail(error.localizedDescription)
        }
    }
    print(describe(service.status))
case "status":
    print(describe(service.status))
default:
    print("This app holds the PyVista Quick Look extensions and render service; it does nothing on its own.")
}
