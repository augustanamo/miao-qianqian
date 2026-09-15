import SwiftUI
import AppKit

// MARK: - 数字格式化
enum Fmt {
    static func group(_ v: Double) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.maximumFractionDigits = 0
        f.groupingSeparator = ","
        return f.string(from: NSNumber(value: v)) ?? "\(Int(v))"
    }
    /// +128 / -3
    static func signed(_ v: Double) -> String {
        (v > 0 ? "+" : (v < 0 ? "-" : "")) + group(abs(v))
    }
}

// MARK: - 自绘顶栏
struct TopBarView: View {
    @EnvironmentObject var m: AppModel

    private static func appIcon() -> NSImage {
        if let icon = NSImage(named: "AppIcon") { return icon }
        if let icon = NSImage(named: NSImage.applicationIconName) { return icon }
        return NSImage(systemSymbolName: "checkmark.seal.fill", accessibilityDescription: nil) ?? NSImage()
    }

    private var running: Bool { m.launchd.installed }

    var body: some View {
        HStack(spacing: 0) {
            // 品牌区：宽度与侧栏一致，内容在侧栏宽度内居中，
            // 使 logo + 文字的光学中心落在侧栏中线上（与下方导航项对齐）。
            // 左侧系统交通灯由背景拖拽区让开，不额外占用布局宽度。
            HStack(spacing: 10) {
                Image(nsImage: TopBarView.appIcon())
                    .resizable()
                    .frame(width: 28, height: 28)
                    .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))

                Text("喵签签")
                    .font(.system(size: 17, weight: .bold))
                    .foregroundColor(Theme.text)
            }
            .frame(width: Theme.sidebarWidth, height: Theme.topBarHeight)
            .background(WindowDragArea())

            WindowDragArea().frame(maxWidth: .infinity, maxHeight: .infinity)

            // 运行状态胶囊
            HStack(spacing: 7) {
                Dot(color: running ? Theme.success : Theme.neutral, size: 8)
                Text(running
                     ? "自动签到运行中 · 下次 \(String(format: "%02d:%02d", m.launchd.hour, m.launchd.minute))"
                     : "自动签到未开启")
                    .font(.system(size: 12.5))
                    .foregroundColor(Theme.textBody)
            }
            .padding(.horizontal, 13)
            .frame(height: 30)
            .background(Theme.chipBG)
            .clipShape(Capsule())

            Button { m.runSign() } label: {
                HStack(spacing: 6) {
                    if m.signRunning {
                        ProgressView().controlSize(.small).scaleEffect(0.7).frame(width: 12, height: 12)
                        Text("签到中")
                    } else {
                        Image(systemName: "bolt.fill").font(.system(size: 11))
                        Text("手动签到")
                    }
                }
                .frame(minWidth: 74)
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(m.signRunning)
            .padding(.leading, 10)
        }
        .padding(.trailing, 20)
        .frame(height: Theme.topBarHeight)
        .background(Color.white)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: 1)
        }
    }
}

// MARK: - 底部状态栏
struct StatusBarView: View {
    @EnvironmentObject var m: AppModel

    var body: some View {
        HStack(spacing: 0) {
            HStack(spacing: 8) {
                Dot(color: m.launchd.installed ? Theme.success : Theme.neutral, size: 8)
                Text(leftText)
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSub)
            }
            Spacer(minLength: 12)
            HStack(spacing: 16) {
                Text(rightText)
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSub)
                Text(Theme.version)
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSub)
                Button {
                    m.refreshAll(loadCredits: true)
                    m.showToast("数据已刷新", .success)
                } label: {
                    Image(systemName: "arrow.clockwise").font(.system(size: 12, weight: .medium))
                }
                .buttonStyle(.plain)
                .foregroundColor(Theme.textSub)
                .help("刷新数据")
            }
        }
        .padding(.horizontal, 20)
        .frame(height: Theme.statusBarHeight)
        .background(Color.white)
        .overlay(alignment: .top) {
            Rectangle().fill(Theme.border).frame(height: 1)
        }
    }

    private var leftText: String {
        if m.launchd.installed {
            return "运行中 · 下次自动签到 \(String(format: "%02d:%02d", m.launchd.hour, m.launchd.minute))（还有 \(m.countdown())）"
        }
        return "未开启自动签到 · 可在设置页开启定时签到"
    }

    private var rightText: String {
        switch m.selectedPage {
        case .checkin:
            return "已启用 \(m.enabledAccounts().count) / \(m.accounts.count) 个账号"
        case .accounts:
            return "已连接 \(m.enabledAccounts().count) / \(m.accounts.count) 个平台"
        case .settings:
            return "设置已保存到本机"
        case .help:
            return "帮助文档 · 更新于 2026-09-14"
        }
    }
}

