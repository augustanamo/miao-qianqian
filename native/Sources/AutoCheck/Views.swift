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
    /// 字节 -> 1.0 TB / 337.3 GB / 512 MB（1024 进制，与盘内文件大小同一口径）
    static func bytes(_ v: Double) -> String {
        let units: [(String, Double)] = [("TB", 1024 * 1024 * 1024 * 1024),
                                         ("GB", 1024 * 1024 * 1024),
                                         ("MB", 1024 * 1024),
                                         ("KB", 1024)]
        for (name, base) in units where v >= base {
            return String(format: "%.1f %@", v / base, name)
        }
        return "\(Int(max(0, v))) B"
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
                     ? "自动签到运行中 · 下次 \(m.nextFireText())"
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
                Dot(color: leftDot, size: 8)
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

    /// 顶栏胶囊已经说了「自动签到运行中 · 下次 21:30」，这里不再重复那件事，
    /// 改说**最近一次签到的结果**，两条信息互不重叠。
    private var leftText: String {
        let sum = m.todaySummary()
        guard m.lastSyncTime() != nil else {
            return "尚无签到记录 · 可在设置页开启定时签到"
        }
        if sum.fail > 0 { return "最近一次签到有 \(sum.fail) 个账号需处理" }
        if sum.restricted > 0 { return "\(sum.restricted) 个账号平台受限（活动下线 / 风控）" }
        return "今日签到已完成 \(sum.done) / \(sum.total)，暂无异常"
    }

    private var leftDot: Color {
        let sum = m.todaySummary()
        if sum.fail > 0 { return Theme.accent }
        if sum.restricted > 0 { return Theme.warn }
        return m.lastSyncTime() != nil ? Theme.success : Theme.neutral
    }

    private var rightText: String {
        switch m.selectedPage {
        case .checkin:
            return "已启用 \(m.enabledAccounts().count) / \(m.accounts.count) 个账号"
        case .accounts:
            return "已连接 \(m.enabledAccounts().count) / \(m.accounts.count) 个平台"
        case .settings:
            return m.launchd.installed ? "定时签到已开启 · 改动即时保存" : "定时签到未开启 · 改动即时保存"
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
// 版式原则：**长明细一律下沉**。
// 绑的平台一多，"分账号余额 / 额度明细"这类会随账号数线性变长的东西如果跟
// 概览卡并排，就会把下面所有内容顶下去。所以三行是刻意这么分的：
//   第 1 行 概览（高度固定，不随账号数变）——今日总览 + 积分状态
//   第 2 行 账号签到任务（最重要的操作区，紧贴概览，不再被挤到屏幕外）
//   第 3 行 明细（账号余额与额度 / 签到结果）——它自己变长就自己变长
// 「自动签到」开关只在设置页保留一处；顶栏胶囊负责报运行状态，页面内不再重复。
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

            HStack(alignment: .top, spacing: 20) {
                CheckinHeroCard(sum: sum).frame(maxWidth: 520)
                CreditCard(chartDays: $chartDays)
            }

            TaskTableCard()

            HStack(alignment: .top, spacing: 20) {
                BalanceCard()
                ResultCard().frame(width: 400)
            }
        }
    }

    /// 只报"是什么日子、上次什么时候签的"。连续天数在总览卡里已经有，
    /// 不在页头重复第二遍。
    private var headerSubtitle: String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "yyyy年M月d日 EEEE"
        var s = f.string(from: Date())
        if let t = m.lastSyncTime() { s += " · 上次签到 \(t)" }
        return s
    }
}

// 深色战绩卡
// 这里只放"今日"口径的概览。刻意不放两样东西：
//   · 自动签到状态 —— 顶栏胶囊已经在报（含下次执行时间），设置页才是开关；
//   · 账号积分总额 —— 相邻的「积分状态」卡就是为它存在的，不重复第二遍。
struct CheckinHeroCard: View {
    @EnvironmentObject var m: AppModel
    let sum: (done: Int, fail: Int, pending: Int, credit: Double, total: Int, restricted: Int)

