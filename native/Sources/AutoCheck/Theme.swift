import SwiftUI

// MARK: - 设计稿配色
enum Theme {
    static let sidebar     = Color(red: 13/255,  green: 13/255,  blue: 13/255)   // #0D0D0D 深黑侧边栏
    static let sidebarCard = Color(red: 26/255,  green: 26/255,  blue: 26/255)   // #1A1A1A 侧边栏卡片
    static let darkCard    = Color(red: 16/255,  green: 16/255,  blue: 16/255)   // 深色签到卡片
    static let sidebarMuted = Color(red: 160/255, green: 160/255, blue: 160/255)
    static let primary     = Color(red: 0/255,   green: 102/255, blue: 255/255)  // #0066FF 主蓝
    static let action      = Color(red: 0/255,   green: 0/255,   blue: 0/255)    // #000000 主操作按钮纯黑
    static let success     = Color(red: 52/255,  green: 199/255, blue: 89/255)   // #34C759 成功绿
    static let danger      = Color(red: 229/255, green: 57/255,  blue: 53/255)   // #E53935 暗砖红
    static let warning     = Color(red: 255/255, green: 149/255, blue: 0/255)
    static let textMain    = Color(red: 232/255, green: 232/255, blue: 232/255)
    static let textDeep    = Color(red: 30/255,  green: 30/255,  blue: 34/255)
    static let textMuted   = Color(red: 102/255, green: 102/255, blue: 102/255)  // #666
    static let mainBg      = Color(red: 245/255, green: 246/255, blue: 248/255)
    static let grayBar     = Color(red: 201/255, green: 201/255, blue: 201/255)
    static let cardBorder  = Color.black.opacity(0.08)
    static let corner: CGFloat = 8
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

// MARK: - 通用卡片 / 按钮 / 文案组件
struct Card<Content: View>: View {
    var padding: CGFloat = 16
    var color: Color = .white
    @ViewBuilder var content: Content
    var body: some View {
        content
            .padding(padding)
            .background(color)
            .cornerRadius(Theme.corner)
            .overlay(RoundedRectangle(cornerRadius: Theme.corner).stroke(Theme.cardBorder, lineWidth: 1))
            .shadow(color: .black.opacity(0.08), radius: 10, y: 3)
    }
}

struct CardHeader: View {
    let title: String
    var subtitle: String? = nil
    var trailing: AnyView? = nil
    init(_ title: String, subtitle: String? = nil, @ViewBuilder trailing: () -> AnyView = { AnyView(EmptyView()) }) {
        self.title = title
        self.subtitle = subtitle
        self.trailing = trailing()
    }
    var body: some View {
        HStack(spacing: 8) {
            Text(title).font(.system(size: 14, weight: .semibold)).foregroundColor(Theme.textDeep)
            if let subtitle = subtitle {
                Text(subtitle).font(.system(size: 12)).foregroundColor(Theme.textMuted)
            }
            Spacer(minLength: 8)
            if let trailing = trailing { trailing }
        }
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    var bg: Color = Theme.action
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .foregroundColor(.white)
            .padding(.horizontal, 14).padding(.vertical, 7)
            .background(configuration.isPressed ? bg.opacity(0.8) : bg)
            .cornerRadius(6)
    }
}

struct GhostButtonStyle: ButtonStyle {
    var color: Color = Theme.textDeep
    var bg: Color = .white
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .medium))
            .foregroundColor(color)
            .padding(.horizontal, 14).padding(.vertical, 7)
            .background(bg)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.cardBorder, lineWidth: 1))
            .cornerRadius(6)
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

struct StatusDot: View {
    let color: Color
    var size: CGFloat = 8
    var body: some View {
        Circle().fill(color).frame(width: size, height: size)
    }
}

struct Pill: View {
    let text: String
    let fg: Color
    let bg: Color
    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(fg)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(bg)
            .cornerRadius(5)
    }
}

struct PageHeader: View {
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var trailing: AnyView
    init(_ title: String, subtitle: String? = nil, @ViewBuilder trailing: () -> AnyView = { AnyView(EmptyView()) }) {
        self.title = title
        self.subtitle = subtitle
        self.trailing = trailing()
    }
    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 28, weight: .bold)).foregroundColor(Theme.textDeep)
                if let subtitle = subtitle {
                    Text(subtitle).font(.system(size: 13)).foregroundColor(Theme.textMuted)
                }
            }
            Spacer()
            trailing
        }
    }
}
