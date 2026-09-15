# 项目约定（喵签签 / native 原生版）

## UI 以设计稿为唯一真源

- 设计稿位置：`~/Downloads/Design file/01 签到页.png` ~ `04 使用说明.png`，画布 **1440×900**。
- **改 UI 前先量稿，不要凭肉眼印象调**。所有尺寸/配色都能用 PIL 量出来（系统 `python3` 没 PIL，
  用 `/Users/augustanamo/.workbuddy/binaries/python/versions/3.13.12/bin/python3`）。
- 量出来的值统一落在 `Theme.swift` 的令牌里，**视图层不许出现魔法数**。
- 工作流细节见 skill `design-spec-to-swiftui`。

## 已定的关键参数

- 网格：顶栏 52 + 主体 816 + 底栏 32 = 900；侧栏 240 + 内容 1200 = 1440；页面左右 padding 38
- 卡片：白底、1px `#E2E2E2` 描边、圆角 12；页面底色是**纯白**（不是浅灰）
- 语义色：主红 `#FF3B30`、绿 `#22C55E`、软底 `#FFECEB` / `#E8F8EF`、灰胶囊 `#F5F5F5`
- 深色面：侧栏 `#0A0A0A`、选中项与内嵌块 `#1A1A1A`、槽 `#2A2A2A`
- 表格表头是**黑底通栏**（撑满卡片宽，不内缩）
- 窗口：`.windowStyle(.hiddenTitleBar)` 自绘顶栏，**左侧预留 78pt** 给交通灯；
  `.defaultSize(1440, 900)`，`minWidth 1280`

## 品牌

- 设计稿顶栏写的是 `AutoCheck`，但**实际品牌是「喵签签」**，logo 用现有黑猫 AppIcon。
  用户 2026-09-15 明确选择保持喵签签，不要再改回 AutoCheck。

## 文案原则

- **不照抄设计稿里的假数据**（「延迟 42ms」「网络正常」等）——换成真实可得的指标。
- 稿子里的功能描述若与代码实际行为不符，**写真实行为**并在交付说明里指出（例：重试次数）。

## 验证

- 用户要求手动 build，**不要自己跑 `swift build` / 开 App**。
- 交付前做 `swiftc -parse` + `swiftc -typecheck`（不产出构建产物），见 skill
  `swift-no-build-verification` 的「SwiftPM 工程」分支。

## 打包

- 用 `bash make_native_app.sh`（5 步：工具链 → swift build → 组装 → 图标 + 平台图标 → **重做 ad-hoc 签名**）。
- **最后一步的签名不能省。** `swift build` 给中间产物打的签名与最终 `.app` 包结构不匹配，
  直接 `cp` 会让 `codesign --verify` 退出码 1（`code has no resources but signature indicates they must be present`），
  arm64 上双击可能被系统拒绝。凡是在 `.app` 里增删文件（Info.plist / 图标）都必须**在其之后**重新签名，
  否则签名立刻失效。校验：`codesign --verify --strict 喵签签.app` 应输出 `valid on disk`。
- 打包需写 `~/.swiftpm`，沙盒内会失败，要放开沙盒执行。

## 平台图标

- 目录 `assets/platform-icons/`，现有 5 个：`trae / workbuddy / bilibili / jd / lenovo`。
  **统一 512×512、满幅**（内容铺满正方形、无透明边距）——留白不一致会让某个图标"看起来更大"，
  就踩过这个坑：Trae 满幅、WorkBuddy 原本有 10% 留白。圆角由 SwiftUI 统一裁 `size*0.23`。
- 来源（均免登录，可复现）：
  - Trae / WorkBuddy：本机 `/Applications/<App>.app/Contents/Resources/*.icns` → `sips` 转 PNG，再去掉透明边距。
  - Bilibili：`cdn.simpleicons.org/bilibili` 官方品牌矢量白色版 + 品牌粉 `#FB7299` 底合成。
    SVG 用 Swift `NSImage(contentsOfFile:)` 栅格化（**本机 PIL 不支持 SVG**，`qlmanage` 在沙盒里会失败）。
  - 京东：iTunes Search API 的官方 App 图标（`artworkUrl512` 中的 `512x512bb` 换成 `1024x1024bb`）——Simple Icons 没有 jd。
  - 联想：官网 `www.lenovo.com.cn/favicon.ico` 取最大帧放大 + `ImageFilter.UnsharpMask` 锐化。
    ⚠️ 试过矢量字标（simple-icons lenovo）：34pt 下白字糊成一个白块，**不如 favicon 的"红底白 L"清晰**。
