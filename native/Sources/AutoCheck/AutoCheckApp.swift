import SwiftUI
import AppKit

@main
struct AutoCheckApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("喵签签") {
            ContentView()
                .environmentObject(model)
                // 强制浅色外观：界面按浅色设计稿绘制（深黑侧边栏 + 白卡片），
                // 避免系统深色模式下 Color.primary 变白，导致输入框/胶囊/正文白字看不清。
                .preferredColorScheme(.light)
                .frame(minWidth: 1280, minHeight: 760)
                .onAppear {
                    model.start()
                    appDelegate.bind(model: model)
                    // 最彻底：整个 App（含弹窗/菜单/选框）统一强制浅色（aqua）
                    NSApp.appearance = NSAppearance(named: .aqua)
                    // 常驻菜单栏、**不进程序坞**：窗口关掉后靠顶部图标回来，
                    // 再摆一个 Dock 图标就是同一件事有两处入口（用户明确不要）。
                    // 真正的"启动即无 Dock 图标"靠 Info.plist 的 LSUIElement
                    // （见 make_native_app.sh）——这里补一刀是给裸跑二进制兜底，
                    // 否则策略从 regular 切过来时 Dock 图标会闪一下。
                    NSApp.setActivationPolicy(.accessory)
                    NSApp.activate(ignoringOtherApps: true)
                }
        }
        .windowResizability(.contentMinSize)
        // 隐藏系统标题栏：顶栏完全自绘（图标 + 品牌 + 运行状态胶囊 + 手动签到），
        // 交通灯仍由系统绘制在左上角，顶栏左侧预留 78pt 让位。
        .windowStyle(.hiddenTitleBar)
        // 默认窗口尺寸对齐设计稿画布 1440×900
        .defaultSize(width: 1440, height: 900)
    }
}

/// 应用代理：负责"关掉窗口后驻留菜单栏 + 不占程序坞"。
/// 关闭按钮 / Cmd+W 只隐藏窗口（windowShouldClose 返回 false），
/// 进程持续运行，回到窗口的唯一入口是**菜单栏那只猫**（点开菜单 →「显示主窗口」）——
/// 所以程序坞里不再出现第二个图标（activation policy = accessory + Info.plist 的 LSUIElement）。
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private weak var model: AppModel?
    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupStatusItem()
    }

    /// "关闭最后窗口不退出应用"
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    /// 重新打开应用（例如从 Finder 再点一次 .app）时把主窗口唤回来。
    /// 现在没有 Dock 图标，这条路径基本不会被触发，保留只为"从 Finder 双击"这一下。
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }

    func bind(model: AppModel) {
        self.model = model
        attachMainWindow()
    }

    // MARK: - 菜单栏状态栏图标
    /// 菜单栏图标 = **应用图标（黑猫）**，不再用 SF Symbol。
    ///
    /// 优先读打包进 Resources 的 `MenuBarIcon`（由 `make_menubar_icon.py` 从
    /// `AppIcon.icns` 生成：白底和眼睛都挖成透明），并以 `isTemplate = true`
    /// 交给系统按菜单栏明暗自动反色 —— 浅色菜单栏是黑猫、深色菜单栏是白猫，
    /// 眼睛位置是挖空的洞，两种模式都看得清。
    ///
    /// 不直接拿整张 App 图标当菜单栏图标：它是白底圆角卡片，贴到菜单栏里像贴了
    /// 一小张纸片，深色模式下尤其突兀，所以只作第二层兜底；裸跑二进制
    /// （没有 bundle 资源）时再退回 SF Symbol。
    private static func menuBarIcon() -> NSImage? {
        if let img = NSImage(named: "MenuBarIcon") {
            img.isTemplate = true
            img.size = NSSize(width: 18, height: 18)
            return img
        }
        if let img = NSImage(named: "AppIcon") {
            img.size = NSSize(width: 18, height: 18)
            return img
        }
        if let img = NSImage(systemSymbolName: "checkmark.seal.fill", accessibilityDescription: "喵签签") {
            img.isTemplate = true
            return img
        }
        return nil
    }

    private func setupStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.title = ""
        if let img = Self.menuBarIcon() {
            item.button?.image = img
            // 按钮不显示文字，VoiceOver 就得靠这个才念得出"喵签签"
            item.button?.setAccessibilityLabel("喵签签")
        } else {
            item.button?.title = "签"
        }

        let menu = NSMenu()
        menu.addItem(
            withTitle: "显示主窗口", action: #selector(showMainWindow), keyEquivalent: ""
        )
        menu.addItem(
            withTitle: "立即签到", action: #selector(runSignNow), keyEquivalent: ""
        )
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "退出喵签签", action: #selector(quit), keyEquivalent: "q"
        )
        menu.items.forEach { $0.target = self }
        item.menu = menu
        statusItem = item
    }

    @objc private func showMainWindow() {
        if let w = mainWindow() {
            w.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        } else {
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    @objc private func runSignNow() {
        model?.runSign()
        showMainWindow()
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    // MARK: - 窗口关闭 = 隐藏
    private func mainWindow() -> NSWindow? {
        NSApp.windows.first { $0.isVisible || $0.title == "喵签签" }
            ?? NSApp.mainWindow
            ?? NSApp.keyWindow
    }

    /// 主窗口可见时，把自己挂为其 delegate，拦截"关闭"改为"隐藏"。
    func attachMainWindow() {
        if let w = mainWindow(), w.delegate !== self {
            w.delegate = self
            w.isReleasedWhenClosed = false
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        // 关闭窗口只隐藏，不退出应用（进程与菜单栏图标长期驻留）
        sender.orderOut(nil)
        return false
    }
}
