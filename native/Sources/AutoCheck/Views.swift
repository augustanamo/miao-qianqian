import SwiftUI
import AppKit

// MARK: - 侧边栏
struct SidebarView: View {
    @EnvironmentObject var m: AppModel

    /// 侧边栏顶部 Logo：优先使用应用图标（Resources/AppIcon.icns），兜底系统应用图标/占位符
    private static func appIcon() -> NSImage {
        if let icon = NSImage(named: "AppIcon") { return icon }
        if let icon = NSImage(named: NSImage.applicationIconName) { return icon }
        return NSImage(systemSymbolName: "checkmark.seal.fill", accessibilityDescription: nil) ?? NSImage()
    }

    var body: some View {
        VStack(spacing: 0) {
            // Logo
            HStack(spacing: 10) {
                Image(nsImage: SidebarView.appIcon())
                    .resizable()
                    .frame(width: 30, height: 30)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                VStack(alignment: .leading, spacing: 1) {
                    Text("喵签签").font(.system(size: 16, weight: .bold)).foregroundColor(.white)
                    Text("自动签到助手").font(.system(size: 11)).foregroundColor(Theme.sidebarMuted)
                }
                Spacer()
            }
            .padding(.horizontal, 18).padding(.top, 20).padding(.bottom, 24)

            // 导航
            VStack(spacing: 4) {
                ForEach(AppModel.Page.allCases) { page in
                    Button {
                        m.selectedPage = page
                    } label: {
                        HStack(spacing: 12) {
                            Text(page.rawValue)
                                .font(.system(size: 14, weight: m.selectedPage == page ? .semibold : .regular))
                                .foregroundColor(m.selectedPage == page ? .white : Color(white: 0.75))
                            Spacer()
                        }
                        .padding(.horizontal, 12).padding(.vertical, 10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                        .background(m.selectedPage == page ? Color.white.opacity(0.10) : Color.clear)
                        .cornerRadius(6)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 10)

            Spacer()

            // 今日进度
            VStack(alignment: .leading, spacing: 8) {
                let sum = m.todaySummary()
                HStack {
                    Text("今日进度").font(.system(size: 12, weight: .semibold)).foregroundColor(Theme.sidebarMuted)
                    Spacer()
                    Text(sum.total > 0 ? "\(Int(Double(sum.done) / Double(sum.total) * 100))%" : "0%")
                        .font(.system(size: 12, weight: .bold)).foregroundColor(.white)
                }
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Color.white.opacity(0.12))
                        Capsule().fill(Theme.success).frame(width: geo.size.width * progress)
                    }
                }
                .frame(height: 6)
                Text(sum.pending > 0 ? "还有 \(sum.pending) 个账号待签到" : "全部账号已处理")
                    .font(.system(size: 11)).foregroundColor(Theme.sidebarMuted)
            }
            .padding(14)
            .background(Theme.sidebarCard)
            .cornerRadius(Theme.corner)
            .padding(.horizontal, 14)

            Text("免费版 · v2.4.1").font(.system(size: 11)).foregroundColor(Theme.sidebarMuted).padding(.vertical, 14)
        }
        .background(Theme.sidebar)
    }

    private var progress: CGFloat {
        let sum = m.todaySummary()
        guard sum.total > 0 else { return 0 }
        return CGFloat(Double(sum.done) / Double(sum.total))
    }
}

// MARK: - 顶部 Toast
struct ToastOverlay: View {
    let toast: Toast?
    var body: some View {
        if let toast = toast {
            VStack {
                Spacer()
                HStack(spacing: 8) {
                    Circle().fill(color(for: toast.kind)).frame(width: 8, height: 8)
                    Text(toast.text).font(.system(size: 13)).foregroundColor(Theme.textDeep)
                }
                .padding(.horizontal, 16).padding(.vertical, 10)
                .background(Color.white)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.cardBorder, lineWidth: 1))
                .cornerRadius(8)
                .shadow(color: .black.opacity(0.12), radius: 10, y: 3)
                .padding(.bottom, 24)
            }
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }
    private func color(for k: Toast.Kind) -> Color {
        switch k { case .info: return Theme.primary; case .success: return Theme.success; case .error: return Theme.danger }
    }
}

// MARK: - 柱状图
struct BarChart: View {
    let values: [Double]
    let highlightLast: Bool
    var body: some View {
        let maxV = max(values.max() ?? 0, 1)
        HStack(alignment: .bottom, spacing: 8) {
            ForEach(values.indices, id: \.self) { i in
                let isLast = i == values.count - 1 && highlightLast
                VStack(spacing: 3) {
                    Spacer(minLength: 0)
                    RoundedRectangle(cornerRadius: 2)
                        .fill(isLast ? Theme.danger : Theme.grayBar.opacity(0.55))
                        .frame(width: 10, height: max(4, 80 * CGFloat(values[i] / maxV)))
                    Text(shortLabel(i: i, count: values.count))
                        .font(.system(size: 10, weight: isLast ? .bold : .regular))
                        .foregroundColor(isLast ? Theme.danger : Theme.textMuted)
                        .lineLimit(1)
                }
            }
        }
        .frame(height: 104, alignment: .bottom)
    }
    private func shortLabel(i: Int, count: Int) -> String {
        // 7 天 -> 中文星期简写（周一~周日）；30 天 -> 间隔显示日期
        let days = DayTool.lastDays(count)
        guard i < days.count else { return "" }
        let d = days[i]
        if count <= 7 {
            let date = DayTool.formatter.date(from: d)
            let f = DateFormatter()
            f.locale = Locale(identifier: "zh_CN")
            f.dateFormat = "EEE"
            return (date.map { f.string(from: $0) } ?? "?")
        }
        return (i % 6 == 0 || days.count <= 7) ? String(d.suffix(2)) : ""
    }
}

// MARK: - 01 签到
struct CheckinView: View {
    @EnvironmentObject var m: AppModel
    @State private var chartDays: Int = 7

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                let sum = m.todaySummary()
                PageHeader("今日签到", subtitle: "已连续签到 \(m.overallStreak()) 天 · 今日 \(sum.done)/\(sum.total) 已完成签到") {
                    AnyView(
                        Button { m.runSign() } label: {
                            ZStack {
                                Label("手动签到", systemImage: "bolt.fill")
                                    .opacity(m.signRunning ? 0 : 1)
                                if m.signRunning {
                                    HStack(spacing: 6) {
                                        ProgressView().controlSize(.small)
                                        Text("签到中")
                                    }
                                }
                            }
                            .frame(width: 110)
                        }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(m.signRunning)
                    )
                }

                HStack(spacing: 16) {
                    CheckinHeroCard(sum: sum).frame(maxHeight: .infinity)
                    CreditCard(chartDays: $chartDays)
                }

                TaskTableCard()

                HStack(alignment: .top, spacing: 16) {
                    AutoSignCard()
                    ResultCard()
                }
            }
            .padding(20)
        }
        .background(Theme.mainBg)
    }
}