- 加新平台：转好 PNG 丢进该目录即可，打包脚本用 `*.png` 通配自动带上（**必须在重做签名之前**）。
- 代码侧统一走 `PlatformIcon.image(_:)`（先 main bundle、再回退工程 assets），取不到回退首字方块。
- 账号分组与头像图标**同源于 `Account.platformIconName`**，不要在视图里另写一套平台判断。

## 侧栏 / 顶栏 / 页面元素约定（2026-09-15 用户确认）

- 侧栏**要保留 `01~04` 序号**：当天上午删过，晚上用户要求恢复，**别再删**。
  样式：编号（26pt 宽，选中转红 `Theme.accent`）→ 图标（22pt 宽）→ 文字，leading 12。
- 不要「导航」小标题；底部不要「本地账户 / 免费版」块；「今日进度」卡固定在侧栏最底部。
- 顶栏 logo + 「喵签签」必须在 240pt 侧栏宽度内**居中**（光学中心落在 120pt 中线），交通灯用背景拖拽区让开。
- **「手动签到」按钮只在顶栏保留一处**，签到页 PageHeader 里不再重复放（用户要求去掉）。
- **账号管理页不要「签到状态摘要」卡**（签到进度属于签到页，与账号管理无关）。
  该页右侧栏现在只承载 `AddAccountPanel`，面板收起时整栏不占位、列表铺满整宽。

## 添加账号面板结构（2026-09-15，同日两次收敛）

- **两个分段**：浏览器登录 / 凭据导入。曾短暂是三项（把 WorkBuddy 单列），用户指出
  "扫码登录说白了就是浏览器登录"——都是"弹内置浏览器 → 登录 → 抓登录态"，故并入平台下拉。
- 「浏览器登录」平台下拉：**WorkBuddy / Trae / Bilibili / 联想智选 / 京东**，按 `LoginKind` 分派：
  - `.workbuddy` → `runWorkBuddyOAuth`（`workbuddy_login.py`）
  - `.trae` → `runTraeLogin`（`trae_login.py`）
  - `.cookie` → `runCookieBrowserLogin`（`browser_login.py --platform <type>`）
- 各平台带**次级路径**（都不弹浏览器，与主按钮并列在下方）：
  - WorkBuddy → 「或读取本机桌面端登录态」`workbuddyLocalSection`
  - Cookie 型 → 「或手动粘贴 Cookie」`manualCookieSection`
  - Trae → 无（粘贴入口在「凭据导入」）
  ⚠️ `addCookieAccount` 写 `{type}_auth`，`addAccountCurl` 写 `requests`，**语义不同，不能互相替代**。
- 「凭据导入」保持通用（TRAE / WORKBUDDY / CURL / 其他 + 粘贴 Cookie/Token）。
- 平台下拉图标 14pt（曾用 16pt，用户反馈 Trae 显得太大）。

## 账号名称自动命名（2026-09-15）

用户要求"不填名称就自动读真实名称"。三个脚本的 `--name` 均已改为**可选**，留空即自动命名：

| 脚本 | 能否拿到真实昵称 | 自动命名规则 |
|---|---|---|
| `browser_login.py` | **能** —— 三个平台的只读验证函数本来就返回 nickname | 昵称 → uid → `<platform>-auto`，如 `bilibili-小明` |
| `workbuddy_login.py` | **能** —— OAuth 的 `fetch_account()` 返回 nickname | 本来就用昵称命名 |
| `trae_login.py` | **不能** —— JWT payload 只有 `data.id`（16 位数字），`trae_api` 也无用户资料接口 | 账号 ID 后 6 位，如 `trae-123456` |

- 别去猜"Trae 也能读昵称"——已查证过 JWT payload（`['data','exp','iat']` → `data` 只有
  `['id','source','source_id','tenant_id','type']`）与 `TraeClient` 全部方法。
- 名称留空时浏览器 profile 目录用 `_auto` 占位（`<platform>/_auto`）。
- Swift 侧对应的硬校验（`请输入账号名称`）已全部移除；`loginAccount` 显示 `(自动命名)`。

