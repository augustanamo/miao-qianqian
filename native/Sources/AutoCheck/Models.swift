import Foundation

// MARK: - 账号模型（对齐 accounts.json）
struct TraeAuth: Codable, Hashable {
    var session: String?
    var token: String?
    var device_id: String?
    var uid: String?
    var saved_at: String?
}

/// WorkBuddy 账号快照（仅非敏感字段）。
/// accessToken 等同账号密码，**绝不**写入 accounts.json / 日志 / UI，
/// 签名时实时从本机登录态文件读取（见 workbuddy.py）。
struct WorkBuddyAuth: Codable, Hashable {
    var uid: String?
    var nickname: String?
    var uin: String?
    var domain: String?
    var enterprise_id: String?
    var saved_at: String?
    var source: String?   // "local"=本机登录态一键读取；"oauth"=扫码登录
}

/// Cookie 型平台账号快照（Bilibili / 联想智选 / 京东等，仅非敏感字段）。
/// cookie 等同账号密码：Swift 侧只透传用户粘贴的 cookie 到 Python 落盘，
/// 展示与日志一律使用脱敏摘要，绝不回显 cookie 明文。
struct CookieAuth: Codable, Hashable {
    var cookie: String?
    var nickname: String?
    var uid: String?
    var saved_at: String?
    var updated_at: String?

    var summary: String {
        guard let c = cookie, !c.isEmpty else { return "(空)" }
        if c.count <= 12 { return "******" }
        return String(c.prefix(8)) + "******"
    }
    var isConfigured: Bool { !(cookie ?? "").isEmpty }
}

struct ReqConf: Codable, Hashable {
    var method: String?
    var url: String?
    var headers: [String: String]?
    var body: String?
}

struct Account: Codable, Identifiable, Hashable {
    var name: String
    var app: String
    var type: String?
    var enabled: Bool?
    var requests: [ReqConf]?
    var trae_auth: TraeAuth?
    var workbuddy_auth: WorkBuddyAuth?
    var bilibili_auth: CookieAuth?
    var lenovo_auth: CookieAuth?
    var jd_auth: CookieAuth?

    var id: String { name }
    var isEnabled: Bool { enabled ?? true }
    var isTrae: Bool { type == "trae" }
    var isWorkBuddy: Bool { type == "workbuddy" }
    var isBilibili: Bool { type == "bilibili" }
    var isLenovo: Bool { type == "lenovo" }
    var isJd: Bool { type == "jd" }
    var platformLabel: String { app.isEmpty ? "?" : app.uppercased() }

    /// Cookie 型平台对应的 auth 快照字段（取本平台 cookie 配置，其它平台返回 nil）
    var cookieAuth: CookieAuth? {
        if isBilibili { return bilibili_auth }
        if isLenovo { return lenovo_auth }
        if isJd { return jd_auth }
        return nil
    }

    /// 平台图标资源名（对应 assets/platform-icons/<name>.png）。
    /// 取不到真实图标时返回 nil，UI 回退为首字方块头像。
    var platformIconName: String? {
        if isTrae { return "trae" }
        if isWorkBuddy { return "workbuddy" }
        if isBilibili { return "bilibili" }
        if isLenovo { return "lenovo" }
        if isJd { return "jd" }
        let a = app.lowercased()
        if a.contains("trae") { return "trae" }
        if a.contains("workbuddy") { return "workbuddy" }
        if a.contains("bilibili") || a.contains("bili") { return "bilibili" }
        if a.contains("lenovo") || a.contains("联想") { return "lenovo" }
        if a.contains("jd") || a.contains("京东") { return "jd" }
        return nil
    }

    func maskSession() -> String { Self.mask(trae_auth?.session) }
    func maskToken() -> String { Self.mask(trae_auth?.token) }

    static func mask(_ v: String?) -> String {
        guard let v = v, !v.isEmpty else { return "(空)" }
        if v.count <= 12 { return "******" }
        return String(v.prefix(8)) + "******"
    }

    /// 凭证健康：token/session 缺失、无法解析或已过期，一律视为需要重新登录
    func credentialHint() -> (text: String, color: ColorProxy)? {
        if isBilibili || isLenovo || isJd {
            guard let auth = cookieAuth else {
                return (text: "Cookie 未配置，请更新", color: .red)
            }
            return auth.isConfigured
                ? (text: "Cookie 已配置", color: .green)
                : (text: "Cookie 未配置，请更新", color: .red)
        }
        guard isTrae else { return nil }
        let token = trae_auth?.token ?? ""
        let session = trae_auth?.session ?? ""
        if token.isEmpty {
            return (text: "Token 为空，请重新登录", color: .red)
        }
        if session.isEmpty {
            return (text: "Session 缺失，请重新登录", color: .red)
        }
        guard let exp = Self.jwtExp(token) else { return (text: "凭证无法解析，请重新登录", color: .neutral) }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        if exp < Date() {
            return (text: "凭证已过期，请重新登录", color: .red)
        }
        return (text: "凭证 \(formatter.string(from: exp)) 前有效", color: .green)
    }

