# 各平台接口参考资料

> 改任何平台客户端之前先读这里，再动手实测。
> **取证方法论与判据在 skill `checkin-endpoint-forensics`**，这里只放各平台的接口事实。
> 最后更新 2026-09-17。

## B站

- **签到动作 = 漫画签到**：`POST https://manga.bilibili.com/twirp/activity.v1.Activity/ClockIn?platform=android`
  - 首次 `{"code":0}`；重复 `{"code":1,"msg":"不能重复签到~"}`；
    ⚠️ 凭据无效时 `code` 是**字符串** `"internal"` → 判据要先转字符串比较。
  - 奖励是**漫读券**不是硬币 → `credits` 恒 0，**别套「本次获得 N 硬币」模板**（会打印"获得 0 硬币"）。
- 成长中心 `/x/member/web/exp/reward` **只能读**（POST → 405）；每日登录经验由平台自行结算，
  实测「访首页 / nav / 推荐流 / 补 buvid3」都不会把它置为已完成 → 纯 API 拿不到。
- 直播 `DoSign`：路由活着（不带凭据回 `-101`），但**带真凭据回 `code 1「签到活动已下线，无法使用」`**。

### 权益类端点（2026-09-17 **真机验证**；只需 cookie + `bili_jct` 当 csrf）

**已接入 `bilibili.py`**：签到动作之后自动领这三项（`_claim_perks(vip_type)`）。

| 能力 | 端点 | 门槛（已实测） |
|---|---|---|
| 大会员每月福利·读 | `GET https://api.bilibili.com/x/vip/privilege/my`（Referer `big.bilibili.com`）→ `list[]{type,state,next_receive_days,...}` + `is_vip` / **`vip_is_annual`** | 只读；**只用于展示，不当"能不能领"的判据** |
| 大会员每月福利·领 | `POST /x/vip/privilege/receive?type={1:B币券\|2:会员福利}&csrf=<bili_jct>` | **只对年度大会员发起**（`vip_type == 2`）。月度/非会员**静默跳过**（社区实现同此判定：月度不赠 B 币券） |
| 漫画会员权益 | `POST manga.bilibili.com/twirp/user.v1.User/GetVipReward?reason_id=1&platform=android` | 任何大会员（`vip_type ∈ {1,2}`）每月一次 |
| 银瓜子换硬币 | 读 `GET api.live.bilibili.com/xlive/revenue/v1/wallet/getStatus`；换 `POST /xlive/revenue/v1/wallet/silver2coin` | 先读 **`silver_2_coin_left`**，`<= 0` 就不动 |

- **真实响应（本机账号实测）**：
  - `getStatus` → `{"silver":2880,"coin":1818,"coin_2_silver_left":50,"silver_2_coin_left":1,"status":1,"vip":1}`。
    兑换成功后 `silver` 2880→2180、`coin` 1818→1819、`silver_2_coin_left` **1→0** ⇒ **700 银瓜子换 1 硬币**。
    `silver_2_coin_left` 是**天然幂等守卫**：第二天的第一次运行才会再换，同日重跑自动跳过。
  - `GetVipReward` 重复调用 → `{"code":1,"msg":"已经领取过该奖励或者未达到领取条件哦~"}`
    ⚠️ **这句话把两种情形合并了**。我们在调用前已确认是大会员，所以按"已领取"理解；
    但别把它当"确定已领"的强证据（用户也可能自己在 App 领过）。
  - `GetVipReward` 成功时 `data` 里**没有 `amount`**（实测打印出「领取成功」但没有张数）→ 文案别依赖它。
- `bili_jct` 从 cookie 里按名字取（`_cookie_field`）。**缺失时签到照常、领取全部跳过**，
  文案说「权益未领：Cookie 缺 bili_jct」——因为令牌型平台的手工粘贴入口很容易只贴 SESSDATA。
- **`nav.data.vipType`：0 非会员 / 1 月度 / 2 年度**；`vipStatus` 1=是大会员。
  本机账号是 **月度**（`vip_type:1 / vip_is_annual:false`）⇒ B币券那两项**拿不到**，是空转。
  这类"不适用"必须**静默**，不能记失败、不能每天刷一句。
- **安静是默认**：`already` 一律返回空串不输出。只有"本次真领到"和"未领到"才写文案。
- `GET /x/member/web/exp/reward`（每日任务只读）会**偶发 `-412`**（风控，`HTTP 412`），
  同一秒重打即回 `code 0`。实测数据 `{"login":true,"watch":true,"share":false,"coins":0}`
  ⇒ 附加信息拉取失败**不该影响签到主流程**。
