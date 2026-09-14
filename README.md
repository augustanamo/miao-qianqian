---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 8a80d0efc7ded89e9248969404d1a2bd_78dfb9dab00111f1ac01525400e6dd8f
    ReservedCode1: hB4mNnxn0PdeCtghDmvnz5lG3+AbVF2xEnADGiQ3OtAGNmVXVRsWko194MwW5+plqr1TcOv+p9XVtvA/gZivjr0OWCUdJocqDmrpUlgTvIdS4iVBtHyomXls0mCEp7PSJpC5D7zVmPTds3kK5muQYTFAN9jwxSvFwhjHp4A2oAzxnE1ml+C5HB8rn1A=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 8a80d0efc7ded89e9248969404d1a2bd_78dfb9dab00111f1ac01525400e6dd8f
    ReservedCode2: hB4mNnxn0PdeCtghDmvnz5lG3+AbVF2xEnADGiQ3OtAGNmVXVRsWko194MwW5+plqr1TcOv+p9XVtvA/gZivjr0OWCUdJocqDmrpUlgTvIdS4iVBtHyomXls0mCEp7PSJpC5D7zVmPTds3kK5muQYTFAN9jwxSvFwhjHp4A2oAzxnE1ml+C5HB8rn1A=
---





# 多账号自动签到助手

支持 Trae、WorkBuddy 等多个软件的**多账号**每日自动签到。
提供**图形界面 App**（推荐）与命令行两种使用方式。

两种接入方式：
- **Trae：免抓包**。用内置浏览器登录一次，自动保存长效会话（约 14 天），
  每日由脚本自动换取新 Token 后签到，全程不需要抓包（见「二、Trae 免抓包登录」）。
- **WorkBuddy / 其他：抓包重放**。抓包拿到签到请求（cURL），本工具格式化、
  多账号管理、定时重放与日志记录。

## 目录结构

```
~/auto-checkin/
├── AutoCheck.app          # 原生版 App（macOS SwiftUI，推荐，按设计稿还原 4 页）
├── AutoCheckin.app        # 旧版 App（Python + Tkinter，回退方案，双击即用）
├── native/                # 原生版 Swift 工程（SwiftPM，构建 AutoCheck.app）
├── make_native_app.sh     # 重建 AutoCheck.app（swift build + 组装 .app）
├── checkin_gui.py         # 旧版图形界面主程序（Tkinter）
├── checkin.py             # 主签到脚本（读取配置、发送请求、记日志）
├── curl_to_account.py     # 抓包 cURL 转账号配置（命令行工具，WorkBuddy 等用）
├── trae_api.py            # Trae 签到 API 客户端（换 Token + 签到，纯标准库）
├── trae_login.py          # Trae 内置浏览器登录（Playwright，免抓包）
├── probe_tk.py            # Python/Tk 运行时探测（App 启动选用）
├── make_app.sh            # 重建 AutoCheckin.app（改代码后运行）
├── make_icon.py           # 应用图标生成
├── accounts.json          # 多账号配置（含登录凭据，请勿外传）
├── install_launchd.sh     # 命令行方式：安装每日定时任务
├── uninstall_launchd.sh   # 命令行方式：卸载定时任务
├── .browser_state/        # 各 Trae 账号的浏览器登录态（勿手动删除）
├── README.md
└── logs/                  # 签到日志
```

## 一、快速开始（图形界面，推荐）

双击 `AutoCheckin.app` 即可打开。
（首次打开若提示"无法验证开发者"：右键 App → 打开 → 确认。）

1. **添加 Trae 账号（免抓包）**：切到「账号管理」→「Trae 内置浏览器登录」
   → 填账号名（trae-1）→「打开登录浏览器」→ 在弹出的浏览器里登录 Trae，
   程序自动捕获登录态并保存（约 14 天有效）。
2. **添加其他账号（WorkBuddy）**：「账号管理」→「添加账号」→ 粘贴抓包 cURL → 解析保存。
3. **立即签到**：「签到」页点「立即签到」，实时查看每条请求结果。
4. **每日自动**：「每日定时」页选择时间 →「安装定时任务」。

## 二、Trae 免抓包登录（推荐）

