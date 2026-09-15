import SwiftUI
import AppKit

// MARK: ==================== 02 账号管理 ====================
struct AccountsView: View {
    @EnvironmentObject var m: AppModel
    @State private var search: String = ""
    @State private var filter: String = "全部"
    @State private var sortRecent = false
    @State private var showImport = false

    private static let colStatus: CGFloat = 100
    private static let colRecent: CGFloat = 108
    private static let colSwitch: CGFloat = 62
    private static let colAction: CGFloat = 76
    private static let hpad: CGFloat = 15

    var body: some View {
        PageScroll(spacing: 17) {
            PageHeader("账号管理", subtitle: headerSubtitle) {
                Button { showImport = true } label: {
                    Label("批量导入", systemImage: "square.and.arrow.down")
                }
                .buttonStyle(GhostButtonStyle())

                Button { m.showingAddPanel.toggle() } label: {
                    Label("新增账号", systemImage: "plus")
                }
                .buttonStyle(PrimaryButtonStyle())
            }

            // 工具条
            Card(padding: nil) {
                HStack(spacing: 14) {
                    HStack(spacing: 8) {
                        Image(systemName: "magnifyingglass")
                            .font(.system(size: 13))
                            .foregroundColor(Theme.textSub)
                        TextField("搜索账号、平台或备注", text: $search)
                            .textFieldStyle(.plain)
                            .font(.system(size: 13))
                            .foregroundColor(Theme.inputText)
                    }
                    .padding(.horizontal, 13)
                    .frame(height: 40)
                    .background(Theme.chipBG)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .frame(width: 290)

                    SegmentedControl(
                        items: [.init("全部", "全部", badge: "\(m.accounts.count)"),
                                .init("已启用", "已启用", badge: "\(m.enabledAccounts().count)"),
                                .init("已停用", "已停用", badge: "\(m.accounts.count - m.enabledAccounts().count)")],
                        selection: $filter
                    )

                    Button { sortRecent.toggle() } label: {
                        Label("按最近签到", systemImage: "arrow.up.arrow.down")
                    }
                    .buttonStyle(GhostButtonStyle(fg: sortRecent ? Theme.text : Theme.textSub))

                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 15)
                .frame(height: 56)
            }

            HStack(alignment: .top, spacing: 23) {
                VStack(spacing: 17) {
                    accountListCard
                    healthCard
                }
                .frame(maxWidth: .infinity)

                // 右侧栏只承载「新增账号」面板。面板收起时整栏不再占位，
                // 列表随之铺满整宽 —— 原来的「签到状态摘要」卡已按要求移除
                // （签到进度属于签到页，与账号管理无关）。
                if m.showingAddPanel {
                    AddAccountPanel()
                        .frame(width: 358)
                }
            }
        }
        .background(Theme.pageBG)
        .animation(.easeOut(duration: 0.18), value: m.showingAddPanel)
        .fileImporter(isPresented: $showImport, allowedContentTypes: [.json, .plainText, .text]) { result in
            if case .success(let url) = result { m.importAccounts(path: url.path) }
        }
    }

    private var headerSubtitle: String {
        var s = "共 \(m.accounts.count) 个平台账号 · \(m.enabledAccounts().count) 个已启用"
        if let t = m.lastSyncTime() { s += " · 数据最后同步 今天 \(t)" }
        return s
    }

