import SwiftUI
import AppKit

// MARK: - 设计稿令牌
// 取值全部来自 4 张设计稿的像素采样（1440×900 画布）
enum Theme {
    // 画布
    static let pageBG     = Color.white                              // 主内容区：纯白
    static let border     = Color(hex: 0xE2E2E2)                     // 卡片 1px 描边
    static let hairline   = Color(hex: 0xEFEFEF)                     // 卡内分隔线
    static let chipBG     = Color(hex: 0xF5F5F5)                     // 灰底胶囊 / 输入框
    static let track      = Color(hex: 0xE5E5E5)                     // 进度槽 / 灰柱

    // 侧边栏 / 深色面
    static let sidebar      = Color(hex: 0x0A0A0A)
    static let sidebarCard  = Color(hex: 0x1A1A1A)                   // 选中项 / 进度卡
    static let sidebarTrack = Color(hex: 0x2A2A2A)
    static let sidebarLabel = Color(hex: 0x6E6E6E)                   // 「导航」小标题
    static let sidebarDim   = Color(hex: 0x5C5C5C)                   // 未选中编号
    static let sidebarText  = Color(hex: 0x787878)                   // 未选中文字/图标
    static let sidebarIcon  = Color(hex: 0xC6C6C6)                   // 选中项图标
    static let darkCaption  = Color(hex: 0x8A8A8A)                   // 深色卡内的灰字

    // 语义色
    static let accent      = Color(hex: 0xFF3B30)                    // 主红：今日柱 / 失败 / 编号
    static let accentSoft  = Color(hex: 0xFFECEB)
    static let success     = Color(hex: 0x22C55E)
    static let successSoft = Color(hex: 0xE8F8EF)
    static let warn        = Color(hex: 0xE8873A)
    static let warnSoft    = Color(hex: 0xFDF4E6)
    static let neutral     = Color(hex: 0xBBBBBB)

    // 文本
    static let text       = Color(hex: 0x0A0A0A)
    static let textBody   = Color(hex: 0x333333)
    static let textSub    = Color(hex: 0x999999)
    static let textFaint  = Color(hex: 0xB4B4B4)
    static let inputText  = Color(hex: 0x0A0A0A)   // App 已强制浅色外观，输入框文字恒为深色

    // 几何
    static let cardCorner: CGFloat = 12
    static let ctrlCorner: CGFloat = 8
    static let chipCorner: CGFloat = 8
    static let topBarHeight: CGFloat = 52
    static let statusBarHeight: CGFloat = 32
    static let sidebarWidth: CGFloat = 240
    static let pagePadding: CGFloat = 38

    // 任务表「今日任务」列的子任务图标。图标偏大（18pt）是因为它在里面还要塞一个
    // SF Symbol —— 再小就只剩一个色块，等于白占一列。
    static let taskIconSize: CGFloat = 18
    static let taskIconGap: CGFloat = 4

    /// 子任务图标的配色：淡底 + 同色字形，与状态胶囊 / 头像共用同一套语义色，
    /// 免得同一个"失败"在页面里出现两种红。
    /// `na`（不适用）走中性灰，但它在数据层就被过滤掉了，不会真的画出来。
    static func taskColors(_ state: SubTaskState) -> (fg: Color, bg: Color) {
        switch state {
        case .done:      return (success, successSoft)
        case .running:   return (warn, warnSoft)
        case .fail:      return (accent, accentSoft)
        case .idle, .na: return (textFaint, chipBG)
        }
    }

    static let version = "v2.4.1"
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0
        )
    }
}

// MARK: - 平台真实图标
/// 加载平台 App 的真实图标（从本机已安装的 Trae / WorkBuddy 提取，见 assets/platform-icons/）。
/// 打包后位于 `Contents/Resources/<name>.png`，由 main bundle 命中；
/// `swift run` 开发期回退到工程 assets 目录。取不到时调用方回退为首字方块。
enum PlatformIcon {
    /// 图标统一渲染尺寸上限（pt）。
    ///
    /// 为什么需要：SwiftUI 的 `Menu` 标签会**忽略内容里的 `.frame()`**，直接用
    /// NSImage 的自然点尺寸绘制。而 `workbuddy.png` 是 512px@72dpi —— 自然尺寸
    /// 就是 512pt，会把「新增账号」面板整个撑爆（同时把左侧账号列表挤窄到裁切）。
    /// 这里统一把点尺寸压到 34pt 以内，任何漏加 `.frame()` 的用法最多只是偏大一点，
    /// 不会再破坏布局；用 `.resizable()` 的地方本来按 frame 缩放，完全不受影响。
    private static let maxPointSize: CGFloat = 34
    private static let cache = NSCache<NSString, NSImage>()

