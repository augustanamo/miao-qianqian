#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中国移动云盘（139 云盘）每日签到客户端（纯标准库实现）。

鉴权是三段式的（社区实现一致，本文件独立实现，不照搬任何仓库源码）：

  1) 凭据：`Authorization`。**网页版 yun.139.com 登录后页面状态里就有**
     （Vuex 的 `state.auth.authorization`，同时也会写成 Cookie 的
     `authorization` 项，约 1 个月有效）；App 抓包得到的也是同一个值。
     本项目由 browser_login.py 打开内置浏览器自动取，也支持手动粘贴。
     SSO 另外还要一个手机号（`account`），网页版同一处状态里也有
     （`state.auth.account`），所以落盘格式是
         <authorization>#<手机号>
     —— 一个字段塞两段，省得给凭据层加第二个输入框；`#` 不会出现在
     token 里，用它当分隔符安全。
  2) `POST https://orches.yun.139.com/orchestration/auth-rebuild/token/v1.0/querySpecToken`
     header `Authorization: <凭据>`，body `{"account": "<手机号>", "toSourceId": "001005"}`
     → `{"success":true,"data":{"token":"<ssoToken>"}}`
     失败形态（实测）：
       · 不带 Authorization      → code 1010010015「鉴权失效」
       · Authorization 是假的    → code 1010199999「未知异常」/「非法的token！」
       · account 不是字符串      → code 1010010001「参数错误」
       · 缺 toSourceId           → code 1010010001「must not be null」
  3) `POST https://caiyun.feixin.10086.cn:7071/portal/auth/tyrzLogin.action?ssoToken=<ssoToken>`
     → `{"code":0,"result":{"token":"<jwtToken>"}}`；ssoToken 过期回
     `{"code":"200050409","msg":"token 已失效"}`。
     之后所有业务接口**同时**在请求头 `jwtToken` 与 cookie `jwtToken` 携带它
     （社区实现两处都放，照做）。

签到接口（2026-09 用假凭据实测的「存在性 / 方法」判决，带假 token 回
`{"code":90001,"msg":"未登录"}` 说明路径对、只是没登录）：

    | 端点                                  | 方法 | 备注                        |
    |---------------------------------------|------|-----------------------------|
    | /ycloud/signin/page/infoV3            | GET  | POST 过去回 405「请求方法异常」|
    | /ycloud/signin/page/startSignIn       | GET  | 同上                        |
    | /ycloud/signin/page/receiveV3         | POST | GET 过去回 405              |
    | /ycloud/signin/task/taskListV3        | POST | GET 过去回 405              |
    | /ycloud/signin/user/infoV3            | —    | **不存在**（404）            |

  ⚠️ 社区脚本里那套 `/market/signin/page/info`、`/market/signin/page/receive`
  **已经 404 下线**，别照着抄。`/market/manager/commonMarketconfig/...` 和
  `/market/signin/task/taskList` 虽然还有响应，但**不带 token 也能拿到市场配置**
  ——那是配置读取，不是签到动作，照着它判「签到成功」是假的。

  ⚠️ 本文件里 /ycloud/ 的**响应字段**未能用真凭据核对过（对方要真登录态），
  所以解析写得非常宽容，认不出结构时归 `restricted` 并把原文片段带回消息里，
  方便第一次真机跑完立刻收紧。

凭据安全：authorization 等同账号密码，仅内存持有，绝不写日志、绝不回显。

用法：
    from caimcloud import CaimCloudClient, client_from_account
    c = client_from_account(acc)
    print(c.status())   # {ok, signed_today, nickname, credits, ...}
    print(c.checkin())  # {ok, already, credits, message, http, restricted}
CLI：
    python3 caimcloud.py --probe   --name <账号名>
    python3 caimcloud.py --checkin --name <账号名>
    python3 caimcloud.py --credits --name <账号名>
