#!/bin/bash
# 生成 AutoCheckin.app（双击即用的图形界面应用）
# 用法：bash make_app.sh
set -e

TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="AutoCheckin"
APP_DIR="$TOOL_DIR/${APP_NAME}.app"
CUR_PY="$(command -v python3 || true)"

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundleDisplayName</key>
  <string>自动签到助手</string>
  <key>CFBundleIdentifier</key>
  <string>com.marvis.autocheckin</string>
  <key>CFBundleExecutable</key>
  <string>main</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
</dict>
</plist>
EOF

# 候选 Python 运行时列表：优先 $CHECKIN_PYTHON，其次本会话 python3、Marvis 运行时、常见路径
CAND_LIST="\"\$CHECKIN_PYTHON\""
if [ -n "$CUR_PY" ]; then
  CAND_LIST="$CAND_LIST \"$CUR_PY\""
fi
for p in /Users/*/Library/Application\ Support/com.tencent.mac.marvis/components/MarvisAgent/Versions/*/runtime/python311/bin/python3; do
  if [ -x "$p" ]; then CAND_LIST="$CAND_LIST \"$p\""; fi
done
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$p" ]; then CAND_LIST="$CAND_LIST \"$p\""; fi
done

# App 启动器：逐个探测可用的 Python（能真正创建窗口），然后用它启动 GUI
cat > "$APP_DIR/Contents/MacOS/main" <<EOF
#!/bin/bash
TOOL_DIR="$TOOL_DIR"
PROBE="\$TOOL_DIR/probe_tk.py"
CANDIDATES=($CAND_LIST)
PY=""
for c in "\${CANDIDATES[@]}"; do
  [ -z "\$c" ] && continue
  [ -x "\$c" ] || continue
  if "\$c" "\$PROBE" >/dev/null 2>&1; then PY="\$c"; break; fi
done
if [ -z "\$PY" ]; then
  /usr/bin/osascript -e 'display alert "自动签到助手" message "未找到可用的 Python 运行时，请安装 Python 3 后重试。"' 2>/dev/null
  exit 1
fi
exec "\$PY" "\$TOOL_DIR/checkin_gui.py"
EOF
chmod +x "$APP_DIR/Contents/MacOS/main"

# 生成应用图标（失败不影响运行）
if [ -n "$CUR_PY" ] && [ -x "$CUR_PY" ]; then
  "$CUR_PY" "$TOOL_DIR/make_icon.py" "$APP_DIR/Contents/Resources/AppIcon.icns" >/dev/null 2>&1 || echo "提示：图标生成跳过，应用使用系统默认图标。"
fi

echo "已生成：${APP_DIR}"
echo "以后改完代码可随时运行  bash ${TOOL_DIR}/make_app.sh  重建。"
open -R "$APP_DIR" 2>/dev/null || true