    // MARK: 账号列表
    private var accountListCard: some View {
        let rows = filteredAccounts()
        let groups = groupedAccounts(rows)
        return Card(padding: nil) {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    Text("账号列表")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    SoftTag(text: "\(rows.count) 个账号")
                    Spacer(minLength: 8)
                    HStack(spacing: 5) {
                        Image(systemName: "info.circle").font(.system(size: 11))
                        Text("点击行可编辑，列表展示最近一次签到结果").font(.system(size: 11.5))
                    }
                    .foregroundColor(Theme.textSub)
                }
                .padding(.horizontal, AccountsView.hpad)
                .padding(.top, 16)
                .padding(.bottom, 12)

                HStack(spacing: 0) {
                    Text("账号").frame(maxWidth: .infinity, alignment: .leading)
                    Text("签到状态").frame(width: AccountsView.colStatus, alignment: .leading)
                    Text("最近签到").frame(width: AccountsView.colRecent, alignment: .leading)
                    Text("启用").frame(width: AccountsView.colSwitch, alignment: .leading)
                    Text("操作").frame(width: AccountsView.colAction, alignment: .leading)
                }
                .font(.system(size: 12))
                .foregroundColor(.white)
                .padding(.horizontal, AccountsView.hpad)
                .frame(height: 34)
                .background(Theme.sidebar)

                if rows.isEmpty {
                    Text("没有匹配的账号")
                        .font(.system(size: 13))
                        .foregroundColor(Theme.textSub)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 40)
                } else {
                    ForEach(groups) { g in
                        groupHeader(g)
                        ForEach(Array(g.accounts.enumerated()), id: \.element.id) { idx, acc in
                            AccountRowView(acc: acc,
                                           statusW: AccountsView.colStatus,
                                           recentW: AccountsView.colRecent,
                                           switchW: AccountsView.colSwitch,
                                           actionW: AccountsView.colAction,
                                           hpad: AccountsView.hpad)
                            if idx != g.accounts.count - 1 {
                                Divider().overlay(Theme.hairline).padding(.horizontal, AccountsView.hpad)
                            }
                        }
                    }
                }

