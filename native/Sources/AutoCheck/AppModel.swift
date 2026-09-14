import SwiftUI
import UserNotifications
import AppKit

struct Toast: Identifiable, Equatable {
    let id = UUID()
    let text: String
    let kind: Kind
    enum Kind { case info, success, error }
}

// MARK: - 中央状态
final class AppModel: ObservableObject {
    @Published var accounts: [Account] = []
    @Published var runLines: [String] = []
    @Published var loginRunning: Bool = false
    /// 当前正在登录的账号名（用于逐账号显示"登录中…"，nil 表示无进行中的登录）
    @Published var loginAccount: String? = nil
    @Published var loginLines: [String] = []
    @Published var signRunning: Bool = false
    /// 行内单个"签到/重试"正在进行中的账号名（nil 表示无）。与 signRunning 独立，仅驱动行内按钮，
    /// 不得联动顶部"手动签到"批量按钮。
    @Published var singleSignName: String? = nil
    @Published var creditsRunning: Bool = false
    @Published var selectedPage: Page = .checkin
    @Published var showingAddPanel: Bool = false
    @Published var toast: Toast?
    /// Toast 自动消失的定时任务（同一轮新 toast 会取消旧任务）
    private var toastDismissTask: DispatchWorkItem?
    @Published var launchd: LaunchdManager.State = .init()
    @Published var creditsByAccount: [String: CreditInfo] = [:]
    @Published var pythonAvailable: Bool = Py.available
    @Published var pythonHint: String = ""

    var parsed = ParsedLogs()
    var pref = AppPrefs.load()
    private var ticker: Timer?
    private var runningProcess: Process?
    private var retryCount = 0
    private let maxRetryRounds = 1   // 失败自动重试最多额外 1 轮，杜绝无限循环

    enum Page: String, CaseIterable, Identifiable {
        case checkin = "签到"
        case accounts = "账号管理"
        case settings = "设置"
        case help = "使用说明"
        var id: String { rawValue }
        var number: Int {
            switch self { case .checkin: return 1; case .accounts: return 2; case .settings: return 3; case .help: return 4 }
        }
    }

    struct CreditInfo: Hashable {
        var total: Double = 0
        var used: Double = 0
        var remaining: Double = 0
        var detail: String = ""
    }

