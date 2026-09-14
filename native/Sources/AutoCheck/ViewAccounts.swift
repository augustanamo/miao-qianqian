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

                VStack(spacing: 17) {
                    if m.showingAddPanel {
                        AddAccountPanel()
                    }
                    summaryCard
                }
                .frame(width: 358)
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

    // MARK: 签到状态摘要
    private var summaryCard: some View {
        let all = m.accounts
        var done = 0, failCount = 0, pendingCount = 0, disabled = 0
        for acc in all {
            if !acc.isEnabled { disabled += 1; continue }
            switch m.accStatus(acc.name).state {
            case "done": done += 1
            case "fail": failCount += 1
            default: pendingCount += 1
            }
        }
        let total = all.count
        let ratio = total > 0 ? Double(done) / Double(total) : 0

        return Card {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("签到状态摘要")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Text("今天").font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                }

                HStack(spacing: 8) {
                    Text("今日已完成 \(done) / \(total) 个账号")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    SoftTag(text: "\(Int(ratio * 100))%", fg: Theme.success, bg: Theme.successSoft)
                }
                .padding(.top, 18)

                ProgressBar(value: ratio, height: 8, fill: Theme.success)
                    .padding(.top, 12)

                VStack(spacing: 0) {
                    summaryRow(Theme.success, "已完成", "\(done) 个账号")
                    summaryRow(Theme.neutral, "待签到", "\(pendingCount) 个账号")
                    summaryRow(Theme.accent, "签到失败", "\(failCount) 个账号")
                    if disabled > 0 { summaryRow(Theme.neutral, "已停用", "\(disabled) 个账号") }
                }
                .padding(.top, 14)

                Divider().overlay(Theme.hairline).padding(.top, 4)

                Text("失败账号会在下次定时任务中自动重试，也可在列表中手动重试。")
                    .font(.system(size: 11.5))
                    .foregroundColor(Theme.textSub)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 12)
            }
        }
    }

    private func summaryRow(_ color: Color, _ title: String, _ value: String) -> some View {
        HStack(spacing: 7) {
            Dot(color: color, size: 7)
            Text(title).font(.system(size: 13)).foregroundColor(Theme.textBody)
            Spacer(minLength: 8)
            Text(value).font(.system(size: 13, weight: .medium)).foregroundColor(Theme.textBody)
        }
        .frame(height: 32)
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

    /// 按平台分组：Trae → WorkBuddy → 其他平台（组内按账号名排序）。
    /// 分组键复用 `Account.platformIconName`，保证分组与头像图标同源，不会各写一套判断。
    private func groupedAccounts(_ rows: [Account]) -> [AccountGroup] {
        let buckets = [
            AccountGroup(id: "trae", title: "Trae", iconName: "trae", accounts: []),
            AccountGroup(id: "workbuddy", title: "WorkBuddy", iconName: "workbuddy", accounts: []),
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
        if fail > 0 { return "\(done)/\(enabled.count) 已完成 · \(fail) 个失败" }
        return "\(done)/\(enabled.count) 已完成"
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
        case "done": return Theme.text
        default: return Theme.textSub
        }
    }
    private var avatarBG: Color {
        (acc.isEnabled && state == "fail") ? Theme.accentSoft : Theme.chipBG
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
            } else if st.state == "fail", let hit = st.hit, !hit.msg.isEmpty {
                Text(hit.msg).font(.system(size: 11.5)).foregroundColor(Theme.accent).lineLimit(1)
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
    @State private var mode = 0                 // 0 Trae 登录 / 1 凭据导入 / 2 WorkBuddy
    @State private var name: String = ""
    @State private var app: String = "trae"
    @State private var platIdx = 0
    @State private var curl: String = ""
    @State private var autoEnable = true
    @State private var savingCurl = false

    private let platforms = ["TRAE", "WORKBUDDY", "CURL", "其他"]

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

                SegmentedControl(items: [.init(0, "Trae 登录"), .init(1, "凭据导入"), .init(2, "WorkBuddy")],
                                 selection: $mode)
                    .padding(.top, 14)

                Group {
                    if mode == 0 { traeForm } else if mode == 1 { curlForm } else { workbuddyForm }
                }
                .padding(.top, 16)
            }
        }
    }

    private func label(_ t: String) -> some View {
        FormLabel(text: t).frame(maxWidth: .infinity, alignment: .leading)
    }

    private var traeForm: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 7) {
                label("账号名称")
                FieldBox {
                    TextField("如：trae-main", text: $name)
                        .textFieldStyle(.plain).font(.system(size: 13)).foregroundColor(Theme.inputText)
                }
            }
            HStack(spacing: 8) {
                Text("加入自动签到").font(.system(size: 13)).foregroundColor(Theme.textBody)
                Spacer(minLength: 8)
                GreenSwitch(isOn: $autoEnable)
            }
            Button { m.runTraeLogin(name: name, enabled: autoEnable) } label: {
                Label(m.loginRunning ? "正在等待登录完成…" : "打开登录浏览器", systemImage: "globe")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(m.loginRunning || name.trimmingCharacters(in: .whitespaces).isEmpty)

            Text("将弹出内置浏览器窗口，登录后程序自动识别并保存登录态（约 14 天有效）。")
                .font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var curlForm: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 7) {
                label("账号 / 邮箱")
                FieldBox {
                    TextField("如：user@mail.com", text: $name)
                        .textFieldStyle(.plain).font(.system(size: 13)).foregroundColor(Theme.inputText)
                }
            }
            VStack(alignment: .leading, spacing: 7) {
                label("平台")
                Menu {
                    ForEach(platforms, id: \.self) { p in
                        Button(p) { app = p.lowercased(); platIdx = platforms.firstIndex(of: p) ?? 0 }
                    }
                } label: {
                    SelectBox {
                        Text(platforms[platIdx]).font(.system(size: 13)).foregroundColor(Theme.text)
                    }
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
            }
            VStack(alignment: .leading, spacing: 7) {
                label("登录凭据（Cookie / Token）")
                TextEditor(text: $curl)
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundColor(Theme.inputText)
                    .scrollContentBackground(.hidden)
                    .padding(8)
                    .frame(height: 92)
                    .background(Color.white)
                    .clipShape(RoundedRectangle(cornerRadius: Theme.ctrlCorner, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Theme.ctrlCorner, style: .continuous)
                        .stroke(Theme.border, lineWidth: 1))
            }
            HStack(spacing: 8) {
                Text("加入自动签到").font(.system(size: 13)).foregroundColor(Theme.textBody)
                Spacer(minLength: 8)
                GreenSwitch(isOn: $autoEnable)
            }
            HStack(spacing: 10) {
                Spacer(minLength: 0)
                Button("取消") { m.showingAddPanel = false }
                    .buttonStyle(GhostButtonStyle())
                Button {
                    savingCurl = true
                    m.addAccountCurl(name: name, app: app, curl: curl, enabled: autoEnable) { _ in savingCurl = false }
                } label: {
                    Label("保存并验证", systemImage: "checkmark")
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty
                          || curl.trimmingCharacters(in: .whitespaces).isEmpty || savingCurl)
            }
        }
    }

    private var workbuddyForm: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Dot(color: wbColor, size: 8)
                Text(wbText).font(.system(size: 12.5)).foregroundColor(wbColor == Theme.accent ? Theme.accent : Theme.textBody)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(11)
            .background(wbColor.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

            VStack(alignment: .leading, spacing: 7) {
                label("账号名称（可选，留空自动以昵称命名）")
                FieldBox {
                    TextField("如：workbuddy", text: $name)
                        .textFieldStyle(.plain).font(.system(size: 13)).foregroundColor(Theme.inputText)
                }
            }
            HStack(spacing: 8) {
                Text("加入自动签到").font(.system(size: 13)).foregroundColor(Theme.textBody)
                Spacer(minLength: 8)
                GreenSwitch(isOn: $autoEnable)
            }
            HStack(spacing: 10) {
                Spacer(minLength: 0)
                Button { m.probeWorkBuddy() } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(GhostButtonStyle())
                    .disabled(m.loginRunning)
                Button {
                    m.addWorkBuddyAccount(name: name, enabled: autoEnable)
                } label: {
                    Label(m.loginRunning ? "正在读取…" : "读取并添加", systemImage: "person.crop.circle.badge.plus")
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(m.loginRunning || m.wbHealthy != true)
            }
            Text("凭据安全：accessToken 等同账号密码，仅在本机内存中使用，不写入配置文件、不写日志、不回显。")
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