- **大积分那套仍不做**：`GET /x/vip_point/task/combine`、`POST /pgc/activity/score/task/{sign2, receive/v2, complete/v2}`、
  `/pgc/activity/deliver/task/complete`、`/x/vip/experience/add`。不带凭据回 **`-401 非法访问`**
  （同级其它端点都是 `-101`）→ 说明多了签名/风控校验，接入成本明显更高。
- **每日任务（刷 65 经验）有接口，但本项目不做**：观看 `/x/click-interface/web/heartbeat`、
  分享 `/x/web-interface/share/add`、投币 `/x/web-interface/coin/add`、投币经验 `/x/web-interface/coin/today/exp`。
  ⚠️ 分享/观看**未登录也回 `code 0`**（可能是静默 no-op）→ 不能拿它证明凭据有效；投币要真花硬币。
- 探测判据：**编造路径对照组**——同 host 编造路径回 HTTP 404 + HTML，真实路径回 HTTP 200 + JSON
  ⇒ **回 JSON 业务体（哪怕 code 是错的）= 路由存在**。
  且**带假 `SESSDATA` 一律回 `-400 请求错误`（噪声）；不带凭据才回干净的 `-101 账号未登录`**
  ⇒ 探"路由是否存活"要**不带凭据**。换有效 `buvid3/buvid4`（`GET /x/frontend/finger/spi` 的 `data.b_3/b_4`）对 `-400` 毫无改善。

## 联想智选

- 提交：`POST /signadd`，参数 `_token`（Laravel CSRF，= 页面 `$CONFIG.token`）/ `memberSource`（= `$CONFIG.userAgent` "0"）/
  `pss` / `deviceId` / `deviceToken`（纯 H5 为空）/ `lenovoId`。
  ⚠️ 旧代码发的是 `token` / `rowKey`，而页面 `$CONFIG` 里**根本没有 `rowKey`** → 恒 419。
- **必须与拉页共用同一 cookie jar**（页面下发 `wap_session`）；`urllib.request.urlopen` 直连会丢 `Set-Cookie`
  → 用 `CookieJar` + `build_opener`，`sign_page()` 与 `_post_sign()` 复用同一个 opener。
- 响应：成功 `{"success":true,"continueCount":1,"ledouValue":20,"scoreValue":10,"rewardTips":"获得20乐豆\n10积分\n2成长值"}`
  —— **没有 `code` 字段**，只认 `code` 会把成功判成失败；重复是 `{"code":0,"msg":"用户已签到"}`。
  判据优先级：`success is True` → `code in (0,"200")` → 文本含"已签到/重复" → 否则 fail。
- 余额：`GET /signuserinfo` → `{"serviceAmount":77,"userCoins":1045,"ledou":"1.6万"}`。
  `ledou` 是站点四舍五入的**展示串**（精度只到千位）→ `credits`（数字）+ `credits_text`（原文）双轨，
  别据此算"较昨日 +N"。该接口**同样抖**，未命中会话节点回 `{"res":"Must login"}` 而 **HTTP 仍是 200**
  → 按"有没有期望字段"判并重试（同一会话 15 次命中率 ~35%，重建会话只有 ~10%）。
- 抖动：同一套 token/cookie/头只有约 **20%** 成功，其余恒回 `419 CSRF token mismatch`。
  真实 Chromium 调页面自带的 `signSubmit()` 复现同样比例（4 次里 3 次 419）→ **对方 openresty 多实例、会话不粘**。
  **正解 = 重试 20 次**（签到接口幂等）。反例（实测都不改善）：连接复用、补 `deviceId`/`deviceToken`、加 `X-XSRF-TOKEN`。
- 页面已签标记：`$CONFIG.signState == "1"`（未签是空串）、`continuity_day` 递增、DOM 里 `已签到`。

## 中国移动云盘

- 三段式鉴权：
  1. `Authorization` 头（凭据）
  2. `POST https://orches.yun.139.com/orchestration/auth-rebuild/token/v1.0/querySpecToken`
     body `{"account":[appid,phone,token],"toSourceId":"001005"}` → `data.token`（ssoToken）
  3. `POST https://caiyun.feixin.10086.cn:7071/portal/auth/tyrzLogin.action?ssoToken=…`
     （**Host 必须是 `caiyun.feixin.10086.cn:7071`**）→ jwtToken
