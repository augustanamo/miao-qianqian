import SwiftUI

// MARK: ==================== 04 使用说明 ====================
struct HelpView: View {
    @EnvironmentObject var m: AppModel
    @State private var openFAQ: Set<Int> = [0]

    var body: some View {
        PageScroll(spacing: 17) {
            PageHeader("使用说明", subtitle: "3 分钟了解账号配置、自动签到与积分查看方式") {
                Button { m.copyDiagnostics() } label: {
                    Label("复制诊断信息", systemImage: "doc.on.doc")
                }
                .buttonStyle(GhostButtonStyle())

                Button {
                    m.selectedPage = .checkin
                    m.showToast("已回到引导起点：按「01 添加账号」开始", .info)
                } label: {
                    Label("重新播放引导", systemImage: "play.fill")
                }
                .buttonStyle(PrimaryButtonStyle())
            }

            // 4 张引导卡
            HStack(alignment: .top, spacing: 18) {
                guideCard(1, "添加账号", "在账号管理页添加需要签到的平台账号", "去添加") { m.selectedPage = .accounts }
                guideCard(2, "校验凭据", "保存时自动校验 Cookie 或 Token 是否有效", "去配置") { m.selectedPage = .accounts }
                guideCard(3, "开启定时签到", "在设置页选择每天的执行时间点与频率", "去设置") { m.selectedPage = .settings }
                guideCard(4, "查看签到与积分", "在签到页查看账号状态与近 7 天积分变化", "去看看") { m.selectedPage = .checkin }
            }

            HStack(alignment: .top, spacing: 23) {
                VStack(spacing: 17) {
                    explainCard
                    faqCard
                }
                .frame(maxWidth: .infinity)

                VStack(spacing: 17) {
                    legendCard
                    versionCard
                }
                .frame(width: 358)
            }
        }
    }

