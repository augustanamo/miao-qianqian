#!/bin/bash
# 自动签到启动器。launchd 定时任务与手动执行都走这里。
#
# 为什么不让 plist 直接调 python：
#   1) Marvis runtime 的路径带版本号（…/MarvisAgent/Versions/<版本>/runtime/python311/bin/python3），
#      版本一升级旧目录就被删除。把路径写死在 plist 里，某天早上就会突然失效 ——
#      表现为 launchd 退出码 127、签到静默不跑（本项目真实踩过）。
#   2) 该路径含空格（"Application Support"）。路径一旦拼进 plist 的 shell 命令串，
#      每个都必须加引号，漏一个就会去执行 /Users/xxx/Library/Application。
# 把「找解释器」放到每次执行时做，两个问题一起消失。
#
# 可用环境变量：
#   CHECKIN_PYTHON   指定解释器（优先级最高，便于排查/换环境）
#   CHECKIN_STAGGER  错峰上限（分钟），0 或未设置表示不等待
set -u

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJ_DIR" || exit 1

find_python() {
    # 1) 显式指定优先
    if [ -n "${CHECKIN_PYTHON:-}" ] && [ -x "${CHECKIN_PYTHON}" ]; then
        printf '%s' "$CHECKIN_PYTHON"; return 0
    fi
    # 2) Marvis runtime：按修改时间倒序（最新装的排最前），取第一个能执行的
    local versions="$HOME/Library/Application Support/com.tencent.mac.marvis/components/MarvisAgent/Versions"
    if [ -d "$versions" ]; then
        local vdir cand
        while IFS= read -r vdir; do
            [ -n "$vdir" ] || continue
            cand="${vdir%/}/runtime/python311/bin/python3"
            if [ -x "$cand" ]; then printf '%s' "$cand"; return 0; fi
        done < <(ls -1dt "$versions"/*/ 2>/dev/null)
    fi
    # 3) 常规安装位置兜底
    local c
    for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        if [ -x "$c" ]; then printf '%s' "$c"; return 0; fi
    done
    return 1
}

if ! PY="$(find_python)"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 找不到可用的 python3，签到未执行。"
    echo "  可设置环境变量 CHECKIN_PYTHON 指定解释器后重试。"
    exit 1
fi

# 错峰：避免与其他定时任务/多设备挤在同一秒发起
STAGGER="${CHECKIN_STAGGER:-0}"
case "$STAGGER" in
    ''|*[!0-9]*) STAGGER=0 ;;
esac
if [ "$STAGGER" -gt 0 ]; then
    DELAY=$(( RANDOM % (STAGGER * 60) ))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 错峰等待 ${DELAY}s（上限 ${STAGGER} 分钟）…"
    sleep "$DELAY"
fi

# 变量后紧跟全角括号时必须写 ${PY}：bash 在 UTF-8 locale 下会把多字节字符的
# 首字节当成变量名的一部分，`$PY）` 就变成"变量 PY\xef 未定义"→ 值丢了。
# （launchd 用 C locale 跑，所以以前一直没暴雷；手动在终端跑就会看到乱码。）
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 定时签到开始（python: ${PY}）"
# "$@" 透传额外参数：手动执行时可以借它做只读自检，例如
#   bash run_checkin.sh --growth        只跑 WorkBuddy 成长中心，不签到
#   bash run_checkin.sh --credits        只查余额
# launchd 那条路不带参数，展开为空，行为与以前完全一致。
exec "$PY" "$PROJ_DIR/checkin.py" "$@"