## 运行环境（重要）

- Swift 侧 `Py.detect()`（`Bridge.swift`）挑解释器的顺序：`CHECKIN_PYTHON` 环境变量 →
  **Marvis runtime 的 python311** → homebrew → `/usr/local` → `/usr/bin`。
- 实机命中 `~/Library/Application Support/com.tencent.mac.marvis/components/MarvisAgent/Versions/<ver>/runtime/python311/bin/python3`
  （Python 3.11.9）。**`playwright` 只装在这个解释器里**；系统 `/usr/bin/python3` 和托管 python 都没有。
- 所以改完 Python 脚本，验证要用这个解释器——否则会得到"playwright 不可用"的错误结论。
- `~/Library/Caches/ms-playwright/` 已有 chromium，内置浏览器可直接用。

## 登录浏览器策略（2026-09-15）

- **统一走内置 Chromium（Playwright）**，不再占用用户日常浏览器。原 `workbuddy_login.py` 走
  `webbrowser.open()` 调系统默认浏览器，用户指出"扫码登录还是调的普通浏览器，该统一一下"。
- WorkBuddy（device flow，不依赖持久登录态）：**每次全新干净 profile**，用完即弃。
- Trae / Cookie 型平台：保留按「平台+账号」隔离的**持久 profile**（`.browser_state*/`），
  避免每次重新扫码。用户若要求"完全不记住"，改这两处 `launch_persistent_context` 即可。
- `workbuddy_login.py` 新增 `--browser auto|embedded|system`（默认 `auto`：内置优先、不可用回退系统浏览器）。
  **Playwright 缺失时的回退路径必须保留**，不能直接报错。
- 实现要点：Playwright sync API 的窗口必须在轮询间隙被"驱动"，否则会卡死。
  `poll_token(state, timeout, tick=...)` 的 `tick` 回调里调 `page.wait_for_timeout()` 驱动事件循环；
  用户关窗时 `tick` 抛 `BrowserClosed` 中断轮询（脚本退出码 2）。

## Trae 的 JWT 必须自动续期（2026-09-15 修的真 bug，极其重要）

**现象**：Trae 白天签到全部失败（`HTTP 200，错误码 1001`：not able to authenticate you），
积分查询全部 `HTTP 401` → 界面「积分状态」显示 0。

**根因**：`TraeClient` 用 `token = self.token or self.get_token()`。`run_trae_acc` / `run_credits`
都会把 `accounts.json` 里存的 JWT 传进来，于是 `self.token` 非空，**永远不会走 `get_token()`**。
而 JWT 只有约 8 小时有效期（长效的是 `X-Cloudide-Session`，约 14 天）。
→ 当天换过一次 token 后，8 小时后开始全灭。日志证据：00:00 成功、09:22 起 401/1001。

**修法**：`trae_api.py` 新增 `_post_authed(path, body)`，三个接口（`credits` / `status` / `checkin`）
统一走它：命中鉴权失败就用 session 重换 JWT 并**重试一次**。
- 鉴权失败判定 `_is_auth_failure`：`HTTP 401/403` **或** 业务码 `1001/1002`
  （Trae 过期后返回 HTTP 200 + code 1001，只看 HTTP 状态码会漏判）。
- 无 `session` 时不重试（避免无意义的失败）；一次就成功时不多换 token。
- 返回值带 `refreshed_token` 便于排查。

**Trae 状态接口的真实响应**（实测，只读）：
`{"checked_in": true, "code": 0, "credits": 150, "did_checked_in": true, "enable": true, "extra_credits": 50, "message": "success"}`
- 这里面 `credits` 是**今日签到奖励**，不是账户可用余额（余额要查 `pay/user_current_entitlement_list`）。
  所以跳过文案里不要写"累计 N 积分"，会被误读。
- `TraeClient.status()` 的字段名容错：按 key 优先级全局匹配
  （`checked_in` → `today_checked_in` → … → `signed`），**key 优先于深度**，
  避免深层同名布尔字段抢先命中。`known=False` 表示判不出来。

## 签到去重：本地台账 + 各平台原生预检（2026-09-15 用户要求）

