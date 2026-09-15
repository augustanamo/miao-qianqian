#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""京东每日签到客户端（纯标准库实现）。

接口思路参考开源项目协议（jd_scripts / lxk0301 等），代码为本项目内
独立实现，不照搬任何仓库源码：
  - POST https://api.m.jd.com/client.action
       functionId=signBeanAct：京豆签到
       （参考协议思路：body 携带 jdkjFrom 等固定参数，cookie 携带 pt_key/pt_pin）
  - GET  https://api.m.jd.com/client.action?functionId=signBeanIndex&appid=ld
       京豆签到首页数据：只读，用来校验登录态 + 读京豆余额。
       未登录返回 {"code":"3","errorMessage":"用户未登录"}，登录有效 code=="0"。
       ⚠️ 旧接口 passport/user/petName/getUserInfoForMini609.action 已于 2026-09
       失效（不再返回 JSON，而是返回 186KB 的京东首页 HTML），继续用它会导致
       「登录态永远校验不通过」——浏览器登录流程会因此一直空等。

签到结果判据（幂等）：
  - code=="0" 且 dailyAward.beanCount>0            -> 签到成功
  - code=="0" 且 errorCode 为空/"0"，
    无有效 dailyAward（或提示已签到）                -> 今日已签（幂等成功）
  - code=="0" 但 errorCode!="0"（如 S109）         -> 活动侧拦截，**不是**已签到
  - 未登录相关 code / errmsg                        -> cookie 失效
  - 其余（如 402「活动现在挤不进去呀」）             -> restricted=True「平台受限」

⚠️ 2026-09 实测：京豆签到活动已在实际层面不可用（bean.m.jd.com 签到页 302 到
京东错误页；signBeanAct 无论怎么拼 appid/client/body 都返回 402/S109）。
所以 checkin() 会把这类结果标成 restricted，上层显示"平台受限"而不是
"签到失败"——免得用户以为是 cookie 坏了去反复重新登录。

凭据安全：cookie 等同账号密码，读取后仅内存使用，绝不写日志、
绝不回显到 stdout / UI。accounts.json 仅保存用户主动添加的 cookie
字段与 nickname 非敏感快照。

用法：
    from jd import JdClient, client_from_account
    c = client_from_account(acc)
    print(c.status())   # {ok, signed_today, nickname, credits, ...}
    print(c.checkin())  # {ok, already, credits, message, ...}
CLI：
    python3 jd.py --probe  --name <账号名>
    python3 jd.py --checkin --name <账号名>
    python3 jd.py --credits --name <账号名>
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from cookie_manager import CookieError, find_account, get_cookie, load_config

API_JD = "https://api.m.jd.com/client.action"
# 只读的京豆签到首页数据：既校验登录态，也带余额
API_SIGN_INDEX = API_JD + "?functionId=signBeanIndex&appid=ld"
# 旧登录态接口（已失效，保留常量仅为注释可追溯，勿再调用）
API_PASSPORT = "https://passport.jd.com/user/petName/getUserInfoForMini609.action"
TIMEOUT = 30
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")

SIGN_BODY = json.dumps({
    "fp": "-1",
    "shshshfp": "-1",
    "shshshfpa": "-1",
    "referUrl": "https://bean.m.jd.com/",
    "jdkjFrom": "M",
    "riskLevel": 5,
}, separators=(",", ":"))

# 签到请求重试次数（含首次）。京东的拦截文案是"待会再来试试吧"，
# 隔几秒再给一次机会；仍失败则归类为「平台受限」，而不是让用户去折腾 cookie。
_SIGN_ATTEMPTS = 2
_SIGN_RETRY_DELAY = 5.0


class JdError(RuntimeError):
    pass


def _get(url: str, cookie: str) -> tuple:
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://bean.m.jd.com/",
        "Cookie": cookie,
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"网络错误：{type(e).__name__}"