    static func jwtExp(_ jwt: String) -> Date? {
        let parts = jwt.split(separator: ".")
        guard parts.count >= 2 else { return nil }
        var payload = String(parts[1])
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        let padLen = (4 - payload.count % 4) % 4
        payload += String(repeating: "=", count: padLen)
        guard let data = Data(base64Encoded: payload),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let exp = obj["exp"] as? Double else { return nil }
        return Date(timeIntervalSince1970: exp)
    }
}

extension Account {
    /// 表格副标题用的凭据摘要（只暴露脱敏摘要，不含任何完整凭据）
    func credentialSummary() -> String {
        if isBilibili || isLenovo || isJd {
            let auth = cookieAuth
            if let n = auth?.nickname, !n.isEmpty { return n }
            guard let a = auth else { return "Cookie 未配置" }
            return a.isConfigured ? "Cookie \(a.summary)" : "Cookie 未配置"
        }
        if isWorkBuddy {
            let n = workbuddy_auth?.nickname ?? ""
            if !n.isEmpty { return n }
            return (workbuddy_auth?.source == "oauth") ? "OAuth 账号（昵称未回填）" : "本机登录态"
        }
        if isTrae {
            let token = trae_auth?.token ?? ""
            if token.isEmpty { return "Token 未配置" }
            if let exp = Self.jwtExp(token), exp < Date() { return "Token 已过期" }
            return "Token \(Self.mask(token))"
        }
        return "Cookie 已配置"
    }
}

enum ColorProxy { case green, red, neutral }

struct ConfigFile: Codable {
    var accounts: [Account]?
}

// MARK: - 日志解析（对齐 checkin.log）
struct LogHit: Hashable {
    var time: String
    var date: String
    var msg: String
    var credits: Double
    /// 该条记录表达的是"今日已签到、本次未重复领取"（各平台/本地台账的跳过结果）
    var skipped: Bool = false
}

struct ParsedLogs {
    // date -> name -> hit（当日最后一次）
    // 三态：成功(done) / 平台受限(restricted) / 失败(fail)。
    // 「平台受限」= 平台活动下线、接口迁移、风控拦截——账号和 cookie 都没问题，
    // 不该算进失败，更不该触发"失败自动重试"去反复打对方接口。
    var okByDay: [String: [String: LogHit]] = [:]
    var failByDay: [String: [String: LogHit]] = [:]
    var restrictedByDay: [String: [String: LogHit]] = [:]
    var rawLines: [String] = []

    mutating func load(path: String) {
        okByDay = [:]; failByDay = [:]; restrictedByDay = [:]; rawLines = []
        guard let content = try? String(contentsOfFile: path, encoding: .utf8) else { return }
        let lines = content.split(separator: "\n")
        for line in lines {
            let s = String(line)
            rawLines.append(s)
            parseLine(s)
        }
    }

    private mutating func parseLine(_ s: String) {
        // "[2026-09-14 10:36:50] [FAIL] 132(trae) -> msg"
        // 注意：split("]", maxSplits: 2) 会消费分隔符：
        //   part0 = "[2026-09-14 10:36:50"
        //   part1 = " [FAIL"         // 右括号已被消费
        //   part2 = " 132(trae) -> msg"
        // 因此 tag 需从 part1 去括号直接取，账号+消息从 part2 提取。
        guard s.hasPrefix("[") else { return }
        let partList = s.split(separator: "]", maxSplits: 2)
        guard partList.count >= 3 else { return }
        let dateTime = String(partList[0].dropFirst())          // 2026-09-14 10:36:50
        guard dateTime.count >= 16 else { return }
        let date = String(dateTime.prefix(10))
        let tagPart = String(partList[1]).trimmingCharacters(in: .whitespaces)  // "[FAIL"
        guard tagPart.hasPrefix("[") else { return }
        let tag = String(tagPart.dropFirst())                   // "FAIL"
        guard tag == "OK" || tag == "FAIL" || tag == "受限" || tag == "预览" else { return }
        var tail = String(partList[2]).trimmingCharacters(in: .whitespaces)
        guard tail.contains("("), tail.contains(")") else { return }
        guard let open = tail.firstIndex(of: "("), let close = tail.firstIndex(of: ")"), open < close else { return }
        let name = String(tail[..<open])
        let app = String(tail[tail.index(after: open)..<close])
        tail = String(tail[tail.index(after: close)...]).trimmingCharacters(in: .whitespaces)
        if tail.hasPrefix("->") { tail = String(tail.dropFirst(2)).trimmingCharacters(in: .whitespaces) }
        if tag == "预览" { return }
        let credits = Self.extractCredits(tail)
        let hit = LogHit(time: String(dateTime.suffix(8)), date: date, msg: tail,
                         credits: credits, skipped: Self.looksAlreadySigned(tail))
        if tag == "OK" {
            okByDay[date, default: [:]][name] = hit
        } else if tag == "受限" {
            restrictedByDay[date, default: [:]][name] = hit
        } else {
            failByDay[date, default: [:]][name] = hit
        }
    }