用户原话："每次都是批量签，他们没有检测到我已经签过就不签了。"
各平台"今日是否已签到"的只读接口覆盖不全，所以做了两层：

1. **原生预检**（有则先用）：Trae（`status()` 读 `checked_in`）、WorkBuddy（`today_checked_in`）、
   联想（`signed_today(cfg)`）。Bilibili / 京东**没有**公开只读接口。
2. **本地台账** `logs/checkin_state.json`：`{日期: {账号名: {platform, ok, credits, message, at}}}`，
   只保留最近 14 天，**不含任何 token/cookie**。签到前若今天已成功 → 直接跳过、记为已签到、不发请求。
   `--force` 可忽略台账强制重签。

- 预检判不出来时**照常签到**（宁可多发一次请求，也绝不漏签）——这是安全方向。
- Swift 侧按描述文案判定是否"已签到"：`ParsedLogs.looksAlreadySigned()` 与 Python 的
  `checkin.is_already_msg()` **必须同口径**（`已签到` / `无需重复` / `跳过重复`），改一处要同步另一处。
- `LogHit.skipped` → 签到表状态胶囊显示「已签到」（而非「已完成」），今日积分列显示 `—`。

## 积分数据：机读 JSON 通道（不要用正则啃中文文案）

各平台文案格式不一致，原 `AppModel.matchCredits` 用正则匹配（`总限额`/`已用`/`剩余`）**只有 Trae 能命中**，
其余平台全部落空。现改为：

- `checkin.py --credits --json` 额外用 `print` 输出一行 `[CREDITS_JSON] {...}`：
  `{date, total, counted, items:[{name, app, type, label, unit, ok, balance, used, limit, streak_days, state, summary, message}]}`
- **必须用 `print` 而不是 `log()`**：`log()` 会加时间戳并写进 `checkin.log`，
  而 `ParsedLogs` 会把它当成签到记录解析。
- Swift 侧 `AppModel.parseCreditsOutput` 只认这一行（取 `last(where: hasPrefix)`，
  因为 `runPythonCapture` 返回的是 `stdout + stderr` 拼接）。
- 统一口径 = **账号当前可用余额**：Trae 剩余积分 / B站硬币 / 联想乐豆 / 京东无接口(留空) /
  WorkBuddy 的 `checkin-status.credit`（实测恒为 0，故留空显示"未提供"）。
  **各平台单位不同，`unit` 字段必须标明**；界面「账号积分总额」= 所有启用账号 balance 之和，
  下方给出逐账号明细便于核对（这也是发现 401 的入口）。
- `workbuddy-悱` 这类**没有 `access_token` 的本机登录态账号**，查询依赖本机桌面端登录文件；
  在沙盒里跑会出 "未找到 WorkBuddy 登录态文件"，属测试环境假象，不是代码问题。

## SwiftUI 约定：`Menu` 标签里不要放 Image（会忽略 .frame()）

- **现象**：「新增账号」面板里选平台的下拉，图标被渲染成 **512pt**，整个面板被撑到 ~550pt，
  左侧账号列表被挤到 391pt 而裁切（五列需要 ≥506pt），底部冒出横向滚动条。
- **原因**：SwiftUI 的 `Menu`（`.menuStyle(.borderlessButton)`）标签会按内容**固有尺寸**布局，
  **忽略标签内容里的 `.frame()`**。`NSImage` 的固有尺寸 = 自然点尺寸，
  而 `workbuddy.png` 是 512×512 @72dpi → 512pt。（`trae.png` 256px@144dpi → 128pt。）
- **实测数字**：`.workbuddy/tools/probe_layout.swift`（`xcrun swift` 直接跑，不用 build）
  → Menu 标签内置图标 **548 × 512**；换 Button 后 **88 × 32**。
- **修法**：改用 `Button { … } label: { … }.buttonStyle(.plain)` + `.popover` 自建下拉
  （`ViewAccounts.platformMenu`）。实测 Button 标签严守 frame：内容 89×32，与设计稿一致。
- **兜底**：`PlatformIcon.image()` 里 `clampPointSize()` 把点尺寸压到 ≤34pt。
  以后哪个视图再漏 `.frame()`，最多偏大一点，不会破坏布局。