// MARK: - 侧边栏
struct SidebarView: View {
    @EnvironmentObject var m: AppModel

    private let icons: [AppModel.Page: String] = [
        .checkin: "calendar.badge.checkmark",
        .accounts: "person.2",
        .settings: "gearshape",
        .help: "book",
    ]

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 6) {
                ForEach(AppModel.Page.allCases) { page in
                    navItem(page)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 20)

            Spacer(minLength: 12)

            // 今日进度卡固定在侧栏最底部
            progressCard
                .padding(.horizontal, 16)
                .padding(.bottom, 22)
        }
        .frame(maxHeight: .infinity)
        .background(Theme.sidebar)
    }

    private func navItem(_ page: AppModel.Page) -> some View {
        let on = m.selectedPage == page
        return Button {
            m.selectedPage = page
        } label: {
            HStack(spacing: 0) {
                Text(String(format: "%02d", page.number))
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundColor(on ? Theme.accent : Theme.sidebarDim)
                    .frame(width: 26, alignment: .leading)
                Image(systemName: icons[page] ?? "circle")
                    .font(.system(size: 13.5, weight: .regular))
                    .foregroundColor(on ? Theme.sidebarIcon : Theme.sidebarText)
                    .frame(width: 22, alignment: .center)
                Text(page.rawValue)
                    .font(.system(size: 13.5, weight: on ? .semibold : .regular))
                    .foregroundColor(on ? .white : Theme.sidebarText)
                Spacer(minLength: 0)
            }
            .padding(.leading, 12)
            .frame(height: 40)
            .frame(maxWidth: .infinity)
            .background(on ? Theme.sidebarCard : Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var progressCard: some View {
        let sum = m.todaySummary()
        let ratio = sum.total > 0 ? Double(sum.done) / Double(sum.total) : 0
        return VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Text("今日进度")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.sidebarLabel)
                Spacer()
                Text("\(sum.done) / \(sum.total)")
                    .font(.system(size: 12.5, weight: .bold))
                    .foregroundColor(.white)
            }
            ProgressBar(value: ratio, height: 6, fill: Theme.success, track: Theme.sidebarTrack)
                .padding(.top, 13)
            Text(sum.pending > 0 ? "还有 \(sum.pending) 个账号待签到"
                 : (sum.restricted > 0 ? "\(sum.restricted) 个账号平台受限" : "全部账号已处理"))
                .font(.system(size: 11))
                .foregroundColor(Theme.sidebarLabel)
                .padding(.top, 14)
        }
        .padding(14)
        .background(Theme.sidebarCard)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

// MARK: - Toast
struct ToastOverlay: View {
    let toast: Toast?
    var body: some View {
        if let toast = toast {
            VStack {
                Spacer()
                HStack(spacing: 8) {
                    Dot(color: color(for: toast.kind), size: 8)
                    Text(toast.text).font(.system(size: 13)).foregroundColor(Theme.text)
                }
                .padding(.horizontal, 16)
                .frame(height: 40)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.border, lineWidth: 1))
                .shadow(color: .black.opacity(0.10), radius: 12, y: 4)
                .padding(.bottom, 46)
            }
            .transition(.move(edge: .bottom).combined(with: .opacity))
            .allowsHitTesting(false)
        }
    }
    private func color(for k: Toast.Kind) -> Color {
        switch k { case .info: return Theme.textSub; case .success: return Theme.success; case .error: return Theme.accent }
    }
}

// MARK: - 主容器
struct ContentView: View {
    @EnvironmentObject var m: AppModel

