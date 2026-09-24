# 项目约定（喵签签 / native 原生版）

> 各平台接口事实在 **`platforms.md`**；取证方法论在 skill `checkin-endpoint-forensics`。
> 本文件只放**约定 + 落点**。

## 验证与打包
- **禁止自己 build/开 App**（用户手动测），静态检查可以。Swift 无构建校验命令见 skill
  `swift-no-build-verification`——`-plugin-path` 必须是 **MacOSX**.platform，否则 @State 报千百条噪声。
- `bash make_native_app.sh`：工具链→build→组装→图标→**重做 ad-hoc 签名**；`.app` 内增删文件必须在签名**之前**；
  校验 `codesign --verify --strict`。
- PIL 在托管 python3.13。

## 内置浏览器依赖（playwright + Chromium）
- **唯一落点是项目自带的 `.venv`**（`bash setup_browser.sh` 建），**不许再装进 Marvis runtime**：
  那个目录带版本号，宿主一升级就整目录删掉 —— 2026-09-17 `1.0.0.10316`→`10339` 就是这么把
  playwright 弄丢的，全部凭据型平台的「重新登录 / 新增账号」一次性失效，而界面只说"登录未完成或已取消"。
- base **必须 >= 3.10**（登录脚本签名用 `str | None`），优先 WorkBuddy 托管 python；
  **base 变了必须 `venv --clear` 重建**，跨 base 复用会得到"playwright 在、却 import 就炸"的半坏态。
- 浏览器本体在 `~/Library/Caches/ms-playwright/`（**在 venv 外面**，清 venv 不影响它）。
- 调用方：`Bridge.detectBrowser()` 优先 `.venv/bin/python3`；`Bridge.browserReady` 认到
  `site-packages/playwright/__init__.py` 才算就绪。**它是 `static let`，改完必须重启 App 才刷新**。
- 报错统一走 `browser_deps.py`（把 ImportError 翻成"执行 bash setup_browser.sh"）。

## 定时任务（launchd）
- `~/Library/LaunchAgents/com.marvis.autocheckin.plist` **只写时间**，执行交给项目根 `run_checkin.sh`
  （`Bridge.swift::install()` 安装前会检查它存在）。**解释器路径绝不能进 plist**：Marvis runtime 是
  带版本号的目录（升级即删旧版本 → launchd 退出码 127、签到静默不跑），且路径含空格
  （拼进 shell 串漏引号会去执行 `/Users/xxx/Library/Application`）。启动器每次自己解析：
  `CHECKIN_PYTHON` → `Versions/*`（按 mtime 倒序取第一个可执行的）→ homebrew → /usr/local → /usr/bin；
  错峰走 `CHECKIN_STAGGER` 环境变量，不再拼命令行。
- 排查入口：`launchctl print gui/501/com.marvis.autocheckin | grep -E "runs|last exit"`（127=命令没找到）；
  `logs/launchd.log` 是脚本输出（改版前它是 bash 的报错，只有一行，极易忽略）。
- **改完 plist 用 `launchctl load -w`**（legacy API，容忍度高）—— 2026-09-21 实测在本环境
  **exit=0 成功**，别再信"自动化环境一律 EIO"（那次是 `bootout` 之后紧接着 bootstrap 命中 EIO，
  连最小-plist 对照实验也一起失败，结论下早了）。遇到 EIO 先换 `load -w` 或隔一会儿重试，
  别急着推给用户终端；`launchctl print gui/501/<label>` 有输出 = 已加载，别只看 `list`。
- `State.installed = 服务在 launchctl 里 || plist 文件存在`；`State.loaded` 单独记录是否真加载。
  设置页三态：**已开启 / 待生效（橙）/ 未开启**，未加载时不显示倒计时。
