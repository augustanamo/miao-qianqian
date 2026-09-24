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
    /// 切走时清掉"从别处跳过来"的高亮：它只在刚跳过来的那一次有意义，
    /// 留着会让下次自己进账号页时莫名有一行是亮着的。
    @Published var selectedPage: Page = .checkin {
        didSet { if selectedPage != .accounts { focusAccount = nil } }
    }
    @Published var showingAddPanel: Bool = false
    /// 从别处（签到页的异常弹窗）跳过来时要高亮的那一行账号名。
    /// 账号列表按平台分组、一屏十几行，光把人丢到这一页他照样找不到是哪一行。
    @Published var focusAccount: String? = nil
    @Published var toast: Toast?
    /// Toast 自动消失的定时任务（同一轮新 toast 会取消旧任务）
    private var toastDismissTask: DispatchWorkItem?
    @Published var launchd: LaunchdManager.State = .init()
    @Published var creditsByAccount: [String: CreditInfo] = [:]
    @Published var pythonAvailable: Bool = Py.available
    @Published var pythonHint: String = ""

    var parsed = ParsedLogs()
    /// 今天各账号的子任务（读自签到台账），任务表「今日任务」列用。
    /// 与 parsed 一样是普通 var：刷新后由 parseLogs() 统一发 objectWillChange。
    var subTasks = SubTaskStore()
    /// 偏好设置。设置页是"即时生效"的：任何字段改动都会立刻 save() 到
    /// native_prefs.json，所以它必须是 @Published —— 否则改了设置、界面靠
    /// 每秒 ticker 硬刷才更新，读数会慢半拍。
    @Published var pref = AppPrefs.load()
    /// 上次拉取积分/余额的时刻（设置页「积分刷新频率」用）
    private var lastCreditsAt: Date?
    /// launchd 任务重装的防抖任务（连点星期圆点不该触发多次 launchctl load）
    private var launchdSyncWork: DispatchWorkItem?
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
    /// 各平台口径不同（Trae 积分 / B站硬币 / 联想乐豆 / 移动云盘云朵），unit 标明实际单位。
    struct CreditInfo: Hashable {
        /// 账号当前可用余额；nil 表示该平台没有给出余额（如京东已停用、无公开接口）
        var balance: Double? = nil
        var used: Double? = nil
        var limit: Double? = nil
        var streak: Int? = nil
        /// 平台侧单位：积分 / 硬币 / 乐豆 / 云朵
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
        /// 逐包额度明细（WorkBuddy 额度包 / Trae 资格包），无该能力的平台为空。
        /// 有了它"总额"才可核对，也才看得到消费明细。
        var packages: [CreditPackage] = []
        /// **非积分型资源**：目前只有阿里云盘的存储容量（字节）。
        /// 与 balance 分开是刻意的 —— 容量不是点数，一旦混进 balance 就会被
        /// 「账号积分总额」当成积分相加（GB 加进积分里，数字直接失去意义）。
        var capacity: CreditCapacity? = nil
        /// 与上一次**跨日**快照的差值。本地推算，不是平台接口给的数。
        var prevBalance: Double? = nil
        var prevDate: String = ""
        var delta: Double? = nil

        /// 明细里所有包的「已用」合计；无明细时回落到 used
        var packageUsedTotal: Double {
            packages.isEmpty ? (used ?? 0) : packages.reduce(0) { $0 + ($1.used ?? 0) }
        }

        /// "较上次"是否值得展示：没有对照、或恰好没变，就不占版面
        var hasDelta: Bool { delta != nil && (delta ?? 0) != 0 }
    }

    /// 非积分型资源：阿里云盘的存储容量。单位一律字节，展示时再换算。
    /// 与 CreditInfo.balance 分开，保证容量永远不会被当成积分参与求和。
    struct CreditCapacity: Hashable {
        var total: Double? = nil
        var used: Double? = nil
        var remain: Double? = nil
    }

    /// 一个额度包（平台侧的一条资源）。
    struct CreditPackage: Hashable {
        var name: String = ""
        /// free / paid（WorkBuddy 的免费赠送包 / 付费包；Trae 统一算 paid）
        var group: String = ""
        var total: Double? = nil
        var remain: Double? = nil
        var used: Double? = nil
        var unit: String = ""
        /// 数字是「当日」口径（切片包确实读到了当日切片），否则为周期口径
        var slice: Bool = false
        var cycleEnd: String = ""
    }

    // MARK: 启动 / 刷新
    func start() {
        pythonAvailable = Py.available
        if !pythonAvailable {
            pythonHint = "未找到可用的 Python 运行时，请安装 Python 3 后重试。\n可用环境变量 CHECKIN_PYTHON 指定。"
        }
        refreshAll(loadCredits: true)
        healLaunchdIfNeeded()   // 服务掉线时自愈，否则"已开启"的定时会静默不触发
        probeWorkBuddy()   // 启动即探测本机 WorkBuddy 登录态健康（只读，不含 token）
        var logTicks = 0
        ticker = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.objectWillChange.send()
            // 定期重读日志，确保外部/后台（launchd 定时、手动脚本、重新登录后）
            // 写入的 [OK]/[FAIL] 台账也能在界面刷新，避免"已签到但统计仍为 0"
            logTicks += 1
            if logTicks % 15 == 0 { self.parseLogs() }

            // 每 15 分钟给定时任务做一次体检。
            // 上面那个启动自愈只覆盖"重启 App"，而这个 App 常常一开好几天
            // （本次事故就是连开三天，服务掉线后一路静默到用户自己发现）。
            // 体检到"plist 在、服务没加载"时会重装，并顺手刷新界面上的定时状态。
            if logTicks % 900 == 0 {
                self.launchd = LaunchdManager.currentState()
                self.healLaunchdIfNeeded()
            }

            // 设置页「积分刷新频率」：按该频率自动同步一次余额。
            // 只在应用处于前台时跑 —— 关闭窗口后进程常驻后台，每 N 分钟拉起
            // python 去打各平台接口没有意义，还容易触发平台限流。
            let period = TimeInterval(max(5, self.pref.creditsRefreshMinutes) * 60)
            if NSApp.isActive, !self.creditsRunning,
               Date().timeIntervalSince(self.lastCreditsAt ?? .distantPast) >= period {
                self.refreshCredits()
            }
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
        // 子任务读的是**签到台账**而不是日志：日志/台账里的 message 被截断到 160 字，
        // "哪一步失败"恰恰最先被切掉，所以 Python 侧另存了结构化的 tasks 字段。
        subTasks.load(path: AppPaths.stateFile, date: DayTool.today())
        objectWillChange.send()
    }

    // MARK: 派生数据
    func enabledAccounts() -> [Account] { accounts.filter { $0.isEnabled } }

    /// 真正参与自动签到的账号：已启用 **且** 平台未在平台侧停用。
    /// 京东这类平台整条链路已下线，Python 侧根本不会为它产生任何记录；
    /// 若统计口径仍用「已启用」，它会永远显示"待签到"、永远挂在进度分母里
    /// —— 正是用户要求消掉的那种噪音。列表里照旧可见（账号管理页），只是不计数。
    func activeAccounts() -> [Account] { accounts.filter { $0.isEnabled && !$0.isRetired } }

    func todaySummary() -> (done: Int, fail: Int, pending: Int, credit: Double, total: Int, restricted: Int) {
        let today = DayTool.today()
        // 以「参与签到的账号」为口径汇总，避免把已删除/未启用/平台已停用的账号计入；
        // 通过 parsed.status 的前缀兼容匹配，确保日志名与账号名存在差异时也能正确归类（如"132" vs "132trae"）。
        // restricted 单列：平台受限不代表失败，不该计进 fail，也不该触发失败重试。
        let en = activeAccounts()
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

    /// 任务表「今日任务」列要画的小图标。
    /// 两条规则收在这**一处**，而不是散在视图里（以后多一个渲染点就得多抄一遍）：
    ///   · `na`（不适用：如非年度会员没有 B币券）不画——长年灰着一个图标就是噪音；
    ///   · 不足 2 项也不画——单动作平台的图标与「状态」列的胶囊完全重复。
    func taskIcons(_ name: String) -> [SubTask] {
        let items = subTasks.all(name).filter { $0.state != .na }
        return items.count >= 2 ? items : []
    }

    // MARK: - 重新登录（凭证失效时的入口，账号页与异常弹窗共用）

    /// 该账号的登录凭据是不是"需要重新登录"才能修好。两个来源，任一成立即可：
    ///
    /// ① 今天的签到失败，且失败文案指向凭据失效 —— **平台自己说的，最硬的证据**。
    ///    判据复用 `CheckinIssue.diagnose()`，不在这里另写一套关键词表；
    ///    之前"异常弹窗让人去重新登录、账号页却没有任何入口"就是因为两侧各判各的
    ///    （弹窗按文案分类，账号页只看"Cookie 字段非空"）。
    /// ② 凭据压根没用上（字段没填 / Trae 的 token 过期）—— 不必等签到失败就该给入口。
    ///
    /// 平台已停用的（京东）恒为 false：整条链路都下线了，重新登录也不会让签到恢复。
    func needsRelogin(_ name: String) -> Bool {
        guard let acc = accounts.first(where: { $0.name == name }), !acc.isRetired else { return false }
        // WorkBuddy「本机登录态」来源（source != oauth）的账号，凭据真源在**桌面端**：
        // 桌面端没登录、或桌面端把凭据加密了，这个账号**现在**就签不了。这属于静态事实，
        // 不该等"今天签到失败"才给入口 —— 否则「凭证健康检查」卡会说"点下方重新登录"，
        // 而账号行一个按钮都没有（smzdm 那个逻辑漏洞的同一形状）。
        // 扫码登录的账号凭据自带在 accounts.json，与桌面端无关，走下面的通用判断。
        if acc.isWorkBuddy, acc.workbuddy_auth?.source != "oauth" {
            return wbHealthy == false
        }
        let st = accStatus(name)
        if st.state == "fail", let hit = st.hit,
           CheckinIssue(state: st.state, name: name, hit: hit).diagnose().fix == .relogin {
            return true
        }
        if let hint = acc.credentialHint() { return hint.color != .green }
        return false
    }

    /// 这个平台能不能在**本机**重新登录（内置浏览器 / 读本机登录态这两条路之一）。
    /// 平台已停用的（京东）不算：整条链路都下线了，重新登录也不会让签到恢复，
    /// 给了按钮反而是骗人去点。
    func canRelogin(_ acc: Account) -> Bool {
        if acc.isRetired { return false }
        return acc.isWorkBuddy || acc.isTrae || acc.isCredentialPlatform
    }

    /// WorkBuddy 的「重新登录」到底该走**扫码**，还是重读本机桌面端登录态。
    ///
    /// 判断只留这一处：`relogin()` 的实际动作和视图上的按钮文案都调它 ——
    /// 两处各判一遍的话，会出现"按钮写着『读取本机登录态』，点下去却弹出扫码页"。
    ///   · 扫码登录（source == "oauth"）的账号，凭据自带，只能再扫一次；
    ///   · 「本机登录态」来源的账号平时重读桌面端最省事，但桌面端把凭据**加密**之后
    ///     那条路注定了只会吐一句"读不到明文 accessToken" → 直接改走扫码。
    func workbuddyReloginUsesOAuth(_ acc: Account) -> Bool {
        let fromDesktop = (acc.workbuddy_auth?.source ?? "") != "oauth"
        return !(fromDesktop && !wbEncrypted)
    }

    /// 按平台路由到对应的重新登录动作，**沿用账号名**。
    ///
    /// 各登录脚本同名即更新凭据（`browser_login.save_cookie_account` → `set_cookie`，
    /// 账号已存在时只改 auth 字段），所以这里不会新增重复账号，也不会动启用状态。
    /// 各条路在成功后都会走 `verifyAfterRelogin` 真签一次，把界面状态同步到"已修好"。
    func relogin(_ acc: Account) {
        guard !acc.isRetired else {
            showToast("「\(acc.app)」平台已停用，无需重新登录", .info)
            return
        }
        if acc.isWorkBuddy {
            if workbuddyReloginUsesOAuth(acc) {
                runWorkBuddyOAuth(name: acc.name, relogin: true)
            } else {
                runWorkBuddyRefresh(name: acc.name)
            }
        } else if acc.isTrae {
            runTraeLogin(name: acc.name, relogin: true)
        } else if acc.isCredentialPlatform, let type = acc.type {
            // relogin：沿用该账号自己的持久登录环境，不是新增
            runCookieBrowserLogin(type: type, name: acc.name, mode: "relogin")
        } else {
            showToast("「\(acc.app)」暂不支持在本机重新登录", .info)
        }
    }

    /// 所有**参与签到**账号的积分/余额合计。
    /// 注意各平台单位不同（积分/硬币/乐豆），这里是"点数总和"，
    /// 界面上会给出逐账号明细标明单位，便于核对。
    func totalCredits() -> Double {
        activeAccounts().reduce(0) { sum, acc in
            sum + (creditsByAccount[acc.name]?.balance ?? 0)
        }
    }

    /// 已查询到资源的账号数（有余额或有容量），用于说明合计覆盖了几个账号
    func creditsCoverage() -> (counted: Int, total: Int) {
        let en = activeAccounts()
        let counted = en.filter {
            let c = creditsByAccount[$0.name]
            return c?.balance != nil || c?.capacity != nil
        }.count
        return (counted, en.count)
    }

    func chartData(days: Int) -> [Double] {
        ParsedLogs.dailyCredits(days: DayTool.lastDays(days), logs: parsed)
    }

    func overallStreak() -> Int {
        ParsedLogs.streak(for: nil, logs: parsed, startDate: DayTool.today())
    }

    /// 今日的异常条目（签到失败 + 平台受限），按账号名排序。
    /// 「签到结果与异常」卡的数据源：每条都能点开详情，并只针对该账号重试。
    /// 平台受限一并列出——它看着像失败，但账号没问题，必须给出与失败不同的处理建议。
    func issueItems() -> [CheckinIssue] {
        let today = DayTool.today()
        var items: [CheckinIssue] = parsed.todayFail(today).map {
            CheckinIssue(state: "fail", name: $0.key, hit: $0.value)
        }
        items += parsed.todayRestricted(today).map {
            CheckinIssue(state: "restricted", name: $0.key, hit: $0.value)
        }
        return items.sorted { $0.name < $1.name }
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

    // MARK: 签到（按**参与签到**的账号 --only，尊重 enabled 与平台停用，不依赖后端改动）
    func runSign(only: String? = nil) {
        guard !signRunning, singleSignName == nil else { return }
        let names: [String]
        if let only = only {
            names = [only]
            runSingleSign(only)
            return
        }
        // 平台侧已停用的账号（京东）不进批量：Python 侧本就会跳过，
        // 放进来只会为它白起一个进程，还多一条 [停用] 日志。
        let en = activeAccounts()
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

    /// 行内单个账号签到的**来由**。动作完全相同，只影响提示文案 ——
    /// 换完凭据后自动验证签到，如果照抄"正在重试…"，用户会以为自己点错了什么。
    enum SingleSignPurpose {
        case retry            // 用户点了行内「重试 / 签到」
        case verifyAfterLogin // 刚更新完登录凭据，自动验证新凭据能不能签上
    }

    /// 行内单个账号签到/重试：与批量手动签到( signRunning)完全独立，
    /// 不驱动顶部"手动签到"批量按钮的 loading，加载态显示在行内。
    func runSingleSign(_ name: String, purpose: SingleSignPurpose = .retry) {
        guard singleSignName == nil, !signRunning else { return }
        singleSignName = name
        // 明确报出"正在重试哪一个"：行内按钮很多，不给反馈就分不清点中的是哪一行
        switch purpose {
        case .retry:
            showToast("正在重试「\(name)」…", .info)
        case .verifyAfterLogin:
            showToast("「\(name)」登录态已更新，正在验证签到…", .info)
        }
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
            // 只报**这一个账号**的结果。旧实现报的是全局汇总（"成功 8 / 失败 1"），
            // 点「重试」之后根本判断不出这个账号到底修好了没有。
            let st = self.accStatus(name)
            self.refreshCredits()
            let ok = st.state == "done"
            switch purpose {
            case .retry:
                if ok {
                    self.showToast("「\(name)」重试成功", .success)
                } else {
                    self.showToast("「\(name)」仍然失败：\(st.hit?.msg ?? "未知原因")", .error)
                }
            case .verifyAfterLogin:
                if ok {
                    self.showToast("「\(name)」重新登录成功，签到已恢复", .success)
                } else {
                    // 凭据换了却仍然签不上 —— 必须说清楚"换的这一步成了、签的那一步还没成"，
                    // 否则用户会以为是重新登录失败了，又去重扫一遍。
                    self.showToast("「\(name)」登录态已更新，但签到仍失败：\(st.hit?.msg ?? "未知原因")", .error)
                }
            }
        }
        runningProcess = proc
    }

    /// 重新登录**成功之后**的关键一步：用真实签到自证凭据可用，并让界面立刻反映结果。
    ///
    /// 为什么非有这一步不可：账号行的状态胶囊、凭证健康检查卡、「重新登录」入口，
    /// 全都从**今天的签到日志**推出来（`ParsedLogs.status`）。换凭据这件事本身
    /// 不会改写今天那条失败记录 —— 于是用户刚扫码成功，那一行依然红着"签到失败"、
    /// 依然挂着「重新登录」，看起来就像"重新登录根本没生效"（其实签到早就能用了，
    /// 只是界面还在复述上午的旧结论）。只调 `loadAccounts()` 修不掉这个错觉：
    /// 它刷新的只是凭据快照，不是状态。
    ///
    /// 所以这里补两件事：
    ///   ① `parseLogs()`：磁盘上的日志/台账可能已被别处（定时任务、另一个窗口）写过；
    ///   ② 真签一次：成功会在今天留下新记录，`status()` 的"成功 > 受限 > 失败"优先级
    ///      自然把这一行翻成"已签到"，异常卡与健康卡同口径跟着落回正常。
    /// 今天已经签过的、账号停用的、正忙的直接跳过 —— 不给对方接口发无谓的请求。
    /// **每条分支都会给出反馈**：扫码成功后一声不吭，用户照样会以为没成功。
    private func verifyAfterRelogin(_ name: String) {
        loadAccounts()
        parseLogs()
        guard !name.isEmpty,
              let acc = accounts.first(where: { $0.name == name }),
              acc.isEnabled, !acc.isRetired else {
            // 账号查不到 / 未启用 / 平台停用：登录这一步本身成功了，如实说一句就收工。
            if !name.isEmpty { showToast("「\(name)」登录态已更新", .success) }
            return
        }
        // 今天已经签过：状态本来就是对的，别为了"刷新一下"去多打一次对方接口。
        if accStatus(name).state == "done" {
            showToast("「\(name)」登录态已更新（今天已签到）", .success)
            return
        }
        guard singleSignName == nil, !signRunning else {
            // 有签到正在进行：它结束后自己会 parseLogs，状态不会漏。
            showToast("「\(name)」登录态已更新（签到进行中，完成后自动刷新）", .info)
            return
        }
        runSingleSign(name, purpose: .verifyAfterLogin)
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
        lastCreditsAt = Date()   // 计时起点：手动点刷新也算一次，避免刚刷完又被定时器追着刷
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
            c.packages = (it["packages"] as? [[String: Any]] ?? []).compactMap { p in
                let name = p["name"] as? String ?? ""
                guard !name.isEmpty else { return nil }
                var k = CreditPackage()
                k.name = name
                k.group = p["group"] as? String ?? ""
                k.total = num(p["total"])
                k.remain = num(p["remain"])
                k.used = num(p["used"])
                k.unit = p["unit"] as? String ?? ""
                k.slice = p["slice"] as? Bool ?? false
                k.cycleEnd = p["cycle_end"] as? String ?? ""
                return k
            }
            c.prevBalance = num(it["prev_balance"])
            c.prevDate = it["prev_date"] as? String ?? ""
            c.delta = num(it["delta"])
            // 非积分型资源（阿里云盘容量）：字段名与 balance 明确分开
            if let cap = it["capacity"] as? [String: Any] {
                var g = CreditCapacity()
                g.total = num(cap["total"])
                g.used = num(cap["used"])
                g.remain = num(cap["remain"])
                c.capacity = (g.total != nil || g.remain != nil) ? g : nil
            }
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
        _ = writeJSONRoot(root)
        loadAccounts()
    }

    func deleteAccount(_ account: Account) {
        guard var root = readJSONRoot() else { return }
        guard var arr = root["accounts"] as? [[String: Any]] else { return }
        arr.removeAll { ($0["name"] as? String) == account.name }
        root["accounts"] = arr
        _ = writeJSONRoot(root)
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
        _ = writeJSONRoot(root)
        loadAccounts()
    }

    /// Trae 浏览器登录。name 可留空：脚本按账号 ID 自动命名
    /// （Trae 登录态只有账号 ID，不含昵称）。
    /// relogin=true 表示这是"更新已有账号的凭据"，成功后会自动验证签到（见 verifyAfterRelogin）。
    func runTraeLogin(name: String, enabled: Bool = true, relogin: Bool = false) {
        let nameT = name.trimmingCharacters(in: .whitespaces)
        // 同 runCookieBrowserLogin：Trae 登录也没有回退路径，缺组件就别开进程
        guard Py.browserReady else {
            showToast("内置浏览器组件缺失，先在终端执行 bash setup_browser.sh", .error)
            return
        }
        if let cur = loginAccount, cur != nameT {
            showToast("请先等待账号「\(cur)」登录完成", .error)
            return
        }
        loginRunning = true
        loginAccount = nameT.isEmpty ? "(自动命名)" : nameT
        loginLines = ["正在打开内置浏览器（\(nameT.isEmpty ? "账号名称将自动读取" : "账号 " + nameT)）…",
                      "请在浏览器窗口中完成 Trae 登录，程序会自动识别并保存。"]
        runningProcess = runPythonStream([AppPaths.projectDir + "/trae_login.py", "--name", nameT,
                                          "--enabled", enabled ? "1" : "0"],
                                         python: Py.detectBrowser()) { [weak self] line in
            self?.loginLines.append(line)
        } completion: { [weak self] code in
            self?.loginRunning = false
            self?.loginAccount = nil
            if code == 0 {
                if relogin {
                    // 换完凭据立刻自证：否则今天那条失败记录会继续挂着，
                    // 界面看起来和"重新登录没生效"一模一样。
                    self?.verifyAfterRelogin(nameT)
                } else {
                    self?.loadAccounts()
                    self?.showToast("登录完成，账号已保存", .success)
                }
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
    ///
    /// mode：`add`=新增账号 / `relogin`=更新已有账号的凭据。
    /// **必须由调用方明确指定**：脚本无法只凭 name 分辨"新增一个叫 X 的账号"和
    /// "更新账号 X"，而这两种动作用的浏览器会话完全不同 —— 新增必须是无痕的
    /// 全新会话（否则会读到上次那个账号的登录态并把它当成新账号保存），
    /// 详见 browser_login._profile_dir。
    func runCookieBrowserLogin(type: String, name: String, enabled: Bool = true,
                               mode: String = "add") {
        let nameT = name.trimmingCharacters(in: .whitespaces)
        let isAdd = mode != "relogin"
        guard let meta = Self.cookiePlatforms.first(where: { $0.type == type }) else {
            showToast("未知平台类型", .error); return
        }
        // 这个动作**没有回退路径**（不像 WorkBuddy OAuth 能退到系统浏览器），
        // 缺了内置浏览器就必然失败 —— 就地拦下，别让用户干等一个注定失败的进程。
        guard Py.browserReady else {
            showToast("内置浏览器组件缺失，先在终端执行 bash setup_browser.sh", .error)
            return
        }
        if let cur = loginAccount, cur != nameT {
            showToast("请先等待账号「\(cur)」登录完成", .error)
            return
        }
        loginRunning = true
        loginAccount = nameT.isEmpty ? "(自动命名)" : nameT
        loginLines = ["正在打开内置浏览器（\(meta.label)\(nameT.isEmpty ? "／账号名称将自动读取" : " / " + nameT)）…",
                      isAdd ? "新增账号：这是全新的浏览器会话，不读取任何历史登录态，"
                            + "可放心登录要新增的那个账号。"
                            : "重新登录：沿用本账号自己的登录环境，登录后覆盖保存它的登录态。",
                      "请在浏览器窗口中扫码或账密登录，程序会自动识别并保存登录态（等同密码，仅本机存储）。"]
        runningProcess = runPythonStream([AppPaths.projectDir + "/browser_login.py", "--platform", type,
                                          "--name", nameT, "--enabled", enabled ? "1" : "0",
                                          "--mode", mode],
                                         python: Py.detectBrowser()) { [weak self] line in
            self?.loginLines.append(line)
        } completion: { [weak self] code in
            self?.loginRunning = false
            self?.loginAccount = nil
            guard let self = self else { return }
            if code == 0 {
                if isAdd {
                    self.loadAccounts()
                    self.showToast("登录完成，账号已保存", .success)
                    // 名称留空时脚本已按昵称自动命名，无需（也无法）再按名回填昵称
                    if !nameT.isEmpty {
                        self.probeCookieNickname(type: type, name: nameT)   // 后台校验并回填昵称
                    }
                } else {
                    // 更新已有账号：换完凭据立刻真签一次，把这一行的状态从"签到失败"
                    // 翻成真实结论（内部会 loadAccounts() 刷新凭据快照）。
                    self.verifyAfterRelogin(nameT)
                }
            } else if code == 3 {
                // 约定：这次登录的其实是本机**已有**账号，脚本按约定没有写盘。
                // 不能提示"成功" —— 那会让用户带着"新账号已经加上了"的误解离开。
                self.showToast("这次登录的还是已有账号，未新增；请换另一个账号再试", .error)
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
    /// relogin=true 表示这是"更新已有账号的凭据"（同名只更新、不新增），
    /// 成功后会自动验证签到（见 verifyAfterRelogin）。
    func runWorkBuddyOAuth(name: String, enabled: Bool = true, relogin: Bool = false) {
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
                                          "--name", nameT, "--enabled", enabled ? "1" : "0"],
                                         python: Py.detectBrowser()) { [weak self] line in
            self?.loginLines.append(line)
        } completion: { [weak self] code in
            self?.loginRunning = false
            self?.loginAccount = nil
            guard let self = self else { return }
            if code == 0 {
                if relogin {
                    // 脚本已把 source 改成 oauth、token 写回 accounts.json，但今天那条
                    // 失败记录不会因此消失 —— 真签一次，让状态跟着凭据走。
                    self.verifyAfterRelogin(nameT)
                } else {
                    self.loadAccounts()
                    self.showToast("扫码登录完成，账号已保存", .success)
                }
                // 本机桌面端探针可能已过期：它的结果决定「本机登录态」来源的账号还给不给
                // 「重新登录」入口，顺手刷一次，别让新增面板继续念旧结论。
                self.probeWorkBuddy()
            } else if code == 2 {
                self.showToast("已取消扫码登录", .error)
            } else {
                self.showToast("扫码登录失败，请重试", .error)
            }
        }
    }

    /// 本机 WorkBuddy 登录态健康探针。nil=未探测；true=文件存在且有**明文** accessToken。
    @Published var wbHealthy: Bool? = nil
    /// 本机 WorkBuddy 昵称（仅用于展示，脱敏无敏感信息）
    @Published var wbNickname: String = ""
    /// 本机登录态是不是已被桌面端**加密**（新版把 accessToken 写成 `$wbEncrypted` 信封，
    /// 密钥在不落盘的原生模块里）。必须和"压根没登录"分开说 —— 前者重登桌面端也没用，
    /// 唯一出路是改走扫码登录；混在一起用户只会反复重登。
    @Published var wbEncrypted: Bool = false
    /// 探针给的原因原文。UI 直接展示，**不在这里另编一句** ——
    /// 之前就是自己编了句"请先打开桌面端登录"，把"加密不可读"这个真实原因盖掉了。
    @Published var wbReason: String = ""

    /// 异步探测本机 WorkBuddy 登录态（只读，不含 token）。
    func probeWorkBuddy() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let r = runPythonCapture([AppPaths.projectDir + "/workbuddy.py", "--probe"], timeout: 20)
            var healthy: Bool? = nil
            var nickname = ""
            var encrypted = false
            var reason = ""
            if let data = r.out.data(using: .utf8),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                healthy = (obj["found"] as? Bool == true) && (obj["has_token"] as? Bool == true)
                nickname = obj["nickname"] as? String ?? ""
                encrypted = obj["encrypted"] as? Bool == true
                reason = obj["reason"] as? String ?? ""
            }
            DispatchQueue.main.async {
                self?.wbHealthy = healthy
                self?.wbNickname = nickname
                self?.wbEncrypted = encrypted
                self?.wbReason = reason
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
                    let old = arr[i]
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
                    self.wbHealthy = true
                    // 凭据快照刷新 ≠ 界面状态刷新：今天那条失败记录还挂着，
                    // 真签一次才算把这一行修好（见 verifyAfterRelogin）。
                    self.verifyAfterRelogin(nameT)
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

    // MARK: Cookie 型平台（Bilibili / 联想智选 / 什么值得买 / 阿里云盘 / 中国移动云盘）
    /// 平台元数据：type -> (标签, accounts.json 的 auth 字段名)
    ///
    /// 京东（jd）仍在表里，但它**不会出现在「新增账号」的平台下拉里**（见
    /// `addableCookiePlatforms`）：京豆签到整条链路已在平台侧下线，留着入口只会
    /// 让用户白折腾一次，然后每天都看到一条无意义的橙色记录。已有账号仍保留，
    /// 可查看、可更新凭据、可删除。
    static let cookiePlatforms: [(type: String, label: String, authKey: String)] = [
        ("bilibili", "Bilibili", "bilibili_auth"),
        ("lenovo", "联想智选", "lenovo_auth"),
        ("smzdm", "什么值得买", "smzdm_auth"),
        ("aliyunpan", "阿里云盘", "aliyunpan_auth"),
        ("caimcloud", "中国移动云盘", "caimcloud_auth"),
        ("jd", "京东", "jd_auth"),
    ]

    /// 可以在「新增账号」里选的平台 = 全部平台 − 平台侧已停用的。
    /// 新平台加进 cookiePlatforms 即自动出现在下拉里，不用再改视图。
    static var addableCookiePlatforms: [(type: String, label: String, authKey: String)] {
        cookiePlatforms.filter { !Account.isRetiredType($0.type) }
    }

    /// 凭据在 <platform>_auth 里的字段名。**必须与真实存的东西一致**：
    /// 阿里云盘是 refresh_token（在浏览器 localStorage 里，不是 Cookie）、
    /// 中国移动云盘是 authorization 授权码（值是 `授权码#手机号`）——
    /// 字段名跟着变，否则又会出现"标签写 Cookie、实际存的是令牌"那种查不出来的错配。
    static func credentialField(for type: String) -> String {
        if type == "aliyunpan" { return "refresh_token" }
        if type == "caimcloud" { return "authorization" }
        return "cookie"
    }

    /// 凭据的中文名（列表标签、提示文案、Toast 共用一处，别再各写一份）。
    static func credentialNoun(for type: String) -> String {
        if type == "aliyunpan" { return "刷新令牌" }
        if type == "caimcloud" { return "授权码" }
        return "Cookie"
    }

    static func cookieScript(for type: String) -> String? {
        switch type {
        case "bilibili": return "bilibili.py"
        case "lenovo": return "lenovo.py"
        case "jd": return "jd.py"
        case "smzdm": return "smzdm.py"
        case "aliyunpan": return "aliyunpan.py"
        case "caimcloud": return "caimcloud.py"
        default: return nil
        }
    }

    /// 名称留空时按凭据自动推导账号名；推不出来返回 nil（目前只有移动云盘有这个能力）。
    /// 单独抽出来是为了让 UI 能提前判断"名称可以留空"（据此决定保存按钮是否可点），
    /// 而不是等点下去才蹦一句"账号名不能为空"。
    static func autoName(type: String, credential: String) -> String? {
        guard type == "caimcloud" else { return nil }
        let digits = credential.split(separator: "#").last.map { String($0) } ?? ""
        let tail = String(digits.filter { $0.isNumber }.suffix(4))
        return tail.count == 4 ? "移动云盘-\(tail)" : nil
    }

    /// 添加/更新 Cookie 型平台账号。cookie 等同密码：仅写入 accounts.json
    ///（已被 .gitignore 排除），UI 与日志一律不回显明文。
    func addCookieAccount(type: String, name: String, cookie: String, enabled: Bool = true,
                          onDone: ((Bool) -> Void)? = nil) {
        guard let meta = Self.cookiePlatforms.first(where: { $0.type == type }) else {
            showToast("未知平台类型", .error); onDone?(false); return
        }
        let cookieT = cookie.trimmingCharacters(in: .whitespaces)
        let noun = Self.credentialNoun(for: type)
        // 名称允许留空：能自动命名就用自动名（移动云盘的凭据自带手机号），
        // 与其他平台的"不填就自动命名"保持一致。
        var nameT = name.trimmingCharacters(in: .whitespaces)
        if nameT.isEmpty, let auto = Self.autoName(type: type, credential: cookieT) { nameT = auto }
        guard !nameT.isEmpty, !cookieT.isEmpty else {
            showToast("账号名与\(noun)不能为空", .error); onDone?(false); return
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
                Self.credentialField(for: type): cookieT,
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
        let noun = Self.credentialNoun(for: type)
        guard !cookieT.isEmpty else {
            showToast("\(noun)不能为空", .error); return
        }
        for i in arr.indices where arr[i]["name"] as? String == name {
            arr[i]["type"] = type
            arr[i]["app"] = meta.label
            var auth = (arr[i][meta.authKey] as? [String: Any]) ?? [:]
            // 写入平台对应的字段名；同时清掉另外两种，避免新旧凭据并存导致读错
            // （阿里云盘 refresh_token / 移动云盘 authorization / 其余 cookie）。
            let field = Self.credentialField(for: type)
            auth[field] = cookieT
            for other in ["cookie", "refresh_token", "authorization"] where other != field {
                auth[other] = nil
            }
            auth["updated_at"] = nowString()
            arr[i][meta.authKey] = auth
        }
        root["accounts"] = arr
        if writeJSONRoot(root) {
            loadAccounts()
            showToast("已更新「\(name)」的\(noun)", .success)
            probeCookieNickname(type: type, name: name)
        } else {
            showToast("保存失败", .error)
        }
    }

    /// 后台探测 Cookie 有效性并回填昵称（只读，不阻塞添加流程）。
    /// 校验失败仅提示，不删除已保存的账号。
    private func probeCookieNickname(type: String, name: String) {
        // 平台侧已停用的（京东）：接口本身就是死的，探测只会换来一句
        // "校验未通过"，属于噪音，直接跳过。
        guard !Account.isRetiredType(type) else { return }
        guard let script = Self.cookieScript(for: type) else { return }
        let noun = Self.credentialNoun(for: type)
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
                    self.showToast("\(noun)校验未通过：\(message.isEmpty ? "无法登录，请检查后重试" : message)", .error)
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
    /// 时间点为空时按默认 21:30 兜底。launchd 任务、汇总提醒、策略预览三处
    /// 都读这一个口径，避免某处拿到空数组各自兜底出不同的时间。
    var effectiveTimes: [[Int]] { pref.times.isEmpty ? [[21, 30]] : pref.times }

    /// 下一个待执行的签到时间点（距零点分钟数）+ 是否落在今天。
    /// 真源是 pref.times —— 不要读 plist 里的首个时间：多时间点或已经过点时它是错的。
    private func nextFire() -> (minutes: Int, today: Bool)? {
        let list = effectiveTimes.map { $0[0] * 60 + ($0.count > 1 ? $0[1] : 0) }.sorted()
        guard !list.isEmpty else { return nil }
        let now = Calendar.current.dateComponents([.hour, .minute], from: Date())
        let nowMin = (now.hour ?? 0) * 60 + (now.minute ?? 0)
        if let n = list.first(where: { $0 > nowMin }) { return (n, true) }
        return (list[0], false)
    }

    func nextFireMinutes() -> Int? { nextFire()?.minutes }

    /// 下一次定时签到的时刻文案："今天 21:30" / "明天 09:00"
    func nextFireText() -> String {
        guard let f = nextFire() else { return "—" }
        return String(format: "%@ %02d:%02d", f.today ? "今天" : "明天", f.minutes / 60, f.minutes % 60)
    }

    func autoToggle(_ on: Bool) {
        if on {
            let r = LaunchdManager.install(times: effectiveTimes,
                                           weekdays: pref.weekdays, staggerMinutes: pref.staggerMinutes)
            showToast(r.msg, r.ok ? .success : .error)
        } else {
            LaunchdManager.uninstall()
            showToast("已关闭自动签到", .info)
        }
        refreshAll()
    }

    /// 设置页改动后的收尾：偏好已经由设置页写盘，这里只负责把 launchd 任务
    /// 按新参数重装一次 —— 做 0.5s 防抖，连点星期圆点只会重装一次。
    /// 未开启自动签到时不安装任务：避免用户在设置页随手改个时间就把定时签到
    /// 静默打开（旧版 commit() 有这个隐患）。
    func syncLaunchdIfNeeded() {
        launchdSyncWork?.cancel()
        guard launchd.installed else { return }
        let work = DispatchWorkItem { [weak self] in
            guard let self = self else { return }
            let r = LaunchdManager.install(times: self.effectiveTimes,
                                           weekdays: self.pref.weekdays,
                                           staggerMinutes: self.pref.staggerMinutes)
            self.launchd = LaunchdManager.currentState()
            if !r.ok { self.showToast(r.msg, .error) }
        }
        launchdSyncWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5, execute: work)
    }

    /// 启动时的自愈：**plist 在、服务却掉线**是最坏的一种状态 ——
    /// 界面上显示"已开启"（installed 为真），实际 launchd 里根本没有它，
    /// 一次都不会触发；而且没有任何路径会去修，因为 syncLaunchdIfNeeded
    /// 只在设置页改动时才调用（用户不改设置就永远救不回来）。
    ///
    /// 2026-09 的真实事故：服务掉线后连着几天 9:00 静默没跑，
    /// 用户只能靠自己发现"到点没签到"。这里在启动后补装一次。
    /// 只在「已安装但未加载」时动手，所以不会覆盖用户的开关状态：
    /// 主动关闭自动签到会删掉 plist → installed 为假 → 这里不触发。
    func healLaunchdIfNeeded() {
        guard launchd.installed, !launchd.loaded else { return }
        // 等启动那波磁盘/进程忙碌过去再动手，避免和 refreshAll 抢 launchctl
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            guard let self = self, self.launchd.installed, !self.launchd.loaded else { return }
            let r = LaunchdManager.install(times: self.effectiveTimes,
                                           weekdays: self.pref.weekdays,
                                           staggerMinutes: self.pref.staggerMinutes)
            self.launchd = LaunchdManager.currentState()
            if r.ok {
                self.showToast("定时任务此前未生效，已自动重新加载", .success)
            } else {
                self.showToast("定时任务未生效，自动加载失败：\(r.msg)", .error)
            }
        }
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

    /// 在当天最后一个签到时间点之后**一小时**提醒，内容为当前仍未签到的账号。
    /// （设置页的「每天 XX:XX 提醒」文案就是按 +1 小时算的，改动这里要同步改文案。）
    func scheduleDailyDigest() {
        let center = UNUserNotificationCenter.current()
        let last = effectiveTimes.max { ($0[0], $0[1]) < ($1[0], $1[1]) } ?? [21, 30]
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
        // 平台已停用的（京东）连提都不提：它不参与签到，报出来只是噪音。
        let enabled = activeAccounts()
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
        // 内置浏览器是"重新登录 / 新增账号 / Trae 登录"的硬依赖，缺了就是一行
        // "No module named 'playwright'" 埋在日志里，所以放进诊断信息随复制带走。
        text += "内置浏览器：\(Py.browserReady ? "就绪" : "缺失（需要 bash setup_browser.sh）")"
        text += "  解释器：\(Py.detectBrowser())\n"
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