                Divider().overlay(Theme.hairline)
                HStack(spacing: 10) {
                    Text("共 \(rows.count) 个账号 · \(m.enabledAccounts().count) 个已启用" +
                         (m.lastSyncTime().map { " · 最后同步 今天 \($0)" } ?? ""))
                        .font(.system(size: 11.5))
                        .foregroundColor(Theme.textSub)
                    Spacer(minLength: 8)
                    Button {
                        m.refreshAll(loadCredits: true)
                        m.showToast("列表已刷新", .success)
                    } label: {
                        Label("刷新列表", systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(GhostButtonStyle(fg: Theme.textBody))
                }
                .padding(.horizontal, AccountsView.hpad)
                .padding(.vertical, 12)
            }
        }
    }

    // MARK: 凭证健康检查
    private var healthCard: some View {
        let bad = m.accounts.filter { acc in
            if acc.isWorkBuddy { return m.wbHealthy == false }
            if let hint = acc.credentialHint() { return hint.color != .green }
            return false
        }
        return Card(padding: nil) {
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.warnSoft)
                    Image(systemName: "checkmark.shield")
                        .font(.system(size: 16))
                        .foregroundColor(Theme.warn)
                }
                .frame(width: 40, height: 40)

                VStack(alignment: .leading, spacing: 3) {
                    Text("凭证健康检查")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(Theme.text)
                    Text(bad.isEmpty
                         ? "\(m.accounts.count) 个账号凭证均正常，可正常参与自动签到"
                         : "\(m.accounts.count) 个账号中 \(bad.count) 个凭证已失效，可能影响自动签到")
                        .font(.system(size: 11.5))
                        .foregroundColor(Theme.textSub)
                }
                Spacer(minLength: 8)
                Button {
                    m.probeWorkBuddy()
                    m.refreshCredits()
                    m.refreshAll()
                    m.showToast("已刷新凭证与积分状态", .info)
                } label: {
                    Label("立即检查", systemImage: "arrow.clockwise")
                }
                .buttonStyle(GhostButtonStyle())
            }
            .padding(.horizontal, 15)
            .padding(.vertical, 16)
        }
    }

    private func filteredAccounts() -> [Account] {
        var list = m.accounts
        if !search.isEmpty {
            list = list.filter {
                $0.name.localizedCaseInsensitiveContains(search) || $0.app.localizedCaseInsensitiveContains(search)
            }
        }
        switch filter {
        case "已启用": list = list.filter { $0.isEnabled }
        case "已停用": list = list.filter { !$0.isEnabled }
        default: break
        }
        if sortRecent {
            list = list.sorted { a, b in
                let ta = m.parsed.lastHit(name: a.name)
                let tb = m.parsed.lastHit(name: b.name)
                let ka = (ta?.date ?? "") + (ta?.time ?? "")
                let kb = (tb?.date ?? "") + (tb?.time ?? "")
                return ka > kb
            }
        }
        return list
    }

    /// 按平台分组：Trae → WorkBuddy → Bilibili → 联想智选 → 京东 → 其他平台（组内按账号名排序）。
    /// 分组键复用 `Account.platformIconName`，保证分组与头像图标同源，不会各写一套判断。
    private func groupedAccounts(_ rows: [Account]) -> [AccountGroup] {
        let buckets = [
            AccountGroup(id: "trae", title: "Trae", iconName: "trae", accounts: []),
            AccountGroup(id: "workbuddy", title: "WorkBuddy", iconName: "workbuddy", accounts: []),
            AccountGroup(id: "bilibili", title: "Bilibili", iconName: "bilibili", accounts: []),
            AccountGroup(id: "lenovo", title: "联想智选", iconName: "lenovo", accounts: []),
            AccountGroup(id: "jd", title: "京东", iconName: "jd", accounts: []),
            AccountGroup(id: "other", title: "其他平台", iconName: nil, accounts: []),
        ]
        return buckets.compactMap { bucket in
            var g = bucket
            g.accounts = rows
                .filter { ($0.platformIconName ?? "other") == bucket.id }
                .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
            return g.accounts.isEmpty ? nil : g
        }
    }

    /// 分组标题行：平台图标 + 平台名 + 数量，右侧是该组签到进度
    private func groupHeader(_ g: AccountGroup) -> some View {
        HStack(spacing: 7) {
            if let img = PlatformIcon.image(g.iconName) {
                Image(nsImage: img)
                    .resizable()
                    .interpolation(.high)
                    .frame(width: 16, height: 16)
                    .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            } else {
                Image(systemName: "square.stack.3d.up")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.textSub)
                    .frame(width: 16, height: 16)
            }
            Text(g.title)
                .font(.system(size: 12.5, weight: .semibold))
                .foregroundColor(Theme.text)
            Text("\(g.accounts.count)")
                .font(.system(size: 11.5, weight: .medium))
                .foregroundColor(Theme.textSub)
                .padding(.horizontal, 6)
                .frame(height: 18)
                .background(Theme.chipBG)
                .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))
            Spacer(minLength: 8)
            Text(groupSummary(g))
                .font(.system(size: 11.5))
                .foregroundColor(Theme.textSub)
        }
        .padding(.horizontal, AccountsView.hpad)
        .frame(height: 34)
        .background(Color(hex: 0xFAFAFA))
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.hairline).frame(height: 1)
        }
    }

    private func groupSummary(_ g: AccountGroup) -> String {
        let enabled = g.accounts.filter { $0.isEnabled }
        if enabled.isEmpty { return "全部已停用" }
        let done = enabled.filter { m.accStatus($0.name).state == "done" }.count
        let fail = enabled.filter { m.accStatus($0.name).state == "fail" }.count
        let restricted = enabled.filter { m.accStatus($0.name).state == "restricted" }.count
        var parts = ["\(done)/\(enabled.count) 已完成"]
        if restricted > 0 { parts.append("\(restricted) 个平台受限") }
        if fail > 0 { parts.append("\(fail) 个失败") }
        return parts.joined(separator: " · ")
    }
}

/// 账号列表的平台分组（纯展示用，不改变数据模型与 accounts.json）
private struct AccountGroup: Identifiable {
    let id: String
    let title: String
    let iconName: String?
    var accounts: [Account]
}

// MARK: - 账号行
struct AccountRowView: View {
    @EnvironmentObject var m: AppModel
    let acc: Account
    let statusW: CGFloat
    let recentW: CGFloat
    let switchW: CGFloat
    let actionW: CGFloat
    let hpad: CGFloat

