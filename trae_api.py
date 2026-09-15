#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Trae 专用签到 API 客户端（仅标准库，无第三方依赖）

原理（对齐官方云端脚本 TRAE-Checkin）：
  - Trae 网页端登录后，localStorage 里的 Cloud-IDE-Token（JWT）仅约 8 小时有效；
    真正的长效会话凭证是 HttpOnly Cookie「X-Cloudide-Session」（约 14 天）。
  - 本模块先用 X-Cloudide-Session 调 GetUserToken 换取全新 JWT，
    再用新 JWT + x-device-id 调签到接口，实现"登录一次，14 天免维护"。

用法：
    from trae_api import TraeClient
    c = TraeClient(session="<X-Cloudide-Session 值>", device_id="<16位数字>")
    print(c.checkin())     # 返回 dict，形如 {"code":0,"message":"...","credits":N}
"""

import json
import random
import urllib.error
import urllib.request

BASE = "https://api.trae.cn"
TIMEOUT = 30

_UA = "TraeCheckin/1.0"

# 鉴权失败的判定口径：
#   - HTTP 401/403：JWT 本身被拒
#   - HTTP 200 + 业务码 1001：接口"能通但认不出你"，实测就是 JWT 过期后的典型返回
#     （日志原文：We're sorry, but we are not able to authenticate you.）
# 命中任一条即认为 JWT 已失效，用 X-Cloudide-Session 重换一次再试。
_AUTH_HTTP = (401, 403)
_AUTH_CODES = (1001, 1002)

# 签到状态接口的字段名各版本不一致，用下面三组 key 做容错匹配
_CHECKED_KEYS = ("checked_in", "today_checked_in", "has_checked_in",
                 "is_checked_in", "today_signed", "signed")
_CREDIT_KEYS = ("checkin_credits", "credits", "credit", "total_credits")
_STREAK_KEYS = ("continuous_days", "streak_days", "continuous_checkin_days",
                "continuous_sign_days", "days")


def _walk_json(obj):
    """深度优先遍历 JSON，产出所有 dict 节点（用于字段名容错匹配）。"""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_json(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_json(v)


def _first_value(nodes, keys, predicate):
    """按 key 的优先级选取字段值。

    外层遍历 keys、内层遍历节点：优先级高的字段名（如 checked_in）在任何深度
    命中都优先于靠后的宽泛字段名（如 signed），避免深层同名布尔字段抢先命中。
    """
    for k in keys:
        for node in nodes:
            if k in node and predicate(node[k]):
                return node[k]
    return None


def random_device_id() -> str:
    """生成 16 位纯数字风控设备号。"""
    return str(random.randint(10**15, 10**16 - 1))


class TraeError(RuntimeError):
    pass


class TraeClient:
    def __init__(self, session: str, device_id: str = "", session_token: str = ""):
        self.session = (session or "").strip()
        self.device_id = (device_id or random_device_id()).strip()
        self.token = (session_token or "").strip()

    # ---------- 内部请求 ----------
    def _post(self, path: str, headers: dict, body: str = "") -> tuple:
        req = urllib.request.Request(
            BASE + path, data=body.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def _authed_headers(self, token: str) -> dict:
        return {
            "Authorization": "Cloud-IDE-JWT " + token,
            "X-User-Region": "cn",
            "x-device-id": self.device_id,
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }

    @staticmethod
    def _is_auth_failure(status: int, text: str) -> bool:
        if status in _AUTH_HTTP:
            return True
        try:
            body = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(body, dict):
            return False
        return body.get("code") in _AUTH_CODES

    def _post_authed(self, path: str, body: str = "{}") -> tuple:
        """带自动续期的 POST。

        先用手头的 JWT 请求；若被判定为鉴权失败（HTTP 401/403 或业务码 1001），
        说明 JWT 已过期（约 8 小时），此时用长效的 X-Cloudide-Session
        重新换取 JWT 并**重试一次**，实现"登录一次、长期免维护"。

        返回 (status, text, refreshed)；refresh 失败时 get_token() 会抛 TraeError
        （带可读中文提示），由调用方翻译成用户能看懂的失败原因。
        """
        token = self.token or self.get_token()
        status, text = self._post(path, self._authed_headers(token), body)
        if not self._is_auth_failure(status, text):
            return status, text, False
        if not self.session:
            return status, text, False
        # 丢弃已失效的 JWT，强制用 session 换一个全新的再重试
        self.token = ""
        token = self.get_token()
        status, text = self._post(path, self._authed_headers(token), body)
        return status, text, True

    # ---------- 换 Token ----------
    def get_token(self) -> str:
        """用 X-Cloudide-Session 换取全新 JWT，返回 token 字符串。"""
        if not self.session:
            raise TraeError("未配置 X-Cloudide-Session，请先通过内置浏览器登录 Trae。")
        headers = {
            "Cookie": "X-Cloudide-Session=" + self.session,
            "Referer": "https://www.trae.cn/",
            "Origin": "https://www.trae.cn",
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
        }
        status, text = self._post("/cloudide/api/v3/common/GetUserToken", headers)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise TraeError(f"GetUserToken 返回非 JSON (HTTP {status})：{text[:200]}") from e
        token = (data.get("Result") or {}).get("Token")
        if status == 401:
            raise TraeError(
                "会话已失效(HTTP 401)：X-Cloudide-Session 可能已过期(约14天)，"
                "请重新打开内置浏览器登录 Trae。"
            )
        if status != 200 or not token:
            raise TraeError(f"GetUserToken 失败：HTTP {status} {text[:200]}")
        self.token = token
        return token

    # ---------- 查询积分/余额 ----------
    def credits(self) -> dict:
        """查询账号剩余积分（汇总所有资格包剩余额度）。

        调 pay/user_current_entitlement_list 拉取额度包，汇总
        total(limit - used) 得到剩余积分。JWT 过期会自动用 session 续期重试。
        返回 dict：
        {ok, http, code, message, remaining, total_limit, total_used, packs, raw}
        """
        status, text, refreshed = self._post_authed(
            "/trae/api/v2/pay/user_current_entitlement_list", "{}"
        )
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"raw": text}

        code = body.get("code", -1)
        message = body.get("message") or body.get("msg") or (
            "HTTP %d" % status
        )
        result = {
            "http": status,
            "code": code,
            "message": message,
            "ok": False,
            "refreshed_token": refreshed,
            "remaining": 0.0,
            "total_limit": 0.0,
            "total_used": 0.0,
            "packs": [],
            "raw": body,
        }

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        packs = body.get("user_entitlement_pack_list")
        # 顶层汇总（接口直接给出，作交叉校验与快速展示）
        summary = body.get("usage_summary") or {}
        # 注意：该接口响应体没有顶层 code/message 字段，HTTP 200 且解析出
        # user_entitlement_pack_list 列表即视为成功。
        if status == 200 and isinstance(packs, list):
            result["ok"] = True
            remaining = total_limit = total_used = 0.0
            for p in packs:
                info = p.get("entitlement_base_info") or {}
                quota = info.get("quota") or {}
                if not quota:
                    quota = ((info.get("product_extra") or {})
                             .get("package_extra") or {}).get("quota") or {}
                limit = _num(quota.get("credits_limit"))
                used = _num((p.get("usage") or {}).get("credits_amount"))
                rem = max(0.0, limit - used)
                total_limit += limit
                total_used += used
                remaining += rem
                result["packs"].append({
                    "desc": p.get("display_desc") or p.get("group_name") or "",
                    "group": p.get("group_name") or "",
                    "name": ((info.get("product_extra") or {})
                             .get("package_extra") or {}).get("package_name") or "",
                    "limit": int(round(limit)),
                    "used": _num(used),
                    "remaining": _num(rem),
                })
            result["remaining"] = remaining
            result["total_limit"] = total_limit
            result["total_used"] = total_used
            # 交叉校验：接口汇总与本地求和一致时优先接口汇总
            s_total = _num(summary.get("total_amount"))
            s_used = _num(summary.get("consumed_amount"))
            if s_total > 0:
                result["total_limit"] = s_total
            if s_used >= 0:
                result["total_used"] = s_used
            result["remaining"] = max(0.0, result["total_limit"] - result["total_used"])
        elif status != 200:
            result["message"] = f"HTTP {status} {message}"
        return result

    # ---------- 查询今日签到状态 ----------
    def status(self) -> dict:
        """查询今日是否已签到（只读，无副作用）。

        该接口字段名随版本变化，这里做容错匹配：在响应树里找
        checked_in / today_checked_in / ... 这类布尔字段。找到才算
        known=True，否则 known=False —— 调用方据此决定"是否跳过重复签到"：
        只有 known 且 checked_in 为真时才跳过，判不出来就照常走签到流程，
        绝不会因为解析不出字段而漏签。

        返回 dict：{http, body, known, checked_in, credits, streak_days, refreshed_token}
        """
        status, text, refreshed = self._post_authed("/trae/api/v2/ug/checkin_credits/status", "{}")
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"raw": text}
        nodes = list(_walk_json(body))
        checked = _first_value(nodes, _CHECKED_KEYS, lambda v: isinstance(v, bool))
        credits = _first_value(nodes, _CREDIT_KEYS, lambda v: isinstance(v, (int, float)) and not isinstance(v, bool))
        streak = _first_value(nodes, _STREAK_KEYS, lambda v: isinstance(v, (int, float)) and not isinstance(v, bool))
        known = checked is not None and status == 200
        return {
            "http": status,
            "body": body,
            "known": known,
            "checked_in": bool(checked) if known else None,
            "credits": float(credits) if credits is not None else None,
            "streak_days": int(streak) if streak is not None else None,
            "refreshed_token": refreshed,
        }

    # ---------- 执行签到 ----------
    def checkin(self) -> dict:
        """执行每日签到。等价的成功条件：HTTP 200 且 code==0 或 checked_in 为真。

        优先在调用侧用 status() 预检；这里保留"重复签到"的自识别能力作为兜底：
        返回的 already_checked 表示本次并未真正领取新的奖励。
        """
        status, text, refreshed = self._post_authed("/trae/api/v2/ug/checkin_credits/claim", "{}")
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"raw": text}
        result = {"http": status, "body": body, "refreshed_token": refreshed}
        code = body.get("code", -1)
        nodes = list(_walk_json(body))
        checked_in = _first_value(nodes, _CHECKED_KEYS, lambda v: isinstance(v, bool))
        checked_bool = bool(checked_in) if isinstance(checked_in, bool) else False
        result["code"] = code
        result["ok"] = (status == 200) and (code == 0 or checked_bool)
        # checked_in 为真但 code!=0，通常是"今天已经签过了"这类已知状态
        result["already_checked"] = bool(checked_bool and code != 0)
        result["message"] = body.get("message") or body.get("msg") or (
            "HTTP %d" % status
        )
        credits = _first_value(nodes, _CREDIT_KEYS, lambda v: isinstance(v, (int, float)) and not isinstance(v, bool))
        result["credits"] = credits if credits is not None else body.get("credits", 0) or 0
        return result


# ---------- 命令行自检 ----------
def main() -> None:
    print("Trae API 客户端自检：本工具不与命令行直接交互。")
    print("请在 Python 中：")
    print("    from trae_api import TraeClient")
    print("    c = TraeClient(session='<X-Cloudide-Session>')")
    print("    print(c.checkin())")


if __name__ == "__main__":
    main()