    var body: some View {
        Card(padding: nil, color: Theme.sidebar, bordered: false) {
            VStack(alignment: .leading, spacing: 0) {
                Text("今日签到总览")
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundColor(Theme.darkCaption)

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

// 积分状态卡（只放概览）：总额 + 趋势图。
// 逐账号余额、逐包额度这类**会随账号数变长**的内容一律下沉到 BalanceCard，
// 否则账号一多就会把下面的卡片全顶到屏幕外。
struct CreditCard: View {
    @EnvironmentObject var m: AppModel
    @Binding var chartDays: Int

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
                    .frame(width: 118, alignment: .leading)

                    BarChart(values: m.chartData(days: chartDays), highlightLast: true)
                    Spacer(minLength: 0)
                }
                .padding(.top, 16)

                Text("趋势统计的是「当天签到获得的积分」，与上方余额是两个口径；"
                     + "各平台单位不同，总额按点数相加，仅作参考。")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.textFaint)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 12)
            }
        }
    }
}

// 账号余额与额度明细（下沉到页面底部的那张卡）。
// 之所以单独成卡：它的行数 = 账号数，是全页唯一"会无限变长"的部分；
// 放在底部，它再长也只影响自己，不会把「账号签到任务」顶走。
struct BalanceCard: View {
    @EnvironmentObject var m: AppModel
    /// 额度明细里已展开的账号名（默认折叠，明细可能很长）
    @State private var expandedDetail: Set<String> = []

    private static let cols = [GridItem(.flexible(), alignment: .leading),
                               GridItem(.flexible(), alignment: .leading)]

