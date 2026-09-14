#!/bin/bash
# 生成 喵签签.app（macOS 原生 SwiftUI 版，双击即用）
# 用法：bash make_native_app.sh
# 依赖：swift（Xcode 或 CommandLineTools），可选 python3（生成图标）
# 说明：喵签签.app 为原生版；原 AutoCheckin.app（Tk 版，Python）保留作回退。
set -e

TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"
NATIVE_DIR="$TOOL_DIR/native"
APP_NAME="喵签签"
APP_DIR="$TOOL_DIR/${APP_NAME}.app"

echo "==> 1/5 检测 Swift 工具链"
SWIFT="$(command -v swift || true)"
if [ -z "$SWIFT" ]; then
  echo "错误：未找到 swift，请安装 Xcode 或 CommandLineTools。"
  exit 1
fi
"$SWIFT" --version | head -1

echo "==> 2/5 编译 Swift 工程（swift build）"
cd "$NATIVE_DIR"
BUILD_OK=0
# 优先直接构建；若因 SDK/编译器版本不匹配失败，则逐个尝试 CLT 内置 SDK
if "$SWIFT" build >/tmp/autocheck_swiftbuild.log 2>&1; then
  BUILD_OK=1
else
  echo "   直接构建失败，尝试逐个 SDK（详见 /tmp/autocheck_swiftbuild.log）..."
  for sdk in /Library/Developer/CommandLineTools/SDKs/MacOSX*.sdk; do
    [ -d "$sdk" ] || continue
    if "$SWIFT" build -Xswiftc -sdk -Xswiftc "$sdk" >/tmp/autocheck_swiftbuild.log 2>&1; then
      echo "   使用 SDK: $sdk 构建成功"
      BUILD_OK=1
      break
    fi
  done
fi
if [ "$BUILD_OK" != "1" ]; then
  echo "错误：swift build 失败，完整日志见 /tmp/autocheck_swiftbuild.log"
  tail -30 /tmp/autocheck_swiftbuild.log
  echo "可能原因：CommandLineTools 编译器与 SDK 版本不匹配，建议安装完整 Xcode 后运行："
  echo "  sudo xcode-select -s /Applications/Xcode.app"
  exit 1
fi

BIN=".build/debug/AutoCheck"
[ -x "$BIN" ] || BIN=$(find .build -name AutoCheck -type f -perm +111 2>/dev/null | head -1)
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
  echo "错误：未找到编译产物 AutoCheck"
  exit 1
fi

echo "==> 3/5 组装 $APP_NAME.app"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BIN" "$APP_DIR/Contents/MacOS/AutoCheck"
chmod +x "$APP_DIR/Contents/MacOS/AutoCheck"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundleDisplayName</key>
  <string>喵签签</string>
  <key>CFBundleIdentifier</key>
  <string>com.marvis.autocheck</string>
  <key>CFBundleExecutable</key>
  <string>AutoCheck</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
  <key>NSSupportsAutomaticTermination</key>
  <false/>
  <key>NSSupportsSuddenTermination</key>
  <false/>
</dict>
</plist>
PLIST

echo "==> 4/5 生成应用图标（可选）"
if [ -f "$TOOL_DIR/assets/AppIcon.icns" ]; then
  cp "$TOOL_DIR/assets/AppIcon.icns" "$APP_DIR/Contents/Resources/AppIcon.icns"
  echo "   已使用自定义图标：$TOOL_DIR/assets/AppIcon.icns"
else
  CUR_PY="$(command -v python3 || true)"
  if [ -n "$CUR_PY" ] && [ -x "$CUR_PY" ] && [ -f "$TOOL_DIR/make_icon.py" ]; then
    "$CUR_PY" "$TOOL_DIR/make_icon.py" "$APP_DIR/Contents/Resources/AppIcon.icns" >/dev/null 2>&1 \
      && echo "   图标已生成" || echo "   提示：图标生成跳过，使用默认图标。"
  else
    echo "   提示：未找到 python3，图标使用默认图标。"
  fi
fi

# 平台真实图标（Trae / WorkBuddy）：账号列表与签到表格的头像用。
# 取不到的平台会自动回退为首字方块，缺图不影响运行。
if [ -d "$TOOL_DIR/assets/platform-icons" ]; then
  icon_n=0
  for png in "$TOOL_DIR/assets/platform-icons"/*.png; do
    [ -f "$png" ] || continue
    cp "$png" "$APP_DIR/Contents/Resources/"
    icon_n=$((icon_n + 1))
  done
  if [ "$icon_n" -gt 0 ]; then
    echo "   已复制 $icon_n 个平台图标到 Resources"
  else
    echo "   提示：assets/platform-icons 下没有 PNG，平台头像将使用首字方块。"
  fi
fi

echo "==> 5/5 重做 ad-hoc 代码签名"
# 必须放在 Info.plist 和图标都就位之后：这两者任一变化都会让已有签名失效。
# 原因：swift build 给 .build 里的中间产物打的 ad-hoc 签名声明了 CodeResources，
# 直接 cp 进 .app 后包内没有 _CodeSignature 目录，签名结构不完整 ——
# codesign --verify 退出码 1（code has no resources but signature indicates they must be present），
# arm64 上双击可能被系统直接拒绝。
if codesign --force --sign - "$APP_DIR" >/dev/null 2>&1; then
  if codesign --verify --strict "$APP_DIR" >/dev/null 2>&1; then
    echo "   签名有效（ad-hoc，标识符 com.marvis.autocheck）"
  else
    echo "   警告：签名校验未通过。若双击被系统拦截，执行：xattr -cr \"$APP_DIR\""
  fi
else
  echo "   提示：codesign 不可用，已跳过签名（双击若被拦截见上面的 xattr 提示）。"
fi

echo "已生成：${APP_DIR}"
echo "以后改完原生代码可随时运行  bash ${TOOL_DIR}/make_native_app.sh  重建。"
open -R "$APP_DIR" 2>/dev/null || true