    var body: some View {
        VStack(spacing: 0) {
            TopBarView()
            HStack(spacing: 0) {
                SidebarView().frame(width: Theme.sidebarWidth)
                ZStack {
                    Theme.pageBG.ignoresSafeArea()
                    Group {
                        switch m.selectedPage {
                        case .checkin: CheckinView()
                        case .accounts: AccountsView()
                        case .settings: SettingsView()
                        case .help: HelpView()
                        }
                    }
                    .transition(.opacity)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .overlay(alignment: .topLeading) {
                    if !m.pythonAvailable { PythonErrorBanner(hint: m.pythonHint) }
                }
                .overlay { ToastOverlay(toast: m.toast).animation(.easeInOut(duration: 0.2), value: m.toast) }
            }
            .frame(maxHeight: .infinity)
            StatusBarView()
        }
        .frame(minWidth: 1280, minHeight: 760)
    }
}

struct PythonErrorBanner: View {
    let hint: String
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle").font(.system(size: 13)).foregroundColor(.white)
            Text("Python 运行时不可用：\(hint)").font(.system(size: 12)).foregroundColor(.white)
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(Theme.accent)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .padding(16)
    }
}

// MARK: - 页面滚动容器（统一内边距与卡片间距）
struct PageScroll<Content: View>: View {
    var spacing: CGFloat = 20
    @ViewBuilder var content: Content
    var body: some View {
        ScrollView {
            VStack(spacing: spacing) { content }
                .padding(.horizontal, Theme.pagePadding)
                .padding(.top, 24)
                .padding(.bottom, 28)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Theme.pageBG)
    }
}

// MARK: - 平台头像（真实图标优先，缺失时回退首字方块）
struct PlatformAvatar: View {
    let text: String
    let tint: Color
    let bg: Color
    var size: CGFloat = 30
    /// 平台图标资源名；传 nil 或取不到时用首字方块
    var iconName: String? = nil
    /// 停用账号：整体降低不透明度，与灰掉的状态保持一致
    var dimmed: Bool = false

    var body: some View {
        Group {
            if let img = PlatformIcon.image(iconName) {
                Image(nsImage: img)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
            } else {
                ZStack {
                    RoundedRectangle(cornerRadius: size * 0.23, style: .continuous).fill(bg)
                    Text(text)
                        .font(.system(size: size * 0.42, weight: .semibold))
                        .foregroundColor(tint)
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.23, style: .continuous))
        .opacity(dimmed ? 0.45 : 1)
    }
}

// MARK: ==================== 01 签到 ====================
struct CheckinView: View {
    @EnvironmentObject var m: AppModel
    @State private var chartDays: Int = 7

    var body: some View {
        PageScroll {
            let sum = m.todaySummary()
            // 「手动签到」按钮只在顶栏保留一处，页面内不再重复放一个
            PageHeader("今日签到", subtitle: headerSubtitle) {
                Button {
                    m.refreshAll(loadCredits: true)
                    m.showToast("数据已刷新", .success)
                } label: {
                    Label("刷新数据", systemImage: "arrow.clockwise")
                }
                .buttonStyle(GhostButtonStyle())
            }

            HStack(alignment: .top, spacing: 26) {
                CheckinHeroCard(sum: sum).frame(maxWidth: 520)
                CreditCard(chartDays: $chartDays)
            }

            TaskTableCard()

            HStack(alignment: .top, spacing: 26) {
                AutoSignCard().frame(maxWidth: 520)
                ResultCard()
            }
        }
    }

    private var headerSubtitle: String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "yyyy年M月d日 EEEE"
        var s = f.string(from: Date())
        if let t = m.lastSyncTime() { s += " · 上次签到 \(t)" }
        let st = m.overallStreak()
        s += " · 已连续签到 \(st) 天"
        return s
    }
}

// 深色战绩卡
struct CheckinHeroCard: View {
    @EnvironmentObject var m: AppModel
    let sum: (done: Int, fail: Int, pending: Int, credit: Double, total: Int, restricted: Int)