// 签到战绩深色卡
struct CheckinHeroCard: View {
    @EnvironmentObject var m: AppModel
    let sum: (done: Int, fail: Int, pending: Int, credit: Double, total: Int)
    var body: some View {
        Card(padding: 22, color: Theme.darkCard) {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text(m.launchd.installed ? "自动签到已开启" : "自动签到未开启")
                        .font(.system(size: 12, weight: .medium)).foregroundColor(Theme.textMain)
                    Spacer()
                    Text("今日签到").font(.system(size: 12)).foregroundColor(Theme.sidebarMuted)
                }
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text("\(sum.done)/\(sum.total)")
                        .font(.system(size: 32, weight: .bold)).foregroundColor(.white)
                    Text("个账号今日已完成签到").font(.system(size: 13)).foregroundColor(Theme.sidebarMuted)
                }
                HStack(spacing: 22) {
                    metric("今日积分", value: sum.credit > 0 ? "+\(Int(sum.credit))" : "0", color: .white)
                    metric("连续签到", value: "\(m.overallStreak()) 天", color: .white)
                }
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Color.white.opacity(0.12))
                        Capsule().fill(Theme.success)
                            .frame(width: geo.size.width * progress)
                    }
                }
                .frame(height: 8)
                HStack {
                    Text("今日累计积分 +\(Int(sum.credit))")
                        .font(.system(size: 11)).foregroundColor(Theme.success)
                    Spacer()
                }
            }
        }
    }
    private var progress: CGFloat {
        guard sum.total > 0 else { return 0 }
        return min(1, CGFloat(Double(sum.done) / Double(sum.total)))
    }
    private func metric(_ t: String, value: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.system(size: 18, weight: .bold)).foregroundColor(color)
            Text(t).font(.system(size: 11)).foregroundColor(Theme.sidebarMuted)
        }
    }
}

// 积分状态卡
struct CreditCard: View {
    @EnvironmentObject var m: AppModel
    @Binding var chartDays: Int
    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                CardHeader("积分状态") {
                    AnyView(
                        Picker("", selection: $chartDays) {
                            Text("近7天").tag(7)
                            Text("近30天").tag(30)
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 150)
                    )
                }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(String(format: "%.0f", m.totalRemainingCredits()))
                        .font(.system(size: 28, weight: .bold)).foregroundColor(Theme.textDeep)
                    Text("累计积分").font(.system(size: 12)).foregroundColor(Theme.textMuted)
                    Spacer()
                    HStack(spacing: 4) {
                        Text("本周")
                        Text("+\(Int(DayTool.weekCredits(logs: m.parsed)))")
                    }
                    .font(.system(size: 11, weight: .semibold)).foregroundColor(Theme.success)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Theme.success.opacity(0.15))
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.success.opacity(0.4), lineWidth: 1))
                    )
                    .fixedSize()
                }
                BarChart(values: m.chartData(days: chartDays), highlightLast: true)
                HStack(spacing: 4) {
                    Text("近 \(chartDays) 天累计")
                        .font(.system(size: 11)).foregroundColor(Theme.textMuted)
                    Text("+\(Int(m.chartData(days: chartDays).reduce(0, +))) 积分")
                        .font(.system(size: 11, weight: .semibold)).foregroundColor(Theme.textDeep)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 2)
            }
        }
    }
}

// 账号签到任务表
struct TaskTableCard: View {
    @EnvironmentObject var m: AppModel
    @State private var filter: String = "全部"

    var body: some View {
        Card {
            VStack(spacing: 10) {
                CardHeader("账号签到任务", subtitle: "共 \(m.enabledAccounts().count) 个账号") {
                    AnyView(
                        Picker("", selection: $filter) {
                            Text("全部").tag("全部")
                            Text("已完成").tag("已完成")
                            Text("待处理").tag("待处理")
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 240)
                    )
                }
                // 表头
                HStack(spacing: 0) {
                    header("平台账号", width: 190)
                    header("最近签到", width: 150)
                    header("今日积分", width: 90)
                    header("状态", width: 110)
                    header("操作", width: 150, trailing: true)
                }
                .padding(.bottom, 4)
                Divider()
                let rows = filteredRows()
                if rows.isEmpty {
                    HStack {
                        Spacer()
                        Text("暂无匹配的账号任务").font(.system(size: 13)).foregroundColor(Theme.textMuted).padding(.vertical, 24)
                        Spacer()
                    }
                } else {
                    ForEach(rows, id: \.name) { acc in
                        TaskRow(acc: acc)
                        if acc.name != rows.last?.name { Divider().opacity(0.5) }
                    }
                }
            }
        }
    }
    private func header(_ t: String, width: CGFloat, trailing: Bool = false) -> some View {
        HStack {
            if trailing { Spacer() }
            Text(t).font(.system(size: 11, weight: .semibold)).foregroundColor(Theme.textMuted)
            if !trailing { Spacer() }
        }
        .frame(width: width)
    }
    private func filteredRows() -> [Account] {
        let en = m.enabledAccounts()
        switch filter {
        case "已完成":
            let okNames = Set(m.parsed.todayOK(DayTool.today()).keys)
            return en.filter { okNames.contains($0.name) }
        case "待处理":
            let okNames = m.parsed.todayOK(DayTool.today())
            let failNames = m.parsed.todayFail(DayTool.today())
            return en.filter { okNames[$0.name] == nil && failNames[$0.name] == nil }
        default:
            return en
        }
    }
}

struct TaskRow: View {
    @EnvironmentObject var m: AppModel
    let acc: Account
    @State private var hovering = false