def _post_sign(cookie: str) -> tuple:
    params = {
        "functionId": "signBeanAct",
        "appid": "ld",
        "client": "apple",
        "clientVersion": "10.3.2",
        "loginType": "3",
        "body": SIGN_BODY,
    }
    url = API_JD + "?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://bean.m.jd.com/bean/signIndex.action",
        "Cookie": cookie,
    }
    req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return -1, json.dumps({"errmsg": f"网络错误：{type(e).__name__}"})


def _parse(text: str) -> dict:
    try:
        body = json.loads(text or "{}")
        return body if isinstance(body, dict) else {}
    except json.JSONDecodeError:
        return {}


# 未登录响应里的 code（京东各接口历史上用过这几个）
_NOT_LOGGED_CODES = {"3", "-100", "-1", "100", "101", "102"}
# 昵称字段候选（不同版本命名不一）
_NICK_KEYS = ("nickName", "nickname", "nick", "userName", "pin")
# 京豆余额字段候选（按优先级）
_BEAN_KEYS = ("totalBeanNum", "beanNum", "totalBeanCount", "beanCount",
              "jbean", "total", "point", "bean")


def _pick(obj, keys, want: str = "str"):
    """在嵌套 dict/list 里按 key 优先级取值（key 优先于深度）。"""
    if not isinstance(obj, (dict, list)):
        return None
    for key in keys:
        for node in _iter_nodes(obj):
            if isinstance(node, dict) and key in node:
                v = node[key]
                if want == "num":
                    n = _to_num(v)
                    if n is not None:
                        return n
                elif isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def _iter_nodes(obj):
    """广度优先遍历 dict/list 节点。"""
    queue = [obj]
    while queue:
        node = queue.pop(0)
        yield node
        if isinstance(node, dict):
            queue.extend(node.values())
        elif isinstance(node, list):
            queue.extend(node)


def _to_num(v):
    try:
        if isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def probe_login(cookie: str) -> dict:
    """只读校验京东登录态 + 读京豆余额。

    返回 {state, body, http, credits, nickname}
      state: "ok"       登录态有效
             "invalid"  明确未登录 / cookie 失效
             "unknown"  接口变更或网络异常，无法判定（调用方不应据此判定失效）
    """
    r = {"state": "unknown", "body": {}, "http": 0, "credits": None, "nickname": ""}
    http, text = _get(API_SIGN_INDEX, cookie)
    r["http"] = http
    if http == -1:
        return r
    if http in (401, 403):
        r["state"] = "invalid"
        return r
    body = _parse(text)
    if http != 200 or not body:
        return r
    r["body"] = body
    r["nickname"] = _pick(body, _NICK_KEYS) or ""
    r["credits"] = _pick(body, _BEAN_KEYS, want="num")
    code = str(body.get("code", ""))
    msg = str(body.get("errorMessage") or body.get("message") or "")
    if code == "0" or body.get("success") is True:
        r["state"] = "ok"
    elif code in _NOT_LOGGED_CODES or "未登录" in msg or "请先登录" in msg:
        r["state"] = "invalid"
    return r


def client_from_account(acc: dict) -> "JdClient":
    from cookie_manager import require_cookie
    cookie = require_cookie(acc, "jd")
    return JdClient(cookie)