    var body: some View {
        Card(padding: nil, color: Theme.sidebar, bordered: false) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("今日签到总览")
                        .font(.system(size: 11.5, weight: .semibold))
                        .foregroundColor(Theme.darkCaption)
                    Spacer(minLength: 8)
                    HStack(spacing: 6) {
                        Dot(color: m.launchd.installed ? Theme.success : Theme.neutral, size: 7)
                        Text(m.launchd.installed ? "自动签到已开启" : "自动签到未开启")
                            .font(.system(size: 12.5, weight: .medium))
                            .foregroundColor(m.launchd.installed ? .white : Theme.darkCaption)
                    }
                    .padding(.horizontal, 11)
                    .frame(height: 26)
                    .background(Color.white.opacity(0.08))
                    .clipShape(Capsule())
                }

                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("\(sum.done) / \(sum.total)")
                        .font(.system(size: 36, weight: .bold))
                        .foregroundColor(.white)
                }
                .padding(.top, 14)

                Text("个账号今日已完成签到")
                    .font(.system(size: 13.5))
                    .foregroundColor(Theme.darkCaption)
                    .padding(.top, 4)

                // 「平台受限」不计入失败：账号和 cookie 都是好的，是对方活动下线/风控。
                if sum.restricted > 0 {
                    Text("\(sum.restricted) 个账号平台受限（活动下线 / 风控，非失败）")
                        .font(.system(size: 12))
                        .foregroundColor(Theme.warn)
                        .padding(.top, 6)
                }

                ProgressBar(value: ratio, height: 8, fill: Theme.success, track: Theme.sidebarTrack)
                    .padding(.top, 12)

                HStack(alignment: .top, spacing: 46) {
                    metric(Fmt.signed(sum.credit), "今日获得积分", .white)
                    metric(Fmt.group(m.totalCredits()), "账号积分总额", .white)
                    metric("\(m.overallStreak()) 天", "连续签到", Theme.success)
                    Spacer(minLength: 0)
                }
                .padding(.top, 18)
            }
            .padding(.horizontal, 24)
            .padding(.top, 24)
            .padding(.bottom, 22)
        }
    }

    private var ratio: Double {
        sum.total > 0 ? min(1, Double(sum.done) / Double(sum.total)) : 0
    }
    private func metric(_ value: String, _ label: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value).font(.system(size: 22, weight: .bold)).foregroundColor(color)
            Text(label).font(.system(size: 12)).foregroundColor(Theme.darkCaption)
        }
    }
}

// 积分状态卡：大数字是**所有启用账号的余额合计**，下方按账号给出明细。
// 各平台单位不同（积分 / 硬币 / 乐豆），明细里标明单位，合计按"点数总和"理解。
struct CreditCard: View {
    @EnvironmentObject var m: AppModel
    @Binding var chartDays: Int

    private static let cols = [GridItem(.flexible(), alignment: .leading),
                               GridItem(.flexible(), alignment: .leading)]

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                CardTitle("积分状态") {
                    SegmentedControl(items: [.init(7, "近 7 天"), .init(30, "近 30 天")], selection: $chartDays)
                }

                HStack(alignment: .bottom, spacing: 16) {
                    VStack(alignment: .leading, spacing: 0) {
                        Text(Fmt.group(m.totalCredits()))
                            .font(.system(size: 28, weight: .bold))
                            .foregroundColor(Theme.text)
                        Text("账号积分总额")
                            .font(.system(size: 11.5))
                            .foregroundColor(Theme.textSub)
                            .padding(.top, 4)
                        SoftTag(text: "本周 \(Fmt.signed(DayTool.weekCredits(logs: m.parsed)))",
                                fg: Theme.success, bg: Theme.successSoft)
                            .padding(.top, 10)
                    }
                    .frame(width: 128, alignment: .leading)

                    BarChart(values: m.chartData(days: chartDays), highlightLast: true)
                    Spacer(minLength: 0)
                }
                .padding(.top, 16)

