#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bilibili 每日签到客户端（纯标准库实现）。

接口思路参考开源项目协议（bilibili_checkin 等），代码为本项目内
独立实现，不照搬任何仓库源码：
  - GET https://api.bilibili.com/x/web-interface/nav
       验证登录态（SESSDATA），返回 uname / 硬币 / 等级
  - GET https://api.bilibili.com/x/member/web/exp/reward
       每日签到 / 任务领取。这是「成长中心 → 每日任务 → 登录」那个
       「领取」按钮背后的接口，返回当日任务完成态：
       login / watch / share(bool)、coins(当日投币所得经验)。
       ⚠️ 旧端点 /x/web-interface/checkin **不存在**（HTTP 404，返回的是
       一个 HTML 错误页）。历史上用它导致签到恒失败（"响应解析失败"）。
       B站本身也早已取消独立的"签到"入口：每日登录经验由"有效登录行为"
       自动发放，本接口是唯一可编程触达的领取/查询入口，天然幂等
       （已领取时返回的 login 恒为 true）。
       注意：B站的签到收益是**经验**，不是硬币。硬币余额见 nav()。

凭据安全：cookie 等同账号密码，读取后仅内存使用，绝不写日志、
绝不回显到 stdout / UI。accounts.json 仅保存用户主动添加的 cookie
字段与 nickname/uid 非敏感快照。

用法：
    from bilibili import BilibiliClient, client_from_account
    c = client_from_account(acc)
    print(c.status())   # {ok, signed_today, nickname, credits, ...}
    print(c.checkin())  # {ok, already, credits, ...}
CLI：
    python3 bilibili.py --probe  --name <账号名>
    python3 bilibili.py --checkin --name <账号名>
    python3 bilibili.py --credits --name <账号名>
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from cookie_manager import CookieError, find_account, get_cookie, load_config

API = "https://api.bilibili.com"
TIMEOUT = 30
# 「每日登录」任务的固定奖励（经验）。B站签到给的是经验，不是硬币。
DAILY_EXP_LOGIN = 5.0
# 领取后到账有极短延迟：首次仍报未完成时，隔一会儿再确认一次，
# 避免把"已发放但状态未刷新"误报成失败。
CONFIRM_DELAY = 1.5
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 BiliCheckin/1.0"
REFERER = "https://www.bilibili.com/"


class BilibiliError(RuntimeError):
    pass


def _read_body(resp) -> str:
    return resp.read().decode("utf-8", errors="replace")


def _parse_json(text: str) -> dict:
    try:
        body = json.loads(text or "{}")
        return body if isinstance(body, dict) else {"code": -1, "message": "非 JSON 响应"}
    except json.JSONDecodeError:
        return {"code": -1, "message": "响应解析失败", "raw": (text or "")[:200]}


def client_from_account(acc: dict) -> "BilibiliClient":
    """从账号条目构造客户端；cookie 缺失时抛 CookieError。"""
    from cookie_manager import require_cookie
    cookie = require_cookie(acc, "bilibili")
    return BilibiliClient(cookie)


