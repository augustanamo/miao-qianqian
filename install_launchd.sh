#!/bin/bash
# 安装 macOS 每日自动签到定时任务（launchd）
# 默认每天 09:30 执行一次签到。想改时间请编辑 ~/Library/LaunchAgents/com.marvis.autocheckin.plist
set -e

TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.marvis.autocheckin.plist"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.marvis.autocheckin</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${TOOL_DIR}/run_checkin.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${TOOL_DIR}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${TOOL_DIR}/logs/launchd.err.log</string>
</dict>
</plist>
EOF

chmod 600 "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "=== 定时任务已安装 ==="
echo "执行时间：每天 09:30（可在 $PLIST 中修改 Hour/Minute 以调整）"
echo "查看下次运行：launchctl list | grep autocheckin"
echo "卸载：bash $TOOL_DIR/uninstall_launchd.sh"