class JdClient:
    """京东京豆每日签到客户端。cookie 由调用方传入（仅内存持有）。"""

    def __init__(self, cookie: str):
        self.cookie = (cookie or "").strip()
        if not self.cookie:
            raise CookieError("缺少京东 cookie（pt_key / pt_pin）")

    @staticmethod
    def _token_from_probe(p: dict) -> dict:
        """把只读探针结果映射成 {ok, nickname, message, http}。"""
        http = p["http"]
        if p["state"] == "ok":
            return {"ok": True, "nickname": p["nickname"], "message": "", "http": http}
        if p["state"] == "invalid":
            return {"ok": False, "nickname": "",
                    "message": "Cookie 已失效（未登录），请重新获取京东 cookie",
                    "http": http}
        if http == -1:
            return {"ok": False, "nickname": "", "message": "网络请求失败", "http": http}
        if http != 200:
            return {"ok": False, "nickname": "",
                    "message": f"登录态接口返回 HTTP {http}", "http": http}
        return {"ok": False, "nickname": "",
                "message": "登录态接口响应异常，无法确认 cookie 是否有效", "http": http}

    def _token_status(self) -> dict:
        """登录态校验（无副作用）。返回 {ok, nickname, message, http}

        用京豆签到首页接口（signBeanIndex）判定：
          - 未登录 -> {"code":"3","errorMessage":"用户未登录"}
          - 登录有效 -> code == "0"
        旧接口 getUserInfoForMini609.action 已失效，见模块 docstring。
        """
        return self._token_from_probe(probe_login(self.cookie))

    def _sign_once(self) -> dict:
        """单次签到请求 → {http, code, message, ok, already, credits, transient}

        transient=True 表示"这次失败像是瞬时/风控拦截，值得隔几秒重试一次"。
        """
        http, text = _post_sign(self.cookie)
        body = _parse(text)
        r = {
            "http": http,
            "code": body.get("code", -1),
            "message": "",
            "ok": False,
            "already": False,
            "credits": 0,
            "transient": False,
        }
        if http == -1:
            r["message"] = "网络请求失败"
            r["transient"] = True
            return r
        if http in (401, 403):
            r["message"] = f"Cookie 被拒绝（HTTP {http}），请重新获取京东 cookie"
            return r
        code = str(body.get("code", ""))
        err_code = str(body.get("errorCode") or "")
        success = body.get("success", True) is not False
        daily = body.get("dailyAward") or {}
        beans = daily.get("beanCount") or daily.get("beanCount2") or 0
        talk = str(body.get("talk") or body.get("errmsg") or body.get("message")
                   or body.get("errorMessage") or "")
        low_talk = talk.lower()

        # 京东网关会给 code="0" 但 errorCode!="0" 的"软失败"
        # （实测 GET 时返回 errorCode=S109「当前签到人数较多，请稍晚再来」）。
        # 这类**不是**"已签到"，必须挡在下面的幂等分支之前，否则会被误报成功。
        if code == "0" and err_code and err_code != "0":
            r["message"] = talk or f"响应 errorCode={err_code}"
            r["transient"] = True
            return r

        if code == "0" and success and beans and int(beans or 0) > 0:
            r.update(ok=True, already=False, credits=int(beans),
                     message=f"签到成功，本次获得 {ints(beans)} 积分（京豆）")
            return r
        if code == "0":
            # 无有效京豆奖励：今日已签 / 活动未开始 / 无签到入口
            if "已签" in low_talk:
                r.update(ok=True, already=True, message="今日已签到（幂等）")
            else:
                r.update(ok=True, already=True,
                         message=("今日已签到（无新增京豆）" if "无" not in low_talk
                                  else "今日无签到奖励"))
            return r
        if code in _NOT_LOGGED_CODES or "未登录" in talk:
            r["message"] = "Cookie 已失效（未登录），请重新获取京东 cookie"
            return r

        # 其余基本都是活动侧拦截：402「活动现在挤不进去呀」等。
        r["message"] = talk or f"响应 code={code}"
        r["transient"] = True
        return r

    def checkin(self) -> dict:
        """每日签到（幂等）。

        返回 {ok, already, credits, message, http, code, restricted}

        restricted=True：京东侧限制（签到活动下线 / 风控拦截）。这类结果
        **不代表 cookie 失效**，上层应显示"平台受限"而不是"签到失败"，
        否则会误导用户反复重新登录。

        实测（2026-09）：bean.m.jd.com 签到页已重定向到京东错误页，
        signBeanAct 对未签名请求统一返回 402「活动现在挤不进去呀」或
        S109「当前签到人数较多，请稍晚再来」；换 appid / client / body
        组合全部无效——属服务端活动状态，不是本客户端的写法问题。
        """
        r = {"http": 0, "code": -1, "message": "", "ok": False,
             "already": False, "credits": 0, "restricted": False}
        for attempt in range(_SIGN_ATTEMPTS):
            if attempt:
                time.sleep(_SIGN_RETRY_DELAY)   # "待会再来试试吧" → 隔几秒再试一次
            r = self._sign_once()
            if r.get("ok") or not r.get("transient"):
                break
        r.pop("transient", None)
        if not r.get("ok"):
            # 被拦下来了：用只读接口确认登录态，把「cookie 真的失效」和
            # 「平台风控 / 活动下线」区分开，否则用户会误以为 cookie 坏了。
            p = probe_login(self.cookie)
            if p["state"] == "invalid":
                r["message"] = "Cookie 已失效（未登录），请重新获取京东 cookie"
            else:
                r["restricted"] = True
                r["message"] = (f"京东侧限制：京豆签到活动当前不可用（{r['message']}）。"
                                "登录态正常，cookie 无需重登；可在京东 App 内手动签到")
        return r

    def status(self) -> dict:
        """查询账号状态（无副作用）。返回 {ok, signed_today, nickname, credits, ...}"""
        p = probe_login(self.cookie)
        t = self._token_from_probe(p)
        r = {
            "http": t["http"],
            "code": -1,
            "message": t["message"],
            "ok": t["ok"],
            "signed_today": None,  # 京东无公开只读签到查询接口
            "nickname": t["nickname"],
            # 京豆余额（signBeanIndex 里带，取不到就是 None → UI 显示「—」）
            "credits": p["credits"] if t["ok"] else None,
            "summary": "",
        }
        if t["ok"]:
            parts = []
            if t["nickname"]:
                parts.append(f"昵称 {t['nickname']}")
            if p["credits"] is not None:
                parts.append(f"京豆 {int(p['credits'])}")
            r["summary"] = "，".join(parts) or "登录态有效"
        return r

    def probe(self) -> dict:
        """脱敏探测（无副作用）。返回 {found, healthy, has_cookie, nickname, message, reason}"""
        try:
            t = self._token_status()
            if t["ok"]:
                return {"found": True, "healthy": True, "has_cookie": True,
                        "nickname": t["nickname"], "message": "", "reason": ""}
            return {"found": False, "healthy": False, "has_cookie": True, "nickname": "",
                    "message": t["message"] or "登录态验证失败",
                    "reason": t["message"] or "登录态验证失败"}
        except Exception as e:  # noqa: BLE001
            return {"found": False, "healthy": False, "has_cookie": True, "nickname": "",
                    "message": f"登录态验证失败：{type(e).__name__}",
                    "reason": f"登录态验证失败：{type(e).__name__}"}