                breakdown.padding(.top, 18)
            }
        }
    }

    /// 逐账号余额明细 —— 让"总额"可核对，也能一眼看出哪个账号查不到余额
    @ViewBuilder
    private var breakdown: some View {
        let rows = m.enabledAccounts()
        if !rows.isEmpty {
            let cov = m.creditsCoverage()
            Divider().overlay(Theme.hairline)

            HStack(spacing: 8) {
                Text("分账号余额")
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundColor(Theme.textSub)
                Spacer(minLength: 8)
                Text(cov.counted == 0 ? "尚未查询到余额" : "\(cov.counted) / \(cov.total) 个账号有余额")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.textSub)
            }
            .padding(.top, 12)
            .padding(.bottom, 6)

            LazyVGrid(columns: CreditCard.cols, spacing: 0) {
                ForEach(rows) { acc in
                    HStack(spacing: 8) {
                        PlatformAvatar(text: String(acc.platformLabel.prefix(1)),
                                       tint: Theme.textSub, bg: Theme.chipBG,
                                       size: 18, iconName: acc.platformIconName,
                                       dimmed: !acc.isEnabled)
                        Text(acc.name)
                            .font(.system(size: 12.5))
                            .foregroundColor(Theme.textBody)
                            .lineLimit(1)
                        Spacer(minLength: 6)
                        Text(balanceText(acc))
                            .font(.system(size: 12.5, weight: .medium))
                            .foregroundColor(balanceColor(acc))
                            .lineLimit(1)
                    }
                    .frame(height: 28)
                }
            }

            Text("各平台单位不同（积分 / 硬币 / 乐豆），上方总额按点数相加。")
                .font(.system(size: 11))
                .foregroundColor(Theme.textFaint)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 8)
        }
    }

    private func balanceText(_ acc: Account) -> String {
        guard let c = m.creditsByAccount[acc.name] else { return "—" }
        if let b = c.balance { return "\(Fmt.group(b)) \(c.unit)" }
        return c.ok ? "未提供" : "查询失败"
    }

    private func balanceColor(_ acc: Account) -> Color {
        guard let c = m.creditsByAccount[acc.name] else { return Theme.textFaint }
        if c.balance != nil { return Theme.text }
        return c.ok ? Theme.textSub : Theme.accent
    }
}

// 柱状图（x 轴用 M/D，今天红色高亮；柱宽随可用宽度自适应，最宽 26pt 与设计稿一致）
struct BarChart: View {
    let values: [Double]
    let highlightLast: Bool

    var body: some View {
        let maxV = max(values.max() ?? 0, 1)
        let days = DayTool.lastDays(values.count)
        let n = max(values.count, 1)
        let maxH: CGFloat = 78

        GeometryReader { geo in
            let gap: CGFloat = n > 1 ? min(17, geo.size.width / CGFloat(n) * 0.42) : 0
            let raw = (geo.size.width - gap * CGFloat(n - 1)) / CGFloat(n)
            let barW = max(10, min(26, raw))
            HStack(alignment: .bottom, spacing: gap) {
                ForEach(values.indices, id: \.self) { i in
                    let isLast = i == values.count - 1 && highlightLast
                    VStack(spacing: 8) {
                        Spacer(minLength: 0)
                        RoundedRectangle(cornerRadius: 4, style: .continuous)
                            .fill(isLast ? Theme.accent : Theme.track)
                            .frame(width: barW, height: max(22, maxH * CGFloat(values[i] / maxV)))
                        Text(label(i, days))
                            .font(.system(size: 11.5, weight: isLast ? .semibold : .regular))
                            .foregroundColor(isLast ? Theme.text : Theme.textSub)
                            .fixedSize()
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(width: geo.size.width, alignment: .leading)
        }
        .frame(height: 118)
    }

    private func label(_ i: Int, _ days: [String]) -> String {
        guard i < days.count else { return "" }
        if i == values.count - 1 && highlightLast { return "今天" }
        let d = days[i]                       // yyyy-MM-dd
        let parts = d.split(separator: "-")
        guard parts.count == 3 else { return d }
        return "\(Int(parts[1]) ?? 0)/\(Int(parts[2]) ?? 0)"
    }
}

// 账号签到任务表
struct TaskTableCard: View {
    @EnvironmentObject var m: AppModel
    @State private var filter: String = "全部"

    private static let colRecent: CGFloat = 150
    private static let colCredit: CGFloat = 90
    private static let colStatus: CGFloat = 130
    private static let colAction: CGFloat = 100
    private static let hpad: CGFloat = 15

    var body: some View {
        let rows = filteredRows()
        let done = m.todaySummary().done
        let pending = m.accounts.filter { m.accStatus($0.name).state == "pending" }.count

        Card(padding: nil) {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    Text("账号签到任务")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    SoftTag(text: "\(m.accounts.count) 个账号")
                    Spacer(minLength: 8)
                    SegmentedControl(
                        items: [.init("全部", "全部", badge: "\(m.accounts.count)"),
                                .init("已完成", "已完成", badge: "\(done)"),
                                .init("待处理", "待处理", badge: "\(pending)")],
                        selection: $filter
                    )
                }
                .padding(.horizontal, TaskTableCard.hpad)
                .padding(.top, 16)
                .padding(.bottom, 12)

                // 黑底表头（通栏）
                HStack(spacing: 0) {
                    Text("平台账号")
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text("最近签到").frame(width: TaskTableCard.colRecent, alignment: .leading)
                    Text("今日积分").frame(width: TaskTableCard.colCredit, alignment: .leading)
                    Text("状态").frame(width: TaskTableCard.colStatus, alignment: .leading)
                    Text("操作").frame(width: TaskTableCard.colAction, alignment: .leading)
                }
                .font(.system(size: 12))
                .foregroundColor(.white)
                .padding(.horizontal, TaskTableCard.hpad)
                .frame(height: 34)
                .background(Theme.sidebar)

                if rows.isEmpty {
                    Text("暂无匹配的账号任务")
                        .font(.system(size: 13))
                        .foregroundColor(Theme.textSub)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 34)
                } else {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { idx, acc in
                        TaskRow(acc: acc,
                                recentW: TaskTableCard.colRecent,
                                creditW: TaskTableCard.colCredit,
                                statusW: TaskTableCard.colStatus,
                                actionW: TaskTableCard.colAction,
                                hpad: TaskTableCard.hpad)
                        if idx != rows.count - 1 {
                            Divider().overlay(Theme.hairline).padding(.horizontal, TaskTableCard.hpad)
                        }
                    }
                }
            }
        }
    }

