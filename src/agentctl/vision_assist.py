# -*- coding: utf-8 -*-
r"""vision_assist.py —— 视觉兜底接口（**不接模型**：只定协议 + 给一个假后端）

★ 这条线接的是哪一步
  UIA 拿不到控件时（自绘 / Canvas / 游戏 / PDF 视图），平台只剩"截图 + 编号标注"这条笨路。
  这里给「UIA 拿不到时」留一个**接口位**：以后真要接视觉模型，就接在 `Backend` 后面。
  ★ 本单**只留协议与假后端**：不接模型、不调模型、不发网络。

★ 三条硬规矩（都是刻意的，不是省事）
  1. **默认关闭**：`ENABLED` 默认 False，只有环境变量 `AGENTCTL_VISION_ASSIST=1` 才开。
     理由：装完就开 = 用户的屏幕被送进一条没人测过的通道 ⇒ 不行。
     开关在**导入时**读一次 ⇒ 设了环境变量要**重启进程**才生效（与本仓其它 env 开关一致）。
  2. **永不当成功**：所有"不确定"出口走**同一个** `_unobservable()` ⇒ `{"ok": False, "reason": "unobservable"}`。
     ★ "没测出来" ≠ "没有" —— 这两件事必须**分开**（M19′ 的教训：把「没测出来」写成「确认没变」
       会让流程静默停住）。所以本接口是**三态**，不是两态：
         ok=True  + candidates 非空 ⇒ **找到了**
         ok=True  + candidates 为空 ⇒ **有效观测到「画面里没有」**（不是没测出来）
         ok=False + reason=unobservable ⇒ **没测出来**（不许当成功、不许当"没有"）
  3. **零网络、零模型、零新依赖**：只用标准库；**不 import core / gate**。
     ⇒ 本仓**只提供假后端**（`FakeBackend`）：不读图、不联网 ⇒ 结构上不可能把图发出去。
     将来接真后端时，必须在这一层加**显式的人为确认**，且默认仍不发。

★ 协议（就三条，别长）
  · `Backend.locate(image_bytes, target_name, window_rect) -> list[dict]`
  · 候选 = `{"label": str, "bbox": [x, y, w, h], "confidence": float}`
  · `locate_safe(image_bytes, target_name, window_rect, *, backend=None, timeout_sec=2.0) -> dict`
      返回 `{"ok": bool, "found": bool, "reason": str, "detail": str, "candidates": list}`

★ 隐私边界
  · 默认关闭 ⇒ **图根本不会被交给任何后端**（`locate_safe` 进门就返回 unobservable）。
  · 本模块**不打印、不落盘** `image_bytes`；候选里只有坐标与标签，没有图像内容。
"""
from __future__ import annotations

import os
import threading
import time

__all__ = [
    "ENV_KEY", "ENABLED", "DEFAULT_TIMEOUT_SEC", "CANDIDATE_KEYS",
    "Backend", "FakeBackend", "locate_safe", "normalize_candidates",
]

ENV_KEY = "AGENTCTL_VISION_ASSIST"
DEFAULT_TIMEOUT_SEC = 2.0
CANDIDATE_KEYS = ("label", "bbox", "confidence")

# ★ 开关：**导入时**读一次（设了环境变量要重启进程才生效）
ENABLED = os.environ.get(ENV_KEY) == "1"


class Backend:
    """视觉兜底后端抽象。子类只实现 `locate`。"""

    name = "backend"

    def locate(self, image_bytes: bytes, target_name: str,
               window_rect=None) -> list[dict]:
        """在 `image_bytes` 这张裁剪图里找 `target_name`。

        返回候选列表，每项 `{"label","bbox","confidence"}`；**空列表 = 看了但没找到**。
        ★ 本方法**不得**把「不知道」写成空列表来糊弄 —— 不知道要抛异常或超时，
          让 `locate_safe` 判成 unobservable。
        """
        raise NotImplementedError("后端必须实现 locate()")


class FakeBackend(Backend):
    """假后端：按目标名返回固定候选。**不读图、不联网、不落盘。**

    注入方式（判据用；以后做真后端时照这个形状来）：
      mode="ok"      → 正常返回候选（未知目标名 ⇒ 空列表）
      mode="empty"   → 恒返回空列表（"看了但没找到"）
      mode="raise"   → 抛异常（模拟后端故障）
      mode="timeout" → 睡 delay_sec 再返回（配合 locate_safe 的 timeout_sec ⇒ 触发超时）
    """

    name = "fake"

    def __init__(self, candidates=None, mode: str = "ok", delay_sec: float = 0.0):
        self._candidates = {k: [dict(c) for c in v]
                            for k, v in (candidates or {}).items()}
        self._mode = mode
        self._delay = float(delay_sec or 0.0)
        # ★ 只记**调用次数与目标名**，不记 image_bytes（隐私：连调试都不留图像）
        self.calls = []

    def locate(self, image_bytes: bytes, target_name: str,
               window_rect=None) -> list[dict]:
        self.calls.append({"target_name": target_name, "window_rect": window_rect})
        if self._mode == "raise":
            raise RuntimeError("假后端故障（注入）")
        if self._mode == "timeout":
            time.sleep(self._delay if self._delay > 0 else 0.5)
        if self._mode == "empty":
            return []
        return [dict(c) for c in self._candidates.get(target_name, [])]


