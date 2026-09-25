import Foundation

// MARK: - 账号模型（对齐 accounts.json）
struct TraeAuth: Codable, Hashable {
    var session: String?
    var token: String?
    var device_id: String?
    var uid: String?
    var saved_at: String?
}

/// WorkBuddy 账号的登录态快照。**两种来源，别混为一谈**（见 `source`）：
///   · "oauth"（扫码登录）：access_token / refresh_token 就存在这里
///     （accounts.json 已被 .gitignore 排除），与桌面端完全无关；
///   · "local"（从本机登录态一键读取）：只有非敏感快照，token 不落盘，
///     签名时实时从桌面端凭据文件读 —— 所以它的真源是**桌面端**。
/// token 等同账号密码：绝不写日志、绝不在界面回显明文。
struct WorkBuddyAuth: Codable, Hashable {
    var uid: String?
    var nickname: String?
    var uin: String?
    var domain: String?
    var enterprise_id: String?
    var saved_at: String?
    var source: String?   // "local"=本机登录态一键读取；"oauth"=扫码登录
}

/// Cookie 型平台账号快照（Bilibili / 联想智选 / 京东 / 什么值得买 / 阿里云盘，
/// 仅非敏感字段）。
/// 凭据等同账号密码：Swift 侧只透传用户粘贴的凭据到 Python 落盘，
/// 展示与日志一律使用脱敏摘要，绝不回显明文。
///
/// 两个凭据字段的区别——`cookie` 是 HTTP Cookie 串；`refresh_token` 是
/// 阿里云盘那种"登录态令牌"（存在浏览器 localStorage 里，不是 Cookie）。
/// 分开存是为了让字段名和内容对得上，不再出现"标签写 Cookie、实际存的是
/// 令牌"那种静默错配。
struct CookieAuth: Codable, Hashable {
    var cookie: String?
    var refresh_token: String?
    var nickname: String?
    var uid: String?
    var saved_at: String?
    var updated_at: String?

