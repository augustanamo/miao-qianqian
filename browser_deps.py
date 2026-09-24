"""内置浏览器依赖（playwright + Chromium）的可用性说明。

**为什么单独一个模块**：playwright 原先装在 Marvis runtime 里
（`MarvisAgent/Versions/<版本号>/runtime/python311/`），而那是**带版本号**的目录。
宿主一升级就把旧目录删掉 —— 2026-09-17 Marvis 从 1.0.0.10316 升到 1.0.0.10339，
playwright 随之消失，于是所有需要内置浏览器的功能（重新登录 / 新增账号 /
Trae 登录 / WorkBuddy OAuth 扫码）在同一时刻全部失效。

可怕的是症状：App 只会说"登录未完成或已取消"，真正的原因
（`No module named 'playwright'`）埋在一行日志里，用户根本无从下手。
所以这里统一把这类异常翻译成**能照着做的下一步**。

现在的安装位置改到项目自己的 `.venv`（用 setup_browser.sh 建），不随宿主升级消失。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(BASE_DIR, ".venv", "bin", "python3")
SETUP_SCRIPT = os.path.join(BASE_DIR, "setup_browser.sh")


def looks_like_missing_playwright(exc: BaseException) -> bool:
    """这次异常是不是「playwright 没装」，而不是浏览器自身启动失败。

    只认这两种形态：ImportError，或消息里明确提到找不到 playwright 模块。
    其它异常（比如 Chromium 崩溃、端口占用）照旧按原始信息报，不要误导。
    """
    if isinstance(exc, ImportError):
        return True
    msg = str(exc).lower()
    return "playwright" in msg and ("no module" in msg or "not found" in msg)


def explain(exc: BaseException) -> list:
    """把异常翻译成给用户看的修复指引；不需要额外解释时返回空列表。"""
    if not looks_like_missing_playwright(exc):
        return []
    lines = [
        "内置浏览器组件缺失（playwright），所以登录窗口没有打开。",
        f"当前解释器：{sys.executable}",
        f"修复：在终端执行一次    bash {SETUP_SCRIPT}",
    ]
    # 项目自带环境已在，但当前跑的却是别的解释器 —— 这种情况值得点出来，
    # 否则用户会疑惑"我明明装过了"。App 侧是指定 .venv 跑的，命令行不一定。
    if os.path.exists(VENV_PY) and os.path.abspath(sys.executable) != os.path.abspath(VENV_PY):
        lines.append(f"（项目自带的浏览器环境已存在：{VENV_PY}，"
                     f"用它可以免去重新下载）")
    return lines