    var body: some View {
        let st = m.accStatus(acc.name)
        let credit = (st.state == "done") ? (st.hit?.credits ?? 0) : 0.0
        HStack(spacing: 0) {
            HStack(spacing: 8) {
                ZStack {
                    RoundedRectangle(cornerRadius: 5).fill(Theme.sidebar).frame(width: 30, height: 30)
                    Text(acc.platformLabel.prefix(2)).font(.system(size: 11, weight: .bold)).foregroundColor(.white)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(acc.name).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.textDeep)
                    Text(acc.platformLabel).font(.system(size: 11)).foregroundColor(Theme.textMuted)
                }
            }
            .frame(width: 190, alignment: .leading)

            Text(recentText(st: st))
                .font(.system(size: 12)).foregroundColor(Theme.textMuted)
                .frame(width: 150, alignment: .leading)

            Text(signText)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(signColor)
                .frame(width: 90, alignment: .leading)

            statusPill(st.state)
                .frame(width: 110, alignment: .leading)

            HStack {
                Spacer()
                actionButton(st.state)
            }
            .frame(width: 150)
        }
        .padding(.vertical, 6)
        .background(hovering ? Color.black.opacity(0.03) : Color.clear)
        .onHover { hovering = $0 }
    }
    private var signText: String {
        let st = m.accStatus(acc.name)
        if st.state == "done" { return st.hit.map { "+\(Int($0.credits))" } ?? "—" }
        return "—"
    }
    private var signColor: Color {
        m.accStatus(acc.name).state == "done" ? Theme.success : Theme.textMuted
    }
    private func recentText(st: (state: String, hit: LogHit?)) -> String {
        if st.state == "pending" {
            if let last = m.parsed.lastHit(name: acc.name) { return "上次 \(last.date) \(last.time)" }
            return "尚未签到"
        }
        return st.hit.map { "今天 \($0.time)" } ?? "—"
    }
    @ViewBuilder
    private func statusPill(_ state: String) -> some View {
        switch state {
        case "done":
            Pill(text: "已完成", fg: Theme.success, bg: Theme.success.opacity(0.12))
        case "fail":
            Pill(text: "签到失败", fg: Theme.danger, bg: Theme.danger.opacity(0.12))
        default:
            Pill(text: "待签到", fg: Theme.textMuted, bg: Theme.grayBar.opacity(0.25))
        }
    }
    @ViewBuilder
    private func actionButton(_ state: String) -> some View {
        let isSelfSigning = m.singleSignName == acc.name
        switch state {
        case "done":
            Text("已签到").font(.system(size: 12)).foregroundColor(Theme.textMuted).opacity(0.6)
        case "fail":
            Button { m.runSingleSign(acc.name) } label: {
                ZStack {
                    Text("重试").opacity(isSelfSigning ? 0 : 1)
                    if isSelfSigning {
                        HStack(spacing: 4) {
                            ProgressView().controlSize(.small)
                            Text("重试中")
                        }
                    }
                }
                .frame(width: 64)
            }
            .buttonStyle(GhostButtonStyle(color: Theme.danger))
            .disabled(m.signRunning || m.singleSignName != nil)
        default:
            Button { m.runSingleSign(acc.name) } label: {
                ZStack {
                    Text("签到").opacity(isSelfSigning ? 0 : 1)
                    if isSelfSigning {
                        HStack(spacing: 4) {
                            ProgressView().controlSize(.small)
                            Text("签到中")
                        }
                    }
                }
                .frame(width: 64)
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(m.signRunning || m.singleSignName != nil)
        }
    }
}

// 自动签到开关卡
struct AutoSignCard: View {
    @EnvironmentObject var m: AppModel
    @State private var showDetail = false

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                CardHeader("自动签到") {
                    AnyView(
                        Toggle("", isOn: Binding(get: { m.launchd.installed }, set: { m.autoToggle($0) }))
                            .toggleStyle(.switch)
                            .tint(Theme.primary)
                            .labelsHidden()
                            .overlay(
                                RoundedRectangle(cornerRadius: 10, style: .continuous)
                                    .stroke(m.launchd.installed ? Color.clear : Theme.textMuted.opacity(0.6), lineWidth: 1)
                            )
                    )
                }
                HStack(spacing: 6) {
                    if m.launchd.installed {
                        StatusDot(color: Theme.success, size: 8)
                        Text("每天 \(String(format: "%02d", m.launchd.hour)):\(String(format: "%02d", m.launchd.minute)) 自动执行 · 距下次 \(m.countdown())")
                            .font(.system(size: 12)).foregroundColor(Theme.textDeep)
                    } else {
                        Text("自动签到未开启，开启后按时间自动执行").font(.system(size: 12)).foregroundColor(Theme.textMuted)
                    }
                    Spacer()
                    Button { m.selectedPage = .settings } label: { Text("调整计划").font(.system(size: 11)).foregroundColor(Theme.primary) }
                        .buttonStyle(.plain)
                }
                if showDetail {
                    Divider()
                    Text("计划：\(m.pref.times.map { String(format: "%02d:%02d", $0[0], $0[1]) }.joined(separator: " / ")) · 星期 \(m.pref.weekdays.sorted().map{ "\($0)" }.joined(separator: "、")) · 错峰 \(m.pref.staggerMinutes) 分")
                        .font(.system(size: 11)).foregroundColor(Theme.textMuted)
                }
            }
        }
    }
}

// 签到结果与异常卡
struct ResultCard: View {
    @EnvironmentObject var m: AppModel
    @State private var showDetail = false
    @State private var showLog = false

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    CardHeader("签到结果与异常") { AnyView(EmptyView()) }
                    Spacer()
                    Text("\(m.todaySummary().fail) 条失败 · \(m.todaySummary().done) 成功")
                        .font(.system(size: 11))
                        .foregroundColor(m.todaySummary().fail > 0 ? Theme.danger : Theme.success)
                }
                // 失败横幅
                let reds = m.redBanners()
                ForEach(reds, id: \.self) { banner in
                    bannerRow(text: banner, color: Theme.danger, bg: Theme.danger.opacity(0.08), action: "立即处理") {
                        m.selectedPage = .accounts
                    }
                }
                // 成功横幅
                let green = m.greenText()
                if !green.isEmpty {
                    bannerRow(text: green, color: Theme.success, bg: Theme.success.opacity(0.10), action: "查看详情") {
                        showLog.toggle()
                    }
                }
                if reds.isEmpty && green.isEmpty {
                    HStack {
                        Spacer()
                        Text("暂无签到记录，点击上方「手动签到」开始").font(.system(size: 12)).foregroundColor(Theme.textMuted).padding(.vertical, 12)
                        Spacer()
                    }
                }
                if showLog {
                    Divider()
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 2) {
                            ForEach(m.runLines, id: \.self) { line in Text(line).font(.system(size: 10, design: .monospaced)) }
                        }
                    }
                    .frame(maxHeight: 120)
                }
            }
        }
    }
    private func bannerRow(text: String, color: Color, bg: Color, action: String, doAction: @escaping () -> Void) -> some View {
        HStack(spacing: 10) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(text).font(.system(size: 12)).foregroundColor(Theme.textDeep).lineLimit(1)
            Spacer()
            Button(action: doAction, label: { Text(action).font(.system(size: 11, weight: .semibold)).foregroundColor(color) })
                .buttonStyle(.plain)
        }
        .padding(10)
        .background(bg)
        .cornerRadius(6)
    }
}

// MARK: - 02 账号管理
struct AccountsView: View {
    @EnvironmentObject var m: AppModel
    @State private var search: String = ""
    @State private var filter: String = "全部"
    @State private var showImport = false

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                PageHeader("账号管理", subtitle: "管理已登录账号的健康状态与签到开关") {
                    AnyView(EmptyView())
                }

