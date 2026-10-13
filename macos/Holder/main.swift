// Holds the extensions; never launched, only run to unregister the agent 0.9.0 registered.
import Foundation
import ServiceManagement

let service = SMAppService.agent(plistName: "org.pyvista.PyVistaQuickLook.Service.plist")

/// How System Settings has the agent.
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
    print("This app holds the PyVista Quick Look extensions; it does nothing on its own.")
}