    var body: some View {
        // 口径与「账号积分总额」一致：平台已停用的账号（京东）不列，
        // 它的余额栏本来就是空的，列出来只会让合计对不上。
        let rows = m.activeAccounts()
        let cov = m.creditsCoverage()
        Card {
            VStack(alignment: .leading, spacing: 0) {
                CardTitle("账号余额与额度") {
                    Text(rows.isEmpty ? "尚未添加账号"
                         : (cov.counted == 0 ? "尚未查询到余额"
                            : "\(cov.counted) / \(cov.total) 个账号有数"))
                        .font(.system(size: 11.5))
                        .foregroundColor(Theme.textSub)
                }

                Text("各平台口径不同：积分 / 硬币 / 乐豆 / 云朵 / 云盘容量，单位跟在数值后面。")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.textFaint)
                    .padding(.top, 6)

                if rows.isEmpty {
                    Text("还没有账号，去「账号管理」添加后这里会显示余额。")
                        .font(.system(size: 12.5))
                        .foregroundColor(Theme.textSub)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 18)
                } else {
                    Divider().overlay(Theme.hairline).padding(.top, 14)

                    LazyVGrid(columns: BalanceCard.cols, spacing: 0) {
                        ForEach(rows) { acc in
                            balanceRow(acc)
                        }
                    }
                    .padding(.top, 6)

                    Text("「较上次」是本地快照推算（跨日才有结论），平台侧没有流水接口。")
                        .font(.system(size: 11))
                        .foregroundColor(Theme.textFaint)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 8)

                    detailSection
                }
            }
        }
    }

    /// 单行余额。阿里云盘这类**没有积分**的平台走 capacity（容量），
    /// 显示成「剩余 337 GB / 共 681 GB」而不是把 GB 塞进积分位。
    private func balanceRow(_ acc: Account) -> some View {
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
            if !deltaSuffix(acc).isEmpty {
                Text(deltaSuffix(acc))
                    .font(.system(size: 10.5))
                    .foregroundColor(Theme.textFaint)
                    .lineLimit(1)
            }
            Text(balanceText(acc))
                .font(.system(size: 12.5, weight: .medium))
                .foregroundColor(balanceColor(acc))
                .lineLimit(1)
        }
        .frame(height: 28)
    }

    /// 逐包额度明细：服务端只按**资源包周期**给已用量，这是能拿到的最细的消费明细。
    /// 只对真的返回了资源的平台出现（目前是 WorkBuddy 与 Trae）。
    @ViewBuilder
    private var detailSection: some View {
        let rows = m.enabledAccounts().filter {
            !((m.creditsByAccount[$0.name]?.packages) ?? []).isEmpty
        }
        if !rows.isEmpty {
            Divider().overlay(Theme.hairline).padding(.top, 14)

            HStack(spacing: 8) {
                Text("额度明细")
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundColor(Theme.textSub)
                Spacer(minLength: 8)
                Text("\(rows.count) 个账号有资源包，点击展开")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.textFaint)
            }
            .padding(.top, 12)
            .padding(.bottom, 2)

            VStack(spacing: 0) {
                ForEach(rows) { acc in
                    detailRow(acc)
                }
            }
        }
    }

    private func detailRow(_ acc: Account) -> some View {
        let info = m.creditsByAccount[acc.name]
        let packs = info?.packages ?? []
        let open = expandedDetail.contains(acc.name)
        return VStack(alignment: .leading, spacing: 0) {
            Button {
                if open { expandedDetail.remove(acc.name) } else { expandedDetail.insert(acc.name) }
            } label: {
                HStack(spacing: 8) {
                    PlatformAvatar(text: String(acc.platformLabel.prefix(1)),
                                   tint: Theme.textSub, bg: Theme.chipBG,
                                   size: 18, iconName: acc.platformIconName,
                                   dimmed: !acc.isEnabled)
                    Text(acc.name)
                        .font(.system(size: 12.5))
                        .foregroundColor(Theme.textBody)
                        .lineLimit(1)
                    Text("\(packs.count) 个资源包")
                        .font(.system(size: 11))
                        .foregroundColor(Theme.textFaint)
                    Spacer(minLength: 6)
                    Text("已用 \(Fmt.group(info?.packageUsedTotal ?? 0))")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(Theme.textSub)
                    Image(systemName: open ? "chevron.up" : "chevron.down")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(Theme.textFaint)
                }
                .frame(height: 30)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if open {
                VStack(spacing: 0) {
                    ForEach(Array(packs.enumerated()), id: \.offset) { _, p in
                        packageRow(p)
                    }
                }
                .padding(.leading, 26)
                .padding(.bottom, 8)
            }
            Divider().overlay(Theme.hairline)
        }
    }

    private func packageRow(_ p: AppModel.CreditPackage) -> some View {
        HStack(spacing: 10) {
            Text(p.name)
                .font(.system(size: 12))
                .foregroundColor(Theme.textBody)
                .lineLimit(1)
            if p.slice {
                // 切片包真的读到了当日切片，数字是当日口径，必须标出来
                SoftTag(text: "当日", fg: Theme.textSub, bg: Theme.chipBG)
            } else if !p.cycleEnd.isEmpty {
                Text("至 \(String(p.cycleEnd.prefix(10)))")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.textFaint)
            }
            Spacer(minLength: 8)
            Text("剩余 \(Fmt.group(p.remain ?? 0)) / \(Fmt.group(p.total ?? 0))")
                .font(.system(size: 12))
                .foregroundColor(Theme.text)
                .lineLimit(1)
            Text("已用 \(Fmt.group(p.used ?? 0))")
                .font(.system(size: 11.5))
                .foregroundColor((p.used ?? 0) > 0 ? Theme.textBody : Theme.textFaint)
                .frame(width: 92, alignment: .trailing)
        }
        .frame(height: 26)
    }

    /// 「较上次」后缀。本地快照推算，没有对照（首次运行 / 当天已比过）就返回空串。
    private func deltaSuffix(_ acc: Account) -> String {
        guard let c = m.creditsByAccount[acc.name], c.hasDelta, let d = c.delta else { return "" }
        return "较上次 \(Fmt.signed(d))"
    }

    /// 余额文案。三种情况：有积分余额 → 数字 + 单位；没有积分但有容量
    /// （阿里云盘）→ 容量；两者都没有 → 如实说"平台没给"。
    private func balanceText(_ acc: Account) -> String {
        guard let c = m.creditsByAccount[acc.name] else { return "—" }
        if let b = c.balance { return "\(Fmt.group(b)) \(c.unit)" }
        if let cap = c.capacity {
            let remain = cap.remain.map(Fmt.bytes) ?? "—"
            if let total = cap.total { return "剩余 \(remain) / 共 \(Fmt.bytes(total))" }
            return "剩余 \(remain)"
        }
        return c.ok ? "未提供" : "查询失败"
    }

    private func balanceColor(_ acc: Account) -> Color {
        guard let c = m.creditsByAccount[acc.name] else { return Theme.textFaint }
        if c.balance != nil || c.capacity != nil { return Theme.text }
        return c.ok ? Theme.textSub : Theme.accent
    }
}

