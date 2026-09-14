#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
把抓包工具中复制出来的 cURL 请求，转成账号配置并追加到 accounts.json。

用法（两种方式任选）：
    方式一：命令行直接给
        python3 curl_to_account.py --name trae-1 --app trae --curl "curl 'https://...' -H '...'"

    方式二：交互式（推荐）
        python3 curl_to_account.py
        程序会依次询问：账号名、所属软件、粘贴 cURL

说明：
  - 一个账号可以添加多个签到请求（重复运行本工具，传入相同 name 会追加到该账号下）。
  - 请求 URL / body 中可写 {today}、{today_iso} 占位符，签到当天自动替换为当前日期，
    适合签到接口需要"今日日期"参数的情况。
"""

import argparse
import json
import os
import shlex
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")

# cURL 中常见的与请求无关的参数，跳过其值
_SKIP_WITH_VALUE = {
    "--compressed", "-s", "--silent", "-o", "--output", "-k", "--insecure",
    "--noproxy", "--location", "-L", "--verbose", "-v", "--head", "-I",
}

_UNKNOWN_FLAGS_WITH_VALUE = {
    "--max-time", "--connect-timeout", "--retry", "--proxy", "-x", "--user-agent",
    "-A", "--referer", "-e", "--cookie", "-b", "-u",
}


def parse_curl(curl_str: str) -> dict:
    """解析 cURL 命令行字符串，返回 {method, url, headers, body}。"""
    try:
        parts = shlex.split(curl_str)
    except ValueError as e:
        raise ValueError(f"cURL 解析失败（引号可能不配对）：{e}") from e
    if not parts:
        raise ValueError("cURL 为空")

    # 去掉开头的 curl
    if parts[0].lower() == "curl":
        parts = parts[1:]

    url = None
    method = None
    headers = {}
    body = None
    i = 0
    while i < len(parts):
        tok = parts[i]
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        if tok in ("-X", "--request"):
            method = nxt
            i += 2
        elif tok in ("-H", "--header"):
            if nxt and ":" in nxt:
                k, v = nxt.split(":", 1)
                headers[k.strip()] = v.strip()
            i += 2
        elif tok.startswith("-H") and tok != "-H":  # 形如 -H'X: Y'
            if ":" in tok[2:]:
                k, v = tok[2:].split(":", 1)
                headers[k.strip()] = v.strip()
            i += 1
        elif tok in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"):
            body = nxt
            i += 2
        elif tok.startswith("--data") and nxt is None:
            body = tok.split("=", 1)[1] if "=" in tok else None
            i += 1
        elif tok in ("--compressed", "-s", "--silent", "-k", "--insecure", "--location", "-L", "--verbose", "-v", "--head", "-I"):
            i += 1
        elif tok in _UNKNOWN_FLAGS_WITH_VALUE or (tok.startswith("-") and nxt is not None and not nxt.startswith("-")):
            i += 2
        elif tok.startswith("-"):
            i += 1
        else:
            if url is None:
                url = tok
            i += 1

    if not url:
        raise ValueError("未从 cURL 中解析到 URL，请检查粘贴内容是否为右键【Copy as cURL】的完整结果")

    # 自动推断 method
    if not method:
        method = "POST" if body is not None else "GET"

    # body 是 JSON 时给出默认 Content-Type
    low_headers = {k.lower() for k in headers}
    if body is not None and "content-type" not in low_headers:
        stripped = body.strip() if isinstance(body, str) else ""
        if stripped.startswith(("{", "[")):
            headers.setdefault("Content-Type", "application/json")

    return {"method": method.upper(), "url": url, "headers": headers, "body": body}


def load_config() -> dict:
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"settings": {"timeout": 20, "min_delay_seconds": 2, "max_delay_seconds": 10, "retry_times": 1}, "accounts": []}


def save_config(config: dict) -> None:
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def add_account(config: dict, name: str, app: str, request: dict, replace: bool, enabled: bool = True) -> None:
    accounts = config.setdefault("accounts", [])
    existing = next((a for a in accounts if a.get("name") == name), None)
    if existing is None:
        existing = {"name": name, "app": app, "requests": [], "enabled": enabled}
        accounts.append(existing)
    if existing.get("app") != app:
        existing["app"] = app
    existing.setdefault("requests", [])
    if replace:
        existing["requests"] = [request]
    else:
        existing["requests"].append(request)


def main() -> None:
    parser = argparse.ArgumentParser(description="cURL 转账号配置工具")
    parser.add_argument("--name", help="账号唯一名称，如 trae-1")
    parser.add_argument("--app", help="所属软件，如 trae / workbuddy")
    parser.add_argument("--curl", help="抓包复制的完整 cURL 命令")
    parser.add_argument("--replace", action="store_true", help="同名账号存在时覆盖其请求而不是追加")
    parser.add_argument("--enabled", type=int, default=1, choices=[0, 1],
                        help="新增账号是否加入自动签到（1=开启，0=关闭，默认开启；仅新建账号时生效）")
    args = parser.parse_args()

    if args.curl:
        curl_str = args.curl
    else:
        # 交互模式
        while True:
            try:
                curl_str = input("请粘贴 cURL（右键 Copy as cURL，可直接多行粘贴，输入空行结束）：\n")
                if curl_str.strip():
                    break
            except EOFError:
                print("已取消")
                return
        if not curl_str.strip():
            print("未输入内容，已取消")
            return

    try:
        request = parse_curl(curl_str)
    except ValueError as e:
        print(f"错误：{e}")
        sys.exit(1)

    name = args.name
    app = args.app
    if not name:
        try:
            name = input("账号名称（建议 trae-1 / wb-1 这种唯一名，用于区分多账号）：").strip()
        except EOFError:
            return
    if not app:
        try:
            app = input("所属软件（trae / workbuddy，用于日志标识）：").strip()
        except EOFError:
            return
    if not name or not app:
        print("账号名称和所属软件不能为空")
        sys.exit(1)

    config = load_config()
    add_account(config, name, app, request, replace=args.replace, enabled=bool(args.enabled))
    save_config(config)

    # 打印安全提示
    req_count = len(next(a for a in config["accounts"] if a.get("name") == name)["requests"])
    print(f"\n已保存账号「{name} ({app})」，当前该账号下共 {req_count} 个签到请求。")
    print("提示：accounts.json 内含登录态凭据（token/cookie），请勿分享该文件。")
    print("下一步：运行  python3 checkin.py --dry-run  预览，确认无误后运行  python3 checkin.py  正式签到。")


if __name__ == "__main__":
    main()
