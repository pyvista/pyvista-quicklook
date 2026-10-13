// What the holder's service agent runs: finds pvql, sends output to the log, and becomes it.
import Darwin
import Foundation

@main
enum ServiceLauncher {
    static func main() {
        let logs = "\(realHome())/Library/Logs"
        try? FileManager.default.createDirectory(atPath: logs, withIntermediateDirectories: true)
        let log = open("\(logs)/pvqld.log", O_WRONLY | O_CREAT | O_APPEND, 0o644)
        if log >= 0 {
            dup2(log, STDOUT_FILENO)
            dup2(log, STDERR_FILENO)
            close(log)
        }
        guard let helper = Helper.locate() else {
            FileHandle.standardError.write(Data("\(Helper.missingHelperMessage)\n".utf8))
            // launchd restarts the service; waiting keeps that from filling the log.
            sleep(60)
            exit(1)
        }
        let arguments = [helper, "daemon"].map { strdup($0) } + [nil]
        execv(helper, arguments)
        FileHandle.standardError.write(Data("could not start \(helper): \(String(cString: strerror(errno)))\n".utf8))
        sleep(60)
        exit(1)
    }
}
