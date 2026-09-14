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
                .frame(minWidth: 1120, minHeight: 700)
                .onAppear {
                    model.start()
                    appDelegate.bind(model: model)
                    NSApp.setActivationPolicy(.regular)
                    NSApp.activate(ignoringOtherApps: true)
                }
        }
        .windowResizability(.contentMinSize)
    }
}

/// 应用代理：负责 "关闭窗口后驻留后台 + 菜单栏常驻图标"。
/// 关闭按钮 / Cmd+W 只隐藏窗口（windowShouldClose 返回 false），
/// 进程持续运行，菜单栏图标右键可回显主窗口或退出。
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

    /// Dock 图标/再次点击时重新显示主窗口
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }

    func bind(model: AppModel) {
        self.model = model
        attachMainWindow()
    }

    // MARK: - 菜单栏状态栏图标
    private func setupStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.title = ""
        if let img = NSImage(systemSymbolName: "checkmark.seal.fill", accessibilityDescription: "喵签签") {
            img.isTemplate = true
            item.button?.image = img
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
