import SwiftUI
import AppKit

// MARK: ==================== 03 设置 ====================
//
// 这一页只有一处真源：AppModel.pref（落到 native_prefs.json）。
//
// 三条交互约定，都是踩过坑才定下来的：
//  1) **即时生效**。改动立刻落盘，不再有"草稿 + 底部保存条"的两段式状态。
//     旧版把「保存设置」放在页面最底部的未保存提示条里，页面一长就滚不到；
//     改完签到时间既看不见保存入口，切到别的页面草稿还会被丢掉（本页是
//     switch 出来的，切页即销毁 @State）—— 表现就是"保存不了"。
//  2) 时间选择不用 DatePicker(.field)。macOS 上它要按下回车或失焦才把输入提交给
//     binding，点别处常常还拿到旧值。改成「时 / 分」两个下拉，点一下就写回。
//  3) launchd 重装做防抖。连点 7 个星期圆点不该触发 7 次 launchctl unload/load，
//     统一交给 AppModel.syncLaunchdIfNeeded() 合并成一次。
struct SettingsView: View {
    @EnvironmentObject var m: AppModel

    /// 时间片的编辑副本：只为给 chips 一个稳定 identity（直接拿下标当 id，
    /// 增删之后会串行错位）。内容的任何改动都立刻同步回 pref.times。
    @State private var times: [TimeSlot] = []
    @State private var editingID: TimeSlot.ID? = nil
    @State private var showResetConfirm = false
    @State private var savedPulse = false
    @State private var pulseToken = 0

    /// 一天最多 8 个时间点：再多也没意义，策略预览也会糊成一片
    private let maxTimes = 8
    private let refreshOptions = [10, 30, 60, 120]

    private struct TimeSlot: Identifiable, Equatable {
        let id = UUID()
        var hour: Int
        var minute: Int
        var minutes: Int { hour * 60 + minute }
        var text: String { String(format: "%02d:%02d", hour, minute) }
    }