                // 顶栏工具
                HStack(spacing: 10) {
                    HStack(spacing: 6) {
                        Image(systemName: "magnifyingglass").font(.system(size: 12)).foregroundColor(Theme.textMuted)
                        TextField("搜索平台 / 账号名", text: $search)
                            .textFieldStyle(.plain)
                            .font(.system(size: 13))
                    }
                    .padding(.horizontal, 10).padding(.vertical, 7)
                    .background(Color.white)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.cardBorder, lineWidth: 1))
                    .frame(width: 220)

                    filterCapsules

                    Spacer()

                    Button { showImport = true } label: { Label("批量导入", systemImage: "square.and.arrow.down") }
                        .buttonStyle(GhostButtonStyle())
                    Button { m.showingAddPanel = true } label: { Label("新增账号", systemImage: "plus") }
                        .buttonStyle(PrimaryButtonStyle())
                }

                HStack(alignment: .top, spacing: 16) {
                    // 账号列表
                    let rows = filteredAccounts()
                    Card {
                        VStack(alignment: .leading, spacing: 2) {
                            if rows.isEmpty {
                                HStack {
                                    Spacer()
                                    Text("没有匹配的账号").font(.system(size: 13)).foregroundColor(Theme.textMuted).padding(.vertical, 30)
                                    Spacer()
                                }
                            } else {
                                ForEach(rows) { acc in
                                    AccountRowView(acc: acc)
                                    if acc.id != rows.last?.id { Divider().opacity(0.5) }
                                }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity)

                    if m.showingAddPanel {
                        AddAccountPanel()
                            .frame(width: 360)
                            .transition(.move(edge: .trailing).combined(with: .opacity))
                    }
                }

                // 今日进度摘要
                Card {
                    let sum = m.todaySummary()
                    HStack(spacing: 16) {
                        Text("今日进度").font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.textDeep)
                        ZStack(alignment: .leading) {
                            Capsule().fill(Theme.grayBar.opacity(0.3))
                            Capsule().fill(Theme.success).frame(width: 160 * progress(sum))
                        }
                        .frame(width: 160, height: 8)
                        Text(sum.total > 0 ? "\(Int(Double(sum.done) / Double(sum.total) * 100))% (已完成 \(sum.done)/\(sum.total))" : "0%")
                            .font(.system(size: 12, weight: .bold)).foregroundColor(Theme.primary)
                        Spacer()
                        stat("成功", value: sum.done, color: Theme.success)
                        stat("待签到", value: sum.pending, color: Theme.warning)
                        stat("失败", value: sum.fail, color: Theme.danger)
                    }
                }
            }
            .padding(20)
            .animation(.easeOut(duration: 0.18), value: m.showingAddPanel)
        }
        .background(Theme.mainBg)
        .fileImporter(isPresented: $showImport, allowedContentTypes: [.json, .plainText, .text]) { result in
            if case .success(let url) = result {
                m.importAccounts(path: url.path)
            }
        }
    }
    private func progress(_ sum: (done: Int, fail: Int, pending: Int, credit: Double, total: Int)) -> CGFloat {
        guard sum.total > 0 else { return 0 }
        return min(1, CGFloat(Double(sum.done) / Double(sum.total)))
    }
    private func stat(_ t: String, value: Int, color: Color) -> some View {
        HStack(spacing: 4) {
            StatusDot(color: color, size: 6)
            Text("\(value) \(t)").font(.system(size: 12)).foregroundColor(Theme.textDeep)
        }
    }
    // 筛选胶囊标签（全部 / 启用 / 停用，带账号计数）
    private var filterCapsules: some View {
        HStack(spacing: 6) {
            capsule("全部", count: m.accounts.count)
            capsule("启用", count: m.accounts.filter { $0.isEnabled }.count)
            capsule("停用", count: m.accounts.filter { !$0.isEnabled }.count)
        }
    }
    private func capsule(_ t: String, count: Int) -> some View {
        Button {
            filter = t
        } label: {
            HStack(spacing: 4) {
                Text(t).font(.system(size: 12, weight: .medium))
                Text("\(count)").font(.system(size: 11))
            }
            .foregroundColor(filter == t ? .white : Theme.textDeep)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(filter == t ? Theme.sidebar : Color.white)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(filter == t ? Color.clear : Theme.cardBorder, lineWidth: 1))
            .cornerRadius(8)
        }
        .buttonStyle(.plain)
    }
    private func filteredAccounts() -> [Account] {
        var list = m.accounts
        if !search.isEmpty {
            list = list.filter { $0.name.localizedCaseInsensitiveContains(search) || $0.app.localizedCaseInsensitiveContains(search) }
        }
        switch filter {
        case "启用": list = list.filter { $0.isEnabled }
        case "停用": list = list.filter { !$0.isEnabled }
        default: break
        }
        return list
    }
}