    private func filteredRows() -> [Account] {
        let en = m.enabledAccounts()
        switch filter {
        case "已完成":
            return en.filter { m.accStatus($0.name).state == "done" }
        case "待处理":
            return m.accounts.filter { m.accStatus($0.name).state != "done" }
        default:
            return en
        }
    }
}

struct TaskRow: View {
    @EnvironmentObject var m: AppModel
    let acc: Account
    let recentW: CGFloat
    let creditW: CGFloat
    let statusW: CGFloat
    let actionW: CGFloat
    let hpad: CGFloat
    @State private var hovering = false

    var body: some View {
        let st = m.accStatus(acc.name)
        HStack(spacing: 0) {
            HStack(spacing: 11) {
                PlatformAvatar(text: letter,
                               tint: acc.isEnabled ? avatarTint(st.state) : Theme.textSub,
                               bg: avatarBG(st.state),
                               iconName: acc.platformIconName,
                               dimmed: !acc.isEnabled)
                VStack(alignment: .leading, spacing: 3) {
                    Text(acc.name)
                        .font(.system(size: 13.5, weight: .semibold))
                        .foregroundColor(acc.isEnabled ? Theme.text : Theme.textSub)
                    subLine(st)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Text(recentText(st))
                .font(.system(size: 13))
                .foregroundColor(Theme.textBody)
                .frame(width: recentW, alignment: .leading)

            Text(creditText(st))
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(creditColor(st))
                .frame(width: creditW, alignment: .leading)

            statusPill(st).frame(width: statusW, alignment: .leading)

            actionView(st.state).frame(width: actionW, alignment: .leading)
        }
        .padding(.horizontal, hpad)
        .frame(height: 56)
        .background(hovering ? Color(hex: 0xF7F7F7) : Color.clear)
        .onHover { hovering = $0 }
    }

    private var letter: String {
        let src = acc.app.isEmpty ? acc.name : acc.app
        return String(src.prefix(1)).uppercased()
    }

    private func avatarTint(_ state: String) -> Color {
        switch state {
        case "fail": return Theme.accent
        case "restricted": return Theme.warn
        case "done": return Theme.text
        default: return Theme.textSub
        }
    }

    /// 头像底色：失败红、平台受限橙、其余中性。
    private func avatarBG(_ state: String) -> Color {
        guard acc.isEnabled else { return Theme.chipBG }
        switch state {
        case "fail": return Theme.accentSoft
        case "restricted": return Theme.warnSoft
        default: return Theme.chipBG
        }
    }

    @ViewBuilder
    private func subLine(_ st: (state: String, hit: LogHit?)) -> some View {
        let streak = ParsedLogs.streak(for: acc.name, logs: m.parsed, startDate: DayTool.today())
        HStack(spacing: 5) {
            Text(acc.credentialSummary())
                .font(.system(size: 11.5))
                .foregroundColor(Theme.textSub)
            Text("·").font(.system(size: 11.5)).foregroundColor(Theme.textFaint)
            if !acc.isEnabled {
                Text("已暂停自动签到")
                    .font(.system(size: 11.5))
                    .foregroundColor(Theme.textSub)
            } else if (st.state == "fail" || st.state == "restricted"), let hit = st.hit {
                // 平台受限也把原因显示出来（橙色），让人一眼看出不是自己账号的问题
                Text(hit.msg.isEmpty ? (st.state == "restricted" ? "平台受限" : "签到失败") : hit.msg)
                    .font(.system(size: 11.5))
                    .foregroundColor(st.state == "restricted" ? Theme.warn : Theme.accent)
                    .lineLimit(1)
            } else {
                Text("连续 \(streak) 天")
                    .font(.system(size: 11.5))
                    .foregroundColor(Theme.textSub)
            }
        }
    }

    private func creditText(_ st: (state: String, hit: LogHit?)) -> String {
        guard acc.isEnabled, st.state == "done" else { return "+0" }
        // 今日已签到（本次跳过）没有新增积分，用破折号而不是 +0，避免看起来像"签了但没给"
        if st.hit?.skipped == true { return "—" }
        return "+\(Int(st.hit?.credits ?? 0))"
    }

    private func creditColor(_ st: (state: String, hit: LogHit?)) -> Color {
        guard acc.isEnabled, st.state == "done", st.hit?.skipped != true else { return Theme.textSub }
        return Theme.success
    }

    private func recentText(_ st: (state: String, hit: LogHit?)) -> String {
        guard acc.isEnabled else { return "—" }
        if st.state == "pending" {
            if let last = m.parsed.lastHit(name: acc.name) {
                return last.date == DayTool.today() ? "今天 \(last.time)" : "\(shortDate(last.date)) \(last.time)"
            }
            return "尚未签到"
        }
        return st.hit.map { h in
            h.date == DayTool.today() ? "今天 \(h.time)" : "\(shortDate(h.date)) \(h.time)"
        } ?? "—"
    }
    private func shortDate(_ d: String) -> String {
        let p = d.split(separator: "-")
        guard p.count == 3 else { return d }
        return "\(Int(p[1]) ?? 0)/\(Int(p[2]) ?? 0)"
    }

    @ViewBuilder
    private func statusPill(_ st: (state: String, hit: LogHit?)) -> some View {
        if !acc.isEnabled {
            StatusPill(text: "已停用", color: Theme.neutral, bg: Theme.chipBG)
        } else {
            switch st.state {
            case "done":
                // 区分「本次真的签到了」和「今天早就签过、本次直接跳过」
                StatusPill(text: st.hit?.skipped == true ? "已签到" : "已完成",
                           color: Theme.success, bg: Theme.successSoft)
            case "fail": StatusPill(text: "签到失败", color: Theme.accent, bg: Theme.accentSoft)
            case "restricted": StatusPill(text: "平台受限", color: Theme.warn, bg: Theme.warnSoft)
            default:     StatusPill(text: "待签到", color: Theme.neutral, bg: Theme.chipBG)
            }
        }
    }

    @ViewBuilder
    private func actionView(_ state: String) -> some View {
        let busy = m.singleSignName == acc.name
        if !acc.isEnabled {
            Text("已停用").font(.system(size: 12.5)).foregroundColor(Theme.textFaint)
        } else {
            switch state {
            case "done":
                Text("已签到").font(.system(size: 12.5)).foregroundColor(Theme.textFaint)
            case "fail", "restricted":
                Button { m.runSingleSign(acc.name) } label: {
                    Text(busy ? "重试中" : "重试")
                        .font(.system(size: 12.5, weight: .semibold))
                        .foregroundColor(Theme.accent)
                }
                .buttonStyle(.plain)
                .disabled(m.signRunning || m.singleSignName != nil)
            default:
                Button { m.runSingleSign(acc.name) } label: {
                    Text(busy ? "签到中" : "签到")
                        .font(.system(size: 12.5, weight: .semibold))
                        .foregroundColor(Theme.text)
                }
                .buttonStyle(.plain)
                .disabled(m.signRunning || m.singleSignName != nil)
            }
        }
    }
}

// 自动签到开关卡
struct AutoSignCard: View {
    @EnvironmentObject var m: AppModel

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("自动签到")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Toggle("", isOn: Binding(get: { m.launchd.installed }, set: { m.autoToggle($0) }))
                        .toggleStyle(.switch)
                        .tint(Theme.success)
                        .labelsHidden()
                        .overlay(Capsule().stroke(m.launchd.installed ? Color.clear : Color(hex: 0xCFCFCF), lineWidth: 1))
                }

                Text(m.launchd.installed
                     ? "每天 \(planTimesText) 自动执行 · 距下次签到 \(m.countdown())"
                     : "自动签到未开启，开启后按设定时间自动执行")
                    .font(.system(size: 13))
                    .foregroundColor(Theme.textBody)
                    .padding(.top, 14)

                HStack(spacing: 7) {
                    Dot(color: m.launchd.installed ? Theme.success : Theme.neutral, size: 6)
                    Text(m.launchd.installed ? "任务计划已生效，将在后台自动运行" : "开启后将安装 macOS 定时任务")
                        .font(.system(size: 12))
                        .foregroundColor(Theme.textSub)
                }
                .padding(.top, 10)
            }
        }
    }

    private var planTimesText: String {
        let times = m.pref.times.isEmpty ? [[21, 30]] : m.pref.times
        return times.map { String(format: "%02d:%02d", $0[0], $0[1]) }.joined(separator: " / ")
    }
}