- ⚠️ **jwtToken 要同时放进请求头和 Cookie**，否则一律回"未登录"且**文案一字不变**
  （曾穷举 11 种 header/cookie 位置全部失败，最后是靠读社区脚本原始源码才定位）。
- 签到：`/ycloud/signin/page/infoV3`(GET) / `page/startSignIn`(GET) / `page/receiveV3`(POST)。
  旧 `/market/` 族已 404。凭据失效特征 `code 90001`。签到窗口 0 点刷新，建议 8 点后跑。
- 凭据落盘 = `<authorization>#<手机号>`（手机号给 SSO 的 `account` 参数）。
- **登录态可由网页自动获取**（`browser_login.py --platform caimcloud`）：
  `authorization` 在 Cookie（可能 HttpOnly → 用 Playwright `ctx.cookies()` 读），
  手机号在页面状态（桌面版 `window.MCloudVM.$store.state.auth.account`；
  移动版 `window.VUEObj.$store.state.auth` **两段都有，可一次取全**）。
  桌面 UA 会被**重定向到 PC 版 `/w/`**，而 **PC 版 store 里没有 `authorization`**；
  桌面版首页 `https://yun.139.com/` 就是完整登录页，⚠️ `/w/#/login` 是 **404 页**别用。
  手动粘贴只是兜底。

## 华住会（未接入，已调研）

- `GET https://appgw.huazhu.com/game/sign_in?date=<unix 秒>` + `Cookie` 头，**没有任何 JS 签名**
  → 纯标准库可行（与什么值得买同级）。
- 网关是 **APISIX**：**缺 `Client-Platform` 头时恒回 `429 {"code":429,"message":"You have been restricted..."}`**
  （带不带 Cookie、换什么 UA 都一样）→ 补上该头才走到业务层，未登录回 `401 {"code":1003,"Unauthorized"}`。
  响应头 CORS 暴露了它认的自定义头：`Client-Platform` / `sid` / `sk` / `version` / `User-Token` / `token` / `authorization`。
- ⚠️ 补对头后 **429 仍会随机出现**（同头连打 3 次：1 次 401、2 次 429）→ 网关多实例计数不同步，**重试**即可。
- 凭据只能从 **App / 小程序**抓（社区走 MITM `game/sign_header` 或小程序 `hweb-minilogin.huazhu.com/bridge/jump`）；
  官网 `www.hworld.com`、预订站 `hrewards.huazhu.com` 未登录 Cookie 里**没有任何登录态字段**，
  且**官网没有签到入口**（签到是 App 专属功能）。
- 收益很小（脚本作者自评"小小毛"，累计 7 天约 5 元券）→ 接入性价比低。

## 京东（**平台已停用**，在 `_RETIRED_PLATFORMS` 里）

- `signBeanAct` 连打 3 次（间隔 8s）恒 `code "402"`「活动现在挤不进去呀」；换网页端形态 `appid=wh5` 回 `code "1"`。
- **只读的 `signBeanIndex` 也回 `{"code":"0","errorCode":"DG-9999","errorMessage":"系统异常"}`**
  → 京东自家 H5 页面的数据接口都挂了，证明是平台侧问题。
- 社区能跑通的实现都要 **h5st 签名**（`H5ST.genH5st('235ec', ..., 'signBeanAct', ts)`，algo 版本 `64e35`），
  是 JS 混淆算法，塞进纯标准库不现实；且 lxk0301 等仓库已归档。
- → 不发请求、不记成功/失败/受限、不写台账。**绝不能让用户去重登。**

## 阿里云盘 / 什么值得买

- **阿里云盘**：凭据是 `refresh_token`（在 localStorage 的 `token` 项里：
  `JSON.parse(localStorage.getItem("token")).refresh_token`），**不是 Cookie**。
  容量单位是**字节**，走独立字段不进 `balance`。新 refresh_token 要**回写**
  （`AliyunpanClient._store_token()` 是唯一写盘处，走 `cookie_manager.update_credential()`）；
  `--probe` 必须 `autosave=False`。
- **什么值得买**：走 **App 端签名接口**，建议用 App 抓包得到的完整 Cookie（含 `sess`）；
  只粘网页版 Cookie 时程序会自动退到网页端接口重试。

## WorkBuddy（`https://copilot.tencent.com`）

**网关前缀不一致**：签到类带 `/v2`，资源类**不能**带。

