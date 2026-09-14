import SwiftUI
import AppKit

// MARK: ==================== 03 设置 ====================
struct SettingsView: View {
    @EnvironmentObject var m: AppModel

    @State private var times: [Date] = []
    @State private var weekdays: Set<Int> = []
    @State private var stagger: Int = 0
    @State private var autoOnLaunch = false
    @State private var retryOnFail = true
    @State private var notify = false
    @State private var dailyDigest = false
    @State private var refreshMin = 30
    @State private var syncHistory = true
    @State private var dirtyKeys: Set<String> = []
    @State private var editingIndex: Int? = nil

    var body: some View {
        PageScroll(spacing: 17) {
            PageHeader("设置", subtitle: "配置自动签到的执行时间、运行方式与通知策略") {
                Button { exportConfig() } label: {
                    Label("导出配置", systemImage: "square.and.arrow.up")
                }
                .buttonStyle(GhostButtonStyle())

                Button { restoreDefaults() } label: {
                    Label("恢复默认设置", systemImage: "arrow.counterclockwise")
                }
                .buttonStyle(GhostButtonStyle())
            }

            HStack(alignment: .top, spacing: 23) {
                VStack(spacing: 17) {
                    timingCard
                    runtimeCard
                }
                .frame(maxWidth: .infinity)

                VStack(spacing: 17) {
                    previewCard
                    dataCard
                }
                .frame(width: 358)
            }

            if !dirtyKeys.isEmpty { dirtyBar }
        }
        .onAppear { if times.isEmpty { loadDraft() } }
    }

