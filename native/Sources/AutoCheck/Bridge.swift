import Foundation

// MARK: - Python 运行时检测（候选顺序与 make_app.sh 对齐）
enum Py {
    static func detect() -> String {
        let fm = FileManager.default
        if let env = ProcessInfo.processInfo.environment["CHECKIN_PYTHON"], !env.isEmpty,
           fm.isExecutableFile(atPath: env) { return env }

        var candidates: [String] = []
        // Marvis runtime 各版本 python311
        let marvis = NSString(string: NSHomeDirectory())
            .appendingPathComponent("Library/Application Support/com.tencent.mac.marvis/components/MarvisAgent/Versions")
        if let versions = try? fm.contentsOfDirectory(atPath: marvis) {
            for v in versions.sorted(by: >) {
                let p = marvis + "/" + v + "/runtime/python311/bin/python3"
                if fm.isExecutableFile(atPath: p) { candidates.append(p) }
            }
        }
        candidates += ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        for c in candidates where fm.isExecutableFile(atPath: c) {
            return c
        }
        return "/usr/bin/python3"
    }

    static let available: Bool = {
        let p = detect()
        let fm = FileManager.default
        return fm.isExecutableFile(atPath: p)
    }()
}

// MARK: - Checkin / 登录 流式输出（stdout 逐行回调）
@discardableResult
func runPythonStream(_ args: [String],
                     onLine: ((String) -> Void)? = nil,
                     completion: ((Int) -> Void)? = nil) -> Process? {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: Py.detect())
    p.arguments = args
    var env = ProcessInfo.processInfo.environment
    env["AUTOCHECKIN_DIR"] = AppPaths.projectDir
    env["PYTHONUNBUFFERED"] = "1"
    p.environment = env
    let out = Pipe()
    let err = Pipe()
    p.standardOutput = out
    p.standardError = err
    var outBuf = Data()
    var errBuf = Data()
    out.fileHandleForReading.readabilityHandler = { h in
        let data = h.availableData
        if data.isEmpty { return }
        outBuf.append(data)
        drain(&outBuf, onLine: onLine)
    }
    err.fileHandleForReading.readabilityHandler = { h in
        let data = h.availableData
        if data.isEmpty { return }
        errBuf.append(data)
        drain(&errBuf, onLine: onLine)
    }
    p.terminationHandler = { proc in
        drain(&outBuf, onLine: onLine, flush: true)
        drain(&errBuf, onLine: onLine, flush: true)
        let code = Int(proc.terminationStatus)
        DispatchQueue.main.async { completion?(code) }
    }
    do {
        try p.run()
        return p
    } catch {
        DispatchQueue.main.async { completion?(-1) }
        return nil
    }
}

private func drain(_ buffer: inout Data, onLine: ((String) -> Void)?, flush: Bool = false) {
    while let nl = buffer.firstIndex(of: 0x0A) {
        let lineData = buffer[..<nl]
        buffer.removeSubrange(...nl)
        if let s = String(data: lineData, encoding: .utf8), !s.trimmingCharacters(in: .whitespaces).isEmpty {
            DispatchQueue.main.async { onLine?(s.trimmingCharacters(in: .newlines)) }
        }
    }
    if flush, let s = String(data: buffer, encoding: .utf8), !s.trimmingCharacters(in: .whitespaces).isEmpty {
        DispatchQueue.main.async { onLine?(s.trimmingCharacters(in: .newlines)) }
        buffer.removeAll()
    }
}

// MARK: - 一次性执行并捕获完整输出
func runPythonCapture(_ args: [String], timeout: TimeInterval = 60) -> (code: Int, out: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: Py.detect())
    p.arguments = args
    var env = ProcessInfo.processInfo.environment
    env["AUTOCHECKIN_DIR"] = AppPaths.projectDir
    env["PYTHONUNBUFFERED"] = "1"
    p.environment = env
    let out = Pipe()
    let err = Pipe()
    p.standardOutput = out
    p.standardError = err
    do {
        try p.run()
    } catch {
        return (-1, "进程启动失败: \(error.localizedDescription)")
    }
    var deadline = CFAbsoluteTimeGetCurrent() + timeout
    while p.isRunning && CFAbsoluteTimeGetCurrent() < deadline {
        RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow: 0.05))
    }
    if p.isRunning {
        p.terminate()
        return (-2, "超时")
    }
    let o = (try? out.fileHandleForReading.readToEnd()).map { String(data: $0, encoding: .utf8) ?? "" } ?? ""
    let e = (try? err.fileHandleForReading.readToEnd()).map { String(data: $0, encoding: .utf8) ?? "" } ?? ""
    return (Int(p.terminationStatus), o + e)
}

// MARK: - launchd 管理（签名/路径沿用 com.marvis.autocheckin）
enum LaunchdManager {
    static let label = "com.marvis.autocheckin"
    static var plistPath: String {
        NSString(string: NSHomeDirectory()).appendingPathComponent("Library/LaunchAgents/com.marvis.autocheckin.plist")
    }

    struct State {
        var installed: Bool = false
        var hour: Int = 21
        var minute: Int = 30
        var nextDate: Date?
        var deltaText: String = ""
    }