Trae 网页端登录后，浏览器会保存一个 **约 14 天有效** 的会话（X-Cloudide-Session）。
本工具用内置浏览器帮你完成登录并自动保存该会话；以后每天签到前，脚本都会用该会话
自动换取全新 JWT 再调签到接口，因此**登录一次，无需任何抓包，可稳定自动签到约两周**，
会话过期前在应用里点一次「Trae 内置浏览器登录」刷新即可。

### 图形界面方式

「账号管理」→「Trae 内置浏览器登录」→ 填账号名 →「打开登录浏览器」，
在弹出的浏览器窗口中用手机号/验证码或扫码登录 Trae → 登录成功后程序自动捕获并保存。

### 命令行方式

```bash
cd ~/auto-checkin
python3 trae_login.py --name trae-1            # 打开浏览器登录
python3 trae_login.py --list                   # 查看已保存的 Trae 账号
python3 checkin.py --dry-run --only trae-1     # 预览
python3 checkin.py                             # 正式签到（所有账号）
```

> 依赖说明：内置浏览器登录需要 Playwright（`pip install playwright && python -m playwright install chromium`，
> 一次性下载约 150MB 内核）。签到脚本本身只依赖 Python 标准库。

> 多账号：每个 Trae 账号用不同账号名登录一次即可（trae-1、trae-2…）。
> 脚本会从 Token 中解析账号唯一 ID，若把同一账号绑到两个名字下会给出提示。

## 三、抓包拿签到请求（WorkBuddy / 其他软件）

WorkBuddy 的签到仍在桌面客户端内，需要抓包一次。

### 步骤

1. **安装抓包工具**（任选其一）：
   - Proxyman（macOS 体验最好，有免费版）
   - Charles
2. **开启 HTTPS 解密**：按工具指引安装并信任根证书，开启 macOS 系统代理。
   桌面客户端（Electron 架构）一般跟随系统代理；若抓不到流量，需在客户端里把
   代理配置为 127.0.0.1:端口。
3. **登录并触发签到**：
   - Trae Work：桌面客户端登录 → 主页点「签到」弹窗
   - WorkBuddy：客户端左下角头像 → 「领取今日礼包」
4. 在抓包工具中按软件域名过滤，找到本次签到触发的 **POST 请求**。
   判断技巧：URL/参数带 `checkin` / `signin` / `sign` / `daily` 等字样，
   响应返回"签到成功/积分+xxx"的即为目标。
5. 右键该请求 → **Copy as cURL**。

### 多账号

每个账号都要在对应登录态下抓一次包，用不同账号名（如 trae-1、trae-2、wb-1）区分添加。
切换账号：客户端退出登录 → 换另一账号登录 → 重新签到 → 重新 Copy as cURL → 添加。

## 三、测试签到（命令行方式可选）

图形界面：直接点「立即签到」或「预览请求」。

命令行：

```bash
cd ~/auto-checkin
python3 checkin.py --dry-run    # 先预览（不发请求）
python3 checkin.py              # 正式签到所有账号
tail -20 ~/auto-checkin/logs/checkin.log   # 查看日志
```

## 五、设置每日自动签到

图形界面：「01 签到」页底部「自动签到」开关一键安装/卸载（联动 launchd），
或在「03 设置 → 每日定时」卡片选择时间后点「安装定时任务」，随时可卸载。

命令行：

```bash
bash ~/auto-checkin/install_launchd.sh   # 默认每天 09:30
bash ~/auto-checkin/uninstall_launchd.sh # 卸载
```

改时间：编辑 `~/Library/LaunchAgents/com.marvis.autocheckin.plist` 中的
`Hour` / `Minute`，然后：

```bash
launchctl unload ~/Library/LaunchAgents/com.marvis.autocheckin.plist
launchctl load   ~/Library/LaunchAgents/com.marvis.autocheckin.plist
```

## 五、配置说明（accounts.json）

每个账号结构：

```json
{
  "name": "trae-1",
  "app": "trae",
  "requests": [
    {
      "method": "POST",
      "url": "https://xxx/checkin?date={today_iso}",
      "headers": { "Authorization": "Bearer xxx", "Cookie": "xxx" },
      "body": "{\"date\":\"{today_iso}\"}"
    }
  ]
}
```

- `url` 和 `body` 支持占位符：`{today}`（20260914）、`{today_iso}`（2026-09-14）。
  签到接口需要当天日期时，直接把抓到的参数换成占位符即可。
