#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""子任务结果的结构化载体（桌面端任务表「今日任务」小图标的数据源）。

为什么单独一个模块：平台客户端（bilibili.py / workbuddy.py）要**产出**它，
checkin.py 要把它**写进台账**，Swift 要把它**读出来渲染**。三方共用同一套
状态字面量，这些字符串一旦各写各的，就会出现"一侧写 already、一侧判 done"
这类静默失配——和凭据字段名踩过的坑同源（见 MEMORY.md），所以集中在这里
当唯一真源。

五态语义（判据要能一眼分清，别把"没领到"和"今天不用领"混起来）：
  done    本次真的做了事：签到成功 / 领到收益
  idle    无需动作：今天已做过、本期已领取、本来就没有可领的
  running 进行中（如 Buddy 正在旅行），下次运行才见结果
  fail    尝试了但没成功 —— **只有这个才需要人跟进**
  na      该任务对这个账号不适用（如非年度大会员没有 B币券）。数据里如实
          记着，但界面**不渲染**：长年灰着一个图标就是噪音，不如不显示。

注意 done 与 idle 的区别是"这次有没有动"，不是"用户满不满意"。所以
「本期已领取」= idle 而不是 done —— 否则每天都会有一排绿图标，反而看不出
今天真正发生了什么。
"""

DONE = "done"
IDLE = "idle"
RUNNING = "running"
FAIL = "fail"
NA = "na"

# 界面只渲染这四态；na 由上层的渲染层过滤掉（见 __doc__）
RENDERED = (DONE, IDLE, RUNNING, FAIL)

_STATES = (DONE, IDLE, RUNNING, FAIL, NA)

# detail 只用于 tooltip，别让它长到撑爆提示框
_DETAIL_MAX = 200


def task(key: str, label: str, state: str, detail: str = "") -> dict:
    """构造一条子任务记录。

    `key` 是稳定的机器标识（Swift 靠它选图标，不能改）；`label` 是给用户看的
    任务名；`detail` 是 tooltip 里那句话。
    """
    if state not in _STATES:
        # 状态拼错时宁可显示成失败，也不要静默滑进"已完成"——报错要吵，别要静。
        state = FAIL
    return {
        "key": str(key),
        "label": str(label),
        "state": state,
        "detail": (detail or "")[:_DETAIL_MAX],
    }


def from_step(ok: bool, msg: str, running_prefix: str | None = None,
              idle_prefix: str | None = None,
              fail_markers: tuple = ("失败", "失效")) -> str:
    """把 (ok, msg) 形式的步骤结果归一成状态。

    项目里各平台步骤的既有约定是：`msg` 为空 = 这一步今天没什么可做的。所以：
      不 ok                              -> fail
      msg 命中 fail_markers              -> fail
      msg 以 running_prefix 开头         -> running（进行中，不是成败）
      msg 以 idle_prefix 开头            -> idle（例如"今日名额已用完"）
      其余有 msg                         -> done（这一步真的动了）
      其余无 msg                         -> idle（无事可做）

    **fail_markers 这一条不是画蛇添足**：有些步骤的 `ok` 只表示"这一步的查询跑通
    了"，里面每个动作是各成各的——成长中心的抽盲盒 / 补登 / 连登兑换都是这样，
    内部动作失败时仍返回 ok=True。少了这一条，界面上就会把「开盲盒失败」画成一个
    绿勾，正是这次要消灭的那种"总状态盖住具体失败"。
    """
    text = msg or ""
    if not ok:
        return FAIL
    if fail_markers and any(m in text for m in fail_markers):
        return FAIL
    if running_prefix and text.startswith(running_prefix):
        return RUNNING
    if idle_prefix and text.startswith(idle_prefix):
        return IDLE
    return DONE if text.strip() else IDLE


def normalize(raw) -> list:
    """读台账时把脏数据收敛一遍。

    台账是本地 JSON，可能被手改过、也可能来自旧版本（还没有 tasks 字段）。
    这里只保留认识的字段与状态，避免把 None / 未知状态透给界面导致渲染成空白。
    """
    out = []
    if not isinstance(raw, list):
        return out
    for it in raw:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key") or "").strip()
        if not key:
            continue
        state = str(it.get("state") or "")
        if state not in _STATES:
            state = IDLE
        out.append({
            "key": key,
            "label": str(it.get("label") or key),
            "state": state,
            "detail": str(it.get("detail") or "")[:_DETAIL_MAX],
        })
    return out