    // MARK: 启动 / 刷新
    func start() {
        pythonAvailable = Py.available
        if !pythonAvailable {
            pythonHint = "未找到可用的 Python 运行时，请安装 Python 3 后重试。\n可用环境变量 CHECKIN_PYTHON 指定。"
        }
        refreshAll(loadCredits: true)
        probeWorkBuddy()   // 启动即探测本机 WorkBuddy 登录态健康（只读，不含 token）
        var logTicks = 0
        ticker = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.objectWillChange.send()
            // 定期重读日志，确保外部/后台（launchd 定时、手动脚本、重新登录后）
            // 写入的 [OK]/[FAIL] 台账也能在界面刷新，避免"已签到但统计仍为 0"
            logTicks += 1
            if logTicks % 15 == 0 { self.parseLogs() }
        }
        if pref.autoSignOnLaunch {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                self?.runSign()
            }
        }
    }

    func refreshAll(loadCredits: Bool = false) {
        loadAccounts()
        parseLogs()
        launchd = LaunchdManager.currentState()
        if loadCredits { refreshCredits() }
    }

    func loadAccounts() {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: AppPaths.accountsFile)) else {
            accounts = []
            return
        }
        if let cfg = try? JSONDecoder().decode(ConfigFile.self, from: data) {
            accounts = cfg.accounts ?? []
        }
    }

    func parseLogs() {
        parsed.load(path: AppPaths.logFile)
        objectWillChange.send()
    }

    // MARK: 派生数据
    func enabledAccounts() -> [Account] { accounts.filter { $0.isEnabled } }

    func todaySummary() -> (done: Int, fail: Int, pending: Int, credit: Double, total: Int) {
        let today = DayTool.today()
        // 以「启用账号」为口径汇总，避免把已删除/未启用账号的日志计入；
        // 通过 parsed.status 的前缀兼容匹配，确保日志名与账号名存在差异时也能正确归类（如"132" vs "132trae"）。
        let en = enabledAccounts()
        var doneCount = 0
        var failCount = 0
        var pendingCount = 0
        var credit = 0.0
        for acc in en {
            let st = parsed.status(name: acc.name, on: today)
            switch st.state {
            case "done":
                doneCount += 1
                if let h = st.hit { credit += h.credits }
            case "fail":
                failCount += 1
            default:
                pendingCount += 1
            }
        }
        return (doneCount, failCount, pendingCount, credit, en.count)
    }

    func accStatus(_ name: String) -> (state: String, hit: LogHit?) {
        parsed.status(name: name, on: DayTool.today())
    }

    func totalRemainingCredits() -> Double {
        creditsByAccount.values.reduce(0) { $0 + $1.remaining }
    }

    func chartData(days: Int) -> [Double] {
        ParsedLogs.dailyCredits(days: DayTool.lastDays(days), logs: parsed)
    }

    func overallStreak() -> Int {
        ParsedLogs.streak(for: nil, logs: parsed, startDate: DayTool.today())
    }

    func redBanners() -> [String] {
        let today = DayTool.today()
        let fails = parsed.todayFail(today)
        return fails.keys.sorted().map { name in
            let hit = fails[name]!
            return "\(name) 签到失败；\(hit.msg)"
        }
    }

    func greenText() -> String {
        let oks = parsed.todayOK(DayTool.today())
        let names = oks.keys.sorted()
        guard !names.isEmpty else { return "" }
        return "\(names.joined(separator: "、"))签到成功"
    }

    func countdown() -> String {
        guard let next = launchd.nextDate else { return "—" }
        let sec = Int(max(0, next.timeIntervalSinceNow))
        if sec > 3600 { return "\(sec / 3600) 小时 \((sec % 3600) / 60) 分" }
        return "\(max(0, sec / 60)) 分 \(sec % 60) 秒"
    }

    /// 最近一次成功/失败签到的时刻（今天优先，跨天回退到最近一天）
    func lastSyncTime() -> String? {
        let today = DayTool.today()
        var times = parsed.todayOK(today).values.map { $0.time }
        times += parsed.todayFail(today).values.map { $0.time }
        if times.isEmpty {
            let dates = Set(parsed.okByDay.keys).union(parsed.failByDay.keys).sorted(by: >)
            guard let d = dates.first else { return nil }
            var t = parsed.okByDay[d]?.values.map { $0.time } ?? []
            t += parsed.failByDay[d]?.values.map { $0.time } ?? []
            return t.sorted().last
        }
        return times.sorted().last
    }

    // MARK: 签到（按启用的账号 --only，尊重 enabled，不依赖后端改动）
    func runSign(only: String? = nil) {
        guard !signRunning, singleSignName == nil else { return }
        let names: [String]
        if let only = only {
            names = [only]
            runSingleSign(only)
            return
        }
        let en = enabledAccounts()
        if en.isEmpty {
            showToast("没有启用的账号，请先在账号管理添加/启用账号", .error)
            return
        }
        names = en.map { $0.name }
        signRunning = true
        retryCount = 0
        runLines = []
        runSignSequence(names, index: 0)
    }

    /// 行内单个账号签到/重试：与批量手动签到( signRunning)完全独立，
    /// 不驱动顶部"手动签到"批量按钮的 loading，加载态显示在行内。
    func runSingleSign(_ name: String) {
        guard singleSignName == nil, !signRunning else { return }
        singleSignName = name
        let proc = runPythonStream([AppPaths.projectDir + "/checkin.py", "--only", name]) { [weak self] line in
            guard let self = self else { return }
            self.runLines.append(line)
            if line.contains("[OK]") || line.contains("[FAIL]") {
                // 解析已含时间戳的行，用于实时刷新表格
                self.parseLogs()
            }
        } completion: { [weak self] _ in
            guard let self = self else { return }
            self.parseLogs()
            self.singleSignName = nil
            let summary = self.todaySummary()
            self.refreshCredits()
            self.showToast("\(name) 签到完成：成功 \(summary.done)，失败 \(summary.fail)",
                           summary.fail == 0 ? .success : .error)
        }
        runningProcess = proc
    }

    private func runSignSequence(_ names: [String], index: Int) {
        guard index < names.count else {
            finishSign()
            return
        }
        let name = names[index]
        let proc = runPythonStream([AppPaths.projectDir + "/checkin.py", "--only", name]) { [weak self] line in
            guard let self = self else { return }
            self.runLines.append(line)
            if line.contains("[OK]") || line.contains("[FAIL]") {
                // 解析已含时间戳的行，用于实时刷新表格
                self.parseLogs()
            }
        } completion: { [weak self] _ in
            self?.parseLogs()
            self?.runSignSequence(names, index: index + 1)
        }
        runningProcess = proc
    }

    private func finishSign() {
        parseLogs()
        let summary = todaySummary()
        // 失败自动重试：仅当启用重试、当天仍有失败账号、且未超过重试轮次上限时执行，防止无限循环
        if pref.retryOnFail && summary.fail > 0 && retryCount < maxRetryRounds {
            retryCount += 1
            let failNames = Array(parsed.todayFail(DayTool.today()).keys)
            let proposals = Set(failNames)
            if !proposals.isEmpty {
                // 重试期间保持 signRunning = true，UI 持续显示"签到中"，避免"界面空闲后台却在循环"
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                    self?.runSignSequence(Array(proposals), index: 0)
                }
                return
            }
        }
        signRunning = false
        afterSignDone()
    }

    private func afterSignDone() {
        signRunning = false
        let summary = todaySummary()
        refreshCredits()
        if pref.notifyOnComplete {
            sendNotification(title: "自动签到完成",
                             body: "成功 \(summary.done)，失败 \(summary.fail)，共 \(summary.total) 个账号")
        }
        showToast("签到完成：成功 \(summary.done)，失败 \(summary.fail)", summary.fail == 0 ? .success : .error)
    }

    // MARK: 积分
    func refreshCredits(only: String? = nil) {
        guard !creditsRunning else { return }
        creditsRunning = true
        var args = [AppPaths.projectDir + "/checkin.py", "--credits"]
        if let only = only { args += ["--only", only] }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let r = runPythonCapture(args, timeout: 90)
            DispatchQueue.main.async {
                self?.creditsRunning = false
                self?.parseCreditsOutput(r.out)
            }
        }
    }

    private func parseCreditsOutput(_ text: String) {
        var newCredits: [String: CreditInfo] = [:]
        for line in text.split(separator: "\n") {
            let s = String(line)
            if let m = matchCredits(s) {
                newCredits[m.name] = CreditInfo(total: m.total, used: m.used, remaining: m.remaining, detail: m.detail)
            }
        }
        if !newCredits.isEmpty {
            creditsByAccount = newCredits
        }
    }

    private func matchCredits(_ s: String) -> (name: String, total: Double, used: Double, remaining: Double, detail: String)? {
        guard let mark = s.range(of: "[积分] ") else { return nil }
        let tail = String(s[mark.upperBound...])
        guard let openP = tail.firstIndex(of: "("), let closeP = tail.firstIndex(of: ")"), openP < closeP else { return nil }
        let name = String(tail[..<openP]).trimmingCharacters(in: .whitespaces)
        guard let total = doubleAfter(tail, "总限额"),
              let used = doubleAfter(tail, "已用"),
              let remaining = doubleAfter(tail, "剩余") else { return nil }
        let detail = String(tail[tail.index(after: closeP)...]).trimmingCharacters(in: .whitespaces)
        return (name, total, used, remaining, detail)
    }

    private func doubleAfter(_ s: String, _ key: String) -> Double? {
        guard let r = s.range(of: key) else { return nil }
        // key 后可能先跟空格/全角逗号等分隔符再出现数字（如"总限额 4650，已用 2142.69"），
        // 必须先跳过分隔符再取数字，否则 prefix 立即因首字符不是数字而返回空。
        let rest = String(s[r.upperBound...])
        let trimmed = rest.drop(while: { $0.isWhitespace || $0 == "，" || $0 == "," })
        let digits = trimmed.prefix { $0.isNumber || $0 == "." }
        guard !digits.isEmpty else { return nil }
        return Double(String(digits))
    }

    // MARK: 账号操作
    func toggleEnabled(_ account: Account, on: Bool) {
        guard var root = readJSONRoot() else { return }
        guard var arr = root["accounts"] as? [[String: Any]] else { return }
        for i in arr.indices where arr[i]["name"] as? String == account.name {
            arr[i]["enabled"] = on
        }
        root["accounts"] = arr
        writeJSONRoot(root)
        loadAccounts()
    }

    func deleteAccount(_ account: Account) {
        guard var root = readJSONRoot() else { return }
        guard var arr = root["accounts"] as? [[String: Any]] else { return }
        arr.removeAll { ($0["name"] as? String) == account.name }
        root["accounts"] = arr
        writeJSONRoot(root)
        loadAccounts()
        showToast("已删除账号 \(account.name)", .info)
    }

    func renameAccount(_ account: Account, to newName: String) {
        let trimmed = newName.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, trimmed != account.name else { return }
        guard var root = readJSONRoot() else { return }
        guard var arr = root["accounts"] as? [[String: Any]] else { return }
        guard !arr.contains(where: { ($0["name"] as? String) == trimmed }) else {
            showToast("账号名 \(trimmed) 已存在", .error)
            return
        }
        for i in arr.indices where arr[i]["name"] as? String == account.name {
            arr[i]["name"] = trimmed
        }
        root["accounts"] = arr
        writeJSONRoot(root)
        loadAccounts()
    }

    func addAccountCurl(name: String, app: String, curl: String, enabled: Bool = true,
                        onDone: ((Bool) -> Void)? = nil) {
        let nameT = name.trimmingCharacters(in: .whitespaces)
        let appT = app.trimmingCharacters(in: .whitespaces).lowercased()
        let curlT = curl.trimmingCharacters(in: .whitespaces)
        guard !nameT.isEmpty, !appT.isEmpty, !curlT.isEmpty else {
            showToast("账号名/所属软件/cURL 不能为空", .error)
            onDone?(false)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let args = [AppPaths.projectDir + "/curl_to_account.py",
                        "--name", nameT, "--app", appT, "--curl", curlT,
                        "--replace", "--enabled", enabled ? "1" : "0"]
            let r = runPythonCapture(args, timeout: 30)
            DispatchQueue.main.async {
                guard let self = self else { return }
                let ok = (r.code == 0) || r.out.contains("已保存")
                self.showToast(ok ? "已保存账号「\(nameT)」" : "保存失败：\(r.out)", ok ? .success : .error)
                if ok {
                    self.loadAccounts()
                    self.showingAddPanel = false   // 仅保存成功才关闭面板，失败保留输入
                }
                onDone?(ok)
            }
        }
    }

    func runTraeLogin(name: String, enabled: Bool = true) {
        let nameT = name.trimmingCharacters(in: .whitespaces)
        if let cur = loginAccount, cur != nameT {
            showToast("请先等待账号「\(cur)」登录完成", .error)
            return
        }
        if nameT.isEmpty { showToast("请输入账号名称", .error); return }
        loginRunning = true
        loginAccount = nameT
        loginLines = ["正在打开内置浏览器（账号 \(nameT)）…", "请在浏览器窗口中完成 Trae 登录，程序会自动识别并保存。"]
        runningProcess = runPythonStream([AppPaths.projectDir + "/trae_login.py", "--name", nameT,
                                          "--enabled", enabled ? "1" : "0"]) { [weak self] line in
            self?.loginLines.append(line)
        } completion: { [weak self] code in
            self?.loginRunning = false
            self?.loginAccount = nil
            if code == 0 {
                self?.loadAccounts()
                self?.showToast("登录完成，账号已保存", .success)
                // 设置页「登录时同步历史积分」：登录成功后顺带拉一次积分
                if self?.pref.syncHistoryCreditsOnLogin == true { self?.refreshCredits() }
            } else {
                self?.showToast("登录未完成或已取消", .error)
            }
            self?.probeWorkBuddy()   // 登录完成后续探本机 WorkBuddy 登录态状态
        }
    }

    // MARK: WorkBuddy
    /// 本机 WorkBuddy 登录态健康探针。nil=未探测；true=文件存在且有 accessToken。
    @Published var wbHealthy: Bool? = nil
    /// 本机 WorkBuddy 昵称（仅用于展示，脱敏无敏感信息）
    @Published var wbNickname: String = ""

    /// 异步探测本机 WorkBuddy 登录态（只读，不含 token）。
    func probeWorkBuddy() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let r = runPythonCapture([AppPaths.projectDir + "/workbuddy.py", "--probe"], timeout: 20)
            var healthy: Bool? = nil
            var nickname = ""
            if let data = r.out.data(using: .utf8),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                healthy = (obj["found"] as? Bool == true) && (obj["has_token"] as? Bool == true)
                nickname = obj["nickname"] as? String ?? ""
            }
            DispatchQueue.main.async {
                self?.wbHealthy = healthy
                self?.wbNickname = nickname
            }
        }
    }

    /// 探测并保存 WorkBuddy 账号（本机登录态一键添加）。
    /// name 为空时默认用本机昵称或 "workbuddy"；同名则更新快照（保留原启用状态）。
    func addWorkBuddyAccount(name: String, enabled: Bool = true,
                             onDone: ((Bool) -> Void)? = nil) {
        loginRunning = true
        loginLines = ["正在读取本机 WorkBuddy 登录态…（accessToken 仅内存使用，不写入配置文件）"]
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let probe = runPythonCapture([AppPaths.projectDir + "/workbuddy.py", "--probe"], timeout: 20)
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.loginRunning = false
                self.loginLines = []
                guard let data = probe.out.data(using: .utf8),
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    self.showToast("读取 WorkBuddy 登录态失败", .error)
                    onDone?(false)
                    return
                }
                let found = obj["found"] as? Bool == true
                let hasToken = obj["has_token"] as? Bool == true
                let reason = obj["reason"] as? String ?? ""
                guard found, hasToken else {
                    self.showToast("未能添加：\(reason)", .error)
                    onDone?(false)
                    return
                }
                let nickname = obj["nickname"] as? String ?? ""
                var finalName = name.trimmingCharacters(in: .whitespaces)
                if finalName.isEmpty {
                    finalName = nickname.isEmpty ? "workbuddy" : "workbuddy-\(nickname)"
                }
                var snapshot: [String: Any] = [
                    "uid": obj["uid"] as? String ?? "",
                    "nickname": nickname,
                    "uin": obj["uin"] as? String ?? "",
                    "domain": obj["domain"] as? String ?? "",
                    "enterprise_id": obj["enterprise_id"] as? String ?? "",
                    "saved_at": self.nowString(),
                ]
                snapshot = snapshot.filter { !(($0.value as? String) ?? "").isEmpty }
                guard var root = self.readJSONRoot(),
                      var arr = root["accounts"] as? [[String: Any]] else {
                    self.showToast("读取账号配置失败", .error)
                    onDone?(false)
                    return
                }
                var entry: [String: Any] = [
                    "name": finalName,
                    "app": "workbuddy",
                    "type": "workbuddy",
                    "workbuddy_auth": snapshot,
                ]
                if let i = arr.firstIndex(where: { ($0["name"] as? String) == finalName }) {
                    var old = arr[i]
                    entry["enabled"] = old["enabled"] ?? enabled
                    arr[i] = entry
                } else {
                    var ne = entry
                    ne["enabled"] = enabled
                    arr.append(ne)
                }
                root["accounts"] = arr
                if self.writeJSONRoot(root) {
                    self.loadAccounts()
                    self.wbHealthy = true
                    self.showToast("已添加 WorkBuddy 账号「\(finalName)」", .success)
                    self.showingAddPanel = false
                    onDone?(true)
                } else {
                    self.showToast("保存账号失败", .error)
                    onDone?(false)
                }
            }
        }
    }

    /// WorkBuddy 账号"重新登录"：重新读取本机 WorkBuddy 登录态并刷新快照。
    func runWorkBuddyRefresh(name: String) {
        let nameT = name.trimmingCharacters(in: .whitespaces)
        if nameT.isEmpty { return }
        loginRunning = true
        loginLines = ["正在重新读取本机 WorkBuddy 登录态…（accessToken 仅内存使用，不写入配置文件）"]
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let probe = runPythonCapture([AppPaths.projectDir + "/workbuddy.py", "--probe"], timeout: 20)
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.loginRunning = false
                self.loginLines = []
                guard let data = probe.out.data(using: .utf8),
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      obj["found"] as? Bool == true,
                      obj["has_token"] as? Bool == true else {
                    let reason = (try? JSONSerialization.jsonObject(with: probe.out.data(using: .utf8) ?? Data())
                                  as? [String: Any])??["reason"] as? String ?? "未检测到 WorkBuddy 登录态"
                    self.showToast("刷新失败：\(reason)", .error)
                    return
                }
                var snapshot: [String: Any] = [
                    "uid": obj["uid"] as? String ?? "",
                    "nickname": obj["nickname"] as? String ?? "",
                    "uin": obj["uin"] as? String ?? "",
                    "domain": obj["domain"] as? String ?? "",
                    "enterprise_id": obj["enterprise_id"] as? String ?? "",
                    "saved_at": self.nowString(),
                ]
                snapshot = snapshot.filter { !(($0.value as? String) ?? "").isEmpty }
                guard var root = self.readJSONRoot(),
                      var arr = root["accounts"] as? [[String: Any]] else {
                    self.showToast("读取账号配置失败", .error)
                    return
                }
                for i in arr.indices where arr[i]["name"] as? String == nameT {
                    arr[i]["type"] = "workbuddy"
                    arr[i]["app"] = "workbuddy"
                    arr[i]["workbuddy_auth"] = snapshot
                }
                root["accounts"] = arr
                if self.writeJSONRoot(root) {
                    self.loadAccounts()
                    self.wbHealthy = true
                    self.showToast("已刷新 WorkBuddy 登录态", .success)
                } else {
                    self.showToast("保存账号失败", .error)
                }
            }
        }
    }

    private func nowString() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f.string(from: Date())
    }

    // MARK: launchd
    func autoToggle(_ on: Bool) {
        if on {
            let r = LaunchdManager.install(times: pref.times.isEmpty ? [[21, 30]] : pref.times,
                                           weekdays: pref.weekdays, staggerMinutes: pref.staggerMinutes)
            showToast(r.msg, r.ok ? .success : .error)
        } else {
            LaunchdManager.uninstall()
            showToast("已关闭自动签到", .info)
        }
        refreshAll()
    }

    func applyLaunchdSettings() {
        // 仅在用户已开启自动签到（launchd 任务已安装）时更新定时任务；
        // 自动签到未开启时，保存设置不应擅自安装任务（避免静默重开自动签到）
        if launchd.installed {
            let r = LaunchdManager.install(times: pref.times.isEmpty ? [[21, 30]] : pref.times,
                                           weekdays: pref.weekdays, staggerMinutes: pref.staggerMinutes)
            showToast(r.msg, r.ok ? .success : .error)
        } else {
            showToast("设置已保存", .info)
        }
        pref.save()
        refreshAll()
    }

    // MARK: 通知
    func sendNotification(title: String, body: String) {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            let req = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
            center.add(req)
        }
    }

    // MARK: 每日汇总提醒（设置页「每日汇总提醒」开关）
    private static let digestID = "com.marvis.autocheck.daily-digest"

    /// 在当天最后一个签到时间点之后半小时提醒，内容为当前仍未签到的账号。
    func scheduleDailyDigest() {
        let center = UNUserNotificationCenter.current()
        let last = (pref.times.isEmpty ? [[21, 30]] : pref.times).max { ($0[0], $0[1]) < ($1[0], $1[1]) } ?? [21, 30]
        var comp = DateComponents()
        comp.hour = (last[0] + 1) % 24
        comp.minute = last[1]
        let content = UNMutableNotificationContent()
        content.title = "今日签到汇总"
        content.body = dailyDigestBody()
        content.sound = .default
        let trigger = UNCalendarNotificationTrigger(dateMatching: comp, repeats: true)
        center.requestAuthorization(options: [.alert, .sound]) { [weak self] granted, _ in
            guard granted else { return }
            center.removePendingNotificationRequests(withIdentifiers: [Self.digestID])
            center.add(UNNotificationRequest(identifier: Self.digestID, content: content, trigger: trigger))
            DispatchQueue.main.async { self?.showToast("每日汇总提醒已开启", .success) }
        }
    }

    func cancelDailyDigest() {
        UNUserNotificationCenter.current()
            .removePendingNotificationRequests(withIdentifiers: [Self.digestID])
    }

    private func dailyDigestBody() -> String {
        let pending = accounts.filter { $0.isEnabled && accStatus($0.name).state != "done" }
        if pending.isEmpty { return "今日所有启用账号均已签到完成" }
        let names = pending.prefix(4).map { $0.name }.joined(separator: "、")
        let tail = pending.count > 4 ? " 等" : ""
        return "还有 \(pending.count) 个账号未签到：\(names)\(tail)"
    }

    // MARK: 配置文件读写
    private func readJSONRoot() -> [String: Any]? {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: AppPaths.accountsFile)),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return obj
    }
    private func writeJSONRoot(_ root: [String: Any]) -> Bool {
        guard let data = try? JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys]) else { return false }
        do {
            try data.write(to: URL(fileURLWithPath: AppPaths.accountsFile), options: .atomic)
            return true
        } catch { return false }
    }

    // MARK: 导出 / 诊断
    func copyDiagnostics() {
        let sum = todaySummary()
        var text = "喵签签 原生版诊断信息\n"
        text += "时间：\(Date())\n"
        text += "Python：\(Py.detect())\n"
        text += "项目目录：\(AppPaths.projectDir)\n"
        text += "账号数：\(accounts.count)（启用 \(enabledAccounts().count)）\n"
        text += "今日：成功 \(sum.done) / 失败 \(sum.fail) / 待签到 \(sum.pending)\n"
        text += "定时任务：\(launchd.installed ? "已安装 \(launchd.hour):\(String(format: "%02d", launchd.minute)) 下次 \(launchd.deltaText)" : "未开启")\n"
        text += "--- 最近日志 ---\n"
        text += parsed.rawLines.suffix(30).joined(separator: "\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        showToast("诊断信息已复制到剪贴板", .success)
    }

    func exportCSV(to url: URL) {
        do {
            try CsvExporter.exportRecords(logs: parsed, to: url)
            showToast("签到记录已导出：\(url.lastPathComponent)", .success)
        } catch {
            showToast("导出失败：\(error.localizedDescription)", .error)
        }
    }

    func exportConfig(to url: URL) {
        let src = URL(fileURLWithPath: AppPaths.accountsFile)
        do {
            try FileManager.default.copyItem(at: src, to: url)
            showToast("配置已导出", .success)
        } catch {
            showToast("导出失败：\(error.localizedDescription)", .error)
        }
    }

    func importAccounts(path: String) {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else { return }
        // JSON 或 cURL 文本
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            guard var root = readJSONRoot() else { return }
            guard var arr = root["accounts"] as? [[String: Any]] else { return }
            if let imports = obj["accounts"] as? [[String: Any]] {
                var added = 0
                for acc in imports {
                    guard let name = acc["name"] as? String else { continue }
                    if !arr.contains(where: { ($0["name"] as? String) == name }) {
                        arr.append(acc)
                        added += 1
                    }
                }
                root["accounts"] = arr
                _ = writeJSONRoot(root)
                loadAccounts()
                showToast("批量导入完成：新增 \(added) 个账号", .info)
            }
        } else if let text = String(data: data, encoding: .utf8) {
            // 每行一个 cURL
            let lines = text.split(separator: "\n").map(String.init).filter { $0.contains("curl") || $0.contains("http") }
            var added = 0
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                for (i, line) in lines.enumerated() {
                    let name = "curl-\(added + i + 1)"
                    let args = [AppPaths.projectDir + "/curl_to_account.py", "--name", name, "--app", "curl", "--curl", line, "--replace"]
                    let r = runPythonCapture(args, timeout: 30)
                    if r.code == 0 || r.out.contains("已保存") { added += 1 }
                }
                DispatchQueue.main.async {
                    self?.loadAccounts()
                    self?.showToast("批量导入完成：新增 \(added) 个账号", .info)
                }
            }
        }
    }

    func showToast(_ text: String, _ kind: Toast.Kind = .info) {
        toastDismissTask?.cancel()
        let t = Toast(text: text, kind: kind)
        toast = t
        // 3.5s 后自动消失，仅当仍是同一条 toast 时清除，避免新 toast 被旧定时器误清
        let work = DispatchWorkItem { [weak self] in
            if self?.toast?.id == t.id { self?.toast = nil }
        }
        toastDismissTask = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.5, execute: work)
    }
}