| 用途 | 路径 |
|---|---|
| 签到状态 | `POST /v2/billing/meter/checkin-activity-status` |
| 签到动作 | `POST /v2/billing/meter/daily-checkin`（幂等，已签回 10001） |
| 余额聚合 | `POST /billing/meter/get-user-resource-summary`（**合计以此为准**） |
| 免费/付费包 | `.../get-user-resource-free-packages` / `...-paid-packages`（分页） |
| 企业月额度 | `.../get-enterprise-user-usage`（需 `X-User-Id`） |

- ⚠️ `/billing/meter/checkin-status`（无 `/v2`）是**废弃端点**，恒回 `active=false`。
- **两个口径不能互冒**：`total_credits` = 签到活动累计发出；账户余额 = `resources()["remain"]`。
- `CapacityType == 4` 是切片包：读 `SlicePeriodUsageDetails[0]` 是当日口径，
  回落 `CycleCapacity * Precise` 是周期口径。
- 分页 `PageSize=200`、`Status:[0,3]`，按 `ResourceId` 去重，**截断必须告警**。
- 消费明细粒度上限 = 资源包周期，再细只能本地快照比
  （`logs/credits_state.json`，**只在跨日**给 delta 并标明是本地推算）。

### 本机登录态：桌面端已改为**加密存储**（2026-09-24 起，读不到明文）

- 文件位置没变（`~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`），
  但 `auth.accessToken` / `auth.refreshToken`、`account.nickname` / `account.phoneNumber`
  都变成了 `{"$wbEncrypted": 1, "envelope": "<base64 信封>"}`。
- 加解密实现：`app.asar/main/credential-protection.js`（`ProtectedFieldCodec`）→ 原生模块
  `app.asar.unpacked/native/turing-sdk/build/Release/turing_sdk.node`（腾讯 TuringShield）。
  **密钥不落盘**，所以第三方程序拿不到明文 —— 这条路对我们是死的，**别再花时间试解密**。
  `workbuddy.py --probe` 会置 `encrypted: true` 并如实说明原因（不是"没登录"）。
- 结论：WorkBuddy 账号现在只能靠**扫码登录**（`workbuddy_login.py`，OAuth device flow，
  不依赖桌面端；`/v2/plugin` 前缀）。
- **账号有两种来源，行为完全不同**（`workbuddy_auth.source`）：

| source | 凭据在哪 | 桌面端状态是否影响它 | 「重新登录」怎么走 |
|---|---|---|---|
| `oauth`（扫码登录） | accounts.json 里的 access_token | **不影响** | 再扫一次码 |
| `local`（本机登录态一键读取） | 只有桌面端有 | **影响**：桌面端没登录 / 已加密就必然签不了 | 桌面端还能读明文时重读；一旦加密就改走扫码 |

- 判据唯一真源：`AppModel.workbuddyReloginUsesOAuth(_:)` —— **路由与按钮文案都调它**，
  免得出现"按钮写着『读取本机登录态』、点下去弹扫码页"这种自相矛盾。
- ⚠️ 别再用 `wbHealthy == false` 一刀切判 WorkBuddy 账号健康：`oauth` 账号自成一体，
  拿桌面端探针判它会在桌面端加密/未登录时**永久误报"凭据失效"**。

### 成长中心六步（`/v2/activity/growth` 前缀；任务表「今日任务」列的第 2~7 个图标）

| key | 任务 | 查询端点 | 动作端点 |
|---|---|---|---|
| travel | Buddy 旅行 | `/buddy/travel/status` | `/buddy/travel/claim`、`/buddy/travel/config`、`/buddy/travel/depart` |
| tasks | 任务奖励 | `/tasks` | `/tasks/accept` |
| makeup | 补登 | `/streak` | `/makeup-cards/use` |
| redeem | 连登兑换 | `/redeem/summary` | `/redeem`（tier 先试天数，被判非法再退回档位名） |
| lottery | 抽盲盒 | `/lottery/chances` | `/lottery/draw` |
| buddy_box | 能量盲盒 | `/buddy/quota` | `/buddy/open` |
| — | 汇总（energy/streak） | `/energy`、`/streak` | — |

- **已实证的字段**：`/energy` + `balance`、`/streak` + `streak.days` —— 日志里能打出「能量 8」「连签 6 天」，
  说明这组路径+字段名是通的。
- ⚠️ **未实证的字段**（当初按同批风格写的，没留真机响应证据）：
  `/redeem/summary` 的 `starter_status`/`advanced_status`/`legendary_status`、`/lottery/chances` 的 `balance`、
  `/buddy/quota` 的 `affordable`、`/tasks` 的 `tasks[]`/`accept_status`、`/buddy/travel/status` 的 `state`。
  **桌面客户端反查不到**：app.asar 里成长中心只调 `/buddy/info` 与 `/buddy/travel/claim`，其余是网页端功能。