class BilibiliClient:
    """Bilibili 每日签到客户端。cookie 由调用方传入（仅内存持有）。"""

    def __init__(self, cookie: str):
        self.cookie = (cookie or "").strip()
        if not self.cookie:
            raise CookieError("缺少 Bilibili cookie（SESSDATA）")

    def _get(self, path: str, params: dict | None = None) -> tuple:
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Referer": REFERER,
            "Cookie": self.cookie,
            "Accept": "application/json, text/plain, */*",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, _parse_json(_read_body(resp))
        except urllib.error.HTTPError as e:
            return e.code, _parse_json(e.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return -1, {"code": -1, "message": f"网络错误：{type(e).__name__}"}

    # ---- 状态查询（无副作用） ----
    def nav(self) -> dict:
        """查询登录态与账户信息：{ok, is_login, nickname, uid, money, level, message, http}"""
        http, body = self._get("/x/web-interface/nav")
        r = {
            "http": http,
            "code": body.get("code", -1),
            "message": body.get("message") or f"HTTP {http}",
            "ok": False,
            "is_login": False,
            "nickname": "",
            "uid": "",
            "money": 0,
            "level": 0,
        }
        if http in (401, 403):
            r["message"] = f"Cookie 被拒绝（HTTP {http}），请重新获取 Bilibili cookie"
            return r
        data = body.get("data") or {}
        if http == 200 and body.get("code") == 0 and data.get("isLogin"):
            r.update(
                ok=True,
                is_login=True,
                nickname=data.get("uname") or "",
                uid=str(data.get("mid") or ""),
                money=data.get("money") or 0,
                level=(data.get("level_info") or {}).get("current_level") or 0,
            )
            return r
        if body.get("code") == -101:
            r["message"] = "Cookie 已失效（未登录），请重新获取 Bilibili cookie"
        return r

    def _reward(self) -> tuple:
        """拉取「每日任务」状态（成长中心的领取入口）。返回 (http, body, data)。"""
        http, body = self._get("/x/member/web/exp/reward")
        data = body.get("data")
        return http, body, (data if isinstance(data, dict) else {})

    def checkin(self) -> dict:
        """每日签到 / 每日任务领取（幂等）。

        返回 {ok, already, credits, message, http, code, restricted, daily}

        - already=True：本次调用**之前**登录经验就已到账，即"今日已签到"，
          没有重复领取；
        - restricted=True：平台侧不可用（端点迁移 / 账号未完成任务），
          不是"签到失败"，更不是 cookie 有问题。
        """
        http, body, data = self._reward()
        r = {
            "http": http,
            "code": body.get("code", -1),
            "message": body.get("message") or f"HTTP {http}",
            "ok": False,
            "already": False,
            "credits": 0.0,
            "restricted": False,
            "daily": {},
        }
        if http in (401, 403):
            r["message"] = f"Cookie 被拒绝（HTTP {http}），请重新获取 Bilibili cookie"
            return r
        code = body.get("code")
        if http != 200 or code != 0:
            # -101 是明确的「账号未登录」；-400 是「请求错误」，SESSDATA 非法时
            # 也会出现（实测：假 SESSDATA -> code -400，无 cookie -> code -101）。
            # 两者都用只读 nav 复核一次再定性，避免把 cookie 失效误报成"平台受限"。
            if code in (-101, -400):
                nav = self.nav()
                if not nav.get("ok"):
                    r["message"] = ("Cookie 已失效（未登录），请重新获取 Bilibili cookie"
                                    if code == -101 else
                                    "Cookie 已失效（SESSDATA 非法），请重新获取 Bilibili cookie")
                    return r
            # 端点迁移/改版：报"受限"而不是"失败"，免得用户去反复重登 cookie。
            r["restricted"] = True
            r["message"] = (f"B站每日任务接口不可用（HTTP {http}，code {code}）"
                            "，该端点可能已迁移，请检查 bilibili.py 的接口地址")
            return r

        was_done = bool(data.get("login"))
        if not was_done:
            # 走到这里说明是本次"领取"触发的；确认一下是否真的到账。
            time.sleep(CONFIRM_DELAY)
            http2, body2, data2 = self._reward()
            if http2 == 200 and body2.get("code") == 0 and data2:
                data = data2
        now_done = bool(data.get("login"))

        coins_exp = data.get("coins")
        r["daily"] = {
            "login": now_done,
            "watch": bool(data.get("watch")),
            "share": bool(data.get("share")),
            "coins": int(coins_exp) if isinstance(coins_exp, (int, float)) else 0,
        }
        tail = self._daily_tail(r["daily"])

        if now_done:
            r["ok"] = True
            r["already"] = was_done
            r["credits"] = 0.0 if was_done else DAILY_EXP_LOGIN
            r["message"] = ("今日已签到（每日登录经验已到账，未重复领取）" if was_done
                            else f"签到成功，本次获得 {DAILY_EXP_LOGIN:g} 经验（每日登录）") + tail
            return r

        # 接口正常但登录经验始终未到账：B站把这项改成"由登录行为自动发放"了，
        # 属于账号/平台侧状态，不是我们请求写错了。
        r["restricted"] = True
        r["message"] = "B站每日登录经验尚未到账（该任务现由登录行为自动发放），可稍后重试" + tail
        return r

    @staticmethod
    def _daily_tail(daily: dict) -> str:
        """当日任务完成情况（简短，附在签到结果后面）。"""
        if not daily:
            return ""
        marks = []
        for key, label in (("login", "登录"), ("watch", "观看"), ("share", "分享")):
            marks.append(f"{label}{'✓' if daily.get(key) else '✗'}")
        coins = daily.get("coins") or 0
        marks.append(f"投币 {coins}/50 经验")
        return "；今日任务：" + " ".join(marks)

    def status(self) -> dict:
        """查询今日签到状态（无副作用）。返回 {ok, signed_today, nickname, credits, ...}"""
        n = self.nav()
        r = {
            "http": n["http"],
            "code": n["code"],
            "message": n["message"],
            "ok": n["ok"],
            "signed_today": None,  # 无公开只读接口，签到态需调 checkin 探测
            "nickname": n["nickname"],
            "uid": n["uid"],
            "credits": n["money"],
            "summary": "",
        }
        if n["ok"]:
            r["summary"] = f"硬币 {n['money']:g}，等级 {n['level']}"
        return r


# ---------- CLI ----------
def _load_acc(name: str) -> dict:
    acc = find_account(load_config(), name)
    if acc is None:
        sys.stderr.write(f"未找到账号：{name}\n")
        sys.exit(2)
    return acc


def _probe(acc: dict) -> dict:
    """脱敏探测 JSON（不含 cookie 明文），供 Swift 侧一键添加/刷新账号。"""
    try:
        cookie = get_cookie(acc, "bilibili")
        has = bool(cookie)
        reason = "" if has else "账号未配置 cookie，请粘贴 SESSDATA 后重试"
        if has:
            try:
                nav = BilibiliClient(cookie).nav()
                if nav.get("ok"):
                    return {
                        "found": True,
                        "healthy": True,
                        "has_cookie": True,
                        "nickname": nav.get("nickname", ""),
                        "uid": nav.get("uid", ""),
                        "credits": nav.get("money", 0),
                        "message": "",
                        "reason": "",
                    }
                reason = nav.get("message", "登录态验证失败")
            except Exception as e:  # noqa: BLE001
                reason = f"登录态验证失败：{type(e).__name__}"
        return {
            "found": has,
            "healthy": False,
            "has_cookie": has,
            "nickname": "",
            "uid": "",
            "credits": 0,
            "message": reason,
            "reason": reason,
        }
    except Exception as e:  # noqa: BLE001
        return {"found": False, "healthy": False, "has_cookie": False, "nickname": "", "uid": "",
                "credits": 0, "message": f"读取失败：{type(e).__name__}",
                "reason": f"读取失败：{type(e).__name__}"}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Bilibili 每日签到客户端")
    parser.add_argument("--probe", action="store_true", help="探测账号登录态（输出脱敏 JSON）")
    parser.add_argument("--checkin", action="store_true", help="执行签到")
    parser.add_argument("--credits", action="store_true", help="查询账号状态/积分")
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
            print(f"今日状态查询成功，{r.get('summary')}（昵称 {r.get('nickname')}）")
        else:
            print(f"查询失败：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(1)
    elif args.checkin:
        r = client.checkin()
        if r.get("ok"):
            mark = "今日已签到，无需重复" if r.get("already") else f"签到成功，本次获得 {r.get('credits'):g} 硬币"
            print(mark)
        else:
            print(f"签到失败：{r.get('message')}（HTTP {r.get('http')}）")
            sys.exit(1)
    else:
        print("用法：--probe / --checkin / --credits（均需 --name）")


if __name__ == "__main__":
    main()
