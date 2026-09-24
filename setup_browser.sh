#!/usr/bin/env bash
# 内置浏览器组件安装 / 修复（playwright + Chromium）
#
# 为什么需要这个脚本：
#   playwright 原先装在 Marvis runtime 里（`MarvisAgent/Versions/<版本号>/runtime/python311/`），
#   而那个目录**带版本号**。宿主一升级就把旧目录删掉 —— 2026-09-17 Marvis 从
#   1.0.0.10316 升到 1.0.0.10339，playwright 随之消失，于是所有需要内置浏览器的
#   功能（重新登录 / 新增账号 / Trae 登录 / WorkBuddy OAuth）全部失败。
#   症状极隐蔽：界面只提示"登录未完成或已取消"，日志里只有一行
#   `No module named 'playwright'`。
#
# 所以这里把依赖装在**项目自己的 .venv** 里，不随宿主升级消失。
# 幂等：已装好时重复执行只会快速跳过。
#
# 用法：  bash setup_browser.sh
set -uo pipefail

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$PROJ_DIR/.venv"
PY="$VENV/bin/python"

echo "== 内置浏览器组件安装 / 修复"
echo "   项目：$PROJ_DIR"
echo "   venv：$VENV"
echo

# ---------------------------------------------------------------- 1. 选 base python
# 两条硬约束：
#  1. **必须 >= 3.10**：登录脚本的签名里用了 `str | None`（PEP 604），在 3.9 上
#     import 就抛 TypeError（实测过，不是理论风险）。所以每个候选都要校验版本。
#  2. **不用 Marvis runtime**：它装在带版本号的目录里，宿主升级会把整个旧目录删掉 ——
#     这正是本次事故的根源（playwright 就是那么丢的）。venv 的 stdlib 又依赖 base，
#     拿它当 base 等于把同一个地雷再埋一次。
BASE=""
try_base() {   # 可执行 + 版本达标 才算可用
  [ -n "$1" ] && [ -x "$1" ] || return 1
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null || return 1
  BASE="$1"
}
try_base "${CHECKIN_BROWSER_BASE:-}"
# WorkBuddy 托管的 Python：版本较新，由宿主长期维护，按版本倒序取第一个达标的
if [ -z "$BASE" ]; then
  for p in $(ls -d "$HOME"/.workbuddy/binaries/python/versions/*/bin/python3 2>/dev/null | sort -Vr); do
    try_base "$p" && break
  done
fi
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -n "$BASE" ] && break
  try_base "$c"
done
if [ -z "$BASE" ]; then
  echo "[x] 找不到 3.10 以上的 Python 3，装上也跑不起来。当前候选：" >&2
  for c in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    [ -x "$c" ] && echo "      $c -> $("$c" -V 2>&1)" >&2
  done
  echo "    登录脚本用了 3.10+ 的注解写法（\`str | None\`）。" >&2
  echo "    装好新版 Python 后：CHECKIN_BROWSER_BASE=/path/to/python3 bash setup_browser.sh" >&2
  exit 1
fi
# 变量后紧跟中文/全角符号时必须写 ${VAR}：bash 在 UTF-8 locale 下会把多字节字符
# 的首字节当成变量名的一部分，`$BASE（` 会被解析成变量 "BASE（" → unbound。
echo "[1/4] base python：${BASE}（$("$BASE" -V 2>&1)）"

# ---------------------------------------------------------------- 2. 建 venv
# "venv 目录在"不等于"venv 能用"，所以除了存在性还要确认两件事：
#   a. 里面的解释器真的跑得起来 —— base 被宿主删掉时，venv 里的 python 会变成断链；
#   b. base 就是这次选中的解释器 —— 换了 base 却继续复用，会得到"解释器是旧的、
#      依赖装在别处"的半坏状态，报错还特别难懂。
# 需要重建时用 `venv --clear`（工具自带的清理参数），而不是自己 rm -rf 目录。
NEED_BUILD=1
if [ -x "$PY" ] && [ -f "$VENV/pyvenv.cfg" ] && "$PY" -c "import sys" >/dev/null 2>&1; then
  VENV_HOME="$(sed -n 's/^home = //p' "$VENV/pyvenv.cfg" | head -1)"
  if [ "$VENV_HOME" = "$(dirname "$BASE")" ]; then
    NEED_BUILD=0
  else
    echo "     (venv 的 base 已变：${VENV_HOME:-未知} → ${BASE}，将重建)"
  fi
fi
if [ "$NEED_BUILD" = "1" ]; then
  echo "[2/4] 创建 / 重建 venv（base：${BASE}）…"
  if ! "$BASE" -m venv --clear "$VENV"; then
    echo "[x] 创建 venv 失败（${BASE} -m venv 报错）。" >&2
    exit 1
  fi
else
  echo "[2/4] venv 已存在且 base 一致，复用。"
fi

# ---------------------------------------------------------------- 3. 装 playwright
echo "[3/4] 安装 / 校验 playwright…"
"$PY" -m pip install -q --upgrade pip >/dev/null 2>&1
if ! "$PY" -c "import playwright" >/dev/null 2>&1; then
  # 国内直连 pypi 实测慢到十几分钟（playwright 的 wheel 约 40MB，还带 node driver），
  # 所以先试清华镜像，失败再回落官方源 —— 用镜像没意义的环境不受影响。
  # 想强制指定源：PIP_INDEX_URL=https://... bash setup_browser.sh
  MIRROR="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
  if ! "$PY" -m pip install -i "$MIRROR" playwright; then
    echo "[!] 镜像源装不上，改用官方 PyPI 重试…" >&2
    "$PY" -m pip install playwright
  fi
fi
# 判据取"能不能 import"，而不是"pip 的返回码"：pip 偶尔会因为无关的告警
# 返回非 0，而包里其实已经装好了。
if ! "$PY" -c "import playwright" >/dev/null 2>&1; then
  echo "[x] playwright 仍不可用（镜像与官方源都没装上）。请检查网络后重试。" >&2
  exit 1
fi
"$PY" - <<'PYEOF'
import importlib.metadata as md
print("      playwright 版本：", md.version("playwright"))
PYEOF

# ---------------------------------------------------------------- 4. 装 Chromium
# 已存在的同 revision 浏览器会被秒跳过；不匹配才下载（约 150MB）。
echo "[4/4] 安装 / 校验 Chromium…"
if ! "$PY" -m playwright install chromium; then
  # 不在这里退出：这条命令失败的原因常常和"浏览器到底能不能用"无关 ——
  # 网络抖动、下载目录里 __dirlock 残留、甚至沙箱/权限拦了它的清理动作都会报错，
  # 而浏览器其实是好的。真正的判据是下面的启动验证，所以让它继续跑。
  echo "[!] 安装命令返回失败（网络/权限？），继续做启动验证以确认实际是否可用…" >&2
fi

# ---------------------------------------------------------------- 验证
echo
# 注意两件事：
#  1. 不能写成 `if "... | tail -3"; then` —— 管道的退出码取的是 tail 的，
#     那样 python 失败也会被判成成功。
#  2. 必须用 headless=False 验证：登录窗口是 headed 模式，而 playwright 把
#     完整 Chromium 与 headless shell 分成两个包，headless 能跑**不代表** headed 能跑。
echo "   验证 headed 启动（会闪一个空窗口，随即自动关闭）…"
if "$PY" -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=False)
    b.close()
print('   Chromium（headed）可正常启动')
"; then
  echo
  echo "✅ 完成。内置浏览器可用了，回到 App 点「重新登录」即可。"
else
  echo
  echo "[x] 组件已装但 Chromium 启动失败，请把上面的报错发给维护者。" >&2
  exit 1
fi