    static func image(_ name: String?) -> NSImage? {
        guard let name = name, !name.isEmpty else { return nil }
        if let hit = cache.object(forKey: name as NSString) { return hit }
        var img = NSImage(named: name)
        if img == nil {
            img = NSImage(contentsOfFile: AppPaths.projectDir + "/assets/platform-icons/\(name).png")
        }
        guard let image = img else { return nil }
        image.isTemplate = false
        clampPointSize(image)
        cache.setObject(image, forKey: name as NSString)
        return image
    }

    /// 等比把 NSImage 的点尺寸压到上限内（只改「按多大画」，不动像素数据）
    private static func clampPointSize(_ image: NSImage) {
        let w = image.size.width, h = image.size.height
        guard w > 0, h > 0, max(w, h) > maxPointSize else { return }
        let k = maxPointSize / max(w, h)
        image.size = NSSize(width: (w * k).rounded(), height: (h * k).rounded())
    }
}

// MARK: - 卡片
struct Card<Content: View>: View {
    var padding: CGFloat? = 18
    var color: Color = .white
    var radius: CGFloat = Theme.cardCorner
    var bordered: Bool = true
    @ViewBuilder var content: Content

    var body: some View {
        Group {
            if let padding = padding {
                content.padding(padding)
            } else {
                content
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color)
        .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: radius, style: .continuous)
                .stroke(bordered ? Theme.border : Color.clear, lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.04), radius: 3, x: 0, y: 1)
    }
}

/// 卡片标题行：左标题（16 semibold）+ 可选右侧内容
struct CardTitle<Trailing: View>: View {
    let title: String
    /// 标题右侧紧跟的灰色补充（如「6 个账号」胶囊由外部传入时留空）
    @ViewBuilder var trailing: Trailing
    init(_ title: String, @ViewBuilder trailing: () -> Trailing = { EmptyView() }) {
        self.title = title
        self.trailing = trailing()
    }
    var body: some View {
        HStack(spacing: 8) {
            Text(title)
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(Theme.text)
            Spacer(minLength: 8)
            trailing
        }
    }
}

// MARK: - 页头
struct PageHeader<Trailing: View>: View {
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var trailing: Trailing
    init(_ title: String, subtitle: String? = nil, @ViewBuilder trailing: () -> Trailing = { EmptyView() }) {
        self.title = title
        self.subtitle = subtitle
        self.trailing = trailing()
    }
    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                    .font(.system(size: 30, weight: .bold))
                    .foregroundColor(Theme.text)
                if let subtitle = subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.system(size: 13))
                        .foregroundColor(Theme.textSub)
                }
            }
            Spacer(minLength: 12)
            HStack(spacing: 10) { trailing }
        }
    }
}

// MARK: - 按钮
struct PrimaryButtonStyle: ButtonStyle {
    var bg: Color = .black
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .foregroundColor(.white)
            .padding(.horizontal, 14)
            .frame(height: 34)
            .background(isEnabled ? (configuration.isPressed ? bg.opacity(0.82) : bg) : Color(hex: 0xBFBFBF))
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
            .contentShape(Rectangle())
    }
}

struct GhostButtonStyle: ButtonStyle {
    var fg: Color = Theme.text
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .medium))
            .foregroundColor(isEnabled ? fg : Theme.textFaint)
            .padding(.horizontal, 14)
            .frame(height: 34)
            .background(Color.white)
            .overlay(
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .stroke(Theme.border, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
            .opacity(configuration.isPressed ? 0.72 : 1)
            .contentShape(Rectangle())
    }
}

/// 胶囊小按钮（时间片 / 星期 / 分类）
struct ChipButtonStyle: ButtonStyle {
    var selected: Bool = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12.5, weight: selected ? .semibold : .regular))
            .foregroundColor(selected ? .white : Theme.textBody)
            .padding(.horizontal, 12)
            .frame(height: 30)
            .background(selected ? Color.black : Theme.chipBG)
            .clipShape(RoundedRectangle(cornerRadius: Theme.chipCorner, style: .continuous))
            .opacity(configuration.isPressed ? 0.8 : 1)
            .contentShape(Rectangle())
    }
}