- **「plist 在、服务掉线」必须有人救，否则完全无声**：这种状态界面显"已开启"却一次都不触发。
  自愈 = `AppModel.healLaunchdIfNeeded()`（仅 `installed && !loaded` 时重装），两个触发点：
  ① `start()` 启动后 1.5s ② ticker 每 15 分钟（`logTicks % 900`）。
  **别再假设"App 启动会自己重装"**：`syncLaunchdIfNeeded()` 只挂在设置页改动上，
  用户不改设置就永远不修（2026-09-21 事故：App 连开 3 天、服务掉线、连着到点不签）。
- 验证服务是否真能跑：`launchctl kickstart -k gui/501/<label>`，再看 `logs/launchd.out.log`
  是否从 0 字节涨起来 —— **该文件为空 = 服务从未成功执行过**，比任何推断都干净。

## UI 版式
- 设计稿 `~/Downloads/Design file/01~04 *.png`（1440×900）。**改 UI 先量稿**（PIL），值落 `Theme.swift`，
  视图层不许魔法数。见 skill `design-spec-to-swiftui`。
- 参数：顶栏 52 + 主体 816 + 底栏 32；侧栏 240 + 内容 1200；页面 padding 38；卡片白底 1px `#E2E2E2` 圆角 12；
  页面底**纯白**；表头**黑底通栏**；`.hiddenTitleBar` 自绘顶栏左侧留 78pt；`minWidth 1280`。
- **行数=账号数 的区块绝不并排概览卡**。三行固定：①概览（高度与账号数无关）②任务表（全宽）③明细。
- **状态信息只留一个真源**：开关只在顶栏胶囊+设置页；积分总额只在积分卡；连续签到只在总览卡；
  **「手动签到」只在顶栏**；账号页不要「签到状态摘要」卡。设置页**即时生效无保存按钮**。
- 侧栏保留 `01~04` 序号；不要「导航」小标题/底部「本地账户」；「今日进度」卡固定侧栏底部。
- **非积分资源走独立字段**（阿里云盘容量是字节）。滚动图装不下时**不压窄柱子** → 横向滚动 + 锚最新 + 标签稀疏化。
- SwiftUI 坑：`Menu` 标签忽略内部 `.frame()` → `Button`+`.popover`；`PlatformIcon.image()` 有 34pt 上限；
  `DatePicker(.field)` 要回车才写回 → 用「时/分」下拉；列表 identity 用带 UUID 的 struct；
  `syncLaunchdIfNeeded()` 0.5s 防抖。**SF Symbol 名字写错会静默渲染成空白** → 用系统 `name_availability.plist` 校验。

## 品牌与文案
- 品牌「喵签签」（设计稿 AutoCheck 是旧名，用户明确保留），logo 用黑猫 AppIcon。
- 不照抄设计稿假数据；稿子与代码行为不符时**写真实行为**并在交付说明指出。
- **输入框标签必须与它接受的格式完全一致**；凭据名词只由 `credentialNoun(for:)` 提供。
- **「平台没有接口」≠「我们没做」**：B站观看/分享/投币都有接口但本项目不做的，文案要写「本工具未代办」。

## 平台清单与凭据字段
| type | 平台 | 凭据字段 |
|---|---|---|
| `trae` | Trae | `trae_auth.token` + `.session` |
| `workbuddy` | WorkBuddy | `workbuddy_auth`（OAuth device flow） |
| `bilibili` | Bilibili | `bilibili_auth.cookie`（签到走漫画接口） |
| `lenovo` | 联想智选 | `lenovo_auth.cookie`（服务端抖动，必须重试） |
| `smzdm` | 什么值得买 | `smzdm_auth.cookie` |
| `aliyunpan` | 阿里云盘 | `aliyunpan_auth.refresh_token` |
| `caimcloud` | 中国移动云盘 | `caimcloud_auth.authorization`（= `<授权码>#<手机号>`） |
| `jd` | 京东 | 平台侧已停用 |