- 设置页那几个 `Menu` 的标签只有 `Text`，不受影响，不用改。
- 顺带：项目窗口 `minWidth: 1280`、侧栏 240pt、页面留白 38pt×2。
  可用宽度 = 窗口 − 240 − 76；`HStack` 里列表面板间距 23pt、面板固定 358pt。
  改布局时按这套数字复算，别凭感觉。

## 平台登录态校验：接口会失效，必须能"判不了"

`browser_login.py` 的 `verify()` 返回 **三态** `(state, nickname, uid, message)`：

| state | 含义 | 处理 |
|---|---|---|
| `ok` | 平台明确说已登录 | 立即保存、关窗 |
| `pending` | 平台明确说还没登录 | 继续等 |
| `unknown` | 接口改版/风控/网络异常 | 连续 3 次（`UNKNOWN_ACCEPT_STREAK`）→ 按「关键 Cookie 已捕获」保存 |

再加一道 `CAPTURE_GRACE_SECONDS=120`：关键 Cookie 到手后最多再等 120s 做只读复核。
**只写 `pending`/`ok` 两态是错的** —— 平台接口一改版就会死等 600s、浏览器还挂着不关（踩过两次）。

各平台只读接口现状（**换接口前先实测一遍再写**）：

- 京东：`api.m.jd.com/client.action?functionId=signBeanIndex&appid=ld` →
  未登录 `{"code":"3","errorMessage":"用户未登录"}`，有效 `code=="0"`，顺带带京豆余额。
  **旧 `passport.jd.com/user/petName/getUserInfoForMini609.action` 已废**（返回 186KB 首页 HTML）。
  `signBeanAct`（签到）对无效 cookie 返回 402「挤不进去」，**不是**登录信号。
  该接口本身不稳（同一请求一会儿 200 一会儿 403）。
- 联想：签到页 `$CONFIG` 已改成 `$CONFIG = {}` + **逐行 `$CONFIG.key = 值`**，
  别再写「匹配一个对象字面量」的正则；且**未登录页面同样有 $CONFIG 和 token**，
  必须用 `_is_logged_in()`（`lenovoId`/`loginName`/`signState`）区分。
- 解析 JS 赋值：值模式要收紧（带引号串 或 `[^\s;<>]*`），最后一项常常**没有分号**，
  写宽了会吞掉后面的 `<script>`/CSS。

其它约定：账号名兜底用 `pt_pin` 时手机号只留尾 4 位（`_mask_pin`），不要把手机号显示到界面上。

## 签到结果必须是三态：成功 / 平台受限 / 失败

**「平台受限」= 平台侧不可用（活动下线、接口迁移、风控），账号和 cookie 都是好的。**
绝不能混进「失败」——否则用户会看到一个红色"签到失败"，跑去反复重新登录一个
本来好的账号；而且"失败自动重试"会反复去打对方接口。

- 平台客户端 `checkin()` 返回 `restricted: bool`；
- `checkin.py`：`run_cookie_acc` → `(ok, desc, restricted)`，统一走 `_report_result()`
  打 `[OK]` / `[受限]` / `[FAIL]`；汇总行加「平台受限 N」；只有 fail 让 exit≠0；
- `Models.swift`：`restrictedByDay` 桶，`status()` 优先级 **成功 > 受限 > 失败**，
  **`todayFail()` 必须排除受限账号**（否则重试逻辑反复重试）；
- UI 用 `Theme.warn`(橙) / `Theme.warnSoft`，文案「平台受限」；
- `AppModel.runSignSequence` 的实时刷新条件是 `[OK]`/`[FAIL]`/`[受限]` 三者。

判据口诀：**cookie 失效、网络错误是"失败"；对方活动没了是"受限"。**

## 各平台签到接口现状（2026-09 实测，改动前先自己实一遍）

- **Bilibili**：`GET /x/member/web/exp/reward`（成长中心→每日任务→登录 的领取接口）。
  ⚠️ 旧的 `/x/web-interface/checkin` **不存在**（404 返回 HTML 错误页），别再用。
  B站已取消独立签到入口，每日登录经验 +5（单位是**经验**，不是硬币）。
  幂等：`data.login` 为 true 即"今日已领"。未登录有两个码：无/空 SESSDATA → `-101`；
  **非法 SESSDATA → `-400 请求错误`（HTTP 200）**，必须再用 `nav()` 复核才能定性。