// 柱状图（x 轴用 M/D，今天红色高亮）。
// 近 30 天时把 30 根柱 + 30 个日期标签硬塞进卡片宽度是塞不下的：柱宽会被下限
// 顶住 → 内容总宽超出容器 → 右边被直接裁掉，而且**滑不动**（之前就是这个毛病）。
// 所以改成：排不下就横向滚动 + 日期标签按天数稀疏化，打开时自动停在今天。
struct BarChart: View {
    let values: [Double]
    var highlightLast: Bool = true
    /// 每根柱的最小宽度。低于它就不再压窄，而是改为滚动 —— 压到 8pt 的柱子
    /// 既看不出高低、也点不准，不如让人滑一下。
    var minBarWidth: CGFloat = 20

    private let maxH: CGFloat = 78
    private let chartHeight: CGFloat = 118

    var body: some View {
        let n = max(values.count, 1)
        let days = DayTool.lastDays(n)
        let gap: CGFloat = n > 14 ? 6 : 12
        let needW = CGFloat(n) * minBarWidth + gap * CGFloat(n - 1)
        let stride = labelStride(n)

        GeometryReader { geo in
            // 装得下就铺满（柱宽自动变大，最宽 26pt）；装不下就按最小柱宽撑开滚动
            let contentW = max(geo.size.width, needW)
            let raw = (contentW - gap * CGFloat(n - 1)) / CGFloat(n)
            let barW = max(8, min(26, raw))

            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .bottom, spacing: gap) {
                        ForEach(values.indices, id: \.self) { i in
                            column(i, days: days, barW: barW, stride: stride).id(i)
                        }
                    }
                    .frame(width: contentW, alignment: .leading)
                }
                // 数据由旧到新排列，默认停在最右（今天），否则一进来先看到 30 天前
                .onAppear { scrollToToday(proxy, n) }
                .onChange(of: n) { _ in scrollToToday(proxy, n) }
            }
        }
        .frame(height: chartHeight)
    }

    private func scrollToToday(_ proxy: ScrollViewProxy, _ n: Int) {
        guard n > 1 else { return }
        DispatchQueue.main.async { proxy.scrollTo(n - 1, anchor: .trailing) }
    }

    /// 日期标签的稀疏步长：柱多了就隔几根标一个，否则标签会互相压在一起。
    private func labelStride(_ n: Int) -> Int {
        if n > 21 { return 5 }
        if n > 9 { return 2 }
        return 1
    }

    private func column(_ i: Int, days: [String], barW: CGFloat, stride: Int) -> some View {
        let maxV = max(values.max() ?? 0, 1)
        let isLast = i == values.count - 1 && highlightLast
        let showLabel = isLast || i % stride == 0
        return VStack(spacing: 8) {
            Spacer(minLength: 0)
            RoundedRectangle(cornerRadius: 4, style: .continuous)
                .fill(isLast ? Theme.accent : Theme.track)
                .frame(width: barW, height: max(22, maxH * CGFloat(values[i] / maxV)))
            // 不标的日子也要占位（用不换行空格），否则柱子的基线会参差不齐
            Text(showLabel ? label(i, days) : "\u{00A0}")
                .font(.system(size: 11.5, weight: isLast ? .semibold : .regular))
                .foregroundColor(isLast ? Theme.text : Theme.textSub)
                .fixedSize()
        }
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
    /// 「今日任务」子任务图标列。172 = 7 个图标 × 18 + 6 个间隔 × 4 + 余量：
    /// WorkBuddy 展开到 7 项（每日签到 + 成长中心六步）时正好一行放得下。
    private static let colTask: CGFloat = 172
    private static let colAction: CGFloat = 100
    private static let hpad: CGFloat = 15

    var body: some View {
        let rows = filteredRows()
        // 口径统一用 activeAccounts()：平台已停用的账号（京东）不参与签到，
        // 放进这张任务表只会永远占着"待处理"。
        let live = m.activeAccounts()
        let done = m.todaySummary().done
        let pending = live.filter { m.accStatus($0.name).state == "pending" }.count

        Card(padding: nil) {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    Text("账号签到任务")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    SoftTag(text: "\(live.count) 个账号")
                    Spacer(minLength: 8)
                    SegmentedControl(
                        items: [.init("全部", "全部", badge: "\(live.count)"),
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
                    Text("今日任务")
                        .frame(width: TaskTableCard.colTask, alignment: .leading)
                        .help("该平台今天各子动作的结果。只有一个动作的平台留空（与「状态」列重复）")
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
                                taskW: TaskTableCard.colTask,
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
        let en = m.activeAccounts()
        switch filter {
        case "已完成":
            return en.filter { m.accStatus($0.name).state == "done" }
        case "待处理":
            return en.filter { m.accStatus($0.name).state != "done" }
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
    let taskW: CGFloat
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

            // 单动作平台 / 平台已停用的账号这里恒为空：规则在 AppModel.taskIcons 里
            TaskIconStrip(tasks: m.taskIcons(acc.name))
                .frame(width: taskW, alignment: .leading)

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
        // 阿里云盘这类**没有积分**的平台：签到给的是容量/会员，写 +0 会让人
        // 误以为"签了却没给"，用破折号如实表示"这一栏对它不适用"。
        if m.creditsByAccount[acc.name]?.capacity != nil { return "—" }
        guard acc.isEnabled, st.state == "done" else { return "+0" }
        // 今日已签到（本次跳过）没有新增积分，用破折号而不是 +0，避免看起来像"签了但没给"
        if st.hit?.skipped == true { return "—" }
        return "+\(Int(st.hit?.credits ?? 0))"
    }

    private func creditColor(_ st: (state: String, hit: LogHit?)) -> Color {
        if m.creditsByAccount[acc.name]?.capacity != nil { return Theme.textSub }
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
                .help("只重试「\(acc.name)」这一个账号")
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

// 签到结果与异常提示。
// 只留"任务表里看不到的东西"：需要处理的条目入口 + 本次运行日志。
// 刻意**不再重复**今日获得积分（总览卡里有）和逐账号状态（账号签到任务表里有）。
struct ResultCard: View {
    @EnvironmentObject var m: AppModel
    @State private var showLog = false
    /// 正在查看哪一条异常的详情（存账号名而不是条目对象：
    /// 重试成功后该账号会从异常列表里消失，用对象的话弹窗会瞬间失联）
    @State private var detailName: String? = nil

    /// 一次最多列几条，剩下的折叠成一行 —— 免得这张卡自己变成一堵墙
    private static let maxRows = 4

    var body: some View {
        let sum = m.todaySummary()
        let issues = m.issueItems()
        let oks = m.parsed.todayOK(DayTool.today()).count
        return Card {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Text("签到结果与异常提示")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Text(headline(sum))
                        .font(.system(size: 11.5))
                        .foregroundColor(sum.fail > 0 ? Theme.accent
                                         : (sum.restricted > 0 ? Theme.warn : Theme.textSub))
                }

                if issues.isEmpty {
                    if oks > 0 {
                        okRow(oks: oks)
                    } else {
                        Text("暂无签到记录：点顶部「手动签到」立即执行，或等待自动签到按计划执行。")
                            .font(.system(size: 12.5))
                            .foregroundColor(Theme.textSub)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 16)
                    }
                } else {
                    ForEach(Array(issues.prefix(ResultCard.maxRows))) { item in
                        issueRow(item)
                    }
                    if issues.count > ResultCard.maxRows {
                        Text("另有 \(issues.count - ResultCard.maxRows) 条异常未列出，"
                             + "可在上方「账号签到任务」里逐个重试。")
                            .font(.system(size: 11.5))
                            .foregroundColor(Theme.textSub)
                    }
                }

                Button { showLog.toggle() } label: {
                    Text(showLog ? "收起运行日志" : "查看运行日志")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(Theme.textSub)
                }
                .buttonStyle(.plain)

                if showLog { logBox }
            }
        }
        .sheet(isPresented: Binding(get: { detailName != nil },
                                    set: { if !$0 { detailName = nil } })) {
            IssueDetailSheet(name: detailName ?? "").environmentObject(m)
        }
    }

    /// 一条异常：账号名 + 一行报错摘要 + 「详情」。
    /// 整行可点 —— 报错原文往往比一行的宽度长，只有点进去才看得全。
    private func issueRow(_ item: CheckinIssue) -> some View {
        let color = item.isRestricted ? Theme.warn : Theme.accent
        let bg = item.isRestricted ? Theme.warnSoft : Theme.accentSoft
        return Button { detailName = item.name } label: {
            HStack(spacing: 10) {
                Image(systemName: item.isRestricted ? "exclamationmark.triangle.fill" : "xmark.circle.fill")
                    .font(.system(size: 12))
                    .foregroundColor(color)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name)
                        .font(.system(size: 12.5, weight: .semibold))
                        .foregroundColor(Theme.text)
                        .lineLimit(1)
                    Text(item.hit.msg.isEmpty ? "签到失败（无详细信息）" : item.hit.msg)
                        .font(.system(size: 11.5))
                        .foregroundColor(Theme.textSub)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                HStack(spacing: 3) {
                    Text("详情").font(.system(size: 12.5, weight: .semibold))
                    Image(systemName: "chevron.right").font(.system(size: 9, weight: .semibold))
                }
                .foregroundColor(color)
            }
            .padding(.horizontal, 12)
            .frame(height: 48)
            .background(bg)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("查看「\(item.name)」的完整报错并重试")
    }

    private func okRow(oks: Int) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 12)).foregroundColor(Theme.success)
            Text("\(oks) 个账号签到成功，今日无异常")
                .font(.system(size: 12.5)).foregroundColor(Theme.textBody)
            Spacer(minLength: 8)
        }
        .padding(.horizontal, 12)
        .frame(height: 40)
        .background(Theme.successSoft)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private var logBox: some View {
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

    private func headline(_ sum: (done: Int, fail: Int, pending: Int, credit: Double, total: Int, restricted: Int)) -> String {
        if sum.fail > 0 { return "\(sum.fail) 条待处理" }
        if sum.restricted > 0 { return "\(sum.restricted) 条平台受限" }
        return "今日正常"
    }
}

// MARK: - 单条异常详情
// 点「详情」弹出，只针对**这一个账号**：完整报错原文 + 可能原因 + 重试入口。
// 旧的「立即处理」直接跳到账号管理页是个死胡同 —— 那一页只有重命名和删除，
// 既看不到报错，也没有重试按钮，用户跳过去什么也做不了。
struct IssueDetailSheet: View {
    @EnvironmentObject var m: AppModel
    let name: String
    @Environment(\.dismiss) private var dismiss

    private var acc: Account? { m.accounts.first { $0.name == name } }
    private var st: (state: String, hit: LogHit?) { m.accStatus(name) }

    var body: some View {
        let st = self.st
        let diag = diagnose()
        VStack(alignment: .leading, spacing: 0) {
            header(state: st.state)
            Divider().overlay(Theme.hairline)

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    // 重试成功就在现场给出结果，不用去别处核对
                    if st.state == "done" {
                        banner(icon: "checkmark.circle.fill",
                               title: "已解决",
                               text: "「\(name)」现在的状态是已签到。",
                               color: Theme.success, bg: Theme.successSoft)
                    }

                    section("报错详情") {
                        Text(st.hit?.msg.isEmpty == false ? (st.hit?.msg ?? "") : "（平台未返回具体原因）")
                            .font(.system(size: 12.5, design: .monospaced))
                            .foregroundColor(Theme.textBody)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                            .background(Theme.chipBG)
                            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        HStack(spacing: 6) {
                            Image(systemName: "clock").font(.system(size: 10))
                            Text("发生于 \(whenText)")
                        }
                        .font(.system(size: 11.5))
                        .foregroundColor(Theme.textSub)
                    }

                    section("可能原因") {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(diag.title)
                                .font(.system(size: 12.5, weight: .semibold))
                                .foregroundColor(Theme.text)
                            Text(diag.detail)
                                .font(.system(size: 12))
                                .foregroundColor(Theme.textSub)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }

                    section("账号信息") {
                        infoRow("平台", acc?.app.isEmpty == false ? (acc?.app ?? "") : (acc?.type ?? "—"))
                        infoRow("登录凭证", acc?.credentialSummary() ?? "账号已不存在")
                        infoRow("自动签到", (acc?.isEnabled ?? false) ? "已启用" : "已停用")
                        if acc?.isRetired == true {
                            infoRow("平台状态", "已停用（平台侧签到链路下线，本工具不再请求）")
                        }
                        infoRow("连续签到",
                                "\(ParsedLogs.streak(for: name, logs: m.parsed, startDate: DayTool.today())) 天")
                    }
                }
                .padding(20)
            }

            Divider().overlay(Theme.hairline)
            footer(diag: diag)
        }
        .frame(width: 520, height: 470)
        .background(Theme.pageBG)
    }

    // MARK: 头部
    private func header(state: String) -> some View {
        HStack(spacing: 10) {
            PlatformAvatar(text: letter, tint: avatarTint(state), bg: avatarBG(state),
                           size: 34, iconName: acc?.platformIconName,
                           dimmed: !(acc?.isEnabled ?? true))
            VStack(alignment: .leading, spacing: 3) {
                Text(name).font(.system(size: 16, weight: .semibold)).foregroundColor(Theme.text)
                Text(pillText(state))
                    .font(.system(size: 11.5))
                    .foregroundColor(pillColor(state))
            }
            Spacer(minLength: 8)
            Button { dismiss() } label: {
                Image(systemName: "xmark").font(.system(size: 12, weight: .medium))
                    .foregroundColor(Theme.textSub)
                    .frame(width: 26, height: 26)
                    .background(Theme.chipBG)
                    .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 15)
    }

    // MARK: 底部操作
    private func footer(diag: (title: String, detail: String, fix: CheckinIssue.Fix)) -> some View {
        let busy = m.singleSignName != nil || m.signRunning
        return HStack(spacing: 10) {
            Button { copyError() } label: { Text("复制报错信息") }
                .buttonStyle(GhostButtonStyle(fg: Theme.textBody))
            Spacer(minLength: 8)
            if diag.fix == .relogin { reloginButton }
            // 平台受限 / 已恢复：重试没有意义，主按钮直接是"知道了"
            if diag.fix == .none {
                Button { dismiss() } label: { Text("知道了") }
                    .buttonStyle(PrimaryButtonStyle())
            } else {
                Button { m.runSingleSign(name) } label: {
                    Text(m.singleSignName == name ? "重试中…" : "重试签到")
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(busy)
                .help("只重试「\(name)」这一个账号")
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    /// 凭证失效时的"正确入口"。能在**本机就地解决**的（内置浏览器 / 读本机登录态）
    /// 就不要把人丢到账号管理页 —— 那一页原来没有任何重新登录入口，用户到了那儿只能干瞪眼。
    /// 各平台重新登录的方式不同，路由统一走 `AppModel.relogin`，与账号行的行内按钮同一条路，
    /// 避免两处判断再次跑偏。
    @ViewBuilder
    private var reloginButton: some View {
        if let a = acc, m.canRelogin(a) {
            Button { m.relogin(a) } label: {
                Text(reloginLabel(a))
            }
            .buttonStyle(GhostButtonStyle(fg: Theme.accent))
            .disabled(m.loginRunning)
            .help("更新的是「\(name)」自己的登录态，不会新增账号")
        } else {
            // 兜底：没有本机登录路径的平台，跳账号页并高亮那一行，
            // 而不是打开"新增账号"面板（那是在要求用户重新填一遍）。
            Button {
                dismiss()
                m.selectedPage = .accounts      // 先切页、再设高亮：顺序反了会被清掉
                m.focusAccount = name
            } label: { Text("去账号页更新") }
            .buttonStyle(GhostButtonStyle(fg: Theme.accent))
        }
    }

    /// 按钮文案必须跟**真实动作**一致（路由与判断都在 `AppModel.workbuddyReloginUsesOAuth`）：
    /// 桌面端把凭据加密后，"读取本机登录态"这条路已经走不通，实际动作是扫码 ——
    /// 还写"读取本机登录态"就是让用户按了之后对着一个扫码页发愣。
    private func reloginLabel(_ a: Account) -> String {
        if a.isWorkBuddy { return m.workbuddyReloginUsesOAuth(a) ? "重新扫码登录" : "读取本机登录态" }
        if a.isTrae { return "重新登录 Trae" }
        return "重新登录（打开浏览器）"
    }

    // MARK: 小部件
    private func section(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 11.5, weight: .semibold))
                .foregroundColor(Theme.textSub)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func infoRow(_ k: String, _ v: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(k).font(.system(size: 12)).foregroundColor(Theme.textSub).frame(width: 62, alignment: .leading)
            Text(v).font(.system(size: 12.5)).foregroundColor(Theme.textBody)
            Spacer(minLength: 0)
        }
    }

    private func banner(icon: String, title: String, text: String, color: Color, bg: Color) -> some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: icon).font(.system(size: 12)).foregroundColor(color)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 12.5, weight: .semibold)).foregroundColor(color)
                Text(text).font(.system(size: 12)).foregroundColor(Theme.textBody)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(bg)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    // MARK: 取值
    private func diagnose() -> (title: String, detail: String, fix: CheckinIssue.Fix) {
        let state = st.state
        let hit = st.hit ?? LogHit(time: "", date: DayTool.today(), msg: "", credits: 0)
        if state == "done" {
            return ("已恢复正常", "这个账号现在的状态是已签到，不需要再处理。", .none)
        }
        return CheckinIssue(state: state, name: name, hit: hit).diagnose()
    }

    private var whenText: String {
        guard let h = st.hit, !h.time.isEmpty else { return "—" }
        return h.date == DayTool.today() ? "今天 \(h.time)" : "\(h.date) \(h.time)"
    }

    private var letter: String {
        let src = (acc?.app.isEmpty ?? true) ? name : (acc?.app ?? name)
        return String(src.prefix(1)).uppercased()
    }

    private func pillText(_ s: String) -> String {
        switch s {
        case "done": return "今日已签到"
        case "fail": return "今日签到失败"
        case "restricted": return "平台受限（账号正常）"
        default: return "今日尚未签到"
        }
    }
    private func pillColor(_ s: String) -> Color {
        switch s {
        case "done": return Theme.success
        case "fail": return Theme.accent
        case "restricted": return Theme.warn
        default: return Theme.textSub
        }
    }
    private func avatarTint(_ s: String) -> Color {
        s == "fail" ? Theme.accent : (s == "restricted" ? Theme.warn : Theme.text)
    }
    private func avatarBG(_ s: String) -> Color {
        s == "fail" ? Theme.accentSoft : (s == "restricted" ? Theme.warnSoft : Theme.chipBG)
    }

    private func copyError() {
        let acc = self.acc
        let text = """
        账号：\(name)
        平台：\(acc?.app ?? "—")（type: \(acc?.type ?? "—")）
        状态：\(pillText(st.state))
        时间：\(whenText)
        报错：\(st.hit?.msg ?? "（无）")
        """
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
        m.showToast("报错信息已复制", .success)
    }
}