    static func extractCredits(_ msg: String) -> Double {
        // "签到成功，本次获得 0 积分" / "本次获得 128 积分"
        guard let range = msg.range(of: #"本次获得\s*([\d.]+)"#, options: .regularExpression) else { return 0 }
        let sub = String(msg[range])
        let digits = String(sub.filter { $0.isNumber || $0 == "." })
        return Double(digits) ?? 0
    }

    /// 该条日志是否表达"今日已签到、本次未重复领取"。
    /// 各平台文案：Trae / 本地台账「今日已签到，已跳过重复签到」、
    /// WorkBuddy「今日已签到（幂等）」、B站/京东「今日已签到（幂等）」。
    /// 与 checkin.py 的 is_already_msg() 保持同一判定口径。
    static func looksAlreadySigned(_ msg: String) -> Bool {
        msg.contains("已签到") || msg.contains("无需重复") || msg.contains("跳过重复")
    }

    func status(name: String, on date: String) -> (state: String, hit: LogHit?) {
        // 先精确匹配；未命中时兼容「日志名是账号名前缀」的写法
        // （日志里写 132(trae)，accounts.json 里账号名为 132trae 的历史数据场景），
        // 避免账号被误判为"未签到/空"。
        // 优先级：成功 > 平台受限 > 失败。同一天里先成功后受限（或反之中间成功过）
        // 都按"已签到"呈现，与用户直觉一致。
        let oks = okByDay[date] ?? [:]
        let fails = failByDay[date] ?? [:]
        let rests = restrictedByDay[date] ?? [:]
        if let h = oks[name] { return ("done", h) }
        if let h = rests[name] { return ("restricted", h) }
        if let h = fails[name] { return ("fail", h) }
        for (k, h) in oks where k.count >= 2 && name.hasPrefix(k) { return ("done", h) }
        for (k, h) in rests where k.count >= 2 && name.hasPrefix(k) { return ("restricted", h) }
        for (k, h) in fails where k.count >= 2 && name.hasPrefix(k) { return ("fail", h) }
        return ("pending", nil)
    }

    func todayOK(_ date: String) -> [String: LogHit] { okByDay[date] ?? [:] }
    /// 当日失败账号。**排除已标为「平台受限」的**：平台受限不是账号的问题，
    /// 重试只会反复去打对方接口（如京东 402），有害无益。
    func todayFail(_ date: String) -> [String: LogHit] {
        let rests = restrictedByDay[date] ?? [:]
        return (failByDay[date] ?? [:]).filter { rests[$0.key] == nil }
    }
    func todayRestricted(_ date: String) -> [String: LogHit] { restrictedByDay[date] ?? [:] }

    /// 最近一次签到记录（跨天）
    func lastHit(name: String) -> LogHit? {
        let dates = Set(okByDay.keys)
            .union(failByDay.keys)
            .union(restrictedByDay.keys)
        for d in dates.sorted(by: >) {
            if let h = okByDay[d]?[name] { return h }
            if let h = restrictedByDay[d]?[name] { return h }
            if let h = failByDay[d]?[name] { return h }
        }
        return nil
    }

    /// 每日积分合计（用于柱状图）
    static func dailyCredits(days: [String], logs: ParsedLogs) -> [Double] {
        days.map { day in
            (logs.okByDay[day]?.values.reduce(0) { $0 + $1.credits }) ?? 0
        }
    }

    /// 连续签到天数：从 startDate 往前累计 OK 的天数
    static func streak(for name: String? = nil, logs: ParsedLogs, startDate: String) -> Int {
        var daysBack = 0
        var count = 0
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let start = formatter.date(from: startDate) else { return 0 }
        var cur = start
        while true {
            let d = formatter.string(from: cur)
            if let name = name {
                if logs.okByDay[d]?[name] != nil { count += 1 } else { break }
            } else {
                if let ok = logs.okByDay[d], !ok.isEmpty { count += 1 } else { break }
            }
            guard let prev = Calendar.current.date(byAdding: .day, value: -1, to: cur) else { break }
            cur = prev
            daysBack += 1
            if daysBack > 366 { break }
        }
        return count
    }
}

// MARK: - 应用偏好（native_prefs.json）
struct AppPrefs: Codable {
    var autoSignOnLaunch: Bool = false
    var retryOnFail: Bool = true
    var notifyOnComplete: Bool = false
    var dailyDigestReminder: Bool = false
    var syncHistoryCreditsOnLogin: Bool = true
    var creditsRefreshMinutes: Int = 30
    var staggerMinutes: Int = 0
    var times: [[Int]] = [[21, 30]]
    var weekdays: [Int] = [1, 2, 3, 4, 5, 6, 7] // 1=周一 ... 7=周日