// 签到结果与异常提示
struct ResultCard: View {
    @EnvironmentObject var m: AppModel
    @State private var showLog = false

    var body: some View {
        let fails = m.todaySummary().fail
        Card {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 8) {
                    Text("签到结果与异常提示")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Text(fails > 0 ? "\(fails) 条待处理" : "今日正常")
                        .font(.system(size: 11.5))
                        .foregroundColor(fails > 0 ? Theme.accent : Theme.textSub)
                }

                let reds = m.redBanners()
                ForEach(reds, id: \.self) { banner in
                    bannerRow(icon: "exclamationmark.triangle.fill",
                              text: banner,
                              color: Theme.accent,
                              bg: Theme.accentSoft,
                              action: "立即处理") { m.selectedPage = .accounts }
                }

                let green = m.greenText()
                if !green.isEmpty {
                    bannerRow(icon: "checkmark.circle.fill",
                              text: green + " · 今日共获得 \(Int(m.todaySummary().credit)) 积分",
                              color: Theme.success,
                              bg: Theme.successSoft,
                              action: "查看详情") { showLog.toggle() }
                }

                if reds.isEmpty && green.isEmpty {
                    Text("暂无签到记录：点顶部「手动签到」立即执行，或等待自动签到按计划执行。")
                        .font(.system(size: 12.5))
                        .foregroundColor(Theme.textSub)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                }

                if showLog {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 3) {
                            ForEach(m.runLines, id: \.self) { line in
                                Text(line).font(.system(size: 10, design: .monospaced)).foregroundColor(Theme.textSub)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(height: 110)
                }
            }
        }
    }

    private func bannerRow(icon: String, text: String, color: Color, bg: Color,
                           action: String, doAction: @escaping () -> Void) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon).font(.system(size: 12)).foregroundColor(color)
            Text(text)
                .font(.system(size: 12.5))
                .foregroundColor(Theme.textBody)
                .lineLimit(1)
            Spacer(minLength: 8)
            Button(action: doAction) {
                Text(action).font(.system(size: 12.5, weight: .semibold)).foregroundColor(color)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .frame(height: 40)
        .background(bg)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}