struct AccountRowView: View {
    @EnvironmentObject var m: AppModel
    let acc: Account
    @State private var editing = false
    @State private var newName: String = ""
    @State private var confirmDelete = false

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 5).fill(Theme.sidebar).frame(width: 34, height: 34)
                Text(acc.platformLabel.prefix(2)).font(.system(size: 12, weight: .bold)).foregroundColor(.white)
            }
            VStack(alignment: .leading, spacing: 2) {
                if editing {
                    HStack(spacing: 6) {
                        TextField("账号名", text: $newName)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 130)
                        Button("保存") {
                            m.renameAccount(acc, to: newName)
                            editing = false
                        }.buttonStyle(PrimaryButtonStyle())
                        Button("取消") { editing = false }.buttonStyle(GhostButtonStyle())
                    }
                } else {
                    Text(acc.name).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.textDeep)
                }
                HStack(spacing: 6) {
                    Pill(text: acc.isTrae ? "内置登录" : "网页登录", fg: Theme.primary, bg: Theme.primary.opacity(0.10))
                    Text("连续 \(streak) 天").font(.system(size: 11)).foregroundColor(Theme.textMuted)
                }
            }
            .frame(width: 200, alignment: .leading)

            credentialView
                .frame(maxWidth: .infinity, alignment: .leading)

            // 今日积分
            HStack(spacing: 2) {
                let st = m.accStatus(acc.name)
                Text(st.state == "done" ? "+\(Int(st.hit?.credits ?? 0))" : "—")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(st.state == "done" ? Theme.success : Theme.textMuted)
            }
            .frame(width: 70, alignment: .center)

            Toggle("", isOn: Binding(get: { acc.isEnabled }, set: { m.toggleEnabled(acc, on: $0) }))
                .toggleStyle(.switch)
                .tint(Theme.success)
                .labelsHidden()
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .stroke(acc.isEnabled ? Color.clear : Theme.textMuted.opacity(0.6), lineWidth: 1)
                )
                .frame(width: 46)

            HStack(spacing: 4) {
                Button { editing = true; newName = acc.name } label: {
                    Image(systemName: "pencil").font(.system(size: 12))
                }
                .buttonStyle(.plain).foregroundColor(Theme.textMuted).help("重命名")

                Button { confirmDelete = true } label: {
                    Image(systemName: "trash").font(.system(size: 12))
                }
                .buttonStyle(.plain).foregroundColor(Theme.danger).help("删除")
            }
            .frame(width: 60)
        }
        .padding(.vertical, 8)
        .confirmationDialog("确认删除账号「\(acc.name)」？", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("删除", role: .destructive) { m.deleteAccount(acc) }
        }
    }
    private var streak: Int {
        ParsedLogs.streak(for: acc.name, logs: m.parsed, startDate: DayTool.today())
    }
    @ViewBuilder
    private var credentialView: some View {
        if acc.isWorkBuddy {
            workbuddyCredentialView
        } else {
            traeCurlCredentialView
        }
    }

    // WorkBuddy 账号：实时反映本机登录态健康（accessToken 仅内存，不显示任何 token）
    @ViewBuilder
    private var workbuddyCredentialView: some View {
        HStack(spacing: 6) {
            if m.loginAccount == acc.name {
                Text("刷新登录态…").font(.system(size: 11)).foregroundColor(Theme.textMuted)
            } else {
                switch m.wbHealthy {
                case .some(true):
                    StatusDot(color: Theme.success, size: 6)
                    Text("本机登录态可用" + (m.wbNickname.isEmpty ? "" : "（\(m.wbNickname)）"))
                        .font(.system(size: 11)).foregroundColor(Theme.textMuted)
                    Button {
                        m.runWorkBuddyRefresh(name: acc.name)
                    } label: {
                        Text("刷新登录态").font(.system(size: 11, weight: .semibold))
                            .foregroundColor(Theme.primary).underline()
                    }
                    .buttonStyle(.plain)
                    .disabled(m.loginRunning)
                case .some(false):
                    StatusDot(color: Theme.danger, size: 6)
                    Text("未检测到 WorkBuddy 登录态，请打开桌面端登录")
                        .font(.system(size: 11)).foregroundColor(Theme.danger)
                    Button {
                        m.runWorkBuddyRefresh(name: acc.name)
                    } label: {
                        Text("重新检测").font(.system(size: 11, weight: .semibold))
                            .foregroundColor(Theme.danger).underline()
                    }
                    .buttonStyle(.plain)
                    .disabled(m.loginRunning)
                case .none:
                    Text("WorkBuddy 待检测").font(.system(size: 11)).foregroundColor(Theme.textMuted)
                    Button {
                        m.probeWorkBuddy()
                    } label: {
                        Text("检测登录态").font(.system(size: 11, weight: .semibold))
                            .foregroundColor(Theme.primary).underline()
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    // Trae / cURL 账号：保持原有健康提示 + 重新登录
    @ViewBuilder
    private var traeCurlCredentialView: some View {
        let hint = acc.credentialHint()
        HStack(spacing: 6) {
            if let hint = hint {
                StatusDot(color: hint.color == .red ? Theme.danger : (hint.color == .green ? Theme.success : Theme.warning), size: 6)
                Text(hint.text).font(.system(size: 11)).foregroundColor(hint.color == .red ? Theme.danger : Theme.textMuted)
                // 凭证非健康状态（过期 / 缺失 / 无法解析）均提供"重新登录"
                if hint.color != .green {
                    Button {
                        m.runTraeLogin(name: acc.name)
                    } label: {
                        Text(m.loginAccount == acc.name ? "登录中…" : "重新登录")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(Theme.danger)
                            .underline()
                    }
                    .buttonStyle(.plain)
                    .disabled(m.loginAccount != nil && m.loginAccount != acc.name)
                }
            } else if acc.isTrae {
                Text("Token \(Account.mask(acc.trae_auth?.token))").font(.system(size: 11, design: .monospaced)).foregroundColor(Theme.textMuted)
            } else {
                Text("Cookie 已配置").font(.system(size: 11)).foregroundColor(Theme.textMuted)
            }
        }
    }
}

// 新增账号右侧面板
struct AddAccountPanel: View {
    @EnvironmentObject var m: AppModel
    @State private var mode = 0
    @State private var name: String = ""
    @State private var app: String = "trae"
    @State private var curl: String = ""
    @State private var autoEnable = true
    @State private var savingCurl = false

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("新增账号").font(.system(size: 16, weight: .bold)).foregroundColor(Theme.textDeep)
                    Spacer()
                    Button { m.showingAddPanel = false } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).foregroundColor(Theme.textMuted)
                }
                Picker("", selection: $mode) {
                    Text("Trae 浏览器登录").tag(0)
                    Text("cURL 抓包").tag(1)
                    Text("WorkBuddy").tag(2)
                }
                .pickerStyle(.segmented)

                if mode == 0 {
                    traeForm
                } else if mode == 1 {
                    curlForm
                } else {
                    workbuddyForm
                }
            }
        }
    }

    private var traeForm: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                Text("账号名称").font(.system(size: 12, weight: .semibold)).foregroundColor(Theme.textMuted)
                TextField("如：trae-main", text: $name).textFieldStyle(.roundedBorder)
            }
            Toggle(isOn: $autoEnable) {
                Text("加入自动签到").font(.system(size: 12)).foregroundColor(Theme.textDeep)
            }
            .toggleStyle(.switch)
            .tint(Theme.primary)
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(autoEnable ? Color.clear : Theme.textMuted.opacity(0.6), lineWidth: 1)
            )
            Button {
                m.runTraeLogin(name: name, enabled: autoEnable)
            } label: {
                Label(m.loginRunning ? "正在等待登录完成…" : "打开登录浏览器", systemImage: "globe")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(m.loginRunning || name.isEmpty)

            Text("将弹出内置浏览器窗口，登录后程序自动识别并保存登录态。")
                .font(.system(size: 11)).foregroundColor(Theme.textMuted)

            if !m.loginLines.isEmpty {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(m.loginLines, id: \.self) { line in
                            Text(line).font(.system(size: 10, design: .monospaced)).foregroundColor(Theme.textMuted)
                        }
                    }
                }
                .frame(height: 140)
                .padding(8)
                .background(Color.black.opacity(0.04))
                .cornerRadius(6)
            }
        }
    }

    private var curlForm: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                Text("账号名称").font(.system(size: 12, weight: .semibold)).foregroundColor(Theme.textMuted)
                TextField("如：wb-1", text: $name).textFieldStyle(.roundedBorder)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("所属软件").font(.system(size: 12, weight: .semibold)).foregroundColor(Theme.textMuted)
                TextField("如：trae / workbuddy", text: $app).textFieldStyle(.roundedBorder)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("登录凭据 (Cookie / Token / cURL)").font(.system(size: 12, weight: .semibold)).foregroundColor(Theme.textMuted)
                TextEditor(text: $curl)
                    .font(.system(size: 11, design: .monospaced))
                    .frame(height: 110)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.cardBorder, lineWidth: 1))
            }
            Toggle(isOn: $autoEnable) {
                Text("加入自动签到").font(.system(size: 12)).foregroundColor(Theme.textDeep)
            }
            .toggleStyle(.switch)
            .tint(Theme.primary)
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(autoEnable ? Color.clear : Theme.textMuted.opacity(0.6), lineWidth: 1)
            )

            Button {
                savingCurl = true
                m.addAccountCurl(name: name, app: app, curl: curl, enabled: autoEnable) { _ in
                    savingCurl = false
                }
            } label: {
                Text("保存并验证").frame(maxWidth: .infinity)
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(name.isEmpty || app.isEmpty || curl.isEmpty || savingCurl)
        }
    }

    private var workbuddyForm: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    switch m.wbHealthy {
                    case .some(true):
                        StatusDot(color: Theme.success, size: 8)
                        Text("已检测到本机 WorkBuddy 登录态" + (m.wbNickname.isEmpty ? "" : "（\(m.wbNickname)）"))
                            .font(.system(size: 12)).foregroundColor(Theme.textDeep)
                    case .some(false):
                        StatusDot(color: Theme.danger, size: 8)
                        Text("未检测到 WorkBuddy 登录态。请先打开 WorkBuddy 桌面端并登录，再返回点击\"重新检测\"。")
                            .font(.system(size: 12)).foregroundColor(Theme.danger)
                    case .none:
                        StatusDot(color: Theme.warning, size: 8)
                        Text("正在检测本机 WorkBuddy 登录态…")
                            .font(.system(size: 12)).foregroundColor(Theme.textMuted)
                    }
                    Spacer()
                }
                .padding(10)
                .background((m.wbHealthy == true ? Theme.success : (m.wbHealthy == false ? Theme.danger : Theme.warning)).opacity(0.08))
                .cornerRadius(8)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("账号名称（可选，留空自动以昵称命名）").font(.system(size: 12, weight: .semibold)).foregroundColor(Theme.textMuted)
                TextField("如：workbuddy 或留空", text: $name).textFieldStyle(.roundedBorder)
            }
            Toggle(isOn: $autoEnable) {
                Text("加入自动签到").font(.system(size: 12)).foregroundColor(Theme.textDeep)
            }
            .toggleStyle(.switch)
            .tint(Theme.primary)
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(autoEnable ? Color.clear : Theme.textMuted.opacity(0.6), lineWidth: 1)
            )
            HStack(spacing: 8) {
                Button {
                    m.addWorkBuddyAccount(name: name, enabled: autoEnable)
                } label: {
                    Label(m.loginRunning ? "正在读取登录态…" : "从本机 WorkBuddy 读取并添加",
                          systemImage: "person.crop.circle.badge.plus")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(m.loginRunning || m.wbHealthy != true)
                Button {
                    m.probeWorkBuddy()
                } label: {
                    Image(systemName: "arrow.clockwise").font(.system(size: 13))
                }
                .buttonStyle(GhostButtonStyle())
                .help("重新检测")
                .disabled(m.loginRunning)
            }
            Text("凭据安全：accessToken 等同账号密码，仅在本机内存中使用，不写入配置文件、不写日志、不回显，可从本机登录态一键添加。")
                .font(.system(size: 11)).foregroundColor(Theme.textMuted)
            if !m.loginLines.isEmpty {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(m.loginLines, id: \.self) { line in
                            Text(line).font(.system(size: 10, design: .monospaced)).foregroundColor(Theme.textMuted)
                        }
                    }
                }
                .frame(height: 100)
                .padding(8)
                .background(Color.black.opacity(0.04))
                .cornerRadius(6)
            }
        }
    }
}

