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

    /// 单个账号的积分/余额快照。
    /// 来源：checkin.py --credits --json 输出里 items 的每一条。
    /// 各平台口径不同（Trae 积分 / B站硬币 / 联想乐豆 / 京豆），unit 标明实际单位。
    struct CreditInfo: Hashable {
        /// 账号当前可用余额；nil 表示该平台没有给出余额（如京东无公开接口）
        var balance: Double? = nil
        var used: Double? = nil
        var limit: Double? = nil
        var streak: Int? = nil
        /// 平台侧单位：积分 / 硬币 / 乐豆 / 京豆
        var unit: String = "积分"
        /// 平台展示名（Trae / WorkBuddy / Bilibili …）
        var label: String = ""
        /// 该账号余额是否查询成功
        var ok: Bool = false
        /// 平台侧状态文案（今日已签 / 今日未签 / 登录态正常 …）
        var state: String = ""
        var summary: String = ""
        /// 失败原因或"该平台未提供余额"的说明
        var message: String = ""
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

    func todaySummary() -> (done: Int, fail: Int, pending: Int, credit: Double, total: Int, restricted: Int) {
        let today = DayTool.today()
        // 以「启用账号」为口径汇总，避免把已删除/未启用账号的日志计入；
        // 通过 parsed.status 的前缀兼容匹配，确保日志名与账号名存在差异时也能正确归类（如"132" vs "132trae"）。
        // restricted 单列：平台受限不代表失败，不该计进 fail，也不该触发失败重试。
        let en = enabledAccounts()
        var doneCount = 0
        var failCount = 0
        var restrictedCount = 0
        var pendingCount = 0
        var credit = 0.0
        for acc in en {
            let st = parsed.status(name: acc.name, on: today)
            switch st.state {
            case "done":
                doneCount += 1
                if let h = st.hit { credit += h.credits }
            case "restricted":
                restrictedCount += 1
            case "fail":
                failCount += 1
            default:
                pendingCount += 1
            }
        }
        return (doneCount, failCount, pendingCount, credit, en.count, restrictedCount)
    }

    func accStatus(_ name: String) -> (state: String, hit: LogHit?) {
        parsed.status(name: name, on: DayTool.today())
    }

    /// 所有**启用**账号的积分/余额合计。
    /// 注意各平台单位不同（积分/硬币/乐豆），这里是"点数总和"，
    /// 界面上会给出逐账号明细标明单位，便于核对。
    func totalCredits() -> Double {
        enabledAccounts().reduce(0) { sum, acc in
            sum + (creditsByAccount[acc.name]?.balance ?? 0)
        }
    }

    /// 已查询到的账号数（余额非空），用于说明合计覆盖了几个账号
    func creditsCoverage() -> (counted: Int, total: Int) {
        let en = enabledAccounts()
        let counted = en.filter { creditsByAccount[$0.name]?.balance != nil }.count
        return (counted, en.count)
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
            if line.contains("[OK]") || line.contains("[FAIL]") || line.contains("[受限]") {
                // 解析已含时间戳的行，用于实时刷新表格
                self.parseLogs()
            }
        } completion: { [weak self] _ in
            guard let self = self else { return }
            self.parseLogs()
            self.singleSignName = nil
            let summary = self.todaySummary()
            self.refreshCredits()
            self.showToast("\(name) 签到完成：\(self.summaryText(summary))",
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
            if line.contains("[OK]") || line.contains("[FAIL]") || line.contains("[受限]") {
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
                             body: "\(summaryText(summary))，共 \(summary.total) 个账号")
        }
        showToast("签到完成：\(summaryText(summary))", summary.fail == 0 ? .success : .error)
    }

    /// 结果文案：「成功 X，平台受限 Y（有才显示），失败 Z」。
    /// 平台受限单列，避免把"京东活动下线"这种平台侧问题报成账号失败。
    private func summaryText(_ s: (done: Int, fail: Int, pending: Int,
                                   credit: Double, total: Int, restricted: Int)) -> String {
        var parts = ["成功 \(s.done)"]
        if s.restricted > 0 { parts.append("平台受限 \(s.restricted)") }
        parts.append("失败 \(s.fail)")
        return parts.joined(separator: "，")
    }

    // MARK: 积分
    /// 刷新各账号积分/余额。走 `checkin.py --credits --json` 拿机读记录，
    /// 避免在 Swift 侧用正则去啃中文文案（各平台文案格式不一致，很容易漏)。
    func refreshCredits(only: String? = nil) {
        guard !creditsRunning else { return }
        creditsRunning = true
        var args = [AppPaths.projectDir + "/checkin.py", "--credits", "--json"]
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
        // 只认 "[CREDITS_JSON] {...}" 这一行：它由脚本用 print 输出（无时间戳、
        // 不写 checkin.log），因此不会被日志解析当成签到记录。
        let marker = "[CREDITS_JSON] "
        guard let line = text.split(separator: "\n").last(where: { $0.hasPrefix(marker) }),
              let data = String(line.dropFirst(marker.count)).data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let items = obj["items"] as? [[String: Any]] else { return }
        var newCredits: [String: CreditInfo] = [:]
        for it in items {
            guard let name = it["name"] as? String else { continue }
            var c = CreditInfo()
            c.balance = num(it["balance"])
            c.used = num(it["used"])
            c.limit = num(it["limit"])
            c.streak = num(it["streak_days"]).map { Int($0) }
            c.unit = it["unit"] as? String ?? "积分"
            c.label = it["label"] as? String ?? ""
            c.ok = it["ok"] as? Bool ?? false
            c.state = it["state"] as? String ?? ""
            c.summary = it["summary"] as? String ?? ""
            c.message = it["message"] as? String ?? ""
            newCredits[name] = c
        }
        creditsByAccount = newCredits
    }

    /// JSON 数值转换。JSON null → nil；JSON 的 true/false 也会桥接成 NSNumber，
    /// 需显式排除，避免把布尔当成余额。
    private func num(_ v: Any?) -> Double? {
        guard let v = v, !(v is NSNull) else { return nil }
        if v is Bool { return nil }
        return (v as? NSNumber)?.doubleValue
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

    /// Trae 浏览器登录。name 可留空：脚本按账号 ID 自动命名
    /// （Trae 登录态只有账号 ID，不含昵称）。
    func runTraeLogin(name: String, enabled: Bool = true) {
        let nameT = name.trimmingCharacters(in: .whitespaces)
        if let cur = loginAccount, cur != nameT {
            showToast("请先等待账号「\(cur)」登录完成", .error)
            return
        }
        loginRunning = true
        loginAccount = nameT.isEmpty ? "(自动命名)" : nameT
        loginLines = ["正在打开内置浏览器（\(nameT.isEmpty ? "账号名称将自动读取" : "账号 " + nameT)）…",
                      "请在浏览器窗口中完成 Trae 登录，程序会自动识别并保存。"]
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

    /// Cookie 型平台浏览器登录（Bilibili / 联想智选 / 京东）：
    /// 弹出内置浏览器，用户扫码/账密登录后自动抓取 Cookie 写回 accounts.json，
    /// 完成后后台校验并回填昵称。Cookie 等同密码，仅本机存储，UI/日志不回显明文。
    /// name 可留空：脚本会在验证通过后读取该账号真实昵称自动命名。
    func runCookieBrowserLogin(type: String, name: String, enabled: Bool = true) {
        let nameT = name.trimmingCharacters(in: .whitespaces)
        guard let meta = Self.cookiePlatforms.first(where: { $0.type == type }) else {
            showToast("未知平台类型", .error); return
        }
        if let cur = loginAccount, cur != nameT {
            showToast("请先等待账号「\(cur)」登录完成", .error)
            return
        }
        loginRunning = true
        loginAccount = nameT.isEmpty ? "(自动命名)" : nameT
        loginLines = ["正在打开内置浏览器（\(meta.label)\(nameT.isEmpty ? "／账号名称将自动读取" : " / " + nameT)）…",
                      "请在浏览器窗口中扫码或账密登录，程序会自动抓取 Cookie 并保存（等同密码，仅本机存储）。"]
        runningProcess = runPythonStream([AppPaths.projectDir + "/browser_login.py", "--platform", type,
                                          "--name", nameT, "--enabled", enabled ? "1" : "0"]) { [weak self] line in
            self?.loginLines.append(line)
        } completion: { [weak self] code in
            self?.loginRunning = false
            self?.loginAccount = nil
            guard let self = self else { return }
            if code == 0 {
                self.loadAccounts()
                self.showToast("登录完成，账号已保存", .success)
                // 名称留空时脚本已按昵称自动命名，无需（也无法）再按名回填昵称
                if !nameT.isEmpty {
                    self.probeCookieNickname(type: type, name: nameT)   // 后台校验并回填昵称
                }
            } else {
                self.showToast("登录未完成或已取消", .error)
            }
        }
    }

    // MARK: WorkBuddy
    /// WorkBuddy 扫码登录（OAuth device flow）添加账号：
    /// 启动 workbuddy_login.py，用内置浏览器（与 Trae / Cookie 平台同一套）打开
    /// 授权页完成扫码，每次都是全新的干净 profile；Playwright 不可用时脚本自动
    /// 回退系统浏览器。登录成功后 accessToken/refreshToken 由脚本写回
    /// accounts.json（.gitignore 排除），UI/日志只显示授权 URL 与脱敏结果，
    /// 绝不回显 token 明文。name 留空时脚本自动以昵称命名。
    func runWorkBuddyOAuth(name: String, enabled: Bool = true) {
        let nameT = name.trimmingCharacters(in: .whitespaces)
        if let cur = loginAccount, cur != nameT {
            showToast("请先等待账号「\(cur)」登录完成", .error)
            return
        }
        loginRunning = true
        loginAccount = nameT.isEmpty ? "(自动命名)" : nameT
        loginLines = ["正在发起 WorkBuddy OAuth 扫码登录…",
                      "内置浏览器将打开授权页，请扫码并在页面中确认；登录成功会自动保存账号（token 等同密码，仅本机存储）。"]
        runningProcess = runPythonStream([AppPaths.projectDir + "/workbuddy_login.py",
                                          "--name", nameT, "--enabled", enabled ? "1" : "0"]) { [weak self] line in
            self?.loginLines.append(line)
        } completion: { [weak self] code in
            self?.loginRunning = false
            self?.loginAccount = nil
            guard let self = self else { return }
            if code == 0 {
                self.loadAccounts()
                self.showToast("扫码登录完成，账号已保存", .success)
            } else if code == 2 {
                self.showToast("已取消扫码登录", .error)
            } else {
                self.showToast("扫码登录失败，请重试", .error)
            }
        }
    }

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

    // MARK: Cookie 型平台（Bilibili / 联想智选 / 京东）
    /// 平台元数据：type -> (标签, accounts.json 的 auth 字段名)
    static let cookiePlatforms: [(type: String, label: String, authKey: String)] = [
        ("bilibili", "Bilibili", "bilibili_auth"),
        ("lenovo", "联想智选", "lenovo_auth"),
        ("jd", "京东", "jd_auth"),
    ]

    static func cookieScript(for type: String) -> String? {
        switch type {
        case "bilibili": return "bilibili.py"
        case "lenovo": return "lenovo.py"
        case "jd": return "jd.py"
        default: return nil
        }
    }

    /// 添加/更新 Cookie 型平台账号。cookie 等同密码：仅写入 accounts.json
    ///（已被 .gitignore 排除），UI 与日志一律不回显明文。
    func addCookieAccount(type: String, name: String, cookie: String, enabled: Bool = true,
                          onDone: ((Bool) -> Void)? = nil) {
        guard let meta = Self.cookiePlatforms.first(where: { $0.type == type }) else {
            showToast("未知平台类型", .error); onDone?(false); return
        }
        let nameT = name.trimmingCharacters(in: .whitespaces)
        let cookieT = cookie.trimmingCharacters(in: .whitespaces)
        guard !nameT.isEmpty, !cookieT.isEmpty else {
            showToast("账号名与 Cookie 不能为空", .error); onDone?(false); return
        }
        guard var root = readJSONRoot() ?? (["accounts": [] as [[String: Any]]] as [String: Any]?),
              var arr = root["accounts"] as? [[String: Any]] else {
            showToast("读取账号配置失败", .error); onDone?(false); return
        }
        var entry: [String: Any] = [
            "name": nameT,
            "app": meta.label,
            "type": type,
            "enabled": enabled,
            meta.authKey: [
                "cookie": cookieT,
                "saved_at": nowString(),
                "updated_at": nowString(),
            ],
        ]
        if let i = arr.firstIndex(where: { ($0["name"] as? String) == nameT }) {
            let old = arr[i]
            guard (old["type"] as? String) == type else {
                showToast("同名账号「\(nameT)」已存在且平台不同，请换一个名称", .error); onDone?(false); return
            }
            entry["enabled"] = old["enabled"] ?? enabled
            arr[i] = entry
        } else {
            arr.append(entry)
        }
        root["accounts"] = arr
        guard writeJSONRoot(root) else {
            showToast("保存账号失败", .error); onDone?(false); return
        }
        loadAccounts()
        showingAddPanel = false
        showToast("已添加 \(meta.label) 账号「\(nameT)」", .success)
        probeCookieNickname(type: type, name: nameT)   // 后台校验并回填昵称，不阻塞添加
        onDone?(true)
    }

    /// 更新已有 Cookie 型账号的 Cookie（重新登录/换号）
    func refreshCookieAccount(type: String, name: String, cookie: String) {
        guard let meta = Self.cookiePlatforms.first(where: { $0.type == type }),
              var root = readJSONRoot(),
              var arr = root["accounts"] as? [[String: Any]] else { return }
        let cookieT = cookie.trimmingCharacters(in: .whitespaces)
        guard !cookieT.isEmpty else {
            showToast("Cookie 不能为空", .error); return
        }
        for i in arr.indices where arr[i]["name"] as? String == name {
            arr[i]["type"] = type
            arr[i]["app"] = meta.label
            var auth = (arr[i][meta.authKey] as? [String: Any]) ?? [:]
            auth["cookie"] = cookieT
            auth["updated_at"] = nowString()
            arr[i][meta.authKey] = auth
        }
        root["accounts"] = arr
        if writeJSONRoot(root) {
            loadAccounts()
            showToast("已更新「\(name)」的 Cookie", .success)
            probeCookieNickname(type: type, name: name)
        } else {
            showToast("保存失败", .error)
        }
    }

    /// 后台探测 Cookie 有效性并回填昵称（只读，不阻塞添加流程）。
    /// 校验失败仅提示，不删除已保存的账号。
    private func probeCookieNickname(type: String, name: String) {
        guard let script = Self.cookieScript(for: type) else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let r = runPythonCapture([AppPaths.projectDir + "/" + script, "--probe", "--name", name], timeout: 25)
            let obj = (try? JSONSerialization.jsonObject(with: r.out.data(using: .utf8) ?? Data())) as? [String: Any]
            let nickname = obj?["nickname"] as? String ?? ""
            let healthy = obj?["healthy"] as? Bool ?? false
            let message = obj?["message"] as? String ?? ""
            DispatchQueue.main.async {
                guard let self = self else { return }
                if !nickname.isEmpty {
                    self.updateCookieNickname(name: name, nickname: nickname)
                }
                if !healthy {
                    self.showToast("Cookie 校验未通过：\(message.isEmpty ? "无法登录，请检查后重试" : message)", .error)
                }
            }
        }
    }

    private func updateCookieNickname(name: String, nickname: String) {
        guard var root = readJSONRoot(), var arr = root["accounts"] as? [[String: Any]] else { return }
        for i in arr.indices where arr[i]["name"] as? String == name {
            guard let type = arr[i]["type"] as? String,
                  let meta = Self.cookiePlatforms.first(where: { $0.type == type }) else { continue }
            var auth = (arr[i][meta.authKey] as? [String: Any]) ?? [:]
            auth["nickname"] = nickname
            arr[i][meta.authKey] = auth
        }
        root["accounts"] = arr
        _ = writeJSONRoot(root)
        loadAccounts()
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
        // 平台受限的账号不算"未签到"（对方活动下线/风控，用户没什么可做的），
        // 但要在摘要里点一句，免得用户看到"全部完成"又发现状态是橙的。
        let enabled = accounts.filter { $0.isEnabled }
        let pending = enabled.filter {
            let s = accStatus($0.name).state
            return s != "done" && s != "restricted"
        }
        let restricted = enabled.filter { accStatus($0.name).state == "restricted" }
        var text: String
        if pending.isEmpty {
            text = "今日所有启用账号均已签到完成"
        } else {
            let names = pending.prefix(4).map { $0.name }.joined(separator: "、")
            let tail = pending.count > 4 ? " 等" : ""
            text = "还有 \(pending.count) 个账号未签到：\(names)\(tail)"
        }
        if !restricted.isEmpty {
            text += "；\(restricted.count) 个账号平台受限（非失败）"
        }
        return text
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