def ints(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---------- CLI ----------
def _load_acc(name: str) -> dict:
    acc = find_account(load_config(), name)
    if acc is None:
        sys.stderr.write(f"未找到账号：{name}\n")
        sys.exit(2)
    return acc


def _probe(acc: dict) -> dict:
    try:
        cookie = get_cookie(acc, "jd")
        if not cookie:
            return {"found": False, "healthy": False, "has_cookie": False, "nickname": "",
                    "message": "账号未配置 cookie，请粘贴京东 pt_key / pt_pin",
                    "reason": "账号未配置 cookie，请粘贴京东 pt_key / pt_pin"}
        p = JdClient(cookie).probe()
        p["has_cookie"] = True
        return p
    except Exception as e:  # noqa: BLE001
        return {"found": False, "healthy": False, "has_cookie": False, "nickname": "",
                "message": f"读取失败：{type(e).__name__}",
                "reason": f"读取失败：{type(e).__name__}"}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="京东每日签到客户端")
    parser.add_argument("--probe", action="store_true", help="探测账号登录态（输出脱敏 JSON）")
    parser.add_argument("--checkin", action="store_true", help="执行签到")
    parser.add_argument("--credits", action="store_true", help="查询账号状态")
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
            print(f"查询失败：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(1)
    elif args.checkin:
        r = client.checkin()
        if r.get("ok"):
            mark = "今日已签到，无需重复" if r.get("already") else r.get("message")
            print(mark)
        else:
            print(r.get("message") or "签到失败")
            sys.exit(1)
    else:
        print("用法：--probe / --checkin / --credits（均需 --name）")


if __name__ == "__main__":
    main()
