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

        先按需用 X-Cloudide-Session 换全新 JWT，再调
        pay/user_current_entitlement_list 拉取额度包，汇总
        total(limit - used) 得到剩余积分。返回 dict：
        {ok, http, code, message, remaining, total_limit, total_used, packs, raw}
        """
        token = self.token or self.get_token()
        headers = {
            "Authorization": "Cloud-IDE-JWT " + token,
            "X-User-Region": "cn",
            "x-device-id": self.device_id,
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }
        status, text = self._post(
            "/trae/api/v2/pay/user_current_entitlement_list", headers, "{}"
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
        """查询今日是否已签到。返回 dict；未知字段则尽量容错。"""
        token = self.token or self.get_token()
        headers = {
            "Authorization": "Cloud-IDE-JWT " + token,
            "X-User-Region": "cn",
            "x-device-id": self.device_id,
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }
        status, text = self._post("/trae/api/v2/ug/checkin_credits/status", headers, "{}")
        try:
            return {"http": status, "body": json.loads(text)}
        except json.JSONDecodeError:
            return {"http": status, "body": {"raw": text}}

    # ---------- 执行签到 ----------
    def checkin(self) -> dict:
        """执行每日签到。等价的成功条件：HTTP 200 且 code==0 或 checked_in 为真。"""
        token = self.token or self.get_token()
        headers = {
            "Authorization": "Cloud-IDE-JWT " + token,
            "X-User-Region": "cn",
            "x-device-id": self.device_id,
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }
        status, text = self._post("/trae/api/v2/ug/checkin_credits/claim", headers, "{}")
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"raw": text}
        result = {"http": status, "body": body}
        code = body.get("code", -1)
        checked_in = body.get("checked_in", False)
        result["code"] = code
        result["ok"] = (status == 200) and (code == 0 or checked_in)
        # checked_in 为真但 code!=0，通常是"今天已经签过了"这类已知状态
        result["already_checked"] = bool(checked_in and code != 0)
        result["message"] = body.get("message") or body.get("msg") or (
            "HTTP %d" % status
        )
        result["credits"] = body.get("credits", 0)
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