def normalize_candidates(items) -> tuple:
    """候选逐个合规化。返回 `(是否合规, 合规候选列表, 不合规原因)`。

    ★ 不合规的候选**不许**半吊子地放行（缺字段 / bbox 不合法 ⇒ 当成没测出来）。
    """
    out = []
    for i, it in enumerate(items or [], 1):
        if not isinstance(it, dict):
            return False, [], "第 %d 个候选不是 dict" % i
        missing = [k for k in CANDIDATE_KEYS if k not in it]
        if missing:
            return False, [], "第 %d 个候选缺字段：%s" % (i, "、".join(missing))
        label = it.get("label")
        if not isinstance(label, str) or not label.strip():
            return False, [], "第 %d 个候选的 label 不是非空字符串" % i
        bbox = it.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return False, [], "第 %d 个候选的 bbox 不是 [x,y,w,h] 四元组" % i
        try:
            x, y, w, h = (int(v) for v in bbox)
        except Exception:
            return False, [], "第 %d 个候选的 bbox 不是整数" % i
        if w <= 0 or h <= 0:
            return False, [], "第 %d 个候选的 bbox 宽高非正" % i
        try:
            conf = float(it.get("confidence"))
        except Exception:
            return False, [], "第 %d 个候选的 confidence 不是数字" % i
        out.append({"label": label, "bbox": [x, y, w, h], "confidence": conf})
    return True, out, ""


def _unobservable(detail: str) -> dict:
    """★ 所有「不确定」路径的**唯一出口**。

    ★ 做成一个函数（而不是每处各写一遍）是给注入自证留的缝：
      判据 `--inject` 把**这一个**函数改成返回 ok:True ⇒「没测出来」就会谎报成功
      ⇒ 判据必须当场变红。（单点出口 ⇒ 注入只需改一处，判据不会漏网。）
    """
    return {"ok": False, "found": False, "reason": "unobservable",
            "detail": detail, "candidates": []}


def _run_with_timeout(fn, args, timeout_sec: float):
    """在独立守护线程里跑后端；超时 ⇒ 抛 `TimeoutError`，其它异常原样抛。"""
    box = {}

    def _worker():
        try:
            box["value"] = fn(*args)
        except BaseException as e:            # noqa: BLE001 —— 原样抛给调用方判
            box["error"] = e

    th = threading.Thread(target=_worker, name="vision_assist", daemon=True)
    th.start()
    th.join(timeout_sec)
    if th.is_alive():
        raise TimeoutError("后端超时（>%.3fs）" % timeout_sec)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def locate_safe(image_bytes: bytes, target_name: str, window_rect=None, *,
                backend: Backend = None,
                timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> dict:
    """兜底定位的**安全包装**：任何不确定 ⇒ `unobservable`（**永不当成功**）。

    · 开关未开 ⇒ unobservable（**且不把图交给任何后端**）
    · 后端超时 / 抛异常 ⇒ unobservable
    · 候选不合规 ⇒ unobservable（宁可说"不知道"，也不放行半吊子坐标）
    · 后端正常回答但没给候选 ⇒ `ok=True, found=False`（**有效观测到「没有」**，≠ 没测出来）
    """
    if not ENABLED:
        return _unobservable("开关未开启（%s 未设为 1）" % ENV_KEY)
    if backend is None:
        return _unobservable("没有可用后端")
    try:
        t = float(timeout_sec or 0) or DEFAULT_TIMEOUT_SEC
    except Exception:
        t = DEFAULT_TIMEOUT_SEC
    try:
        raw = _run_with_timeout(backend.locate,
                                (image_bytes, target_name, window_rect), t)
    except TimeoutError as e:
        return _unobservable("后端超时：%s" % e)
    except Exception as e:
        return _unobservable("后端异常：%s: %s" % (type(e).__name__, e))
    ok, cands, why = normalize_candidates(raw)
    if not ok:
        return _unobservable("候选不合规：%s" % why)
    if not cands:
        # ★ 有效观测：后端正常回答「画面里没有这个目标」——**不是**没测出来（两者必须分开）
        return {"ok": True, "found": False, "reason": "",
                "detail": "后端正常回答：画面里没有这个目标", "candidates": []}
    return {"ok": True, "found": True, "reason": "", "detail": "",
            "candidates": cands}