    var body: some View {
        PageScroll(spacing: 17) {
            PageHeader("设置", subtitle: "配置自动签到的执行时间、运行方式与通知策略 · 改动即时生效，无需手动保存") {
                if savedPulse { savedBadge }

                Button { exportConfig() } label: {
                    Label("导出配置", systemImage: "square.and.arrow.up")
                }
                .buttonStyle(GhostButtonStyle())

                Button { showResetConfirm = true } label: {
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
        }
        .onAppear { reload() }
        .alert("恢复默认设置？", isPresented: $showResetConfirm) {
            Button("取消", role: .cancel) {}
            Button("恢复默认", role: .destructive) { restoreDefaults() }
        } message: {
            Text("签到时间、执行日、错峰延迟与各项开关都会重置为初始值并立即写入本机配置；已添加的账号与凭据不受影响。")
        }
    }

    private var savedBadge: some View {
        HStack(spacing: 5) {
            Image(systemName: "checkmark.circle.fill").font(.system(size: 12, weight: .semibold))
            Text("已保存").font(.system(size: 12, weight: .medium))
        }
        .foregroundColor(Theme.success)
        .padding(.horizontal, 10)
        .frame(height: 26)
        .background(Theme.successSoft)
        .clipShape(Capsule())
    }

    // MARK: 定时签到
    private var timingCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 10) {
                    Text("定时签到")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    // 三态：已开启 / **已配置但未生效** / 未开启。
                    // 中间那态必须显出来：plist 在、服务却没被 launchd 加载时定时根本不会触发，
                    // 而界面原来只有"已开启/未开启"，于是显示"已开启"却一次都没跑（本次事故即如此）。
                    Text(m.launchd.loaded ? "已开启" : (m.launchd.installed ? "待生效" : "未开启"))
                        .font(.system(size: 11.5, weight: .medium))
                        .foregroundColor(m.launchd.loaded ? Theme.success
                                         : (m.launchd.installed ? Theme.warn : Theme.textSub))
                        .padding(.horizontal, 8)
                        .frame(height: 20)
                        .background(m.launchd.loaded ? Theme.successSoft
                                    : (m.launchd.installed ? Theme.warnSoft : Theme.chipBG))
                        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                    Spacer(minLength: 8)
                    GreenSwitch(isOn: Binding(get: { m.launchd.installed },
                                              set: { m.autoToggle($0) }))
                }

                rowDesc("开启后由 macOS 按下面的时间点自动执行签到；关闭时只保留打开应用后的手动签到。")
                    .padding(.top, 6)

                // 签到时间（整行自己占一行：时间片贴着标题走，编辑条在正下方展开，
                // 不再像旧版那样点右边的片、编辑框跑到左边去）
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 12) {
                        rowTitle("签到时间")
                        Spacer(minLength: 12)
                        timeChips
                    }
                    if let slot = editingSlot {
                        timeEditor(slot)
                    } else {
                        rowDesc(nextTimeHint)
                    }
                }
                .padding(.vertical, 11)

                settingRow {
                    rowTitle("签到频率")
                } right: {
                    Menu {
                        ForEach(1...max(5, times.count), id: \.self) { n in
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

                settingRow {
                    VStack(alignment: .leading, spacing: 3) {
                        rowTitle("执行日")
                        if m.pref.weekdays.count <= 1 {
                            Text("至少保留一天，否则定时任务不会执行")
                                .font(.system(size: 11.5)).foregroundColor(Theme.warn)
                        } else if m.pref.weekdays.count == 7 {
                            rowDesc("每天都会执行")
                        } else {
                            rowDesc("每周 \(m.pref.weekdays.count) 天")
                        }
                    }
                } right: {
                    HStack(spacing: 8) {
                        ForEach(1...7, id: \.self) { d in
                            Button { toggleWeekday(d) } label: {
                                Text(weekLabel(d))
                                    .font(.system(size: 13, weight: m.pref.weekdays.contains(d) ? .semibold : .regular))
                                    .foregroundColor(m.pref.weekdays.contains(d) ? .white : Theme.textSub)
                                    .frame(width: 34, height: 34)
                                    .background(m.pref.weekdays.contains(d) ? Color.black : Theme.chipBG)
                                    .clipShape(Circle())
                                    .contentShape(Circle())
                            }
                            .buttonStyle(.plain)
                            .help(m.pref.weekdays.contains(d) ? "取消勾选\(weekLabel(d))" : "勾选\(weekLabel(d))")
                        }
                    }
                }

                settingRow {
                    VStack(alignment: .leading, spacing: 3) {
                        rowTitle("错峰延迟")
                        rowDesc("启动后随机等一会儿再跑，避免同一时刻集中请求")
                    }
                } right: {
                    Menu {
                        ForEach(staggerOptions, id: \.0) { opt in
                            Button(opt.1) { set(\.staggerMinutes, opt.0) }
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

    // MARK: 时间片
    private var timeChips: some View {
        HStack(spacing: 8) {
            ForEach(times) { slot in
                Button { toggleEdit(slot.id) } label: {
                    Text(slot.text)
                }
                .buttonStyle(ChipButtonStyle(selected: editingID == slot.id))
                // 下一个将要执行的时间点用红圈标出来（是"即将执行"，不是"已选中"，
                // 所以用描边而不是黑底 —— 旧版把两者画成同一种黑底，很容易误解）
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.chipCorner, style: .continuous)
                        .stroke(Theme.accent, lineWidth: 1.4)
                        .opacity(isNext(slot) && editingID != slot.id ? 1 : 0)
                        .allowsHitTesting(false)
                )
                .help(editingID == slot.id ? "收起调整面板" : "点击调整这个时间点")
            }

            Button { addTime() } label: {
                HStack(spacing: 4) {
                    Image(systemName: "plus").font(.system(size: 10, weight: .semibold))
                    Text("添加")
                }
                .font(.system(size: 12.5))
                .foregroundColor(times.count >= maxTimes ? Theme.textFaint : Theme.textBody)
                .padding(.horizontal, 12)
                .frame(height: 30)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: Theme.chipCorner, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Theme.chipCorner, style: .continuous)
                    .stroke(Theme.border, lineWidth: 1))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(times.count >= maxTimes)
            .help(times.count >= maxTimes ? "最多 \(maxTimes) 个时间点" : "再加一个时间点")
        }
    }

    private var editingSlot: TimeSlot? {
        guard let id = editingID else { return nil }
        return times.first { $0.id == id }
    }

    /// 展开在签到时间行正下方的调整条（时 / 分两个下拉，点一下即写回并落盘）
    private func timeEditor(_ slot: TimeSlot) -> some View {
        HStack(spacing: 8) {
            Text("调整")
                .font(.system(size: 11.5))
                .foregroundColor(Theme.textSub)

            Menu {
                ForEach(0...23, id: \.self) { h in
                    Button(String(format: "%02d 时", h)) { setHour(slot.id, h) }
                }
            } label: {
                SelectBox { Text(String(format: "%02d 时", slot.hour)).font(.system(size: 13)).foregroundColor(Theme.text) }
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()

            Menu {
                ForEach(minuteOptions(for: slot.minute), id: \.self) { mi in
                    Button(String(format: "%02d 分", mi)) { setMinute(slot.id, mi) }
                }
            } label: {
                SelectBox { Text(String(format: "%02d 分", slot.minute)).font(.system(size: 13)).foregroundColor(Theme.text) }
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()

            Button { removeTime(slot.id) } label: {
                HStack(spacing: 4) {
                    Image(systemName: "trash").font(.system(size: 11))
                    Text("删除").font(.system(size: 12))
                }
                .foregroundColor(times.count <= 1 ? Theme.textFaint : Theme.accent)
                .padding(.horizontal, 10)
                .frame(height: 32)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(times.count <= 1)
            .help(times.count <= 1 ? "至少要保留一个时间点" : "删除这个时间点")

            Spacer(minLength: 8)

            Button { endEdit() } label: {
                Text("完成")
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundColor(Theme.accent)
                    .padding(.horizontal, 10)
                    .frame(height: 32)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Theme.chipBG)
        .clipShape(RoundedRectangle(cornerRadius: Theme.chipCorner, style: .continuous))
        .transition(.opacity)
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

                toggleRow("启动应用时自动签到", "打开喵签签后立即检查今日签到状态", bind(\.autoSignOnLaunch))
                toggleRow("签到失败自动重试", "失败账号自动重试一轮，间隔约 10 秒", bind(\.retryOnFail))
                toggleRow("签到完成后发送系统通知", "在 macOS 通知中心显示每个账号的签到结果", bind(\.notifyOnComplete))
                toggleRow("每日汇总提醒", "每天 \(digestTimeText) 提醒未完成的账号",
                          bind(\.dailyDigestReminder, after: { on in
                              if on { m.scheduleDailyDigest() } else { m.cancelDailyDigest() }
                          }))
            }
        }
    }

    private func toggleRow(_ title: String, _ desc: String, _ binding: Binding<Bool>) -> some View {
        HStack(alignment: .center, spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                rowTitle(title)
                rowDesc(desc)
            }
            Spacer(minLength: 12)
            GreenSwitch(isOn: binding)
        }
        .padding(.vertical, 10)
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

    private var enabledToggleCount: Int {
        [m.pref.autoSignOnLaunch, m.pref.retryOnFail, m.pref.notifyOnComplete, m.pref.dailyDigestReminder]
            .filter { $0 }.count
    }

    /// 汇总提醒实际触发在**最后一个签到时间之后 1 小时**（见 AppModel.scheduleDailyDigest），
    /// 文案必须跟着算，不能直接写最后一个签到时间
    private var digestTimeText: String {
        guard let last = times.map({ $0.minutes }).max() else { return "22:30" }
        let total = (last + 60) % (24 * 60)
        return String(format: "%02d:%02d", total / 60, total % 60)
    }

    // MARK: 定时策略预览
    private var previewCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                CardTitle("定时策略预览") {
                    Text(m.pref.weekdays.count == 7 ? "每天" : "每周 \(m.pref.weekdays.count) 天")
                        .font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                }

                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 8) {
                        Text("下次自动签到")
                            .font(.system(size: 12.5))
                            .foregroundColor(Theme.darkCaption)
                        Spacer(minLength: 8)
                        HStack(spacing: 6) {
                            // 服务没被 launchd 加载时不要显示倒计时 —— 那个时间根本不会触发
                            Dot(color: m.launchd.loaded ? Theme.success
                                 : (m.launchd.installed ? Theme.warn : Theme.neutral), size: 7)
                            Text(m.launchd.loaded ? "\(m.countdown())后"
                                 : (m.launchd.installed ? "安装未生效" : "未开启"))
                                .font(.system(size: 12))
                                .foregroundColor(.white)
                        }
                        .padding(.horizontal, 10)
                        .frame(height: 24)
                        .background(Color.white.opacity(0.08))
                        .clipShape(Capsule())
                    }
                    Text(nextTimeText)
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

    /// 与时间片同源（草稿 → 立即落盘，两者恒等），不再读 launchd plist 里的首个时间
    private var nextTimeText: String {
        m.launchd.installed ? m.nextFireText() : "未开启定时签到"
    }

    private struct PlanRow { var time: String; var status: String; var color: Color; var statusColor: Color }

    private var planRows: [PlanRow] {
        let list = times.sorted { $0.minutes < $1.minutes }
        let today = DayTool.today()
        return list.map { t in
            let ref = t.text
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
                    VStack(alignment: .leading, spacing: 3) {
                        rowTitle("积分刷新频率")
                        rowDesc("应用在前台时按此频率自动同步一次余额")
                    }
                    Spacer(minLength: 12)
                    Menu {
                        ForEach(refreshMenuOptions, id: \.self) { n in
                            Button("每 \(n) 分钟") { set(\.creditsRefreshMinutes, n) }
                        }
                    } label: {
                        SelectBox {
                            Text("每 \(m.pref.creditsRefreshMinutes) 分钟").font(.system(size: 13)).foregroundColor(Theme.text)
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
                    GreenSwitch(isOn: bind(\.syncHistoryCreditsOnLogin))
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

    /// 选项固定的同时兜住手工改过配置文件的情况（否则当前值不在菜单里，看着像没选中）
    private var refreshMenuOptions: [Int] {
        var list = refreshOptions
        if !list.contains(m.pref.creditsRefreshMinutes) {
            list.append(m.pref.creditsRefreshMinutes)
            list.sort()
        }
        return list
    }

    // MARK: 数据
    private var staggerOptions: [(Int, String)] {
        [(0, "不延迟"), (15, "0 - 15 分钟随机"), (30, "0 - 30 分钟随机"), (60, "0 - 60 分钟随机")]
    }
    private var staggerLabel: String {
        staggerOptions.first(where: { $0.0 == m.pref.staggerMinutes })?.1 ?? "0 - \(m.pref.staggerMinutes) 分钟随机"
    }

    /// 还没执行的最近一个时间点（在片子上画一圈红框）
    private func isNext(_ slot: TimeSlot) -> Bool {
        guard m.launchd.installed else { return false }
        let sorted = times.sorted { $0.minutes < $1.minutes }
        let target = sorted.first(where: { $0.minutes == m.nextFireMinutes() })
        return target?.id == slot.id
    }

    private var nextTimeHint: String {
        guard m.launchd.installed else {
            return "定时签到未开启 · 点击任意时间片可先调好时间"
        }
        return "下一次 \(m.nextFireText()) 触发 · 点击时间片可调整"
    }

    private func minuteOptions(for current: Int) -> [Int] {
        var list = Array(stride(from: 0, through: 55, by: 5))
        if !list.contains(current) { list.append(current); list.sort() }
        return list
    }

    private func weekLabel(_ d: Int) -> String {
        ["一", "二", "三", "四", "五", "六", "日"][max(0, min(6, d - 1))]
    }

    // MARK: 读写
    /// 把偏好字段包成"写完立刻落盘"的 binding —— 这是"即时生效"的实现基座
    private func bind<T>(_ keyPath: WritableKeyPath<AppPrefs, T>,
                         after: ((T) -> Void)? = nil) -> Binding<T> {
        Binding(
            get: { m.pref[keyPath: keyPath] },
            set: { v in
                m.pref[keyPath: keyPath] = v
                after?(v)
                applyChange()
            }
        )
    }

    private func set<T>(_ keyPath: WritableKeyPath<AppPrefs, T>, _ value: T) {
        m.pref[keyPath: keyPath] = value
        applyChange()
    }

    private func reload() {
        let list = m.pref.times.isEmpty ? [[21, 30]] : m.pref.times
        times = list
            .map { TimeSlot(hour: max(0, min(23, $0.first ?? 21)),
                            minute: max(0, min(59, $0.count > 1 ? $0[1] : 30))) }
            .sorted { $0.minutes < $1.minutes }
        editingID = nil
    }

    private func applyChange() {
        m.pref.save()
        m.syncLaunchdIfNeeded()
        pulseSaved()
    }

    private func pulseSaved() {
        withAnimation(.easeOut(duration: 0.15)) { savedPulse = true }
        pulseToken += 1
        let token = pulseToken
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) {
            if pulseToken == token {
                withAnimation(.easeIn(duration: 0.3)) { savedPulse = false }
            }
        }
    }

    // MARK: 时间片动作
    private func toggleEdit(_ id: TimeSlot.ID) {
        if editingID == id {
            endEdit()
        } else {
            editingID = id
        }
    }

    private func endEdit() {
        editingID = nil
        times.sort { $0.minutes < $1.minutes }
        commitTimes()
    }

    private func setHour(_ id: TimeSlot.ID, _ h: Int) {
        guard let i = times.firstIndex(where: { $0.id == id }) else { return }
        times[i].hour = h
        commitTimes()
    }

    private func setMinute(_ id: TimeSlot.ID, _ m: Int) {
        guard let i = times.firstIndex(where: { $0.id == id }) else { return }
        times[i].minute = m
        commitTimes()
    }

    private func addTime() {
        guard times.count < maxTimes else { return }
        let base = times.sorted { $0.minutes < $1.minutes }.last ?? TimeSlot(hour: 21, minute: 0)
        let slot = shifted(base, by: 180)
        times.append(slot)
        times.sort { $0.minutes < $1.minutes }
        editingID = slot.id
        commitTimes()
    }

    private func removeTime(_ id: TimeSlot.ID) {
        guard times.count > 1 else { return }   // 至少留一个，不再静默重置成 21:30
        times.removeAll { $0.id == id }
        if editingID == id { editingID = nil }
        commitTimes()
    }

    private func setFrequency(_ n: Int) {
        var list = times.sorted { $0.minutes < $1.minutes }
        while list.count > n { list.removeLast() }
        while list.count < n {
            let base = list.last ?? TimeSlot(hour: 21, minute: 0)
            list.append(shifted(base, by: 180))
        }
        list.sort { $0.minutes < $1.minutes }
        times = list
        editingID = nil
        commitTimes()
    }

    /// 往后推 delta 分钟；跨过零点就往前挪，避免出现 00:30 这种"其实是第二天"的时间点
    private func shifted(_ slot: TimeSlot, by delta: Int) -> TimeSlot {
        var total = slot.minutes + delta
        if total >= 24 * 60 { total = max(0, slot.minutes - delta) }
        return TimeSlot(hour: total / 60, minute: total % 60)
    }

    private func commitTimes() {
        m.pref.times = times.map { [$0.hour, $0.minute] }
            .sorted { ($0[0], $0[1]) < ($1[0], $1[1]) }
        applyChange()
    }

    // MARK: 其他动作
    private func toggleWeekday(_ d: Int) {
        var set = m.pref.weekdays
        if set.contains(d) {
            guard set.count > 1 else { return }   // 全取消 = 定时任务永不触发，直接拦掉
            set.removeAll { $0 == d }
        } else {
            set.append(d)
        }
        m.pref.weekdays = set.sorted()
        applyChange()
    }

    private func restoreDefaults() {
        m.pref = AppPrefs()
        m.pref.save()
        reload()
        m.syncLaunchdIfNeeded()
        m.cancelDailyDigest()
        m.showToast("已恢复默认设置", .info)
        pulseSaved()
    }

    private func exportCSV() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "checkin_records.csv"
        if panel.runModal() == .OK, let url = panel.url { m.exportCSV(to: url) }
    }

    private func exportConfig() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "accounts_export.json"
        panel.message = "导出的文件包含各平台的登录凭据（Cookie / Token），请妥善保管。"
        if panel.runModal() == .OK, let url = panel.url { m.exportConfig(to: url) }
    }
}