- 字段名唯一真源：`cookie_manager.CREDENTIAL_FIELD` / Swift `AppModel.credentialField(for:)` / `credentialNoun(for:)`。
- 登录浏览器统一内置 Chromium：WorkBuddy 一次性干净 profile；Trae/Cookie 型按平台+账号持久 profile；
  Playwright 缺失要保留回退。同步窗口要在轮询间隙驱动（`tick` 里 `page.wait_for_timeout()`），
  关窗抛 `BrowserClosed`（退出码 2）。
- **验证接口不需要真凭据**：带假凭据打一发，从错误形态分辨端点不存在/参数缺失/凭据无效/签名不对。
- **WorkBuddy 账号有两种来源，行为完全不同**（`workbuddy_auth.source`）：`oauth`（扫码登录，
  凭据自带在 accounts.json，**与桌面端无关**）/ `local`（从本机登录态一键读取，凭据只有桌面端有）。
  桌面端状态**只影响 local**。判据唯一真源 `AppModel.workbuddyReloginUsesOAuth(_:)`
  ——`relogin()` 的路由与按钮文案都调它。⚠️ 别再拿 `wbHealthy == false` 一刀切判 WorkBuddy 健康，
  那会在桌面端未登录/加密时给 oauth 账号**永久误报**。
- **桌面端 2026-09-24 起把凭据改成加密存储**（`$wbEncrypted` 信封，密钥在不落盘的原生模块里）
  → `local` 来源的账号**读不到明文，只能改走扫码登录**。细节在 `platforms.md` 的 WorkBuddy 一节。

## 新增凭据型平台的固定改动面
**6 步清单在 `platforms.md` 末尾**（新建 `<platform>.py` → `checkin.py` 注册 → `Models.swift` →
`AppModel.swift` → `ViewAccounts.swift` → 图标资源）。`LoginKind.manual` = 无浏览器抓取路径（当前无平台使用）。

## 签到内核
- **四态**：成功 / 平台受限（对方活动下线、cookie 好）/ 失败（cookie 失效、网络错）/ **平台已停用**。
  `_RETIRED_PLATFORMS`（Py）↔ `Account.isRetiredType()`（Swift）**必须同口径**；停用平台不产生任何记录、
  不进新增面板、账号行显「平台已停用」（别与「已停用」混），已有账号仍可查看/改凭据/删。
  `activeAccounts()` 是**唯一进度统计口径**。
- **去重 = 平台原生预检 + 本地台账** `logs/checkin_state.json`（14 天、不含凭据、`--force` 忽略）。
  Swift `ParsedLogs.looksAlreadySigned()` 与 Python `checkin.is_already_msg()` 必须同口径。
- **日志文案**：`[FAIL]` 前缀「签到失败：」由 `checkin.py` 统一加（平台模块不要再带）；成功文案要能被两侧正则取到数字。
- **对方"服务端抖动"先怀疑多实例会话不粘 → 重试**，别急着改参数。判据：真实浏览器复现同样失败率。

## 子任务图标（任务表「今日任务」列）
- 唯一真源 **`subtasks.py`**，五态：`done`（本次有收益）/ `idle`（无需动作）/ `running` / `fail`（尝试未成功）/
  `na`（界面不渲染）。**`done` vs `idle` 看"这次有没有动"，不是"满不满意"**（"本期已领取"记 idle）。
- **台账每条记录加 `tasks` 字段**（结构化落盘），Swift 读它渲染——**不解析被截断到 160 字的 message**
  （"哪一步失败"最先被切掉）。读写两侧都过 `subtasks.normalize()` 收敛脏数据。
- ⚠️ 成长中心类接口**内部动作失败也可能返回 `ok=True`**（ok 只表示查询跑通）→
  `from_step(fail_markers=("失败","失效"))` 命中文案才算 fail，否则会把"开盲盒失败"画成绿勾。
