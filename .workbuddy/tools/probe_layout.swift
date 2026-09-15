// 离屏量 SwiftUI 视图的真实布局尺寸：不用 build、不用起 App、不弹窗。
//
// 用法：  xcrun swift .workbuddy/tools/probe_layout.swift
//
// 为什么需要：用户只会说"太大了""还是不对"，靠截图目测很容易猜错方向。
// `NSHostingView.fittingSize` 会直接暴露「`.frame()` 有没有生效」——
// 本项目就靠它定位到「Menu 标签忽略 .frame()，图标按 NSImage 自然点尺寸
// （512px@72dpi → 512pt）渲染，把新增账号面板撑到 ~550pt」这个根因。
//
// 加新用例：复制一个 struct，在 measure(...) 列表里加一行即可。

import AppKit
import SwiftUI

// ⚠️ 资源必须挂在 enum 的 static 上：脚本里用顶层 `let` 会被类型定义里的闭包
// 引用成"声明之前捕获"（error: closure captures 'x' before it is declared）。
enum ProbeRes {
    static let iconPath = "assets/platform-icons/workbuddy.png"
    static let img: NSImage = NSImage(contentsOfFile: iconPath) ?? NSImage()
}

/// 复刻 SelectBox：白底 + 描边 + 右侧 chevron
struct Box<C: View>: View {
    @ViewBuilder var c: C
    var body: some View {
        HStack(spacing: 6) {
            c
            Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold))
        }
        .padding(.horizontal, 12)
        .frame(height: 32)
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
    }
}

@ViewBuilder func glyph(_ size: CGFloat) -> some View {
    Image(nsImage: ProbeRes.img)
        .resizable()
        .interpolation(.high)
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.25, style: .continuous))
}

// MARK: 待验证的写法

/// A：Menu 标签内置图标 —— 已知会忽略 .frame()
struct CaseMenuWithIcon: View {
    var body: some View {
        Menu { Button("x") {} } label: {
            Box { HStack(spacing: 7) { glyph(14); Text("平台").font(.system(size: 13)) } }
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
    }
}

/// B：Button + .plain（当前采用）—— 标签严守 frame
struct CaseButtonWithIcon: View {
    var body: some View {
        Button {} label: {
            Box { HStack(spacing: 7) { glyph(14); Text("平台").font(.system(size: 13)) } }
        }
        .buttonStyle(.plain)
    }
}

/// C：图标放 Menu 标签外面 —— 也可行，但图标跑到框外
struct CaseIconOutsideMenu: View {
    var body: some View {
        HStack(spacing: 7) {
            glyph(14)
            Menu { Button("x") {} } label: {
                Box { Text("平台").font(.system(size: 13)) }
            }
            .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
        }
    }
}

// MARK: 运行

func measure<V: View>(_ name: String, _ v: V) {
    let host = NSHostingView(rootView: v)
    host.frame = NSRect(x: 0, y: 0, width: 600, height: 300)
    host.layoutSubtreeIfNeeded()
    // 用 Swift 侧补齐宽度：`%-22s` 按 UTF-8 字节数算，中文会错位
    let label = name.padding(toLength: 24, withPad: " ", startingAt: 0)
    let size = String(format: "%.0f x %.0f", host.fittingSize.width, host.fittingSize.height)
    print("\(label) fittingSize = \(size.padding(toLength: 12, withPad: " ", startingAt: 0))")
}

let img = ProbeRes.img
print("NSImage(\(ProbeRes.iconPath))")
print("  size = \(img.size)  reps = \(img.representations.map { "\($0.pixelsWide)x\($0.pixelsHigh)" })")
print("  ↳ size 是「点」不是像素：点 = 像素 / (dpi/72)，512px@72dpi 就是 512pt")
print("")
measure("A Menu 标签内置图标", CaseMenuWithIcon())
measure("B Button + .plain", CaseButtonWithIcon())
measure("C Menu 外置图标", CaseIconOutsideMenu())
print("")
print("判读：A 的高度 ≈ 图标的自然点尺寸 → 说明 .frame() 被容器吃掉了；")
print("      B/C 的高度 ≈ 16~32pt → 正常。")