- **京东**：京豆签到**活动已下线**（`bean.m.jd.com` 302 到错误页；`signBeanAct` 对未
  签名请求恒返回 402「活动现在挤不进去呀」或 `code=0+errorCode=S109`；换 appid/client/
  body 全无效，需要 h5st 签名）。只读 `signBeanIndex` 仍可用于**校验登录态**
  （未登录 `code=3`），但已返回 `errorCode=DG-9999 系统异常`，**拿不到京豆余额**。
  **⚠️ `code=="0"` 不等于成功**：务必先看 `errorCode` 非空且 ≠"0" → 归为失败/瞬时，
  否则会把 S109 误报成"今日已签到（幂等）"。
- **联想**：签到页 `$CONFIG` 的逐行赋值写法见上一条；未登录页同样有 `$CONFIG`。

## 日志/文案约定

- `[FAIL]` 行里的文案由 `checkin.py` 统一加「签到失败：」前缀，
  **平台客户端自己的 message 不要再带这个前缀**（否则会出现"签到失败：签到失败："）。
- 成功行想被 Swift 的 `extractCredits` 和 Python 的 `credit_from_message` 取到数字，
  文案要写成「…，本次获得 N 积分（京豆/经验…）」这种形状
  （正则：`(?:本次获得|累计|共)\s*([\d.]+)\s*(?:积分|经验|京豆|乐豆|硬币)`）。

## 工具/协作坑

- **不要在同一条消息里对同一个文件发两个 Edit** —— 会互相覆盖（本轮踩了两次：
  `_report_result` 定义和汇总行都被悄悄吞掉，编辑还报"成功"）。改完要 grep 复核。
- zsh 下 `grep "a\|b"` 这类 BRE 交替经常静默返回空，用带 `-n` 的专用搜索工具更稳。

## 新增账号面板：只有「浏览器登录」一条主入口

面板结构（`AddAccountPanel`，2026-09-15 定型）：

- **主路径**：平台下拉（WorkBuddy / Trae / Bilibili / 联想 / 京东）→「打开登录浏览器」。
  账号名称可留空，脚本自动命名（昵称 → 账号 ID → auto）。
- **次级路径**（同一表单内、分隔线以下，不弹浏览器）：
  WorkBuddy →「读取本机桌面端登录态」；B站/联想/京东 →「或手动粘贴 Cookie」；Trae → 无。
- 「**凭据导入**」（cURL 抓包导入）**已删除，不要再加回 GUI**。它是 Trae 早期没有原生客户端
  时的遗留物：收一整条 cURL、存成 `requests[]` 原始 HTTP 请求、签到时照原样重放。
  被删的三条理由：① 与「手动粘贴 Cookie」在用户视角重复；② 输入框标签写「Cookie / Token」
  但 `parse_curl()` 只认 cURL，粘 Cookie **不报错**、静默存成 `GET SESSDATA=…` 的必失败账号；
  ③ 实测 7 个账号无一使用（`accounts.json` 里 `requests` 字段出现 0 次）。

**通用判据：一个输入框的标签必须和它接受的格式完全一致；不一致就先修，修不动就删。**
"标签写 A、代码只吃 B、还静默成功"是最坏的一类入口。

凭据落盘位置（改 UI 时别搞混）：

| 平台 | 存哪 | 谁读它 |
|---|---|---|
| Trae | `<name>.trae_auth`（JWT + X-Cloudide-Session） | `trae_api.py` |
| WorkBuddy | `<name>.workbuddy_auth`（accessToken/refreshToken） | `workbuddy.py` |
| B站 / 联想 / 京东 | `<name>.<platform>_auth.cookie` | `bilibili.py` / `lenovo.py` / `jd.py` |
| cURL 抓包型（仅 CLI） | `<name>.requests[]` | `checkin.py` 的 `send_request()` 分支 |

cURL 能力没完全消失，但只剩两条**非 GUI**路径：`curl_to_account.py` 命令行，
以及账号管理页的「导入账号文件」（`AppModel.importAccounts`，支持 JSON 或每行一条 cURL）。

其余约定：平台图标用 `PlatformIcon.image()`（有 34pt 上限兜底）；平台下拉用
`Button` + `.popover`，不要用 `Menu`（会忽略标签内的 `.frame()`）。
