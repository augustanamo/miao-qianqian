import SwiftUI

// MARK: - 子任务模型（任务表「今日任务」列的数据源）
/// 一条子任务 = 某平台今天的一个具体动作：B站是漫画签到 / 漫画权益 / 银瓜子换硬币，
/// WorkBuddy 是每日签到 + 成长中心六步。
///
/// **为什么不让 Swift 去正则解析日志文案**：那些文案是给人看的散句，随时会改措辞；
/// 更要命的是台账里的 message 被截断到 160 字，而"哪一步失败了"恰恰是最先被切掉的
/// 那一截。所以 Python 侧（subtasks.py）另落一份结构化 tasks 到
/// logs/checkin_state.json，这里只负责读。
struct SubTask: Hashable {
    var key: String
    var label: String
    var state: SubTaskState
    var detail: String

    /// 悬停提示。detail 为空时按状态给一句兜底，别让提示框只显示一个任务名。
    var tooltip: String {
        "\(label)：\(detail.isEmpty ? state.fallbackDetail : detail)"
    }

    /// 图标：**按任务本身给符号**，不是清一色对勾 —— 一排对勾看不出"哪一个"，
    /// 而"到底哪个动作掉了"正是这一列存在的理由。成功与否由颜色承担
    /// （见 `Theme.taskColors`）。
    ///
    /// key 是 Python 侧 PERK_ORDER / _exec 里的机器标识，改名等于改契约；认不出的
    /// key 退化成虚线圆，既不会崩，也不会静默变成别的任务的图标。
    var symbol: String {
        switch key {
        case "signin":       return "checkmark.seal"               // WorkBuddy 每日签到
        case "manga_signin": return "book.closed"                  // B站 漫画签到
        case "manga_vip":    return "bookmark"                     // B站 漫画会员权益（漫读券）
        case "silver2coin":  return "arrow.triangle.2.circlepath"  // B站 银瓜子换硬币
        case "vip_bcoin":    return "ticket"                       // B站 大会员B币券
        case "vip_benefit":  return "crown"                        // B站 大会员福利
        case "travel":       return "airplane"                     // 成长中心 Buddy 旅行
        case "tasks":        return "list.bullet.rectangle"        // 成长中心 任务奖励
        case "makeup":       return "calendar.badge.plus"          // 成长中心 补登
        case "redeem":       return "gift"                         // 成长中心 连登兑换
        case "lottery":      return "shippingbox"                  // 成长中心 抽盲盒
        case "buddy_box":    return "bolt"                         // 成长中心 能量盲盒
        default:             return "circle.dashed"
        }
    }
}

/// 子任务状态。与 Python 侧 subtasks.py 的五态一一对应，语义也必须对上：
/// `done` 是"这次真的动了"，`idle` 是"今天不用动"——两者在界面上都算过得去，
/// 但只有 `fail` 需要人跟进。
enum SubTaskState: String {
    case done      // 本次真的有收益 / 领取成功
    case idle      // 无需动作：今天已做过、本期已领取、本来就没有可领的
    case running   // 进行中（如 Buddy 正在旅行），下次运行才见结果
    case fail      // 尝试了但没成功
    case na        // 对该账号不适用（如非年度大会员没有 B币券）

    init(raw: String) {
        // 认不出的状态按 idle 处理：宁可少显示一个绿勾，也不要凭空变出一个红叉
        // 去吓人，更不要漏成"已完成"。
        self = SubTaskState(rawValue: raw) ?? .idle
    }

    var fallbackDetail: String {
        switch self {
        case .done:    return "已完成"
        case .idle:    return "今天无需动作"
        case .running: return "进行中"
        case .fail:    return "未成功"
        case .na:      return "不适用"
        }
    }
}

// MARK: - 台账读取
/// logs/checkin_state.json 里**当天**各账号的 tasks。
/// 读不到 / 格式不认识 / 只有别的日期 → 一律当作"没有数据"（界面不画图标），不猜。
struct SubTaskStore {
    private var byName: [String: [SubTask]] = [:]

    mutating func load(path: String, date: String) {
        byName = [:]
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let day = root[date] as? [String: Any] else { return }
        for (name, raw) in day {
            guard let rec = raw as? [String: Any],
                  let items = rec["tasks"] as? [[String: Any]] else { continue }
            let parsed: [SubTask] = items.compactMap { it in
                guard let key = it["key"] as? String, !key.isEmpty else { return nil }
                return SubTask(key: key,
                               label: (it["label"] as? String) ?? key,
                               state: SubTaskState(raw: (it["state"] as? String) ?? ""),
                               detail: (it["detail"] as? String) ?? "")
            }
            if !parsed.isEmpty { byName[name] = parsed }
        }
    }

    func all(_ name: String) -> [SubTask] { byName[name] ?? [] }
}

// MARK: - 图标条（任务表「今日任务」列）
/// 一行小图标，每个代表该账号今天的一个子动作：**符号说明是哪个任务，颜色说明结果**。
///
/// 装不下就折叠：最多 7 个，多出来的收成「+N」。刻意不压窄图标——18pt 的方形图标
/// 再挤就看不出形状了（这条和积分柱状图"装不下不压窄柱子"是同一个取舍）。
/// 宽度由调用方用 `.frame(width:)` 给，和别的列一样。
struct TaskIconStrip: View {
    let tasks: [SubTask]

    private static let maxIcons = 7

    var body: some View {
        let shown = tasks.count > Self.maxIcons ? Array(tasks.prefix(Self.maxIcons - 1)) : tasks
        let hidden = tasks.count - shown.count

        HStack(spacing: Theme.taskIconGap) {
            ForEach(shown, id: \.key) { t in
                icon(t)
            }
            if hidden > 0 {
                Text("+\(hidden)")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.textSub)
                    .help(tasks.suffix(hidden).map(\.tooltip).joined(separator: "\n"))
            }
        }
    }

    private func icon(_ t: SubTask) -> some View {
        let c = Theme.taskColors(t.state)
        return Image(systemName: t.symbol)
            .font(.system(size: 10, weight: .semibold))
            .foregroundColor(c.fg)
            .frame(width: Theme.taskIconSize, height: Theme.taskIconSize)
            .background(RoundedRectangle(cornerRadius: 5, style: .continuous).fill(c.bg))
            .help(t.tooltip)
    }
}