    static var path: String { AppPaths.projectDir + "/native_prefs.json" }

    init() {}

    /// 逐字段兜底解码：老版本 native_prefs.json 缺少新增字段时不会整份重置
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = AppPrefs()
        autoSignOnLaunch = (try? c.decode(Bool.self, forKey: .autoSignOnLaunch)) ?? d.autoSignOnLaunch
        retryOnFail = (try? c.decode(Bool.self, forKey: .retryOnFail)) ?? d.retryOnFail
        notifyOnComplete = (try? c.decode(Bool.self, forKey: .notifyOnComplete)) ?? d.notifyOnComplete
        dailyDigestReminder = (try? c.decode(Bool.self, forKey: .dailyDigestReminder)) ?? d.dailyDigestReminder
        syncHistoryCreditsOnLogin = (try? c.decode(Bool.self, forKey: .syncHistoryCreditsOnLogin)) ?? d.syncHistoryCreditsOnLogin
        creditsRefreshMinutes = (try? c.decode(Int.self, forKey: .creditsRefreshMinutes)) ?? d.creditsRefreshMinutes
        staggerMinutes = (try? c.decode(Int.self, forKey: .staggerMinutes)) ?? d.staggerMinutes
        times = (try? c.decode([[Int]].self, forKey: .times)) ?? d.times
        weekdays = (try? c.decode([Int].self, forKey: .weekdays)) ?? d.weekdays
    }

    static func load() -> AppPrefs {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let p = try? JSONDecoder().decode(AppPrefs.self, from: data) else {
            return AppPrefs()
        }
        return p
    }
    func save() {
        guard let data = try? JSONEncoder().encode(self) else { return }
        try? data.write(to: URL(fileURLWithPath: Self.path), options: .atomic)
    }
}

// MARK: - 路径探测
enum AppPaths {
    static let toolName = "auto-checkin"

    static var projectDir: String {
        // 1) 运行在 .app 包内：bundle 位于 ~/auto-checkin/AutoCheck.app
        let bundle = Bundle.main.bundlePath
        if bundle.hasSuffix(".app") {
            return (bundle as NSString).deletingLastPathComponent
        }
        // 2) 环境变量覆盖（swift run / 命令行测试）
        if let env = ProcessInfo.processInfo.environment["AUTOCHECKIN_DIR"], !env.isEmpty {
            return env
        }
        // 3) 从可执行文件路径向上找存储根
        return (Bundle.main.executablePath as NSString?)?.deletingLastPathComponent ?? ""
    }

    static var accountsFile: String { projectDir + "/accounts.json" }
    static var logFile: String { projectDir + "/logs/checkin.log" }
    static var launchdLog: String { projectDir + "/logs/launchd.log" }
    static var launchdErr: String { projectDir + "/logs/launchd.err.log" }
    static var curDir: String { FileManager.default.currentDirectoryPath }
}

// MARK: - 日期工具
enum DayTool {
    static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
    static func today() -> String { formatter.string(from: Date()) }

    static func lastDays(_ n: Int) -> [String] {
        var out: [String] = []
        for i in (0..<n).reversed() {
            guard let d = Calendar.current.date(byAdding: .day, value: -i, to: Date()) else { continue }
            out.append(formatter.string(from: d))
        }
        return out
    }
    static func weekCredits(logs: ParsedLogs) -> Double {
        let days = lastDays(7)
        return ParsedLogs.dailyCredits(days: days, logs: logs).reduce(0, +)
    }
}