    /// 实际持有的凭据值（两者取其一）
    var credential: String {
        let rt = (refresh_token ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !rt.isEmpty { return rt }
        return (cookie ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var summary: String {
        let c = credential
        if c.isEmpty { return "(空)" }
        if c.count <= 12 { return "******" }
        return String(c.prefix(8)) + "******"
    }
    var isConfigured: Bool { !credential.isEmpty }
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
    var smzdm_auth: CookieAuth?
    var aliyunpan_auth: CookieAuth?
    var caimcloud_auth: CookieAuth?

    var id: String { name }
    var isEnabled: Bool { enabled ?? true }
    var isTrae: Bool { type == "trae" }
    var isWorkBuddy: Bool { type == "workbuddy" }
    var isBilibili: Bool { type == "bilibili" }
    var isLenovo: Bool { type == "lenovo" }
    var isJd: Bool { type == "jd" }
    var isSmzdm: Bool { type == "smzdm" }
    var isAliyunpan: Bool { type == "aliyunpan" }
    var isCaimcloud: Bool { type == "caimcloud" }
    /// 走「浏览器登录 / 粘贴凭据」这条路的平台
    var isCredentialPlatform: Bool {
        isBilibili || isLenovo || isJd || isSmzdm || isAliyunpan || isCaimcloud
    }

    /// 该平台在**平台侧**是否已停用（对方整条链路都挂了，我们怎么改客户端都签不上）。
    /// 这不等于"账号不可用"：已有账号仍可查看、可更新凭据、可删除，只是不再自动签到，
    /// 也不再产生橙色「平台受限」记录（那会变成每天都挂一条的无意义噪音）。
    /// 目前只有京东——京豆签到整条链路已下线，连只读的 signBeanIndex 都回
    /// errorCode DG-9999「系统异常」。判据见 checkin.py 的 _RETIRED_PLATFORMS。
    static func isRetiredType(_ t: String?) -> Bool { t == "jd" }
    var isRetired: Bool { Self.isRetiredType(type) }

    /// 本平台凭据的中文名。**必须与字段里真实存的东西一致**：阿里云盘存的是
    /// refresh_token、移动云盘存的是 authorization 授权码，都别叫成 Cookie，
    /// 否则用户会照着标签粘错东西，而且还不报错。
    var credentialNoun: String {
        if isAliyunpan { return "刷新令牌" }
        if isCaimcloud { return "授权码" }
        return "Cookie"
    }
    var platformLabel: String { app.isEmpty ? "?" : app.uppercased() }

    /// 凭据型平台对应的 auth 快照字段（其它平台返回 nil）
    var cookieAuth: CookieAuth? {
        if isBilibili { return bilibili_auth }
        if isLenovo { return lenovo_auth }
        if isJd { return jd_auth }
        if isSmzdm { return smzdm_auth }
        if isAliyunpan { return aliyunpan_auth }
        if isCaimcloud { return caimcloud_auth }
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
        if isSmzdm { return "smzdm" }
        if isAliyunpan { return "aliyunpan" }
        if isCaimcloud { return "caimcloud" }
        let a = app.lowercased()
        if a.contains("trae") { return "trae" }
        if a.contains("workbuddy") { return "workbuddy" }
        if a.contains("bilibili") || a.contains("bili") { return "bilibili" }
        if a.contains("lenovo") || a.contains("联想") { return "lenovo" }
        if a.contains("jd") || a.contains("京东") { return "jd" }
        if a.contains("smzdm") || a.contains("值得买") { return "smzdm" }
        if a.contains("aliyunpan") || a.contains("阿里云盘") { return "aliyunpan" }
        if a.contains("caimcloud") || a.contains("移动云盘") || a.contains("139") { return "caimcloud" }
        return nil
    }

    /// 长效会话（X-Cloudide-Session）的脱敏摘要。
    ///
    /// 这里**没有**对应的 `maskToken()`：Trae 的 JWT 只有 8 小时，签到时会用长效会话
    /// 现换一个新的，把它展示出来只会让人误以为"token 过期 = 账号坏了"（这个误判
    /// 真的发生过）。要看 Trae 的登录态就看 `credentialSummary()` 里的长效会话。
    func maskSession() -> String { Self.mask(trae_auth?.session) }

    static func mask(_ v: String?) -> String {
        guard let v = v, !v.isEmpty else { return "(空)" }
        if v.count <= 12 { return "******" }
        return String(v.prefix(8)) + "******"
    }

    /// 凭证健康：**只回答"这账号现在能不能签"**，不预测未来会不会失效。
    ///
    /// ⚠️ 这里踩过一次实打实的误报，改回去之前先读这段：
    ///   旧实现拿 Trae 的 JWT（`trae_auth.token`）里的 `exp` 跟当前时间比，
    ///   过期就报红"凭证已过期，请重新登录"。但 Trae 的 JWT 只有 **8 小时**，
    ///   而签到时 `trae_api.TraeClient._post_authed` 会先用长效会话
    ///   `X-Cloudide-Session` 调 GetUserToken 换一个**全新** JWT 再请求 ——
    ///   存下来的那个 token 只是"上次换的票根"，根本不参与鉴权。
    ///   实测：token 的 exp 已过 10 天，当天签到照样 HTTP 200 成功；
    ///   界面却把它标红、挂上「重新登录」，而用户点完发现根本不用重登。
    /// ⇒ **Trae 只看 session 在不在**，JWT 的 exp 不参与任何判定。
    func credentialHint() -> (text: String, color: ColorProxy)? {
        if isCredentialPlatform {
            guard let auth = cookieAuth else {
                return (text: "\(credentialNoun)未配置，请更新", color: .red)
            }
            return auth.isConfigured
                ? (text: "\(credentialNoun)已配置", color: .green)
                : (text: "\(credentialNoun)未配置，请更新", color: .red)
        }
        guard isTrae else { return nil }
        // token 缺失**不**算坏：签到时会拿 session 现换一个（trae_login.py 也有这层兜底）
        if (trae_auth?.session ?? "").isEmpty {
            return (text: "缺少长效会话（X-Cloudide-Session），请重新登录 Trae", color: .red)
        }
        return (text: "长效会话已配置", color: .green)
    }

    /// 凭据保存至今的天数（读 saved_at，格式 "yyyy-MM-dd HH:mm:ss"）。
    ///
    /// 只用于**文案参考**（"已保存 11 天"），**绝不作失效判定**：
    /// 会话到底还有多久由服务端说了算，"约 14 天"只是观测值，拿它算倒计时
    /// 就会重演上面那段"没过期却报过期"的误报。
    static func savedDays(_ savedAt: String?) -> Int? {
        guard let s = savedAt, !s.isEmpty else { return nil }
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        guard let d = f.date(from: s) else { return nil }
        return Calendar.current.dateComponents([.day], from: d, to: Date()).day
    }
}

extension Account {
    /// 表格副标题用的凭据摘要（只暴露脱敏摘要，不含任何完整凭据）
    func credentialSummary() -> String {
        if isCredentialPlatform {
            let auth = cookieAuth
            if let n = auth?.nickname, !n.isEmpty { return n }
            guard let a = auth else { return "\(credentialNoun)未配置" }
            return a.isConfigured ? "\(credentialNoun) \(a.summary)" : "\(credentialNoun)未配置"
        }
        if isWorkBuddy {
            let n = workbuddy_auth?.nickname ?? ""
            if !n.isEmpty { return n }
            return (workbuddy_auth?.source == "oauth") ? "OAuth 账号（昵称未回填）" : "本机登录态"
        }
        if isTrae {
            // 展示"长效会话"而不是那个 8 小时就过期的 JWT —— 真正决定能不能签到的是它。
            // 天数只是参考信息，不写成"还剩 X 天"：那等于拿观测值当倒计时，会误报。
            if (trae_auth?.session ?? "").isEmpty { return "缺少长效会话，请重新登录" }
            if let d = Self.savedDays(trae_auth?.saved_at) {
                return "长效会话 · 已保存 \(d) 天"
            }
            return "长效会话已配置"
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

// MARK: - 签到问题条目（「签到结果与异常」卡的数据源）
/// 为什么不再返回拼好的 String：旧实现把「账号名 + 报错」合成一行文本交给 UI，
/// 结果只能是 `lineLimit(1)` 截断显示，报错详情永远看不到；而且从拼好的字符串里
/// 抠不出账号名，「立即处理」只能盲目跳到账号管理页 —— 那一页甚至没有重试入口。
/// 改成逐账号的对象后，UI 才能做到「点这一条 → 弹这一条的详情 → 只重试这个账号」。
struct CheckinIssue: Identifiable, Hashable {
    /// "fail" 需要用户处理；"restricted" 平台侧不可用（账号与凭证都是好的）
    var state: String
    var name: String
    var hit: LogHit

    var id: String { name }
    var isRestricted: Bool { state == "restricted" }

    /// 下一步该干什么。只是**建议**，不做任何自动处理 —— 判错最多是建议不对，不会误伤账号。
    enum Fix {
        case relogin    // 凭证失效 → 重新登录 / 更新 Cookie
        case retry      // 疑似偶发 → 直接重试
        case wait       // 网络 / 超时 → 稍后再试
        case none       // 平台侧问题 → 重试也没用
    }

    /// 按报错文案粗分类。关键词取自 checkin.py 与各平台客户端实际会吐出的文案
    /// （"登录态已失效，请重新扫码登录" / "签到失败：…（HTTP 401）" / "CookieError: …"）。
    func diagnose() -> (title: String, detail: String, fix: Fix) {
        if isRestricted {
            return ("平台侧限制，无需处理",
                    "该平台的签到活动已下线、接口迁移或触发风控。账号和 Cookie 都是好的，反复重试或重新登录都不会有变化，等平台恢复即可。",
                    .none)
        }
        let low = hit.msg.lowercased()
        func has(_ keys: [String]) -> Bool { keys.contains { low.contains($0) } }

        // 必须排在通用"登录态失效"之前：WorkBuddy 桌面端把凭据加密后，我们读不到明文，
        // 这**不是**"凭证失效"，重登桌面端也没用。并进下面那条的话，用户会照
        // "重新登录即可恢复"去重登桌面端一遍，症状一模一样 —— 白折腾。
        if has(["读不到明文", "加密存储", "$wbencrypted"]) {
            return ("本机登录态已读不到",
                    "WorkBuddy 桌面端把登录凭据改成了加密存储（密钥在它的原生模块里、不落盘），"
                    + "所以本工具读不到明文 accessToken。能真正签到的凭据来自「扫码登录」："
                    + "点这个账号的「重新登录」扫一次码即可恢复，不需要动桌面端。",
                    .relogin)
        }

        if has(["登录态已失效", "请重新登录", "请重新扫码", "未登录", "请先登录", "logout",
                "cookie 失效", "cookie失效", "cookie缺失", "凭证失效", "凭据失效",
                "token 过期", "token已过期", "token 为空", "unauthorized", "forbidden",
                "401", "403",
                // WorkBuddy「本机登录态」来源的账号，凭据只有桌面端有：文件不在
                // （桌面端没登录）时也是这一类 —— 修法就是登录一次并更新凭据。
                "未找到 workbuddy 登录态"]) {
            return ("登录凭证已失效",
                    "报错里出现了登录态失效的信号。重新登录（或更新 Cookie）后再重试即可恢复，签到任务本身没有问题。",
                    .relogin)
        }
        if has(["超时", "timeout", "timed out", "网络", "network", "连接失败", "connection",
                "拒绝连接", "reset by peer", "dns", "暂时不可用", "502", "503", "504"]) {
            return ("网络或平台临时不可用",
                    "看起来是网络不通，或对方服务临时抖动。稍等一会儿再重试通常就能通过，不需要重新登录。",
                    .wait)
        }
        if has(["未配置", "不支持的平台", "未找到账号", "缺少"]) {
            return ("账号配置缺失",
                    "这条严格说不是签到失败，而是账号本身没配好（缺 Cookie / 平台类型不认识）。去账号管理页补齐配置再回来重试。",
                    .relogin)
        }
        return ("签到未成功",
                "没识别出明确的失效或网络原因。可以先重试一次；若反复失败，再去账号管理页更新这个账号的登录凭证。",
                .retry)
    }
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
        // 括号里是 app 字段，日志解析用不上（按账号名索引即可），跳过不取。
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
    /// 签到台账（含当日各账号的子任务清单），由 checkin.py 写、SubTaskStore 读。
    static var stateFile: String { projectDir + "/logs/checkin_state.json" }
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
