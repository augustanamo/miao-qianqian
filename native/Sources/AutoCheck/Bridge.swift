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

    /// 需要**内置浏览器**（playwright）的脚本专用解释器。
    ///
    /// 为什么不能直接用 `detect()`：playwright 原先装在 Marvis runtime 里
    /// （`Versions/<版本号>/runtime/python311/`），而那是个**带版本号**的目录 ——
    /// 2026-09-17 Marvis 从 1.0.0.10316 升到 10339，旧目录被删，playwright 随之消失。
    /// 于是所有"重新登录 / 新增账号 / Trae 登录"在同一时刻全部失效；而 `detect()`
    /// 恰好会选中那个新版本目录（它只检查解释器可不可执行）。所以这里优先用项目自带的
    /// `.venv`（由 setup_browser.sh 建，不随宿主升级消失）。
    static func detectBrowser() -> String {
        let venv = AppPaths.projectDir + "/.venv/bin/python3"
        if FileManager.default.isExecutableFile(atPath: venv) { return venv }
        return detect()   // 没建 venv 时照旧；至少脚本的报错会指向 setup_browser.sh
    }

    /// 内置浏览器组件是否就绪（纯文件检查，不起子进程）。
    /// 要能分辨"venv 建了但 playwright 没装成"—— 只看 venv 目录存在会误报就绪。
    static let browserReady: Bool = {
        let venv = AppPaths.projectDir + "/.venv/bin/python3"
        let fm = FileManager.default
        guard fm.isExecutableFile(atPath: venv) else { return false }
        let lib = AppPaths.projectDir + "/.venv/lib"
        guard let entries = try? fm.contentsOfDirectory(atPath: lib) else { return false }
        for e in entries where e.hasPrefix("python") {
            if fm.fileExists(atPath: lib + "/" + e + "/site-packages/playwright/__init__.py") {
                return true
            }
        }
        return false
    }()

    static let available: Bool = {
        let p = detect()
        let fm = FileManager.default
        return fm.isExecutableFile(atPath: p)
    }()
}