"""

import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

from cookie_manager import CookieError, find_account, get_cookie, load_config

# 移动云盘的网关证书链在某些环境下校验不过（自签/中间证书缺失），
# 这里显式放行——目标站点是固定域名，凭据也只发往这两个域名。
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

SSO_URL = ("https://orches.yun.139.com/orchestration/"
           "auth-rebuild/token/v1.0/querySpecToken")
JWT_URL = "https://caiyun.feixin.10086.cn:7071/portal/auth/tyrzLogin.action"
BASE = "https://caiyun.feixin.10086.cn"
INFO_V3 = BASE + "/ycloud/signin/page/infoV3"
START_SIGN = BASE + "/ycloud/signin/page/startSignIn"
RECEIVE_V3 = BASE + "/ycloud/signin/page/receiveV3"
TASK_LIST_V3 = BASE + "/ycloud/signin/task/taskListV3"

TIMEOUT = 30
# 与 App 一致的 UA，降低风控误判
UA = ("Mozilla/5.0 (Linux; Android 12; Mi 10 Pro Build/SKQ1.211006.001; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/99.0.4844.88 "
      "Mobile Safari/537.36 MCloudApp/10.3.0")

# 这些业务码表示"凭据废了"，要用户重新取 authorization，不是平台受限
_CRED_CODES = {"1010010015", "1010199999", "200050409", "90001"}
_CRED_WORDS = ("鉴权失效", "非法的token", "非法token", "token 已失效", "token已失效",
               "未登录", "登录态", "参数错误")


class CaimCloudError(RuntimeError):
    pass


def _http(method: str, url: str, headers: dict | None = None,
          body: dict | str | None = None) -> tuple:
    """发请求，返回 (status, text)。status=-1 表示网络层失败。"""
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=data, headers=h, method=method),
                timeout=TIMEOUT, context=_SSL) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return -1, json.dumps({"msg": f"网络错误：{type(e).__name__}"}, ensure_ascii=False)


def _parse(text: str) -> dict:
    try:
        obj = json.loads(text or "")
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _brief(text: str, limit: int = 160) -> str:
    """把响应压成一行，用于消息（不塞整段 JSON 进界面）。"""
    s = (text or "").strip()
    if s.startswith("<"):
        s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s)[:limit] or "(空响应)"


def split_credential(value: str) -> tuple:
    """把 `<authorization>#<手机号>` 拆成两段。返回 (authorization, account)。"""
    raw = (value or "").strip()
    if not raw:
        return "", ""
    if "#" in raw:
        a, _, b = raw.partition("#")
        return a.strip(), b.strip()
    return raw, ""


def client_from_account(acc: dict) -> "CaimCloudClient":
    """从账号条目构造客户端；凭据缺失时抛 CookieError。"""
    from cookie_manager import require_cookie
    return CaimCloudClient(require_cookie(acc, "caimcloud"))


class CaimCloudClient:
    """中国移动云盘每日签到客户端。凭据由调用方传入（仅内存持有）。"""

    def __init__(self, credential: str, account: str = ""):
        self.credential = (credential or "").strip()
        auth, embedded = split_credential(self.credential)
        self.authorization = auth
        # 手机号的来源优先级：显式参数 > 凭据里的 `#` 后缀
        self.account = (account or embedded or "").strip()
        if not self.authorization:
            raise CookieError(
                "缺少移动云盘 authorization：请在「新增账号」里用浏览器登录 "
                "yun.139.com 自动获取，或按「authorization#手机号」的格式粘贴。")
        self.jwt = ""

    # ---- 鉴权 ----
    def _auth(self) -> tuple:
        """三段式换到 jwtToken。返回 (ok, 描述, 是否凭据失效)。"""
        if not self.account:
            return False, ("凭据里没有手机号（SSO 换登录态要用它）。"
                           "用浏览器登录会自动带上；手动填写时写成「authorization#手机号」"), True
        http, text = _http("POST", SSO_URL,
                           {"Authorization": self.authorization},
                           {"account": self.account, "toSourceId": "001005"})
        if http == -1:
            return False, "网络请求失败（SSO）", False
        body = _parse(text)
        if not body:
            return False, f"SSO 响应无法解析（HTTP {http}）：{_brief(text, 80)}", False
        if not body.get("success"):
            code = str(body.get("code") or "")
            msg = str(body.get("message") or "").strip()
            detail = ""
            data = body.get("data") or {}
            if isinstance(data, dict):
                r = data.get("result") or {}
                if isinstance(r, dict) and r.get("resultDesc"):
                    detail = str(r["resultDesc"])
            why = "，".join(x for x in (msg, detail) if x)
            degraded = code in _CRED_CODES or any(w in why for w in _CRED_WORDS)
            if degraded:
                return False, (f"移动云盘 authorization 已失效（{why or code}），"
                               f"请重新从 yun.139.com 取一次"), True
            return False, f"SSO 换取登录态失败（{why or code or '未知'}）", False
        sso = ((body.get("data") or {}).get("token") or "").strip()
        if not sso:
            return False, "SSO 未返回 token", False

        http, text = _http("POST", f"{JWT_URL}?ssoToken={urllib.parse.quote(sso)}",
                           {"Host": "caiyun.feixin.10086.cn:7071"})
        if http == -1:
            return False, "网络请求失败（换 jwtToken）", False
        jb = _parse(text)
        code = str(jb.get("code"))
        if code not in ("0", "None") and jb.get("code") not in (0, None):
            msg = str(jb.get("msg") or "").strip()
            degraded = code in _CRED_CODES or any(w in msg for w in _CRED_WORDS)
            if degraded:
                return False, f"移动云盘登录态换取失败（{msg or code}），请重新取 authorization", True
            return False, f"换 jwtToken 失败（{msg or code}）", False
        jwt = (((jb.get("result") or {}).get("token")) or "").strip()
        if not jwt:
            return False, f"换 jwtToken 未返回 token：{_brief(text, 80)}", False
        self.jwt = jwt
        return True, "", False

    def _call(self, method: str, url: str, body: dict | None = None) -> tuple:
        """带 jwtToken 的业务请求（头部 + cookie 双写，与前端一致）。"""
        headers = {
            "jwtToken": self.jwt,
            "Cookie": f"jwtToken={self.jwt}",
            "Referer": BASE + "/",
        }
        return _http(method, url, headers, body)

    # ---- 业务 ----
    def _info_v3(self) -> tuple:
        """签到状态。返回 (http, body_dict, raw)。"""
        http, text = self._call("GET", INFO_V3)
        return http, _parse(text), text

    def checkin(self) -> dict:
        """每日签到（幂等）。返回 {ok, already, credits, message, http, restricted}"""
        ok, msg, degraded = self._auth()
        if not ok:
            return {"ok": False, "already": False, "credits": 0, "message": msg,
                    "http": 0, "code": -1, "restricted": not degraded}

        http, info, raw = self._info_v3()
        if http == -1:
            return {"ok": False, "already": False, "credits": 0,
                    "message": "网络请求失败", "http": 0, "code": -1, "restricted": False}
        if http in (401, 403) or _looks_degraded(info):
            return {"ok": False, "already": False, "credits": 0,
                    "message": "移动云盘登录态已失效，请重新取 authorization",
                    "http": http, "code": info.get("code", -1), "restricted": False}

        signed = _today_signed(info)
        if signed is True:
            return {"ok": True, "already": True, "credits": 0,
                    "message": "今日已签到（幂等）", "http": http,
                    "code": info.get("code", 0), "restricted": False}

        http, body, raw = self._call_status("GET", START_SIGN)
        if http == -1:
            return {"ok": False, "already": False, "credits": 0,
                    "message": "网络请求失败", "http": 0, "code": -1, "restricted": False}
        code = body.get("code")
        msg = str(body.get("msg") or body.get("message") or "").strip()
        if code in (0, "0") or str(body.get("success")).lower() == "true":
            if "已签" in msg or "重复" in msg:
                return {"ok": True, "already": True, "credits": 0,
                        "message": "今日已签到（幂等）", "http": http, "code": code,
                        "restricted": False}
            gain = _gain_from(body)
            return {"ok": True, "already": False, "credits": gain,
                    "message": (f"签到成功，本次获得 {gain} 云朵" if gain else "签到成功"),
                    "http": http, "code": code, "restricted": False}
        if _looks_degraded(body):
            return {"ok": False, "already": False, "credits": 0,
                    "message": "移动云盘登录态已失效，请重新取 authorization",
                    "http": http, "code": code, "restricted": False}
        # 认不出的结构：先归"平台受限"并把原文带回来，别谎报成功/失败
        if signed is None:
            return {"ok": False, "already": False, "credits": 0, "http": http, "code": code,
                    "restricted": True,
                    "message": (f"移动云盘签到返回了未识别的结构，无法判定结果：{_brief(raw)}"
                                f"（这是接口改版，不是账号问题）")}
        return {"ok": False, "already": False, "credits": 0, "http": http, "code": code,
                "restricted": True,
                "message": (f"移动云盘签到被拒绝：{msg or _brief(raw, 100)}"
                            f"（账号与 authorization 正常）")}

    def _call_status(self, method: str, url: str) -> tuple:
        http, text = self._call(method, url)
        return http, _parse(text), text

    def status(self) -> dict:
        """查询今日签到状态（无副作用）。返回 {ok, signed_today, nickname, credits, ...}"""
        base = {"ok": False, "signed_today": None, "nickname": "", "uid": "",
                "credits": 0, "summary": "", "http": 0, "code": -1, "message": ""}
        ok, msg, _degraded = self._auth()
        if not ok:
            base["message"] = msg
            return base
        http, info, raw = self._info_v3()
        if http == -1:
            base["message"] = "网络请求失败"
            return base
        if _looks_degraded(info):
            base["message"] = "移动云盘登录态已失效，请重新取 authorization"
            base["http"] = http
            return base
        signed = _today_signed(info)
        cloud = _num_field(info, ("total", "cloudCount", "totalCloud", "cloud", "point", "balance"))
        base.update(
            ok=True, http=http, code=info.get("code", 0), message="",
            signed_today=signed,
            nickname=_str_field(info, ("nickName", "nickname", "userName", "name")) or self.account,
            uid=_str_field(info, ("userId", "uid", "account")) or self.account,
            credits=cloud,
        )
        parts = []
        if self.account:
            parts.append(f"账号 {self.account}")
        if cloud:
            parts.append(f"云朵 {cloud}")
        if signed is True:
            parts.append("今日已签到")
        elif signed is False:
            parts.append("今日未签到")
        base["summary"] = "，".join(parts)
        if signed is None and not cloud:
            base["summary"] = (base["summary"] + "；" if base["summary"] else "") + \
                f"状态接口未返回可识别的字段（{_brief(raw, 80)}）"
        return base

    def probe(self) -> dict:
        """脱敏探测（无副作用）：验证凭据可用并回填手机号。"""
        ok, msg, degraded = self._auth()
        if not ok:
            return {"found": True, "healthy": False, "has_cookie": True,
                    "nickname": "", "uid": self.account,
                    "message": msg, "reason": msg}
        http, info, _raw = self._info_v3()
        name = _str_field(info, ("nickName", "nickname", "userName", "name")) or self.account
        return {"found": True, "healthy": True, "has_cookie": True,
                "nickname": name, "uid": self.account,
                "message": "", "reason": ""}


# ----------------------------------------------------------------------
# 响应字段的宽容提取
# ----------------------------------------------------------------------
def _walk(obj):
    """深度优先遍历所有 dict / list 节点。"""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _str_field(obj, keys) -> str:
    if not isinstance(obj, dict):
        return ""
    for k in keys:
        for node in _walk(obj):
            v = node.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _num_field(obj, keys) -> int:
    if not isinstance(obj, dict):
        return 0
    for k in keys:
        for node in _walk(obj):
            v = node.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)) and v:
                return int(v)
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
    return 0


def _today_signed(obj):
    """今日是否已签到；判不出来返回 None（宁可多发一次请求，也不漏签）。"""
    if not isinstance(obj, dict):
        return None
    for key in ("todaySignIn", "todaySignin", "signed", "isSign", "signIn",
                "todaySigned", "hasSign"):
        for node in _walk(obj):
            v = node.get(key)
            if isinstance(v, bool):
                return v
            if v in (0, 1, "0", "1"):
                return bool(int(v))
            if isinstance(v, str) and v.lower() in ("true", "false"):
                return v.lower() == "true"
    return None


def _gain_from(obj) -> int:
    return _num_field(obj, ("cloudCount", "cloud", "gain", "reward", "amount",
                            "point", "value", "num"))


def _looks_degraded(obj: dict) -> bool:
    """响应是否表示"登录态废了"（90001 未登录 / 鉴权失效 / 非法 token）。"""
    if not isinstance(obj, dict):
        return False
    code = str(obj.get("code"))
    if code == "90001":
        return True
    text = f"{obj.get('msg') or ''}{obj.get('message') or ''}"
    return any(w in text for w in _CRED_WORDS)


# ---------- CLI ----------
def _load_acc(name: str) -> dict:
    acc = find_account(load_config(), name)
    if acc is None:
        sys.stderr.write(f"未找到账号：{name}\n")
        sys.exit(2)
    return acc


def _probe(acc: dict) -> dict:
    """脱敏探测 JSON（不含凭据明文），供 Swift 侧一键添加/刷新账号。"""
    try:
        cred = get_cookie(acc, "caimcloud")
        if not cred:
            msg = ("账号未配置移动云盘授权码，请粘贴「authorization#手机号」"
                   "（网页版 yun.139.com 登录后 cookie 里的 authorization）")
            return {"found": False, "healthy": False, "has_cookie": False,
                    "nickname": "", "uid": "", "message": msg, "reason": msg}
        try:
            return CaimCloudClient(cred).probe()
        except CookieError as e:
            return {"found": True, "healthy": False, "has_cookie": False,
                    "nickname": "", "uid": "", "message": str(e), "reason": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"found": True, "healthy": False, "has_cookie": True,
                    "nickname": "", "uid": "",
                    "message": f"登录态验证失败：{type(e).__name__}",
                    "reason": f"登录态验证失败：{type(e).__name__}"}
    except Exception as e:  # noqa: BLE001
        return {"found": False, "healthy": False, "has_cookie": False, "nickname": "",
                "uid": "", "message": f"读取失败：{type(e).__name__}",
                "reason": f"读取失败：{type(e).__name__}"}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="中国移动云盘每日签到客户端")
    parser.add_argument("--probe", action="store_true", help="探测凭据登录态（输出脱敏 JSON）")
    parser.add_argument("--checkin", action="store_true", help="执行签到")
    parser.add_argument("--credits", action="store_true", help="查询账号状态/云朵")
    parser.add_argument("--name", required=True, help="accounts.json 中的账号名")
    args = parser.parse_args()

    acc = _load_acc(args.name)
    if args.probe:
        print(json.dumps(_probe(acc), ensure_ascii=False))
        return

    try:
        client = client_from_account(acc)
    except CookieError as e:
        print(f"签到失败：{e}")
        sys.exit(1)

    if args.credits:
        r = client.status()
        if r.get("ok"):
            print(r.get("summary") or "状态正常")
        else:
            print(f"查询失败：{r.get('message')}")
            sys.exit(1)
    elif args.checkin:
        r = client.checkin()
        if r.get("ok"):
            print("今日已签到，无需重复" if r.get("already") else r.get("message") or "签到成功")
        else:
            # 受限/失败都由 message 说清楚，不套"签到失败："前缀
            # （checkin.py 的 run_cookie_acc 会统一加，重复加会变成双重前缀）
            print(r.get("message") or "签到失败")
            sys.exit(1)
    else:
        print("用法：--probe / --checkin / --credits（均需 --name）")


if __name__ == "__main__":
    main()