    @State private var editing = false
    @State private var newName: String = ""
    @State private var confirmDelete = false
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 0) {
            HStack(spacing: 11) {
                PlatformAvatar(text: letter, tint: avatarTint, bg: avatarBG, size: 34,
                               iconName: acc.platformIconName, dimmed: !acc.isEnabled)
                VStack(alignment: .leading, spacing: 3) {
                    if editing {
                        HStack(spacing: 6) {
                            TextField("账号名", text: $newName)
                                .textFieldStyle(.plain)
                                .font(.system(size: 13))
                                .foregroundColor(Theme.inputText)
                                .frame(width: 130)
                                .padding(.horizontal, 8)
                                .frame(height: 26)
                                .background(Theme.chipBG)
                                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                            Button("保存") { m.renameAccount(acc, to: newName); editing = false }
                                .buttonStyle(.plain)
                                .font(.system(size: 12.5, weight: .semibold))
                                .foregroundColor(Theme.text)
                            Button("取消") { editing = false }
                                .buttonStyle(.plain)
                                .font(.system(size: 12.5))
                                .foregroundColor(Theme.textSub)
                        }
                    } else {
                        Text(acc.name)
                            .font(.system(size: 13.5, weight: .semibold))
                            .foregroundColor(acc.isEnabled ? Theme.text : Theme.textSub)
                    }
                    subLine
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            statusView.frame(width: statusW, alignment: .leading)

            Text(recentText)
                .font(.system(size: 13))
                .foregroundColor(Theme.textBody)
                .frame(width: recentW, alignment: .leading)

            Toggle("", isOn: Binding(get: { acc.isEnabled }, set: { m.toggleEnabled(acc, on: $0) }))
                .toggleStyle(.switch)
                .tint(Theme.success)
                .labelsHidden()
                .overlay(Capsule().stroke(acc.isEnabled ? Color.clear : Color(hex: 0xCFCFCF), lineWidth: 1))
                .frame(width: switchW, alignment: .leading)

            HStack(spacing: 12) {
                Button { editing = true; newName = acc.name } label: {
                    Image(systemName: "pencil").font(.system(size: 12.5))
                }
                .buttonStyle(.plain).foregroundColor(Theme.textSub).help("重命名")

                Button { confirmDelete = true } label: {
                    Image(systemName: "trash").font(.system(size: 12.5))
                }
                .buttonStyle(.plain).foregroundColor(Theme.textSub).help("删除")
            }
            .frame(width: actionW, alignment: .leading)
        }
        .padding(.horizontal, hpad)
        .frame(height: 58)
        .background(hovering ? Color(hex: 0xF7F7F7) : Color.clear)
        .contentShape(Rectangle())
        .onTapGesture { if !editing { editing = true; newName = acc.name } }
        .onHover { hovering = $0 }
        .confirmationDialog("确认删除账号「\(acc.name)」？", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("删除", role: .destructive) { m.deleteAccount(acc) }
        }
    }

    private var letter: String {
        let src = acc.app.isEmpty ? acc.name : acc.app
        return String(src.prefix(1)).uppercased()
    }
    private var state: String { m.accStatus(acc.name).state }
    private var avatarTint: Color {
        if !acc.isEnabled { return Theme.textSub }
        switch state {
        case "fail": return Theme.accent
        case "restricted": return Theme.warn
        case "done": return Theme.text
        default: return Theme.textSub
        }
    }
    private var avatarBG: Color {
        if !acc.isEnabled { return Theme.chipBG }
        switch state {
        case "fail": return Theme.accentSoft
        case "restricted": return Theme.warnSoft
        default: return Theme.chipBG
        }
    }

    @ViewBuilder
    private var subLine: some View {
        let st = m.accStatus(acc.name)
        let streak = ParsedLogs.streak(for: acc.name, logs: m.parsed, startDate: DayTool.today())
        HStack(spacing: 5) {
            Text(acc.credentialSummary()).font(.system(size: 11.5)).foregroundColor(Theme.textSub)
            Text("·").font(.system(size: 11.5)).foregroundColor(Theme.textFaint)
            if !acc.isEnabled {
                Text("已暂停自动签到").font(.system(size: 11.5)).foregroundColor(Theme.textSub)
            } else if (st.state == "fail" || st.state == "restricted"), let hit = st.hit, !hit.msg.isEmpty {
                // 受限也把原因摆出来（"京东侧限制：…"），避免用户以为是自己的 cookie 坏了
                Text(hit.msg)
                    .font(.system(size: 11.5))
                    .foregroundColor(st.state == "restricted" ? Theme.warn : Theme.accent)
                    .lineLimit(1)
            } else {
                Text("连续 \(streak) 天").font(.system(size: 11.5)).foregroundColor(Theme.textSub)
            }
        }
    }

    @ViewBuilder
    private var statusView: some View {
        if !acc.isEnabled {
            StatusPill(text: "已停用", color: Theme.neutral, bg: Theme.chipBG)
        } else {
            switch state {
            case "done": StatusPill(text: "已完成", color: Theme.success, bg: Theme.successSoft)
            case "fail": StatusPill(text: "签到失败", color: Theme.accent, bg: Theme.accentSoft)
            case "restricted": StatusPill(text: "平台受限", color: Theme.warn, bg: Theme.warnSoft)
            default:     StatusPill(text: "待签到", color: Theme.neutral, bg: Theme.chipBG)
            }
        }
    }

    private var recentText: String {
        if !acc.isEnabled { return "—" }
        let st = m.accStatus(acc.name)
        if st.state == "pending" {
            guard let last = m.parsed.lastHit(name: acc.name) else { return "尚未签到" }
            return last.date == DayTool.today() ? "今天 \(last.time)" : "\(shortDate(last.date)) \(last.time)"
        }
        guard let h = st.hit else { return "—" }
        return h.date == DayTool.today() ? "今天 \(h.time)" : "\(shortDate(h.date)) \(h.time)"
    }
    private func shortDate(_ d: String) -> String {
        let p = d.split(separator: "-")
        guard p.count == 3 else { return d }
        return "\(Int(p[1]) ?? 0)/\(Int(p[2]) ?? 0)"
    }
}