- `settings` 中可调：请求超时、账号间随机延迟区间、失败重试次数。
  随机延迟用于降低风控触发概率，不建议调为 0。

## 七、注意事项

- `accounts.json` 内含登录凭据（token/cookie），**不要分享、不要提交到代码仓库**。
  `.browser_state/` 目录保存浏览器登录态，同样注意保密。
- **Trae**：登录态（会话）约 14 天有效，过期后再次运行「Trae 内置浏览器登录」刷新即可；
  签到前脚本会自动换取新 Token，无需手动更新。
- **WorkBuddy 等抓包账号**：登录态会过期。过期后重新登录软件并重新抓包替换即可
  （重新运行 `curl_to_account.py` 加 `--replace`，或在图形界面先删旧账号再添加）。
- 自动签到属于平台规则边缘行为，存在被风控/封禁的可能，请自行评估风险、控制频率。
## 七、界面技术选型与更新说明

### 新版 UI 做了什么

- **按设计稿重构界面**：左侧深黑导航栏（#0D0D0D，Logo AutoCheck + 编号菜单
  01 签到 / 02 账号管理 / 03 设置 / 04 使用说明）+ 顶部应用条（状态「自动签到
  运行中 · 下次 HH:MM」+ 手动签到）+ 主区三区块（今日签到概览 · 账号签到任务
  表格 · 自动签到开关与异常提示）。配色 #0066FF / #34C759 / #FF3B30 / #E0E0E0。
- **签到概览卡片**：今日签到 N/M 大数字 + 连续签到天数 + 绿色进度条 + 三个统计
  （今日获积分 / 累计积分 / 连续天数）；积分状态卡片展示 Trae 账号累计总额、
  可用剩余 / 已用与近 7 天 / 近 30 天积分柱状图（tk.Canvas 自绘，数据来自
  logs/checkin.log 与积分接口，无历史时给出提示而非报错）。
- **账号签到任务表格**：平台账号 | 最近签到 | 今日积分 | 状态 | 操作，状态取
  真实值（已完成/签到失败红/待签到灰），支持「全部 / 已完成 / 待处理」筛选，
  行内签到 / 重试按钮。
- **签到错误完整展示**：从 API 返回中提取错误码 `code` + 可读 `message` 一并显示，
  不再出现"当前参******"这类被截断的残缺提示；界面输出对 token/session/uid 等
  敏感字段做定向脱敏，只打码凭据本身，不影响错误文案阅读。
- **账号列表自动刷新**：Trae 内置浏览器登录子进程结束后立即触发列表刷新，并在
  账号写盘延迟时每 0.7 秒自动重查、直至新账号可见（修复"登录后列表偶尔不刷新"）。
- **积分状态**：Query 真实接口 `pay/user_current_entitlement_list`（GetUserToken
  换 JWT 后调用），展示每个 Trae 账号的**总限额 / 已用 / 剩余积分**与额度包明细；
  WorkBuddy 无公开余额接口，显示占位说明。

### 技术选型结论（答复"是不是因为 Python 导致 UI 简陋"）

不是语言的问题，而是设计投入的问题。本项目评估了两条路线并选定：

| 对比项 | Tkinter/ttk 深度美化（已选） | pywebview（HTML/CSS/JS，macOS 走 WKWebView） |
| --- | --- | --- |
| 现代视觉 | 通过配色/间距/卡片/导航栏达到接近原生效果 | 更强，CSS 动效自由 |
| 运行依赖 | 仅 Python 自带 Tk（8.6） | 需 pywebview + pyobjc 等约 20 个包 |
| Marvis 运行时 | Tk 开箱即用 | 需额外安装 pyobjc，易缺失 |
| 可打包性 | 与 make_app.sh / probe_tk.py 完全兼容，App 双击即用 | 需改探测与打包链路，体积与复杂度上升 |
| 稳定性 | 单事件队列 + 线程，稳定 | 桥接回调多，崩溃面更大 |

**结论**：选择 Tkinter 深度美化，是因为它在"视觉效果可提升 + 零新增依赖 +
现有打包链路零改动"三者之间最平衡，足以交付现代、流畅、可双击运行的界面；
若后续需要更重的动效/复杂图表，再评估迁移 pywebview 或内嵌 WebView。

