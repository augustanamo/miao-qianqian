#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里云盘 每日签到客户端（纯标准库实现）。

接口来源与实测结论（2026-09-15 在本机逐条探测，**不含任何真实凭据**）：

  POST https://auth.aliyundrive.com/v2/account/token
       {"grant_type": "refresh_token", "refresh_token": "<令牌>"}
       -> 200 {"access_token": "…", "refresh_token": "…", "expires_in": 7200}
       实测失效信号：
         refresh_token 非法 -> 400 {"code":"InvalidParameter.RefreshToken"}
         缺参数             -> 400 {"code":"InvalidParameterMissing.RefreshToken"}

  POST https://member.aliyundrive.com/v1/activity/sign_in_list
       Header: Authorization: Bearer <access_token>
       -> {"result": {"signInLogs": [{day, status, reward}, …], "signInCount": N}}
       实测无 token -> 401 {"code":"AccessTokenInvalid","message":"not login"}

  POST https://member.aliyundrive.com/v1/activity/sign_in_reward
       {"signInDay": N}  —— 领取第 N 天的签到奖励
       实测无 Authorization 头 -> 401 MissingRequestHeaderException
                                   （要求 x-forwarded-user-id，由 Bearer 推导）

  POST https://api.aliyundrive.com/v2/user/get   （只读，用于 status()）
       实测无 token -> 401 {"code":"AccessTokenInvalid"}，端点存在。

⚠️ 凭据是 refresh_token，**不是 Cookie**。它在浏览器 localStorage 的 `token`
   项里，不在 Cookie 里，所以：
     - 账号管理里的「浏览器登录」对它无效（抓不到 Cookie 域的东西）；
     - 手动粘贴时粘的应该是 `JSON.parse(localStorage.getItem("token")).refresh_token`。
   落盘字段也跟着叫 `refresh_token`，不叫 `cookie` —— 免得出现"标签写 Cookie、
   实际存的是令牌"那种静默错配。cookie_manager.CREDENTIAL_FIELD 负责这个映射。

⚠️ refresh_token 会轮换：每次刷新都可能下发新的，旧的可能失效。因此
   `_refresh()` 成功后会把新令牌写回 accounts.json（autosave，默认开）。
   这是本模块唯一会写盘的地方，回写失败会在结果 message 里明说。

用法：
    from aliyunpan import AliyunpanClient, client_from_account
    c = client_from_account(acc)
    print(c.status())
    print(c.checkin())
CLI：
    python3 aliyunpan.py --probe   --name <账号名>
    python3 aliyunpan.py --checkin --name <账号名>
    python3 aliyunpan.py --credits --name <账号名>