    // MARK: 定时签到
    private var timingCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("定时签到")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Toggle("", isOn: Binding(get: { m.launchd.installed }, set: { m.autoToggle($0) }))
                        .toggleStyle(.switch)
                        .tint(Theme.success)
                        .labelsHidden()
                        .overlay(Capsule().stroke(m.launchd.installed ? Color.clear : Color(hex: 0xCFCFCF), lineWidth: 1))
                }

                // 签到时间
                settingRow {
                    VStack(alignment: .leading, spacing: 3) {
                        rowTitle("签到时间")
                        if let idx = editingIndex, times.indices.contains(idx) {
                            HStack(spacing: 8) {
                                DatePicker("", selection: $times[idx], displayedComponents: .hourAndMinute)
                                    .datePickerStyle(.field)
                                    .labelsHidden()
                                    .onChange(of: times[idx]) { _ in dirtyKeys.insert("times") }
                                Button {
                                    times.remove(at: idx)
                                    if times.isEmpty { times = [makeTime(21, 30)] }
                                    editingIndex = nil
                                    dirtyKeys.insert("times")
                                } label: {
                                    Text("删除该时间").font(.system(size: 11.5)).foregroundColor(Theme.accent)
                                }
                                .buttonStyle(.plain)
                            }
                        } else {
                            rowDesc("每天在这些时间点自动执行签到")
                        }
                    }
                } right: {
                    HStack(spacing: 8) {
                        ForEach(times.indices, id: \.self) { i in
                            Button { editingIndex = (editingIndex == i ? nil : i) } label: {
                                Text(dayText(times[i]))
                            }
                            .buttonStyle(ChipButtonStyle(selected: highlightedIndex == i))
                        }
                        Button { addTime() } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "plus").font(.system(size: 10, weight: .semibold))
                                Text("添加")
                            }
                            .font(.system(size: 12.5))
                            .foregroundColor(Theme.textBody)
                            .padding(.horizontal, 12)
                            .frame(height: 30)
                            .background(Color.white)
                            .clipShape(RoundedRectangle(cornerRadius: Theme.chipCorner, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: Theme.chipCorner, style: .continuous)
                                .stroke(Theme.border, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                    }
                }

                // 签到频率
                settingRow {
                    rowTitle("签到频率")
                } right: {
                    Menu {
                        ForEach(1...5, id: \.self) { n in
                            Button("每天 \(n) 次") { setFrequency(n) }
                        }
                    } label: {
                        SelectBox {
                            Text("每天 \(times.count) 次").font(.system(size: 13)).foregroundColor(Theme.text)
                        }
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                }

                // 执行日
                settingRow {
                    rowTitle("执行日")
                } right: {
                    HStack(spacing: 8) {
                        ForEach(1...7, id: \.self) { d in
                            Button {
                                if weekdays.contains(d) { weekdays.remove(d) } else { weekdays.insert(d) }
                                dirtyKeys.insert("weekdays")
                            } label: {
                                Text(weekLabel(d))
                                    .font(.system(size: 13, weight: weekdays.contains(d) ? .semibold : .regular))
                                    .foregroundColor(weekdays.contains(d) ? .white : Theme.textSub)
                                    .frame(width: 34, height: 34)
                                    .background(weekdays.contains(d) ? Color.black : Theme.chipBG)
                                    .clipShape(Circle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                // 错峰延迟
                settingRow {
                    VStack(alignment: .leading, spacing: 3) {
                        rowTitle("错峰延迟")
                        rowDesc("随机延迟执行，避免同一时间集中请求")
                    }
                } right: {
                    Menu {
                        ForEach(staggerOptions, id: \.0) { opt in
                            Button(opt.1) { stagger = opt.0; dirtyKeys.insert("stagger") }
                        }
                    } label: {
                        SelectBox {
                            Text(staggerLabel).font(.system(size: 13)).foregroundColor(Theme.text)
                        }
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                }
            }
        }
    }

    private func settingRow<L: View, R: View>(@ViewBuilder left: () -> L, @ViewBuilder right: () -> R) -> some View {
        HStack(alignment: .center, spacing: 14) {
            left()
            Spacer(minLength: 12)
            right()
        }
        .padding(.vertical, 9)
    }

    private func rowTitle(_ t: String) -> some View {
        Text(t).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.text)
    }
    private func rowDesc(_ t: String) -> some View {
        Text(t).font(.system(size: 11.5)).foregroundColor(Theme.textSub)
    }

    // MARK: 运行与通知
    private var runtimeCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("运行与通知")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Text("已开启 \(enabledToggleCount) 项")
                        .font(.system(size: 11.5))
                        .foregroundColor(Theme.textSub)
                }
                .padding(.bottom, 6)

                toggleRow("启动应用时自动签到", "打开喵签签后立即检查今日签到状态", $autoOnLaunch, key: "autoOnLaunch")
                toggleRow("签到失败自动重试", "失败账号自动重试一轮，间隔约 10 秒", $retryOnFail, key: "retry")
                toggleRow("签到完成后发送系统通知", "在 macOS 通知中心显示每个账号的签到结果", $notify, key: "notify")
                toggleRow("每日汇总提醒", "每天 \(digestTimeText) 提醒未完成的账号", $dailyDigest, key: "digest")
            }
        }
    }

    private func toggleRow(_ title: String, _ desc: String, _ binding: Binding<Bool>, key: String) -> some View {
        HStack(alignment: .center, spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                rowTitle(title)
                rowDesc(desc)
            }
            Spacer(minLength: 12)
            GreenSwitch(isOn: Binding(get: { binding.wrappedValue }, set: { binding.wrappedValue = $0; dirtyKeys.insert(key) }))
        }
        .padding(.vertical, 10)
    }

    private var enabledToggleCount: Int {
        [autoOnLaunch, retryOnFail, notify, dailyDigest].filter { $0 }.count
    }
    private var digestTimeText: String {
        times.map { dayText($0) }.max() ?? "21:00"
    }

    // MARK: 定时策略预览
    private var previewCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                CardTitle("定时策略预览") {
                    Text("今天").font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                }

                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 8) {
                        Text("下次自动签到")
                            .font(.system(size: 12.5))
                            .foregroundColor(Theme.darkCaption)
                        Spacer(minLength: 8)
                        HStack(spacing: 6) {
                            Dot(color: m.launchd.installed ? Theme.success : Theme.neutral, size: 7)
                            Text(m.launchd.installed ? "\(m.countdown())后" : "未开启")
                                .font(.system(size: 12))
                                .foregroundColor(.white)
                        }
                        .padding(.horizontal, 10)
                        .frame(height: 24)
                        .background(Color.white.opacity(0.08))
                        .clipShape(Capsule())
                    }
                    Text(nextPlanText)
                        .font(.system(size: 22, weight: .bold))
                        .foregroundColor(.white)
                        .padding(.top, 10)
                    Text("执行 \(m.enabledAccounts().count) 个已启用账号 · 错峰延迟 \(staggerLabel)")
                        .font(.system(size: 12))
                        .foregroundColor(Theme.darkCaption)
                        .padding(.top, 6)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.sidebar)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .padding(.top, 14)

                VStack(spacing: 0) {
                    ForEach(planRows.indices, id: \.self) { i in
                        let row = planRows[i]
                        HStack(spacing: 10) {
                            Dot(color: row.color, size: 8)
                            Text("今天 \(row.time)")
                                .font(.system(size: 13))
                                .foregroundColor(Theme.text)
                            Text(row.status)
                                .font(.system(size: 13))
                                .foregroundColor(row.statusColor)
                            Spacer(minLength: 0)
                        }
                        .frame(height: 40)
                    }
                }
                .padding(.top, 6)
            }
        }
    }

    private var nextPlanText: String {
        guard m.launchd.installed else { return "未开启定时签到" }
        return "今天 \(String(format: "%02d:%02d", m.launchd.hour, m.launchd.minute))"
    }

    private struct PlanRow { var time: String; var status: String; var color: Color; var statusColor: Color }

    private var planRows: [PlanRow] {
        let list = m.pref.times.isEmpty ? [[21, 30]] : m.pref.times
        let today = DayTool.today()
        return list.map { t in
            let ref = String(format: "%02d:%02d", t[0], t[1])
            let oks = m.parsed.todayOK(today).values.filter { $0.time >= ref }.count
            let fails = m.parsed.todayFail(today).values.filter { $0.time >= ref }.count
            if oks == 0 && fails == 0 {
                return PlanRow(time: ref, status: "等待执行", color: Theme.neutral, statusColor: Theme.textSub)
            }
            if fails == 0 {
                return PlanRow(time: ref, status: "已完成 · \(oks) 个账号", color: Theme.success, statusColor: Theme.success)
            }
            if oks == 0 {
                return PlanRow(time: ref, status: "已完成 · \(fails) 个失败", color: Theme.success, statusColor: Theme.warn)
            }
            return PlanRow(time: ref, status: "已完成 · \(oks) 个账号（\(fails) 个失败）",
                           color: Theme.success, statusColor: Theme.textSub)
        }
    }

    // MARK: 数据与积分
    private var dataCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                CardTitle("数据与积分") {
                    Text("自动同步").font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                }

                HStack(spacing: 12) {
                    rowTitle("积分刷新频率")
                    Spacer(minLength: 12)
                    Menu {
                        ForEach([10, 30, 60], id: \.self) { n in
                            Button("每 \(n) 分钟") { refreshMin = n; dirtyKeys.insert("refresh") }
                        }
                    } label: {
                        SelectBox {
                            Text("每 \(refreshMin) 分钟").font(.system(size: 13)).foregroundColor(Theme.text)
                        }
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                }
                .padding(.top, 16)
                .padding(.bottom, 10)

                HStack(spacing: 12) {
                    rowTitle("登录时同步历史积分")
                    Spacer(minLength: 12)
                    GreenSwitch(isOn: Binding(get: { syncHistory }, set: { syncHistory = $0; dirtyKeys.insert("syncHistory") }))
                }
                .padding(.vertical, 10)

                HStack(spacing: 12) {
                    rowTitle("导出签到记录")
                    Spacer(minLength: 12)
                    Button { exportCSV() } label: {
                        Label("导出 CSV", systemImage: "square.and.arrow.down")
                    }
                    .buttonStyle(GhostButtonStyle(fg: Theme.textBody))
                }
                .padding(.top, 10)
            }
        }
    }

    // MARK: 未保存提示
    private var dirtyBar: some View {
        Card(padding: nil) {
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.warnSoft)
                    Image(systemName: "exclamationmark.circle").font(.system(size: 16)).foregroundColor(Theme.warn)
                }
                .frame(width: 40, height: 40)

                VStack(alignment: .leading, spacing: 3) {
                    Text("有 \(dirtyKeys.count) 项设置尚未保存")
                        .font(.system(size: 15, weight: .semibold)).foregroundColor(Theme.text)
                    Text(dirtySummary)
                        .font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                }
                Spacer(minLength: 8)
                Button("放弃修改") { loadDraft(); dirtyKeys.removeAll() }
                    .buttonStyle(GhostButtonStyle())
                Button { commit() } label: { Label("保存设置", systemImage: "checkmark") }
                    .buttonStyle(PrimaryButtonStyle())
            }
            .padding(.horizontal, 15)
            .padding(.vertical, 16)
        }
    }

    private var dirtySummary: String {
        var parts: [String] = []
        if dirtyKeys.contains("times") { parts.append("签到时间调整为 \(times.map { dayText($0) }.joined(separator: " / "))") }
        if dirtyKeys.contains("weekdays") { parts.append("执行日 \(weekdays.sorted().map { weekLabel($0) }.joined(separator: "、"))") }
        if dirtyKeys.contains("stagger") { parts.append("错峰延迟 \(staggerLabel)") }
        if dirtyKeys.contains("retry") { parts.append("失败自动重试 \(retryOnFail ? "已开启" : "已关闭")") }
        if dirtyKeys.contains("notify") { parts.append("系统通知 \(notify ? "已开启" : "已关闭")") }
        if dirtyKeys.contains("digest") { parts.append("每日汇总提醒 \(dailyDigest ? "已开启" : "已关闭")") }
        if dirtyKeys.contains("autoOnLaunch") { parts.append("启动自动签到 \(autoOnLaunch ? "已开启" : "已关闭")") }
        if dirtyKeys.contains("refresh") { parts.append("积分刷新频率每 \(refreshMin) 分钟") }
        if dirtyKeys.contains("syncHistory") { parts.append("登录时同步历史积分 \(syncHistory ? "已开启" : "已关闭")") }
        return parts.isEmpty ? "确认后将写入本机配置文件" : parts.joined(separator: "；")
    }

    // MARK: 数据
    private var staggerOptions: [(Int, String)] {
        [(0, "不延迟"), (15, "0 - 15 分钟随机"), (30, "0 - 30 分钟随机"), (60, "0 - 60 分钟随机")]
    }
    private var staggerLabel: String {
        staggerOptions.first(where: { $0.0 == stagger })?.1 ?? "0 - \(stagger) 分钟随机"
    }
    private var highlightedIndex: Int? {
        if let idx = editingIndex, times.indices.contains(idx) { return idx }
        let cal = Calendar.current
        let nowMinutes = cal.component(.hour, from: Date()) * 60 + cal.component(.minute, from: Date())
        let mins = times.map { cal.component(.hour, from: $0) * 60 + cal.component(.minute, from: $0) }
        return mins.enumerated().filter { $0.element >= nowMinutes }.min(by: { $0.element < $1.element })?.offset
            ?? times.indices.last
    }

    // MARK: 动作
    private func loadDraft() {
        let p = AppPrefs.load()
        times = (p.times.isEmpty ? [[21, 30]] : p.times).map { makeTime($0[0], $0[1]) }
        weekdays = Set(p.weekdays)
        stagger = p.staggerMinutes
        autoOnLaunch = p.autoSignOnLaunch
        retryOnFail = p.retryOnFail
        notify = p.notifyOnComplete
        dailyDigest = p.dailyDigestReminder
        refreshMin = p.creditsRefreshMinutes
        syncHistory = p.syncHistoryCreditsOnLogin
        editingIndex = nil
        dirtyKeys.removeAll()
    }

    private func commit() {
        let cal = Calendar.current
        m.pref.times = times.map { [cal.component(.hour, from: $0), cal.component(.minute, from: $0)] }
            .sorted { ($0[0], $0[1]) < ($1[0], $1[1]) }
        m.pref.weekdays = weekdays.sorted()
        m.pref.staggerMinutes = stagger
        m.pref.autoSignOnLaunch = autoOnLaunch
        m.pref.retryOnFail = retryOnFail
        m.pref.notifyOnComplete = notify
        m.pref.dailyDigestReminder = dailyDigest
        m.pref.creditsRefreshMinutes = refreshMin
        m.pref.syncHistoryCreditsOnLogin = syncHistory
        m.applyLaunchdSettings()
        dirtyKeys.removeAll()
        if dailyDigest { m.scheduleDailyDigest() } else { m.cancelDailyDigest() }
    }

    private func setFrequency(_ n: Int) {
        var new = times.sorted { $0 < $1 }
        while new.count > n { new.removeLast() }
        while new.count < n {
            let last = new.last ?? makeTime(21, 0)
            new.append(Calendar.current.date(byAdding: .hour, value: 3, to: last) ?? last)
        }
        times = new.map { $0 }
        dirtyKeys.insert("times")
    }

    private func addTime() {
        let last = times.last ?? makeTime(21, 0)
        if let next = Calendar.current.date(byAdding: .hour, value: 3, to: last) {
            times.append(next)
            dirtyKeys.insert("times")
        }
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
        ["一", "二", "三", "四", "五", "六", "日"][max(0, min(6, d - 1))]
    }

    private func restoreDefaults() {
        m.pref = AppPrefs()
        m.pref.save()
        loadDraft()
        m.refreshAll()
        m.cancelDailyDigest()
        m.showToast("已恢复默认设置", .info)
    }
    private func exportCSV() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "checkin_records.csv"
        if panel.runModal() == .OK, let url = panel.url { m.exportCSV(to: url) }
    }
    private func exportConfig() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "accounts_export.json"
        if panel.runModal() == .OK, let url = panel.url { m.exportConfig(to: url) }
    }
}