// MARK: - 03 设置
struct SettingsView: View {
    @EnvironmentObject var m: AppModel
    @State private var draftTimes: [Date] = []
    @State private var weekdays: Set<Int> = []
    @State private var stagger: Double = 0
    @State private var autoOnLaunch = false
    @State private var retryOnFail = true
    @State private var notify = false
    @State private var refreshMin = 30
    @State private var dirty = false
    @State private var showExportConfig = false

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                PageHeader("设置", subtitle: "配置自动签到的规则、频率与提醒偏好") {
                    AnyView(
                        HStack(spacing: 8) {
                            Button { exportConfig() } label: { Label("导出配置", systemImage: "square.and.arrow.up") }
                                .buttonStyle(GhostButtonStyle())
                            Button { restoreDefaults() } label: { Label("恢复默认设置", systemImage: "arrow.counterclockwise") }
                                .buttonStyle(GhostButtonStyle())
                        }
                    )
                }

                HStack(alignment: .top, spacing: 16) {
                    VStack(spacing: 16) {
                        timingCard
                        runtimeCard
                    }
                    VStack(spacing: 16) {
                        strategyCard
                        dataCard
                    }
                    .frame(maxWidth: 420)
                }

                if dirty {
                    HStack {
                        Spacer()
                        Text("有未保存的设置更改").font(.system(size: 12)).foregroundColor(Theme.warning)
                        Button("放弃更改") {
                            loadDraft()
                            dirty = false
                        }.buttonStyle(GhostButtonStyle())
                        Button("保存设置") {
                            commit()
                        }.buttonStyle(PrimaryButtonStyle())
                    }
                }
            }
            .padding(20)
        }
        .background(Theme.mainBg)
        .onAppear { if draftTimes.isEmpty { loadDraft() } }
        .fileImporter(isPresented: $showExportConfig, allowedContentTypes: [.json]) { _ in }
    }

    // MARK: 定时
    private var timingCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                CardHeader("定时签到")
                HStack(spacing: 12) {
                    Text("每天执行").font(.system(size: 12)).foregroundColor(Theme.textMuted)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(draftTimes.indices, id: \.self) { i in
                                HStack(spacing: 4) {
                                    Text(dayText(draftTimes[i]))
                                        .font(.system(size: 12, weight: .semibold)).foregroundColor(.white)
                                    Button {
                                        removeTime(i)
                                    } label: {
                                        Image(systemName: "xmark.circle.fill").font(.system(size: 11)).foregroundColor(Theme.sidebarMuted)
                                    }
                                    .buttonStyle(.plain)
                                }
                                .padding(.horizontal, 8).padding(.vertical, 4)
                                .background(Theme.sidebar).cornerRadius(6)
                            }
                        }
                    }
                    Button { addTime() } label: { Image(systemName: "plus.circle").foregroundColor(Theme.primary) }
                        .buttonStyle(.plain)
                }
                if draftTimes.indices.contains(0) {
                    HStack {
                        DatePicker("添加时间", selection: $draftTimes[0], displayedComponents: .hourAndMinute)
                            .datePickerStyle(.field)
                            .onChange(of: draftTimes[0]) { _ in dirty = true }
                        Spacer()
                    }
                }
                Divider()
                Text("星期").font(.system(size: 12)).foregroundColor(Theme.textMuted)
                HStack(spacing: 6) {
                    ForEach(1...7, id: \.self) { d in
                        Button {
                            if weekdays.contains(d) { weekdays.remove(d) } else { weekdays.insert(d) }
                            dirty = true
                        } label: {
                            Text(weekLabel(d))
                                .font(.system(size: 12))
                                .foregroundColor(weekdays.contains(d) ? .white : Theme.textDeep)
                                .frame(width: 28, height: 28)
                                .background(weekdays.contains(d) ? Theme.sidebar : Color.white)
                                .overlay(Circle().stroke(Theme.cardBorder, lineWidth: 1))
                                .clipShape(Circle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                Divider()
                HStack {
                    Text("错峰延迟").font(.system(size: 12)).foregroundColor(Theme.textMuted)
                    Text("\(Int(stagger)) 分钟").font(.system(size: 12, weight: .semibold)).foregroundColor(Theme.primary)
                    Spacer()
                    Slider(value: $stagger, in: 0...30, step: 1) { _ in dirty = true }
                        .frame(width: 160)
                }
            }
        }
    }

    // MARK: 运行与通知
    private var runtimeCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                CardHeader("运行与通知")
                toggleRow("启动应用时自动签到", desc: "打开 App 时立即执行一次签到", on: $autoOnLaunch)
                Divider()
                toggleRow("签到失败自动重试", desc: "失败账号自动重试一次", on: $retryOnFail)
                Divider()
                toggleRow("签到完成后发送系统通知", desc: "使用 macOS 通知中心提醒", on: $notify)
            }
        }
    }

    // MARK: 运行策略预览
    private var strategyCard: some View {
        Card(color: Theme.darkCard) {
            VStack(alignment: .leading, spacing: 10) {
                Text("运行策略预览").font(.system(size: 14, weight: .semibold)).foregroundColor(.white)
                HStack(spacing: 6) {
                    StatusDot(color: m.launchd.installed ? Theme.success : Color(white: 0.35), size: 8)
                    if m.launchd.installed {
                        Text("下次自动签到 今天 \(String(format: "%02d:%02d", m.launchd.hour, m.launchd.minute))（\(m.launchd.deltaText)后）")
                            .font(.system(size: 12)).foregroundColor(Theme.textMain)
                    } else {
                        Text("定时任务未安装").font(.system(size: 12)).foregroundColor(Theme.sidebarMuted)
                    }
                }
                Divider().background(Color.white.opacity(0.12))
                HStack(spacing: 6) {
                    StatusDot(color: m.enabledAccounts().isEmpty ? Color(white: 0.35) : Theme.success, size: 8)
                    Text("执行 \(m.enabledAccounts().count) 个已启用账号")
                        .font(.system(size: 12)).foregroundColor(Theme.textMain)
                }
                Divider().background(Color.white.opacity(0.12))
                let times = m.pref.times.isEmpty ? [[21, 30]] : m.pref.times
                ForEach(times.indices, id: \.self) { i in
                    let t = times[i]
                    HStack {
                        Text(String(format: "%02d:%02d", t[0], t[1])).font(.system(size: 12, weight: .semibold)).foregroundColor(.white)
                        Spacer()
                        Text(planStatus(t[0], t[1])).font(.system(size: 11)).foregroundColor(planColor(t[0], t[1]))
                    }
                    .padding(.vertical, 5)
                }
                Divider().background(Color.white.opacity(0.12))
                Text("最近执行结果见 logs/launchd.log").font(.system(size: 10)).foregroundColor(Theme.sidebarMuted)
            }
        }
    }
    private func planStatus(_ h: Int, _ min: Int) -> String {
        // 如实读取今日真实执行结果：该计划时间点之后是否有签到日志
        let today = DayTool.today()
        let ref = String(format: "%02d:%02d", h, min)
        let oks = m.parsed.todayOK(today).values.filter { $0.time >= ref }
        let fails = m.parsed.todayFail(today).values.filter { $0.time >= ref }
        if oks.isEmpty && fails.isEmpty { return "等待执行" }
        if fails.isEmpty { return "今日已执行 · 成功 \(oks.count)" }
        if oks.isEmpty { return "今日已执行 · 失败 \(fails.count)" }
        return "今日已执行 · 成功 \(oks.count) 失败 \(fails.count)"
    }
    private func planColor(_ h: Int, _ min: Int) -> Color {
        let today = DayTool.today()
        let ref = String(format: "%02d:%02d", h, min)
        let oks = m.parsed.todayOK(today).values.filter { $0.time >= ref }
        let fails = m.parsed.todayFail(today).values.filter { $0.time >= ref }
        if oks.isEmpty && fails.isEmpty { return Theme.sidebarMuted }
        return fails.isEmpty ? Theme.success : Theme.warning
    }

    // MARK: 数据与积分
    private var dataCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                CardHeader("数据与积分")
                HStack {
                    Text("积分刷新频率").font(.system(size: 12)).foregroundColor(Theme.textDeep)
                    Spacer()
                    Picker("", selection: $refreshMin) {
                        Text("每 10 分钟").tag(10)
                        Text("每 30 分钟").tag(30)
                        Text("每 60 分钟").tag(60)
                    }
                    .pickerStyle(.menu)
                    .onChange(of: refreshMin) { _ in dirty = true }
                }
                Divider()
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("导出签到记录").font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.textDeep)
                        Text("导出为 CSV，便于表格查看").font(.system(size: 11)).foregroundColor(Theme.textMuted)
                    }
                    Spacer()
                    Button { exportCSV() } label: { Label("导出 CSV", systemImage: "doc.text") }
                        .buttonStyle(PrimaryButtonStyle())
                }
            }
        }
    }

    // MARK: 动作
    private func loadDraft() {
        let p = m.pref
        let cal = Calendar.current
        draftTimes = p.times.map {
            var comp = cal.dateComponents([.year, .month, .day], from: Date())
            comp.hour = $0[0]; comp.minute = $0[1]
            return cal.date(from: comp) ?? Date()
        }
        if draftTimes.isEmpty { draftTimes = [makeTime(21, 30)] }
        weekdays = Set(p.weekdays)
        stagger = Double(p.staggerMinutes)
        autoOnLaunch = p.autoSignOnLaunch
        retryOnFail = p.retryOnFail
        notify = p.notifyOnComplete
        refreshMin = p.creditsRefreshMinutes
        dirty = false
    }
    private func commit() {
        var times: [[Int]] = []
        let cal = Calendar.current
        for d in draftTimes {
            times.append([cal.component(.hour, from: d), cal.component(.minute, from: d)])
        }
        m.pref.times = times
        m.pref.weekdays = weekdays.sorted()
        m.pref.staggerMinutes = Int(stagger)
        m.pref.autoSignOnLaunch = autoOnLaunch
        m.pref.retryOnFail = retryOnFail
        m.pref.notifyOnComplete = notify
        m.pref.creditsRefreshMinutes = refreshMin
        m.applyLaunchdSettings()
        dirty = false
    }
    private func restoreDefaults() {
        m.pref = AppPrefs()
        m.pref.save()
        loadDraft()
        m.refreshAll()
        m.showToast("已恢复默认设置", .info)
    }
    private func exportCSV() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "checkin_records.csv"
        if panel.runModal() == .OK, let url = panel.url {
            m.exportCSV(to: url)
        }
    }
    private func exportConfig() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "accounts_export.json"
        if panel.runModal() == .OK, let url = panel.url {
            m.exportConfig(to: url)
        }
    }
    private func toggleRow(_ t: String, desc: String, on: Binding<Bool>) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(t).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.textDeep)
                Text(desc).font(.system(size: 11)).foregroundColor(Theme.textMuted)
            }
            Spacer()
            Toggle("", isOn: on)
                .toggleStyle(.switch)
                .tint(Theme.primary)
                .labelsHidden()
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .stroke(on.wrappedValue ? Color.clear : Theme.textMuted.opacity(0.6), lineWidth: 1)
                )
        }
        .padding(.vertical, 2)
    }
    private func makeTime(_ h: Int, _ min: Int) -> Date {
        let cal = Calendar.current
        var comp = cal.dateComponents([.year, .month, .day], from: Date())
        comp.hour = h; comp.minute = min
        return cal.date(from: comp) ?? Date()
    }
    private func dayText(_ d: Date) -> String {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f.string(from: d)
    }
    private func weekLabel(_ d: Int) -> String {
        let names = ["一", "二", "三", "四", "五", "六", "日"]
        return "周" + names[d - 1]
    }
    private func addTime() {
        let last = draftTimes.last ?? makeTime(21, 0)
        if let next = Calendar.current.date(byAdding: .hour, value: 1, to: last) {
            draftTimes.append(next)
            dirty = true
        }
    }
    private func removeTime(_ i: Int) {
        if draftTimes.count > 1 {
            draftTimes.remove(at: i)
            dirty = true
        }
    }
}