- **每个非 done 状态都必须能解释自己**：`idle` 的图标是灰的，不给原因用户就只会看到"一直是灰的"
  而无法判断是"真没得做"还是"我们解析错了"。做法：步骤函数多返回一项 `note`（第 4 项），
  只进 `task.detail`（tooltip）、**不进 steps/日志**（纪律：稳态静默）。`note` 以 `running_prefix`
  开头时判 `running` —— 进行态既能保住橙图标、又不必写日志。**并把读到的原值写进 note**
  （"入门 未读到"/"读到 0 条任务"）：字段名被服务端改掉时，tooltip 自己就会喊出来。
- Swift：`SubTasks.swift`（`SubTask` / `SubTaskStore` / `TaskIconStrip`）+ `Theme.taskColors()`。
  **形状=哪个任务，颜色=结果状态**；`na` 不渲染；子任务 < 2 个（单动作平台）整列留空；最多 7 个、超出收 `+N`；hover 出 tooltip。

## 积分口径
- `checkin.py --credits --json` 用 `print`（**不能用 `log()`**，会被当签到记录）输出一行
  `[CREDITS_JSON] {date,total,counted,items:[…]}`；Swift `parseCreditsOutput` 取最后一条（stdout+stderr 拼接）。
  统一口径 = **账号当前可用余额**，`unit` 必须标。
- WorkBuddy 端点表在 `platforms.md`（签到类带 `/v2`、资源类不能带，别写混）。

## 异常呈现
- 「签到结果与异常」卡按**账号逐条**：`AppModel.issueItems() -> [CheckinIssue]`（fail + restricted，含账号名与原始 LogHit）。
- 点条目 → `IssueDetailSheet`：完整报错原文（可选中）+ 时间 + `diagnose()`（relogin/retry/wait/none）+ 账号信息 +
  「重试签到」（`checkin.py --only <name>`）+ 复制报错。**弹窗存账号名不存条目对象**。
- **诊断给出什么建议，就必须在建议指向的页面有可执行入口**，否则是逻辑漏洞（smzdm 签到失败 →
  弹窗说"请重新登录"、账号页却只有重命名/删除，用户无处可点）。落点：`needsRelogin(_:)` 是**唯一判据**
  （今天失败且 `diagnose().fix == .relogin`，**或** `credentialHint()` 报红）；`canRelogin(_:)` 判平台有无本机登录路径；
  `relogin(_:)` 按平台路由并**沿用账号名**（同名只更新凭据、不新增账号、不动启用状态）。
  入口 = **账号行报错那一行末尾的灰色 chip**（不是操作列、不用红色胶囊）；跳账号页要设 `focusAccount` 高亮
  （**先切页再设**，反了会被 `selectedPage` 的 didSet 清掉）。
- `credentialHint()` 只判"字段填没填"，**不能当成"凭据还能用"**——Cookie 过期了它照样报绿。
- **「换了凭据」≠「改了状态」**：状态胶囊 / 健康卡 / 「重新登录」入口**全部从当天的签到日志推出来**，
  而登录动作**不写日志**。所以任何"重新登录 / 刷新登录态"成功分支都**必须以真签一次收尾**
  （`AppModel.verifyAfterRelogin(_:)`），否则界面会继续复述上午那条 FAIL，看起来像"重新登录没生效"
  ——用户 2026-09-24 报的就是这个。`loadAccounts()` 只刷凭据快照，**刷不了状态**。
  该函数四条分支**都必须给反馈**（扫码成功却一声不吭，用户照样以为没成功）；
  今天已签 / 已停用 / 未启用 / 有签到在跑 → 跳过真签但要说明。

## 协作坑
- **同一条消息里对同一文件发两个 Edit 会互相覆盖**（都报成功，前一次静默丢弃）→ 串行，或一次大 Edit，改完 grep 复核。
- zsh 下 `grep "a\|b"` 常静默返回空 → 用专用搜索工具，或拆成多条 grep。
- 临时探测脚本放 `/tmp`，别留项目根。