### 检测命令

```bash
cd ~/auto-checkin
python3 checkin.py --credits            # 命令行查看全部 Trae 账号积分
python3 trae_login.py --list            # 列出已保存的 Trae 登录账号（脱敏）
bash make_app.sh                        # 改完代码后重建图形界面 App
```

- 本工具只重放"签到这一步请求"，不会去爬取或修改其他数据。
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*

## 八、原生版（SwiftUI）AutoCheck

> 本目录目前包含 **两个** 图形界面应用，互相独立、互不影响：

| 应用 | 技术栈 | 说明 | 打包脚本 |
| --- | --- | --- | --- |
| `AutoCheckin.app` | Python + Tkinter | 旧版（回退方案） | `bash make_app.sh` |
| `AutoCheck.app` | macOS 原生 SwiftUI | 新版，界面按 4 张设计稿还原 | `bash make_native_app.sh` |

**推荐使用新版 `AutoCheck.app`**（原生外观、动画流畅）；若新版出现异常，可随时双击旧版
`AutoCheckin.app` 回退。两个 App 共用同一套 Python 后端与 `accounts.json` / `logs/` 数据，
**不会重复签到、不会互相冲突**。

### 构建与运行

```bash
bash ~/auto-checkin/make_native_app.sh   # 编译并组装 AutoCheck.app
open ~/auto-checkin/AutoCheck.app        # 打开
```

- 首次打开若提示"无法验证开发者"：右键 App → 打开 → 确认。
- 工程源码位于 `~/auto-checkin/native/`（SwiftPM：`Package.swift` + `Sources/AutoCheck/`）。
- 若 `swift build` 因 CommandLineTools 编译器与 SDK 版本不匹配失败，请安装完整 Xcode 并
  `sudo xcode-select -s /Applications/Xcode.app` 后重新运行脚本（脚本会自动逐个尝试可用 SDK）。

### 原生版四个页面

1. **01 签到**：顶栏状态 + 手动签到；深色签到战绩卡（N/M、今日积分、连续天数、自动签到已开启）；
   积分状态卡（累计积分 + 近 7 天/近 30 天柱状图，今日柱红色高亮）；账号签到任务表格
   （全部/已完成/待处理筛选，行内 已签到/重试/签到）；自动签到开关与下次倒计时；签到结果与异常红/绿横幅。
2. **02 账号管理**：搜索 + 状态筛选（全部/启用/停用）+ 批量导入 + 新增账号；账号列表（平台、连续天数、
   凭证健康提示、行内启停开关、编辑/删除）；右侧新增表单弹窗（Trae 内置浏览器登录 / cURL 两种入口）；
   右下今日进度摘要。
3. **03 设置**：定时签到（时间/每天次数/星期/错峰延迟）；运行与通知开关；运行策略预览（下次执行时间 +
   历史执行结果）；数据与积分（刷新频率 + 导出 CSV）；导出配置 / 恢复默认设置。
4. **04 使用说明**：新手引导 4 卡带跳转、自动签到说明、积分与状态含义图例、FAQ 折叠面板、
   复制诊断信息、重新播放引导。

### 与旧版的差异

- 界面：原生 SwiftUI（侧边栏 `#0D0D0D`、主蓝 `#0066FF`、成功 `#34C759`、失败 `#FF3B30`、8px 圆角、
  深色卡片、卡片轻阴影、32/28/20/16/14/12 字号层级），系统原生动画。
- 后端：完全复用既有 Python 脚本（`checkin.py` 签到/积分、`trae_login.py` 内置浏览器登录、
  `curl_to_account.py` 抓包 cURL 导入），Swift 通过 `Process` 调用，并自动探测 Python 运行时
  （`$CHECKIN_PYTHON` → 本机 `python3` → Marvis runtime → 常见路径）。
- 定时：读写同一 `~/Library/LaunchAgents/com.marvis.autocheckin.plist`（与 Tk 版共用同一套
  launchd 逻辑），请勿同时关闭两个 App 的自动签到开关。
- 数据：`accounts.json`、`logs/checkin.log` 与 `native_prefs.json`（原生版偏好）位于同一目录。
*（内容由AI生成，仅供参考）*
