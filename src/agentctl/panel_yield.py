# -*- coding: utf-8 -*-
r"""panel_yield.py —— 面板让位通道（agent 干活时把自己的面板收起来，干完再弹回来）

## 为什么需要（2026-10-20 实测出来的，不是设想）

闸门把「Windows agent-ctl 组件」判成 `sensitive_window`（**agent 不许从外部点自己的面板**
——防绕过授权，红线）。但这个面板是**常驻**的，经常正好盖在别的目标窗上，于是：

    点 QQ → 落点归属判成 wrong_target（遮挡者 = 自己的面板）→ 动作被拒

人手动处理就是「先把面板收起来，点完再弹回来」。**agent 应该能自己做这一步** ——
它并不需要点面板本身，只需要面板**别挡路**。这两件事必须分开：

    · 敏感窗黑名单：仍然拒（不许点自己的面板）—— 红线不动
    · 遮挡让位：新增（面板自动让路）—— 只是不让它挡视线

## 机制：一个 JSON 文件做双向通道（面板与 core 是两个进程）

    %LOCALAPPDATA%\AgentCtl\panel.json
    {"hwnd": 顶层窗口句柄, "pid": 进程号, "yield": true/false, "until": 时间戳}

    · 面板启动时写自己的 hwnd/pid —— core 据此认「这个窗口是我自己的面板」
    · core 判出落点被它挡住 ⇒ 写 `yield=true`（带 `until` 兜底时间）
    · 面板的 1 fps tick 看到 `yield=true` ⇒ `root.withdraw()`（收起来）
    · 动作做完，core 在 dispatch 出口写 `yield=false` ⇒ 面板 `deiconify()` 弹回来
    · 兜底：core 中途崩了没写回来，`until` 过期后面板自己回来（不会永久消失）

★ 写文件一律「临时文件 + os.replace」，避免面板与 core 同时写坏 JSON。
★ 通道读不到/写不了 ⇒ 一律**当作没有让位**（fail-safe：宁可被拒，不许乱点）。
"""
from __future__ import annotations

import json
import os
import time

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AgentCtl")
PATH = os.path.join(APP_DIR, "panel.json")


def _read() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(d: dict) -> bool:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, PATH)
        return True
    except Exception:
        return False


def register(hwnd: int, pid: int = None) -> bool:
    """面板启动时自报家门（core 据此认「这是我自己的面板」）。"""
    d = _read()
    d.update({"hwnd": int(hwnd), "pid": int(pid or os.getpid())})
    return _write(d)


def unregister() -> bool:
    try:
        if os.path.exists(PATH):
            os.remove(PATH)
        return True
    except Exception:
        return False


def panel_hwnd() -> int:
    """我们自己的面板顶层窗口句柄；没有登记 ⇒ 0。"""
    try:
        return int(_read().get("hwnd") or 0)
    except Exception:
        return 0


def is_yielded() -> bool:
    """当前是否处于「让位中」（面板应收起来）。"""
    d = _read()
    if not d.get("yield"):
        return False
    until = float(d.get("until") or 0)
    if until and time.time() > until:
        return False                      # 兜底过期 ⇒ 不再让位（面板自己会回来）
    return True


def request_yield(seconds: float = 2.5) -> bool:
    """请求面板让位（收起来）。写不了 ⇒ False（调用方按「没让位」处理）。"""
    d = _read()
    if not d.get("hwnd"):
        return False
    d["yield"] = True
    d["until"] = time.time() + float(seconds)
    return _write(d)


def request_back() -> bool:
    """动作做完，请面板弹回来。"""
    d = _read()
    if not d:
        return False
    d["yield"] = False
    d["until"] = 0
    return _write(d)


def panel_visible() -> bool:
    """面板窗口此刻是否可见（让位是否真的生效了）。读不到 ⇒ False（别假定成功）。"""
    h = panel_hwnd()
    if not h:
        return False
    try:
        import ctypes                                     # noqa: PLC0415
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        return bool(u32.IsWindowVisible(ctypes.c_void_p(h)))
    except Exception:
        return False