- **自证出口**：这几步 idle 时会把读到的**原值**写进 tooltip（如「入门 未读到」「读到 0 条任务」）。
  悬停若显示"未读到"，就是字段名错了 —— 别再去猜。
- 纪律：**idle 的原因只进 tooltip，不进日志**（`_exec` 第 4 项 `note`）。
  `note` 以 `running_prefix` 开头时判 `running` —— 进行态既能保住橙色图标、又不必写日志。

## Trae（`https://www.trae.cn` / `https://work.trae.cn`）

**两套凭证，别搞混**（2026-09-25 实测订正）：

| 字段 | 是什么 | 有效期 | 谁在用 |
|---|---|---|---|
| `trae_auth.session` | HttpOnly Cookie `X-Cloudide-Session` | 约 14 天（**观测值**） | **真正的长效凭证**：签到时调 `GetUserToken` 换 JWT |
| `trae_auth.token` | localStorage `Cloud-IDE-Token`（JWT，`exp = iat + 28800`） | 8 小时 | 只是上次换到的票根，撞 401/403/业务码 1001 就丢弃重换 |

- 签到链路：`TraeClient._post_authed` 先用手头 JWT，被判鉴权失败 → `get_token()` 用 session
  换一个全新 JWT → 重试一次。**所以存下来的 token 过不过期完全不影响签到**；
  `checkin.py` 的失败判据也是"缺少 `trae_auth.session`"，不是 token。
- 实测证据：token 的 exp 已过 10 天（132trae）/ 已过 15 小时（136），当天 09:00 签到
  照样 `HTTP 200` 成功；换 token 那次重试是静默发生的。
- ⚠️ UI 曾拿 JWT 的 `exp` 判"凭证已过期" → 天天误报红、挂「重新登录」，用户点了发现根本不用重登。
  判据已改成**只看 session 在不在**（`Models.credentialHint()`），注释写在那里，别再加回来。
- "session 约 14 天"是观测值，**不要拿它做倒计时**（那跟上面是同一类错误）；
  UI 只显示"长效会话 · 已保存 N 天"作参考。
- 登录侧：`trae_login.py` 抓 localStorage token + 长效 Cookie；没抓到 token 时会用 session 现换一个，
  所以 token 为空也不算账号坏了。

## 新增一个凭据型平台：固定改动面（6 步）

原来放在 `MEMORY.md`，为控制那边体积搬到这里——**加平台前照单走一遍**：

1. `<platform>.py`，契约同 `bilibili.py`：
   `client_from_account(acc)` / `checkin()`→`{ok,already,credits,message,http,restricted,tasks}` /
   `status()`→`{ok,signed_today,nickname,credits,summary,http,message}` / `_probe(acc)` /
   CLI `--probe/--checkin/--credits --name`。返回值带 `tasks`（子任务结构化，见 `subtasks.py`）。
2. `checkin.py`：`_COOKIE_PLATFORMS` 注册 + `_PLATFORM_LABEL` / `_PLATFORM_UNIT` 补条目。
3. `Models.swift`：加 `<p>_auth: CookieAuth?` + `is<P>`，挂进
   `isCredentialPlatform` / `cookieAuth` / `platformIconName`（含 `app` 字符串兜底）/ `credentialNoun`。
4. `AppModel.swift`：`cookiePlatforms` 加元组 + `cookieScript(for:)` + `credentialField(for:)`；
   需要自动命名的加进 `autoName`。
5. `ViewAccounts.swift`：`groupedAccounts` buckets + `cookieSymbol(_:)` + `browserOptions` 的 hint/kind +
   `manualCredentialTip`。
6. `assets/platform-icons/<platform>.png`（512×512 满幅；打包走 `*.png` 通配，**须在重签名之前**）。
   取图见 skill `platform-brand-icons`。

登录路径三选一（在 `browser_login.py` 的 `PLATFORMS` 里登记）：

- `{"cookies": …}` —— 凭据就是 Cookie（B站 / 联想 / 什么值得买）。
- `{"script": …}` —— 凭据不在 Cookie 里，要在页面里执行 JS 取值。
- `pair` 规则 —— 「Cookie 字段 + 页面状态字段」两段拼成的，缺的前半段由 `_cookie_value()`
  从 Playwright 的 `ctx.cookies()` 补（HttpOnly 也读得到，`document.cookie` 读不到）。