// MARK: - 分段控件（黑胶囊选中态）
struct SegmentedControl<T: Hashable>: View {
    struct Item: Identifiable {
        let id: T
        let title: String
        var badge: String? = nil
        init(_ id: T, _ title: String, badge: String? = nil) {
            self.id = id; self.title = title; self.badge = badge
        }
    }
    let items: [Item]
    @Binding var selection: T
    var body: some View {
        HStack(spacing: 2) {
            ForEach(items) { item in
                let on = item.id == selection
                Button {
                    selection = item.id
                } label: {
                    HStack(spacing: 5) {
                        Text(item.title)
                            .font(.system(size: 12.5, weight: on ? .semibold : .regular))
                        if let badge = item.badge {
                            Text(badge).font(.system(size: 12, weight: on ? .semibold : .regular)).opacity(0.75)
                        }
                    }
                    .foregroundColor(on ? .white : Color(hex: 0x5C5C5C))
                    .padding(.horizontal, 12)
                    .frame(height: 26)
                    .background(on ? Color.black : Color.clear)
                    .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(2)
        .background(Theme.chipBG)
        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
    }
}

// MARK: - 小部件
struct Dot: View {
    let color: Color
    var size: CGFloat = 8
    var body: some View { Circle().fill(color).frame(width: size, height: size) }
}

/// 灰底胶囊（「6 个账号」「今天」等）
struct SoftTag: View {
    let text: String
    var fg: Color = Color(hex: 0x8A8A8A)
    var bg: Color = Theme.chipBG
    var body: some View {
        Text(text)
            .font(.system(size: 11.5, weight: .medium))
            .foregroundColor(fg)
            .padding(.horizontal, 8)
            .frame(height: 20)
            .background(bg)
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
    }
}

/// 状态胶囊：圆点 + 文案（已完成 / 签到失败 / 待签到 / 已停用）
struct StatusPill: View {
    let text: String
    let color: Color
    var bg: Color
    var body: some View {
        HStack(spacing: 5) {
            Dot(color: color, size: 6)
            Text(text).font(.system(size: 12, weight: .medium)).foregroundColor(color)
        }
        .padding(.horizontal, 9)
        .frame(height: 24)
        .background(bg)
        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
    }
}

/// 细进度条
struct ProgressBar: View {
    var value: Double            // 0...1
    var height: CGFloat = 8
    var fill: Color = Theme.success
    var track: Color = Theme.track
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(track)
                Capsule().fill(fill)
                    .frame(width: max(0, min(1, value)) * geo.size.width)
            }
        }
        .frame(height: height)
    }
}

/// 表单字段标签 + 容器
struct FieldBox<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content
            .padding(.horizontal, 12)
            .frame(height: 40)
            .background(Color.white)
            .clipShape(RoundedRectangle(cornerRadius: Theme.ctrlCorner, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: Theme.ctrlCorner, style: .continuous)
                    .stroke(Theme.border, lineWidth: 1)
            )
    }
}

struct FormLabel: View {
    let text: String
    var body: some View {
        Text(text).font(.system(size: 12.5)).foregroundColor(Theme.textSub)
    }
}

/// 下拉选择（照设计稿：白底 + 描边 + 右侧 chevron）
struct SelectBox<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        HStack(spacing: 6) {
            content
            Image(systemName: "chevron.down")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.textSub)
        }
        .padding(.horizontal, 12)
        .frame(height: 32)
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: Theme.ctrlCorner, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.ctrlCorner, style: .continuous)
                .stroke(Theme.border, lineWidth: 1)
        )
    }
}

/// 绿色开关（描边兜底，避免关闭态在浅色底上看不见）
struct GreenSwitch: View {
    @Binding var isOn: Bool
    var body: some View {
        Toggle("", isOn: $isOn)
            .toggleStyle(.switch)
            .tint(Theme.success)
            .labelsHidden()
            .overlay(
                Capsule().stroke(isOn ? Color.clear : Color(hex: 0xCFCFCF), lineWidth: 1)
            )
    }
}

/// 顶栏拖拽区：让自绘顶栏的空白处仍可拖动窗口
/// （.windowStyle(.hiddenTitleBar) 后系统不再提供标题栏拖拽区域）
struct WindowDragArea: NSViewRepresentable {
    final class DragView: NSView {
        override var mouseDownCanMoveWindow: Bool { true }
    }
    func makeNSView(context: Context) -> NSView { DragView() }
    func updateNSView(_ nsView: NSView, context: Context) {}
}