    static func currentState() -> State {
        var st = State()
        if let data = try? Data(contentsOf: URL(fileURLWithPath: plistPath)),
           let dict = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any] {
            if let cal = dict["StartCalendarInterval"] as? [String: Any] {
                st.hour = (cal["Hour"] as? Int) ?? 21
                st.minute = (cal["Minute"] as? Int) ?? 30
            } else if let arr = dict["StartCalendarInterval"] as? [[String: Any]], let first = arr.first {
                st.hour = (first["Hour"] as? Int) ?? 21
                st.minute = (first["Minute"] as? Int) ?? 30
            }
        }
        let list = (try? runPythonCaptureByShell(["launchctl", "list"])) ?? ""
        st.installed = list.contains(label)
        if st.installed {
            var comp = Calendar.current.dateComponents([.year, .month, .day], from: Date())
            comp.hour = st.hour
            comp.minute = st.minute
            var next = Calendar.current.date(from: comp) ?? Date()
            if next <= Date() {
                next = Calendar.current.date(byAdding: .day, value: 1, to: next) ?? Date()
            }
            st.nextDate = next
            let sec = Int(next.timeIntervalSinceNow)
            st.deltaText = "\(sec / 3600) 小时 \((sec % 3600) / 60) 分"
        }
        return st
    }

    /// times: [hour, minute]; weekdays: [1...7] 周一..周日，全选则每日
    static func install(times: [[Int]], weekdays: [Int], staggerMinutes: Int) -> (ok: Bool, msg: String) {
        let base = AppPaths.projectDir
        let python = Py.detect()
        var cmd = "cd \(base) && "
        if staggerMinutes > 0 {
            cmd += "sleep $((RANDOM % \(staggerMinutes * 60))) && "
        }
        cmd += "\(python) \(base)/checkin.py >> \(base)/logs/launchd.log 2>&1"

        var intervalXML: String
        if weekdays.count == 7 {
            let items = times.map { "<dict><key>Hour</key><integer>\($0[0])</integer><key>Minute</key><integer>\($0[1])</integer></dict>" }
            intervalXML = times.count == 1 ? "<dict><key>Hour</key><integer>\(times[0][0])</integer><key>Minute</key><integer>\(times[0][1])</integer></dict>"
                                           : "<array>\(items.joined())</array>"
        } else {
            var entries: [String] = []
            for t in times {
                for wd in weekdays {
                    let w = wd == 7 ? 7 : wd
                    entries.append("<dict><key>Hour</key><integer>\(t[0])</integer><key>Minute</key><integer>\(t[1])</integer><key>Weekday</key><integer>\(w)</integer></dict>")
                }
            }
            intervalXML = "<array>\(entries.joined())</array>"
        }

        let escapedCmd = cmd.replacingOccurrences(of: "&", with: "&amp;")
        let plist = """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>\(label)</string>
            <key>ProgramArguments</key>
            <array>
                <string>/bin/bash</string>
                <string>-l</string>
                <string>-c</string>
                <string>\(escapedCmd)</string>
            </array>
            <key>StartCalendarInterval</key>
            \(intervalXML)
            <key>StandardOutPath</key>
            <string>\(base)/logs/launchd.out.log</string>
            <key>StandardErrorPath</key>
            <string>\(base)/logs/launchd.err.log</string>
        </dict>
        </plist>
        """
        do {
            let dir = (plistPath as NSString).deletingLastPathComponent
            try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try plist.write(toFile: plistPath, atomically: true, encoding: .utf8)
        } catch {
            return (false, "写入 plist 失败：\(error.localizedDescription)")
        }
        _ = runPythonCaptureByShell(["launchctl", "unload", plistPath])
        let r = runPythonCaptureByShell(["launchctl", "load", plistPath])
        if r.contains("Load failed") {
            return (false, "launchctl load 失败：\(r)")
        }
        return (true, "定时任务已安装")
    }

    static func uninstall() {
        _ = runPythonCaptureByShell(["launchctl", "unload", plistPath])
        try? FileManager.default.removeItem(atPath: plistPath)
    }

    /// 用 shell 执行命令（launchctl 需要当前用户域）
    static func runPythonCaptureByShell(_ args: [String]) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-lc", args.map { ShellQuote.quote($0) }.joined(separator: " ")]
        let out = Pipe()
        let err = Pipe()
        p.standardOutput = out
        p.standardError = err
        do { try p.run() } catch { return "" }
        p.waitUntilExit()
        let o = (try? out.fileHandleForReading.readToEnd()).map { String(data: $0, encoding: .utf8) ?? "" } ?? ""
        let e = (try? err.fileHandleForReading.readToEnd()).map { String(data: $0, encoding: .utf8) ?? "" } ?? ""
        return o + e
    }
}

enum ShellQuote {
    static func quote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}

// MARK: - CSV 导出
enum CsvExporter {
    static func exportRecords(logs: ParsedLogs, to url: URL) throws {
        var lines = ["日期,时间,平台,账号,结果,说明"]
        let dates = Set(logs.okByDay.keys).union(logs.failByDay.keys).sorted(by: >)
        for d in dates {
            let oks = logs.okByDay[d] ?? [:]
            let fails = logs.failByDay[d] ?? [:]
            let names = Set(oks.keys).union(fails.keys)
            for name in names.sorted() {
                if let h = oks[name] {
                    lines.append("\(h.date),\(h.time),trae,\(name),成功,\"\(h.msg)\"")
                } else if let h = fails[name] {
                    lines.append("\(h.date),\(h.time),trae,\(name),失败,\"\(h.msg)\"")
                }
            }
        }
        try lines.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
    }
}