// MARK: - Checkin / 登录 流式输出（stdout 逐行回调）
@discardableResult
func runPythonStream(_ args: [String],
                     python: String? = nil,
                     onLine: ((String) -> Void)? = nil,
                     completion: ((Int) -> Void)? = nil) -> Process? {
    let p = Process()
    // 需要 playwright 的脚本（browser_login / trae_login / workbuddy_login）必须传
    // `python: Py.detectBrowser()` —— 理由见那个函数：Marvis runtime 升级会删掉旧
    // 版本目录，连带把装在里面 playwright 一起带走。
    p.executableURL = URL(fileURLWithPath: python ?? Py.detect())
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
    let deadline = CFAbsoluteTimeGetCurrent() + timeout
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
        /// 服务此刻是否真的被 launchd 加载。plist 在、服务没加载 = 定时不会触发，
        /// 这种状态界面必须能看出来，否则"设了定时却永远不跑"完全无声。
        var loaded: Bool = false
        var hour: Int = 21
        var minute: Int = 30
        var nextDate: Date?
        var deltaText: String = ""
    }

    static func currentState() -> State {
        var st = State()
        var entries: [(hour: Int, minute: Int, weekday: Int?)] = []
        if let data = try? Data(contentsOf: URL(fileURLWithPath: plistPath)),
           let dict = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any] {
            // StartCalendarInterval 的形态有两种：单个时间点是 dict，
            // 多时间点或指定了星期时是 array
            if let one = dict["StartCalendarInterval"] as? [String: Any] {
                entries = [(one["Hour"] as? Int ?? 21, one["Minute"] as? Int ?? 30, one["Weekday"] as? Int)]
            } else if let arr = dict["StartCalendarInterval"] as? [[String: Any]] {
                entries = arr.map { ($0["Hour"] as? Int ?? 21, $0["Minute"] as? Int ?? 30, $0["Weekday"] as? Int) }
            }
        }
        if let first = entries.first {
            st.hour = first.hour
            st.minute = first.minute
        }
        let list = runPythonCaptureByShell(["launchctl", "list"])
        let inLaunchd = list.contains(label)
        // 「已安装」= 服务在 launchd 里**或** plist 文件还在。
        // 只看 launchctl 会漏掉一种真实情况：plist 明明在、服务却没被加载
        // （被手动 unload，或系统/环境变动后掉线）。那时界面显示"未开启"，
        // 用户以为自己没设过定时，而 App 也不会去重装 —— 签到就这么无声地停了。
        // 把「文件在」也算已安装，syncLaunchdIfNeeded 才有机会把它救回来。
        st.loaded = inLaunchd
        st.installed = inLaunchd || FileManager.default.fileExists(atPath: plistPath)
        // 下一次触发时刻：逐条目算"此后第一次命中"，取最近的一个。
        // 旧实现只拿第一个时间点 + 只看时分（忽略星期），多时间点或非每天执行时
        // 会算出一个根本不会触发的时刻，倒计时就一直是错的。
        if st.installed, !entries.isEmpty, let next = nextFireDate(entries) {
            st.nextDate = next
            let sec = Int(next.timeIntervalSinceNow)
            st.deltaText = "\(sec / 3600) 小时 \((sec % 3600) / 60) 分"
        }
        return st
    }

    /// launchd 的 Weekday：0 和 7 都是周日、1 是周一；
    /// Calendar 的 weekday：1 是周日。换算 = wd % 7 + 1（1→2 周一 … 6→7 周六、7→1 周日）。
    private static func nextFireDate(_ entries: [(hour: Int, minute: Int, weekday: Int?)]) -> Date? {
        let cal = Calendar.current
        let now = Date()
        var best: Date?
        for e in entries {
            for offset in 0...7 {
                guard let day = cal.date(byAdding: .day, value: offset, to: now) else { continue }
                var comp = cal.dateComponents([.year, .month, .day], from: day)
                comp.hour = e.hour
                comp.minute = e.minute
                comp.second = 0
                guard let cand = cal.date(from: comp), cand > now else { continue }
                if let wd = e.weekday, wd != 0,
                   cal.component(.weekday, from: cand) != wd % 7 + 1 { continue }
                if best == nil || cand < best! { best = cand }
                break   // 该条目最近的一次已经找到
            }
        }
        return best
    }

    /// times: [hour, minute]; weekdays: [1...7] 周一..周日，全选则每日
    static func install(times: [[Int]], weekdays: [Int], staggerMinutes: Int) -> (ok: Bool, msg: String) {
        // 兜底：空时间点 / 空执行日都会写出一份永不触发的 plist（launchd 甚至可能
        // 直接 load 失败）。设置页已经拦了这两种输入，这里再兜一层，保证 plist 合法。
        let times = times.isEmpty ? [[21, 30]] : times
        let weekdays = weekdays.isEmpty ? [1, 2, 3, 4, 5, 6, 7] : weekdays
        let base = AppPaths.projectDir
        // 交给固定启动器执行，**不在 plist 里拼 shell 命令**。
        // 之前是 "/bin/bash -l -c \"<python> <base>/checkin.py >> log\""，两个坑：
        //   1) 解释器路径是写死的。Marvis 升级后版本目录被删，launchd 从此静默失败
        //      （退出码 127，日志只有一行 bash 报错，界面上"定时已开启"却什么都不跑）；
        //   2) 该路径含空格（"Application Support"），拼进命令串必须逐段加引号，
        //      一旦漏了就会去执行 /Users/xxx/Library/Application。
        // 现在 plist 只负责"什么时候跑"，"拿哪个解释器、怎么错峰"由启动器每次自己决定。
        let runner = base + "/run_checkin.sh"
        guard FileManager.default.fileExists(atPath: runner) else {
            return (false, "缺少启动器 \(runner)，无法安装定时任务")
        }

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

        // 错峰上限通过环境变量传给启动器（比塞进命令行更干净，也不用担心引号）
        var envXML = ""
        if staggerMinutes > 0 {
            envXML = """
            <key>EnvironmentVariables</key>
            <dict>
                <key>CHECKIN_STAGGER</key>
                <string>\(staggerMinutes)</string>
            </dict>

            """
        }

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
                <string>\(runner)</string>
            </array>
            <key>StartCalendarInterval</key>
            \(intervalXML)
            \(envXML)<key>StandardOutPath</key>
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