    // MARK: 引导卡
    private func guideCard(_ n: Int, _ title: String, _ desc: String,
                           _ link: String, action: @escaping () -> Void) -> some View {
        Card(padding: 15) {
            VStack(alignment: .leading, spacing: 0) {
                Text(String(format: "%02d", n))
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.accent)
                Text(title)
                    .font(.system(size: 15, weight: .bold))
                    .foregroundColor(Theme.text)
                    .padding(.top, 10)
                Text(desc)
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSub)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 7)
                Spacer(minLength: 12)
                Button(action: action) {
                    HStack(spacing: 5) {
                        Text(link).font(.system(size: 13.5, weight: .semibold))
                        Image(systemName: "arrow.right").font(.system(size: 11, weight: .semibold))
                    }
                    .foregroundColor(Theme.text)
                }
                .buttonStyle(.plain)
            }
            .frame(maxWidth: .infinity, minHeight: 104, alignment: .topLeading)
        }
    }

    // MARK: 自动签到使用说明
    private var explainCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                CardTitle("自动签到使用说明") {
                    Text("建议按顺序阅读").font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                }
                VStack(alignment: .leading, spacing: 0) {
                    explainRow("bolt.fill", "如何开启自动签到",
                               "在设置页打开「定时签到」开关，选择每天的执行时间点与频率。设置页改动即时生效，"
                               + "不需要再点保存；定时任务会跟着重装。")
                    explainRow("person.2", "账号如何配置",
                               "在账号管理页粘贴 Cookie 或 Token。保存时会自动校验有效性，失效的账号会标记出来。")
                    explainRow("arrow.triangle.2.circlepath", "签到失败如何处理",
                               "失败账号会在下一次定时任务中自动重试；也可以在签到页点击「重试」立即手动签到。")
                    explainRow("chart.line.uptrend.xyaxis", "积分 / 容量如何同步",
                               "签到成功后自动抓取。签到页分两处看：上方「积分状态」是总额与趋势，"
                               + "下方「账号余额与额度」是逐账号数——各平台口径不同（积分 / 硬币 / 乐豆），"
                               + "阿里云盘没有积分，显示的是网盘剩余容量与总容量。")
                }
                .padding(.top, 12)
            }
        }
    }

    private func explainRow(_ icon: String, _ title: String, _ desc: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color(hex: 0xF0F0F0))
                Image(systemName: icon).font(.system(size: 14)).foregroundColor(Theme.text)
            }
            .frame(width: 34, height: 34)

            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 13.5, weight: .semibold)).foregroundColor(Theme.text)
                Text(desc)
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSub)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 11)
    }

    // MARK: 常见问题
    private var faqCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                CardTitle("常见问题与排查") {
                    Text("4 个问题").font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                }
                VStack(alignment: .leading, spacing: 0) {
                    faqRow(0, "签到时间到了但没有自动执行，怎么办？",
                           "请确认应用正在运行、设置中的「定时签到」开关已打开，且相关账号处于启用状态。Mac 休眠期间的任务会在唤醒后自动补执行。")
                    faqRow(1, "Cookie 失效后会收到提醒吗？",
                           "会。签到页「签到结果与异常提示」会按账号列出异常，点任一条可查看完整报错、"
                           + "判断是凭证失效还是网络问题，并只对这一个账号重试；确属凭证失效时，"
                           + "弹窗里会直接给出该平台的重新登录入口（账号管理页里，"
                           + "失效账号的报错行后面也一直有「重新登录」）。")
                    faqRow(2, "积分数据多久刷新一次？",
                           "默认每 30 分钟自动同步一次，可在设置页「数据与积分」中调整；点击窗口底部状态栏的刷新图标可立即同步。")
                    faqRow(3, "如何备份或迁移账号配置？",
                           "设置页「导出配置」会导出 accounts.json（含登录凭据，请妥善保管）；在新机器上放回项目目录并重启应用即可。")
                }
                .padding(.top, 6)
            }
        }
    }

    private func faqRow(_ id: Int, _ q: String, _ a: String) -> some View {
        let open = openFAQ.contains(id)
        return VStack(alignment: .leading, spacing: 0) {
            Button {
                if open { openFAQ.remove(id) } else { openFAQ.insert(id) }
            } label: {
                HStack(spacing: 10) {
                    Text(q).font(.system(size: 13.5, weight: .semibold)).foregroundColor(Theme.text)
                    Spacer(minLength: 8)
                    Image(systemName: open ? "chevron.up" : "chevron.down")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(Theme.textSub)
                }
                .frame(height: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if open {
                Text(a)
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSub)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, 12)
            }
            Divider().overlay(Theme.hairline)
        }
    }

    // MARK: 积分与状态含义
    private var legendCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                CardTitle("积分与状态含义") {
                    Text("图例").font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                }
                VStack(alignment: .leading, spacing: 0) {
                    legendRow(Theme.success, "已完成", "该账号今日签到成功")
                    legendRow(Theme.neutral, "待签到", "今天尚未执行签到")
                    legendRow(Theme.accent, "签到失败", "需要重新登录或手动重试")
                    legendRow(Theme.warn, "平台受限", "活动下线 / 接口迁移 / 风控拦截，账号本身没问题，不必重登")
                    legendRow(Theme.neutral, "已停用", "已暂停该账号的自动签到")
                    legendRow(Theme.warn, "平台已停用", "平台侧整条签到链路已下线（目前是京东），不再自动签到，也不计入进度")
                }
                .padding(.top, 12)

                Text("积分只在签到成功后累加，失败不影响已有积分。"
                     + "「平台受限」不算失败：它既不会触发自动重试，也不会出现在待处理里。")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSub)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 8)

                // 「今日任务」列的小图标：形状 = 哪个动作，颜色 = 什么结果。
                // 只解释状态含义，不逐个列动作——动作清单随平台增删，写死会过期。
                Text("任务表「今日任务」列的小图标，是该账号今天各个子动作的结果"
                     + "（如 B站的漫画权益、银瓜子换硬币，WorkBuddy 的成长中心六步）："
                     + "图标形状表示哪个动作，颜色表示结果——绿=本次拿到了，灰=今天无需动作，"
                     + "橙=进行中，红=没做成。鼠标悬停可看具体原因。")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSub)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 6)
            }
        }
    }

    private func legendRow(_ c: Color, _ t: String, _ d: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Dot(color: c, size: 9).offset(y: -1)
            Text(t).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.text)
            Text(d).font(.system(size: 12)).foregroundColor(Theme.textSub)
            Spacer(minLength: 0)
        }
        .frame(height: 30)
    }

    // MARK: 版本与反馈
    private var versionCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                CardTitle("版本与反馈") {
                    Text(Theme.version).font(.system(size: 11.5)).foregroundColor(Theme.textSub)
                }
                VStack(spacing: 0) {
                    HStack(spacing: 12) {
                        Text("当前版本").font(.system(size: 13.5)).foregroundColor(Theme.textBody)
                        Spacer(minLength: 8)
                        Text("\(Theme.version) · 已是最新")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(Theme.success)
                    }
                    .frame(height: 42)

                    HStack(spacing: 12) {
                        Text("检查更新").font(.system(size: 13.5)).foregroundColor(Theme.textBody)
                        Spacer(minLength: 8)
                        Button {
                            m.showToast("当前为本机构建版本 \(Theme.version)，未配置更新源", .info)
                        } label: {
                            Label("检查", systemImage: "arrow.clockwise")
                        }
                        .buttonStyle(GhostButtonStyle(fg: Theme.textBody))
                    }
                    .frame(height: 46)

                    HStack(spacing: 12) {
                        Text("问题反馈").font(.system(size: 13.5)).foregroundColor(Theme.textBody)
                        Spacer(minLength: 8)
                        Button {
                            m.copyDiagnostics()
                            m.showToast("诊断信息已复制，可粘贴到反馈渠道", .success)
                        } label: {
                            HStack(spacing: 5) {
                                Text("提交反馈").font(.system(size: 13.5, weight: .medium))
                                Image(systemName: "arrow.right").font(.system(size: 11, weight: .semibold))
                            }
                            .foregroundColor(Theme.text)
                        }
                        .buttonStyle(.plain)
                    }
                    .frame(height: 42)

                    HStack(spacing: 12) {
                        Text("诊断信息").font(.system(size: 13.5)).foregroundColor(Theme.textBody)
                        Spacer(minLength: 8)
                        Button { m.copyDiagnostics() } label: {
                            HStack(spacing: 5) {
                                Image(systemName: "doc.on.doc").font(.system(size: 12))
                                Text("复制").font(.system(size: 13.5))
                            }
                            .foregroundColor(Theme.textBody)
                        }
                        .buttonStyle(.plain)
                    }
                    .frame(height: 42)
                }
                .padding(.top, 6)
            }
        }
    }
}