// MARK: - 04 使用说明
struct HelpView: View {
    @EnvironmentObject var m: AppModel
    @State private var openFAQ: Set<Int> = []

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                PageHeader("使用说明", subtitle: "新手引导与常见问题排查") {
                    AnyView(
                        HStack(spacing: 8) {
                            Button { m.copyDiagnostics() } label: { Label("复制诊断信息", systemImage: "doc.on.doc") }
                                .buttonStyle(GhostButtonStyle())
                            Button { m.selectedPage = .checkin; m.showToast("已回到引导起点：按「01 添加账号」开始", .info) }
                                label: { Label("重新播放引导", systemImage: "arrow.clockwise") }
                                .buttonStyle(GhostButtonStyle())
                        }
                    )
                }

                // 新手引导 4 卡
                HStack(spacing: 12) {
                    guideCard(1, "添加账号", "通过 Trae 内置浏览器登录或粘贴 cURL 导入账号。", "去添加", .accounts)
                    guideCard(2, "校验凭据", "在账号管理查看凭证健康提示，过期账号及时重新登录。", "去校验", .accounts)
                    guideCard(3, "开启定时签到", "在设置页选择时间与星期，或直接在签到页打开自动签到。", "去设置", .settings)
                    guideCard(4, "查看签到与积分", "首页查看今日战绩、积分趋势与异常提醒。", "去查看", .checkin)
                }