"""

import json
import sys
import time
import urllib.error
import urllib.request

from cookie_manager import (CookieError, find_account, get_cookie, load_config,
                            update_credential)

AUTH_URL = "https://auth.aliyundrive.com/v2/account/token"
MEMBER_API = "https://member.aliyundrive.com"
USER_API = "https://api.aliyundrive.com"
TIMEOUT = 25
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 AliyunpanCheckin/1.0")

SIGN_LIST = "/v1/activity/sign_in_list"
SIGN_REWARD = "/v1/activity/sign_in_reward"
USER_GET = "/v2/user/get"
DRIVE_GET = "/v2/drive/get"

# 已签到的日志状态。阿里云盘用 "normal" 表示这一天的签到已生效，
# "miss" 表示漏签，"notSign"/"" 表示尚未到那一天。多认几个同义词，
# 免得平台改一个字就把"今天已经签过"误判成"签到失败"。
_SIGNED_STATES = ("normal", "signed", "complete", "received", "done")
_MISSED_STATES = ("miss", "missed", "none", "notsign", "not_signed", "")

# 已领取奖励的失败信号（各版本文案不定，宽松匹配）
_ALREADY_WORDS = ("已领取", "已经领取", "重复领取", "已领", "已签到", "already")


class AliyunpanError(RuntimeError):
    pass


def _read_body(resp) -> str:
    return resp.read().decode("utf-8", errors="replace")


def _parse_json(text: str) -> dict:
    try:
        body = json.loads(text or "{}")
        return body if isinstance(body, dict) else {"code": -1, "message": "非 JSON 响应"}
    except json.JSONDecodeError:
        return {"code": -1, "message": "响应解析失败", "raw": (text or "")[:200]}


def _post(url: str, body: dict, access_token: str = "") -> tuple:
    """POST JSON，返回 (http, body_dict)。access_token 非空时带 Bearer 头。"""
    headers = {"Content-Type": "application/json", "User-Agent": UA,
               "Accept": "application/json, text/plain, */*"}
    if access_token:
        headers["Authorization"] = "Bearer " + access_token
    req = urllib.request.Request(url, data=json.dumps(body or {}).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, _parse_json(_read_body(resp))
    except urllib.error.HTTPError as e:
        return e.code, _parse_json(e.read().decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        return -1, {"code": -1, "message": f"网络错误：{type(e).__name__}"}


def _num(v):
    """宽松转 float（接口偶尔把数字写成字符串）；转不动返回 None。"""
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt_size(nbytes, digits: int = 1) -> str:
    """字节 -> 人类可读（1.00 TB / 337.3 GB / 512 MB）。

    用 1024 进制：云盘厂商对外说"T/G"时口径不一，但盘内文件大小是 1024 进制，
    界面上按同一个进制显示才不会出现"两个 GB 对不上"的困惑。
    """
    n = _num(nbytes)
    if n is None:
        return "—"
    units = (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024))
    for name, base in units:
        if n >= base:
            return f"{n / base:.{digits}f} {name}"
    return f"{max(0.0, n):.0f} B"


def _is_credential_error(http: int, body: dict) -> bool:
    """凭据失效判定：刷新令牌不合法，或 Bearer 不被接受。"""
    code = str(body.get("code") or "")
    if code in ("InvalidParameter.RefreshToken", "InvalidParameterMissing.RefreshToken",
                "AccessTokenInvalid", "InvalidParameter.RefreshTokenMissing"):
        return True
    if http in (401, 403):
        return True
    msg = str(body.get("message") or "")
    return "invalid" in msg.lower() and "token" in msg.lower()


def client_from_account(acc: dict) -> "AliyunpanClient":
    """从账号条目构造客户端；refresh_token 缺失时抛 CookieError。"""
    from cookie_manager import require_cookie
    token = require_cookie(acc, "aliyunpan")
    return AliyunpanClient(token, account_name=str(acc.get("name") or ""))


class AliyunpanClient:
    """阿里云盘签到客户端。refresh_token 仅内存持有（轮换回写除外）。"""

    def __init__(self, refresh_token: str, account_name: str = "",
                 autosave: bool = True):
        self.refresh_token = (refresh_token or "").strip()
        if not self.refresh_token:
            raise CookieError("缺少阿里云盘刷新令牌（refresh_token）")
        self.account_name = (account_name or "").strip()
        # autosave：刷新后把新 refresh_token 写回 accounts.json。
        # 只有从账号条目构造时才开；单独 new 出来做自测时不写盘。
        self.autosave = bool(autosave and self.account_name)
        self.access_token = ""
        self.save_note = ""
        # access_token 有效期（unix 秒）与 user/get 的缓存：同一次运行里
        # status() / space() 都要用户信息，缓存一次免得反复刷新令牌
        # ——refresh_token 每刷一次都可能轮换，刷太勤反而容易踩到竞态。
        self._access_expires = 0.0
        self._user_cache = None

    # ---------------- 令牌 ----------------
    def _refresh(self) -> dict:
        """用 refresh_token 换 access_token。返回 {ok, message, restricted, http}。

        已持有未过期的 access_token 时直接复用（不再刷一次）。
        """
        if self.access_token and time.time() < self._access_expires - 60:
            return {"ok": True, "http": 200, "restricted": False, "message": ""}
        http, body = _post(AUTH_URL, {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        })
        if http == 200:
            token = str(body.get("access_token") or "").strip()
            if not token:
                return {"ok": False, "http": http, "restricted": True,
                        "message": "阿里云盘返回了空的 access_token，接口可能已变更"}
            self.access_token = token
            self._access_expires = time.time() + (_num(body.get("expires_in")) or 7200)
            new_refresh = str(body.get("refresh_token") or "").strip()
            if new_refresh:
                self._store_token(new_refresh)
            return {"ok": True, "http": http, "restricted": False, "message": ""}
        if _is_credential_error(http, body):
            return {"ok": False, "http": http, "restricted": False,
                    "message": ("阿里云盘刷新令牌已失效，请在浏览器控制台重新获取 "
                                "refresh_token 后更新账号")}
        return {"ok": False, "http": http, "restricted": True,
                "message": (f"阿里云盘令牌接口异常（HTTP {http}，"
                            f"{body.get('code') or body.get('message') or '未知'}）")}

    def _store_token(self, token: str) -> None:
        """把轮换后的 refresh_token 写回 accounts.json（唯一写盘动作）。"""
        self.refresh_token = token
        if not self.autosave:
            return
        try:
            cfg = load_config()
            if not update_credential(cfg, self.account_name, "aliyunpan", token):
                self.save_note = "（新令牌回写账号文件失败，请手动更新一次）"
        except Exception as e:  # noqa: BLE001
            self.save_note = f"（新令牌回写失败：{type(e).__name__}）"

    # ---------------- 状态查询（只读） ----------------
    def _user(self) -> dict:
        """refresh + user/get，带回用户信息。返回 {ok, http, message, body}。

        同一次运行里只打一次（缓存），status() 与 space() 共用。
        """
        if self._user_cache is not None:
            return self._user_cache
        ref = self._refresh()
        if not ref["ok"]:
            self._user_cache = {"ok": False, "http": ref["http"],
                                "message": ref["message"], "body": {}}
            return self._user_cache
        http, body = _post(USER_API + USER_GET, {}, self.access_token)
        if _is_credential_error(http, body):
            out = {"ok": False, "http": http, "body": {},
                   "message": "阿里云盘登录态已失效，请重新获取 refresh_token"}
        elif http != 200:
            out = {"ok": False, "http": http, "body": {},
                   "message": (f"阿里云盘用户接口异常（HTTP {http}，"
                               f"{body.get('code') or body.get('message') or '未知'}）")}
        else:
            out = {"ok": True, "http": http, "message": "", "body": body}
        self._user_cache = out
        return out

    def space(self) -> dict:
        """只读查询云盘容量。返回 {ok, http, total, used, remain, unit, …}。

        阿里云盘没有"积分"这个东西，签到给的是**容量/会员**，所以积分口径对它
        本就不适用；能反映账号价值的就是盘一共多大、还剩多少。单位一律字节，
        由调用方（checkin.py / 界面）负责格式化，避免这里先四舍五入再被二次换算。
        """
        r = {"ok": False, "http": 0, "total": None, "used": None, "remain": None,
             "unit": "B", "drive_id": "", "message": ""}
        u = self._user()
        if not u["ok"]:
            r["http"] = u["http"]
            r["message"] = u["message"]
            return r
        drive_id = str(u["body"].get("default_drive_id") or "")
        r["drive_id"] = drive_id
        if not drive_id:
            r["message"] = "账号未返回 default_drive_id，无法查询容量"
            return r
        http, body = _post(USER_API + DRIVE_GET, {"drive_id": drive_id},
                           self.access_token)
        r["http"] = http
        if _is_credential_error(http, body):
            r["message"] = "阿里云盘登录态已失效，请重新获取 refresh_token"
            return r
        if http != 200:
            r["message"] = (f"容量接口异常（HTTP {http}，"
                            f"{body.get('code') or body.get('message') or '未知'}）")
            return r
        total = _num(body.get("total_size"))
        if total is None:
            r["message"] = "容量接口未返回 total_size，接口可能已变更"
            return r
        used = _num(body.get("used_size")) or 0.0
        r["total"] = total
        r["used"] = used
        r["remain"] = max(0.0, total - used)
        r["ok"] = True
        return r

    def status(self) -> dict:
        """只读查询账号信息 + 云盘容量。返回 {ok, signed_today, nickname, …}。

        signed_today 恒为 None：阿里云盘没有"今天签没签"的只读接口，
        sign_in_list 会顺带执行签到，不能拿来做体检。如实返回 None。
        credits 也恒为 None（该平台无积分），容量放在 capacity 里，
        由 checkin.py 原样带进积分记录的 capacity 字段。
        """
        r = {"http": 0, "ok": False, "signed_today": None, "nickname": "",
             "uid": "", "credits": None, "summary": "", "capacity": None,
             "message": ""}
        u = self._user()
        r["http"] = u["http"]
        if not u["ok"]:
            r["message"] = u["message"]
            return r
        body = u["body"]
        r["ok"] = True
        r["nickname"] = str(body.get("nick_name") or body.get("nickname") or "")
        r["uid"] = str(body.get("user_id") or body.get("userId") or "")
        phone = str(body.get("phone") or "")
        if phone:
            r["summary"] = f"手机号 {phone[:3]}****{phone[-2:]}" if len(phone) >= 6 else phone
        if r["nickname"]:
            r["summary"] = (r["summary"] + "，" if r["summary"] else "") + \
                           f"昵称 {r['nickname']}"

        # 容量是只读的，查询失败不影响登录态结论：清空字段 + 说明原因即可
        sp = self.space()
        if sp["ok"]:
            r["capacity"] = {"total": sp["total"], "used": sp["used"],
                             "remain": sp["remain"], "unit": "B"}
            cap_text = (f"剩余 {fmt_size(sp['remain'])} / 共 {fmt_size(sp['total'])}")
            r["summary"] = (r["summary"] + "，" if r["summary"] else "") + cap_text
        else:
            r["message"] = f"容量查询未成功：{sp['message']}"
        return r

    # ---------------- 签到 ----------------
    @staticmethod
    def _entry_for_day(logs, day):
        """在 signInLogs 里找第 day 天的条目。"""
        if not isinstance(logs, list):
            return None
        for e in logs:
            if isinstance(e, dict) and e.get("day") == day:
                return e
        return None

    @staticmethod
    def _entry_status(entry) -> str:
        if not isinstance(entry, dict):
            return ""
        return str(entry.get("status") or "").strip().lower()

    def checkin(self) -> dict:
        """执行每日签到。

        返回 {ok, already, credits, message, http, code, restricted, daily, saved}

        关于 already 的判定：阿里云盘没有公开"今天是否已签"的只读接口，
        sign_in_list 本身就会把今天的签到落上。因此这里以**奖励领取**的结果
        为准：sign_in_reward 成功 = 本次确实拿到奖励（already=False）；
        它回报"已领取" = 今天早些时候已经签过（already=True）。
        这样无论平台把签到做在 sign_in_list 还是 reward 上，结论都收敛到
        同一个不会骗人的说法。
        """
        r = {"http": 0, "code": "", "message": "", "ok": False, "already": False,
             "credits": 0.0, "restricted": False, "daily": {}, "saved": True}

        ref = self._refresh()
        if not ref["ok"]:
            r["http"] = ref["http"]
            r["restricted"] = ref["restricted"]
            r["message"] = ref["message"] + self.save_note
            return r

        # 1) 签到 / 拉取本月签到记录
        http, body = _post(MEMBER_API + SIGN_LIST, {}, self.access_token)
        r["http"] = http
        r["code"] = str(body.get("code") or "")
        if _is_credential_error(http, body):
            r["message"] = ("阿里云盘登录态已失效（刚刷新过令牌仍被拒绝），"
                            "请重新获取 refresh_token")
            return r
        if http != 200 or body.get("success") is False or "result" not in body:
            r["restricted"] = True
            r["message"] = (f"阿里云盘签到活动不可用（HTTP {http}，"
                            f"{body.get('code') or body.get('message') or '无 result 字段'}）"
                            "，活动可能已下线或接口已迁移")
            return r

        result = body.get("result") or {}
        logs = result.get("signInLogs") or []
        try:
            count = int(result.get("signInCount") or 0)
        except (TypeError, ValueError):
            count = 0
        entry = self._entry_for_day(logs, count)
        status = self._entry_status(entry)
        r["daily"] = {"sign_in_count": count, "day_status": status}

        # 2) 领今天的奖励 —— 它同时充当"今天是否已签过"的判据
        http2, body2 = _post(MEMBER_API + SIGN_REWARD,
                             {"signInDay": count}, self.access_token)
        r["http"] = http2 if http2 else r["http"]
        reward = self._reward_text(body2.get("result")) if isinstance(body2, dict) else ""
        # success 为 True 或缺省（HTTP 200 时）都算领到；显式 False 才算没领到。
        success2 = body2.get("success") if isinstance(body2, dict) else None
        ok2 = (http2 == 200
               and not _is_credential_error(http2, body2)
               and (success2 is True or success2 is None))

        tail = f"，连续签到 {count} 天" if count else ""
        if status in _MISSED_STATES and status:
            tail += f"（第 {count} 天状态：{status}）"

        if ok2:
            r["ok"] = True
            r["already"] = False
            r["message"] = ("签到成功" + (f"，获得「{reward}」" if reward else "")
                            + tail)
        else:
            msg2 = str(body2.get("message") or body2.get("code") or "")
            if any(w in msg2 for w in _ALREADY_WORDS) or any(w in reward for w in _ALREADY_WORDS):
                r["ok"] = True
                r["already"] = True
                r["message"] = ("今日已签到（奖励此前已领取，未重复领取）" + tail)
            else:
                # 签到列表拿到了、奖励没领到：不当失败报，但如实说明。
                r["ok"] = True
                r["already"] = True
                r["message"] = (f"今日签到状态已更新{tail}，但奖励领取未完成"
                                f"（{msg2 or f'HTTP {http2}'}）")
        r["saved"] = not self.save_note
        r["message"] += self.save_note
        return r

    @staticmethod
    def _reward_text(result) -> str:
        """把 sign_in_reward 的 result 压成一句可读奖励文案。"""
        if isinstance(result, str):
            return result
        if not isinstance(result, dict):
            return ""
        for k in ("name", "description", "notice", "title"):
            v = result.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    # ---------------- 探测（供 Swift 侧一键添加/刷新） ----------------
    def probe(self) -> dict:
        """探测登录态 + 顺带完成当日签到。

        用签到接口本身判定，理由与 smzdm.py 相同：只有它能准确区分
        「令牌失效」和「活动下线/接口变更」，而只读接口通不通并不代表签到能成功。
        签到幂等，重复调用不会重复发奖。
        """
        res = self.checkin()
        if res.get("ok"):
            daily = res.get("daily") or {}
            return {"found": True, "healthy": True, "has_cookie": True,
                    "nickname": "", "uid": "",
                    "credits": daily.get("sign_in_count") or 0,
                    "message": "", "reason": ""}
        reason = res.get("message") or "登录态验证失败"
        return {"found": True, "healthy": False, "has_cookie": True,
                "nickname": "", "uid": "", "credits": 0,
                "message": reason, "reason": reason}


# ---------- CLI ----------
def _load_acc(name: str) -> dict:
    acc = find_account(load_config(), name)
    if acc is None:
        sys.stderr.write(f"未找到账号：{name}\n")
        sys.exit(2)
    return acc


def _probe(acc: dict) -> dict:
    """脱敏探测 JSON（不含令牌明文），供给 Swift 侧。"""
    try:
        token = get_cookie(acc, "aliyunpan")
        if not token:
            reason = "账号未配置刷新令牌，请在浏览器控制台取 refresh_token 后粘贴"
            return {"found": False, "healthy": False, "has_cookie": False,
                    "nickname": "", "uid": "", "credits": 0,
                    "message": reason, "reason": reason}
        client = client_from_account(acc)
        return client.probe()
    except Exception as e:  # noqa: BLE001
        reason = f"读取失败：{type(e).__name__}"
        return {"found": False, "healthy": False, "has_cookie": False,
                "nickname": "", "uid": "", "credits": 0,
                "message": reason, "reason": reason}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="阿里云盘 每日签到客户端")
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
            print(f"登录态正常，{r.get('summary') or '（接口未返回账号信息）'}")
        else:
            print(f"查询失败：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(1)
    elif args.checkin:
        r = client.checkin()
        if r.get("ok"):
            print(r.get("message"))
        elif r.get("restricted"):
            print(f"平台受限：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(3)
        else:
            print(f"签到失败：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(1)
    else:
        print("用法：--probe / --checkin / --credits（均需 --name）")


if __name__ == "__main__":
    main()