// MARK: - 新增账号面板
struct AddAccountPanel: View {
    @EnvironmentObject var m: AppModel
    @State private var name: String = ""
    @State private var browserIdx = 0           // 「浏览器登录」的平台选择（Trae + Cookie 型平台）
    @State private var platOpen = false         // 平台下拉是否展开（popover）
    @State private var cookie: String = ""
    @State private var autoEnable = true

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("新增账号")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Button { m.showingAddPanel = false } label: {
                        Image(systemName: "xmark").font(.system(size: 12, weight: .medium))
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(Theme.textSub)
                }

                browserForm
                    .padding(.top, 16)
            }
        }
    }

    private func label(_ t: String) -> some View {
        FormLabel(text: t).frame(maxWidth: .infinity, alignment: .leading)
    }

    /// 浏览器登录：WorkBuddy 扫码 / Trae / Cookie 型平台对用户是同一个动作
    /// （弹出内置浏览器 → 登录 → 抓登录态），故合并成一个入口，用平台下拉切换。
    /// 账号名称可留空：脚本会读登录结果自动命名（昵称 → 账号 ID → auto）。
    private var browserForm: some View {
        let cur = browserOptions[browserIdx]
        return VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 7) {
                label("平台")
                // 用 Button + popover，而不是 Menu：
                // SwiftUI 的 Menu 标签会忽略内容里的 .frame()，里面的平台图标会按
                // NSImage 的自然点尺寸绘制（最大 512pt），把整个面板撑爆、顺带把
                // 左侧账号列表挤到裁切。Button 的标签正常遵守 frame（实测 89×32，
                // 与设计稿一致），图标能稳稳待在 14pt。
                Button { platOpen.toggle() } label: {
                    SelectBox {
                        HStack(spacing: 7) {
                            platformGlyph(cur, size: 14)
                            Text(cur.label).font(.system(size: 13)).foregroundColor(Theme.text)
                        }
                    }
                }
                .buttonStyle(.plain)
                .popover(isPresented: $platOpen, arrowEdge: .bottom) { platformMenu }
            }

            VStack(alignment: .leading, spacing: 7) {
                label("账号名称（可不填）")
                FieldBox {
                    TextField(cur.placeholder, text: $name)
                        .textFieldStyle(.plain).font(.system(size: 13)).foregroundColor(Theme.inputText)
                }
            }

            HStack(spacing: 8) {
                Text("加入自动签到").font(.system(size: 13)).foregroundColor(Theme.textBody)
                Spacer(minLength: 8)
                GreenSwitch(isOn: $autoEnable)
            }

            Button {
                switch cur.kind {
                case .workbuddy:
                    m.runWorkBuddyOAuth(name: name, enabled: autoEnable)
                case .trae:
                    m.runTraeLogin(name: name, enabled: autoEnable)
                case .cookie:
                    m.runCookieBrowserLogin(type: cur.type, name: name, enabled: autoEnable)
                }
            } label: {
                Label(m.loginRunning ? "正在等待登录完成…" : "打开登录浏览器", systemImage: "globe")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(m.loginRunning)

            Text(cur.hint)
                .font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                .fixedSize(horizontal: false, vertical: true)

            // 平台专属的次级路径（都不弹浏览器）：
            //   WorkBuddy -> 直接读本机桌面端登录态
            //   Cookie 型 -> 手动粘贴 Cookie
            //   Trae      -> 无（Trae 只有浏览器登录一条路）
            switch cur.kind {
            case .workbuddy:
                workbuddyLocalSection
            case .cookie:
                manualCookieSection(cur)
            case .trae:
                EmptyView()
            }
        }
    }

    /// 平台角标：有真实图标用图标，否则回退 SF Symbol
    @ViewBuilder
    private func platformGlyph(_ o: BrowserOption, size: CGFloat) -> some View {
        if let img = PlatformIcon.image(o.iconName) {
            Image(nsImage: img)
                .resizable()
                .interpolation(.high)
                .frame(width: size, height: size)
                .clipShape(RoundedRectangle(cornerRadius: size * 0.25, style: .continuous))
        } else {
            Image(systemName: o.symbol)
                .font(.system(size: size * 0.72))
                .foregroundColor(Theme.textSub)
                .frame(width: size, height: size)
        }
    }

    /// 平台下拉的内容（popover）。用 Button 实现，避免 Menu 标签忽略 .frame() 的问题。
    private var platformMenu: some View {
        VStack(alignment: .leading, spacing: 1) {
            ForEach(Array(browserOptions.enumerated()), id: \.element.type) { i, p in
                Button {
                    browserIdx = i
                    platOpen = false
                } label: {
                    HStack(spacing: 8) {
                        platformGlyph(p, size: 14)
                        Text(p.label)
                            .font(.system(size: 12.5))
                            .foregroundColor(Theme.text)
                        Spacer(minLength: 18)
                        if i == browserIdx {
                            Image(systemName: "checkmark")
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundColor(Theme.success)
                        }
                    }
                    .padding(.horizontal, 9)
                    .frame(height: 26)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(5)
        .frame(width: 188)
    }

    /// Cookie 型平台的「或手动粘贴 Cookie」次级路径
    @ViewBuilder
    private func manualCookieSection(_ cur: BrowserOption) -> some View {
        Divider().overlay(Theme.hairline).padding(.vertical, 2)
        VStack(alignment: .leading, spacing: 10) {
            Text("或手动粘贴 Cookie")
                .font(.system(size: 12.5, weight: .semibold))
                .foregroundColor(Theme.text)
            TextEditor(text: $cookie)
                .font(.system(size: 11.5, design: .monospaced))
                .foregroundColor(Theme.inputText)
                .scrollContentBackground(.hidden)
                .padding(8)
                .frame(height: 88)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: Theme.ctrlCorner, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Theme.ctrlCorner, style: .continuous)
                    .stroke(Theme.border, lineWidth: 1))
            HStack(spacing: 10) {
                Text("同名提交会覆盖更新，用于换号/续期。")
                    .font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                Spacer(minLength: 8)
                Button {
                    m.addCookieAccount(type: cur.type, name: name, cookie: cookie, enabled: autoEnable) { ok in
                        if ok { cookie = ""; name = "" }
                    }
                } label: {
                    Label("手动粘贴保存", systemImage: "checkmark")
                }
                .buttonStyle(GhostButtonStyle())
                .disabled(m.loginRunning
                          || name.trimmingCharacters(in: .whitespaces).isEmpty
                          || cookie.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
    }

    /// 「浏览器登录」的平台列表：WorkBuddy 扫码 / Trae / Cookie 型平台
    private var browserOptions: [BrowserOption] {        var list: [BrowserOption] = [
            BrowserOption(type: "workbuddy", label: "WorkBuddy", iconName: "workbuddy",
                          symbol: "qrcode.viewfinder",
                          placeholder: "留空则自动读取账号昵称",
                          hint: "将弹出内置浏览器打开授权页，扫码后自动换取并保存登录态；"
                              + "token 等同密码，仅本机存储。名称留空时自动以昵称命名。",
                          kind: .workbuddy),
            BrowserOption(type: "trae", label: "Trae", iconName: "trae",
                          symbol: "chevron.left.forwardslash.chevron.right",
                          placeholder: "留空则按账号 ID 自动命名",
                          hint: "将弹出内置浏览器窗口，登录后程序自动识别并保存登录态（约 14 天有效）。"
                              + "Trae 登录态不含昵称，名称留空时按账号 ID 命名。",
                          kind: .trae),
        ]
        for p in AppModel.cookiePlatforms {
            let extra = p.type == "lenovo"
                ? "联想智选也可在终端执行 python3 lenovo.py --login-account --name <账号名> 走账密登录。"
                : ""
            list.append(BrowserOption(type: p.type, label: p.label, iconName: p.type,
                                      symbol: cookieSymbol(p.type),
                                      placeholder: "留空则自动读取账号昵称",
                                      hint: "将弹出内置浏览器，扫码或账密登录后自动抓取 Cookie 并保存；"
                                          + "Cookie 等同密码，仅本机存储，列表/日志只显示脱敏摘要；"
                                          + "名称留空时自动以昵称命名。" + extra,
                                      kind: .cookie))
        }
        return list
    }

    private func cookieSymbol(_ type: String) -> String {
        switch type {
        case "bilibili": return "play.rectangle"
        case "lenovo": return "laptopcomputer"
        case "jd": return "cart"
        default: return "globe"
        }
    }

    /// WorkBuddy 的次级路径：直接读取本机桌面端已登录的账号，不弹浏览器。
    private var workbuddyLocalSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Divider().overlay(Theme.hairline).padding(.vertical, 2)

            Text("或读取本机桌面端登录态")
                .font(.system(size: 12.5, weight: .semibold))
                .foregroundColor(Theme.text)

            HStack(spacing: 8) {
                Dot(color: wbColor, size: 8)
                Text(wbText).font(.system(size: 12.5))
                    .foregroundColor(wbColor == Theme.accent ? Theme.accent : Theme.textBody)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(11)
            .background(wbColor.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

            HStack(spacing: 10) {
                Spacer(minLength: 0)
                Button { m.probeWorkBuddy() } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(GhostButtonStyle())
                    .disabled(m.loginRunning)
                Button {
                    m.addWorkBuddyAccount(name: name, enabled: autoEnable)
                } label: {
                    Label(m.loginRunning ? "正在读取…" : "读取本机登录态并添加",
                          systemImage: "person.crop.circle.badge.plus")
                }
                .buttonStyle(GhostButtonStyle())
                .disabled(m.loginRunning || m.wbHealthy != true)
            }

            Text("读取桌面端当前已登录的账号（不弹浏览器）；名称留空时自动用本机昵称命名。"
                 + "凭据安全：accessToken/refreshToken 等同账号密码，仅写入 accounts.json（.gitignore 排除），"
                 + "不写日志、不在界面回显明文。")
                .font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var wbColor: Color {
        switch m.wbHealthy {
        case .some(true): return Theme.success
        case .some(false): return Theme.accent
        default: return Theme.warn
        }
    }
    private var wbText: String {
        switch m.wbHealthy {
        case .some(true): return "已检测到本机 WorkBuddy 登录态" + (m.wbNickname.isEmpty ? "" : "（\(m.wbNickname)）")
        case .some(false): return "未检测到 WorkBuddy 登录态，请先打开桌面端登录后重新检测。"
        default: return "正在检测本机 WorkBuddy 登录态…"
        }
    }

}

/// 登录方式：三个平台各有自己的底层脚本，但对用户是同一个动作
/// （弹出内置浏览器 → 登录 → 抓登录态），所以共用一套 UI、用平台下拉切换。
///   - workbuddy -> workbuddy_login.py（OAuth device flow 扫码）
///   - trae      -> trae_login.py（抓 localStorage token + 长效会话 Cookie）
///   - cookie    -> browser_login.py（抓平台 Cookie）
private enum LoginKind { case workbuddy, trae, cookie }

/// 「浏览器登录」的平台选项
private struct BrowserOption {
    let type: String
    let label: String
    let iconName: String?
    let symbol: String
    let placeholder: String
    let hint: String
    let kind: LoginKind
}