                HStack(alignment: .top, spacing: 16) {
                    VStack(spacing: 16) {
                        autoExplainCard
                        faqCard
                    }
                    legendCard
                        .frame(width: 340)
                }
            }
            .padding(20)
        }
        .background(Theme.mainBg)
    }

    private func guideCard(_ n: Int, _ t: String, _ d: String, _ link: String, _ page: AppModel.Page) -> some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                Text(String(format: "%02d", n))
                    .font(.system(size: 15, weight: .bold)).foregroundColor(Theme.textDeep)
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(Theme.grayBar.opacity(0.18))
                    .cornerRadius(6)
                Text(t).font(.system(size: 14, weight: .bold)).foregroundColor(Theme.textDeep)
                Text(d).font(.system(size: 12)).foregroundColor(Theme.textMuted).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                Button { m.selectedPage = page } label: { Text(link) }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .semibold)).foregroundColor(Theme.primary)
            }
            .frame(maxWidth: .infinity, minHeight: 130, alignment: .leading)
        }
    }

    private var autoExplainCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                CardHeader("自动签到使用说明")
                VStack(alignment: .leading, spacing: 8) {
                    bullet("如何开启自动签到", "签到页右侧开关或设置页「定时签到」，点击后安装 macOS 定时任务。")
                    bullet("账户配置规则", "每个账号可独立启用/停用；停用账号不会被自动签到。")
                    bullet("登录态管理", "Trae 登录态约 14 天有效，失效后账号管理会红色提示，重新浏览器登录即可。")
                    bullet("错峰延迟", "设置页可开启随机延迟 0-30 分钟，降低风控触发概率。")
                }
            }
        }
    }

    private var faqCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                CardHeader("常见问题与排查")
                faqRow(0, "签到时间到了但没有自动执行？", "确认签到页开关为开启状态；在「关于本机→登录项」允许 App 常驻，或用 launchctl list | grep autocheckin 检查任务。")
                faqRow(1, "凭证过期后怎么办？", "在账号管理找到红色「凭证已过期」提示的账号，点击重新浏览器登录即可刷新。")
                faqRow(2, "如何导出签到记录？", "设置页「数据与积分 → 导出 CSV」。")
                faqRow(3, "开启后想临时关闭？", "回到签到页关闭「自动签到」开关，即可卸载定时任务。")
            }
        }
    }

    private func faqRow(_ id: Int, _ q: String, _ a: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                if openFAQ.contains(id) { openFAQ.remove(id) } else { openFAQ.insert(id) }
            } label: {
                HStack {
                    Text(q).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.textDeep)
                    Spacer()
                    Image(systemName: openFAQ.contains(id) ? "chevron.up" : "chevron.down")
                        .font(.system(size: 11)).foregroundColor(Theme.textMuted)
                }
            }
            .buttonStyle(.plain)
            if openFAQ.contains(id) {
                Text(a).font(.system(size: 12)).foregroundColor(Theme.textMuted).padding(.top, 2)
            }
            Divider().opacity(0.5)
        }
        .padding(.vertical, 4)
    }

    private var legendCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                CardHeader("积分与状态含义")
                VStack(alignment: .leading, spacing: 12) {
                    legendRow(Theme.success, "已完成", "账号今日已成功签到")
                    legendRow(Theme.grayBar, "待签到", "尚未执行签到，自动定时会处理")
                    legendRow(Theme.danger, "签到失败", "签到被拒绝，需重新登录或稍后重试")
                    legendRow(Theme.primary, "积分柱状图", "近 7/30 天每日获得积分，今日红色高亮")
                }
            }
        }
    }
    private func legendRow(_ c: Color, _ t: String, _ d: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Circle().fill(c).frame(width: 10, height: 10).padding(.top, 3)
            VStack(alignment: .leading, spacing: 1) {
                Text(t).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.textDeep)
                Text(d).font(.system(size: 11)).foregroundColor(Theme.textMuted)
            }
        }
    }
    private func bullet(_ t: String, _ d: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Circle().fill(Theme.primary).frame(width: 6, height: 6).padding(.top, 5)
            VStack(alignment: .leading, spacing: 1) {
                Text(t).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.textDeep)
                Text(d).font(.system(size: 12)).foregroundColor(Theme.textMuted).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - 主内容区
struct ContentView: View {
    @EnvironmentObject var m: AppModel

    var body: some View {
        GeometryReader { geo in
            HStack(spacing: 0) {
                SidebarView()
                    .frame(width: max(200, geo.size.width * 0.17))
                ZStack {
                    Theme.mainBg.ignoresSafeArea()
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
                    if !m.pythonAvailable {
                        PythonErrorBanner(hint: m.pythonHint)
                    }
                }
                .overlay {
                    ToastOverlay(toast: m.toast)
                        .animation(.easeInOut(duration: 0.2), value: m.toast)
                }
            }
        }
        .frame(minWidth: 1120, minHeight: 700)
    }
}

struct PythonErrorBanner: View {
    let hint: String
    var body: some View {
        HStack {
            Image(systemName: "exclamationmark.triangle").foregroundColor(.white)
            Text("Python 运行时不可用：\(hint)").font(.system(size: 12))
            Spacer()
        }
        .padding(12)
        .background(Theme.danger)
        .cornerRadius(8)
        .padding(16)
    }
}
