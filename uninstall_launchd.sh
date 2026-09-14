#!/bin/bash
# 卸载每日自动签到定时任务（不删除账号配置和日志）
set -e

PLIST="$HOME/Library/LaunchAgents/com.marvis.autocheckin.plist"

if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "定时任务已卸载。"
else
  echo "未找到已安装的定时任务。"
fi
