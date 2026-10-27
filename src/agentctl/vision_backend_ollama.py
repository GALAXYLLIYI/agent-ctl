# -*- coding: utf-8 -*-
r"""vision_backend_ollama.py —— 视觉兜底的**本地后端**（Ollama /api/chat · 纯标准库）

定位：与 vision_backend_cloud 同一 Backend 协议；**图不出门**（127.0.0.1）。
按 DSH 降级策略（2026-10-27）：敏感窗只走本地、绝不降级到云——本模块就是那条"只本地"的路。
base_url 可覆盖（OLLAMA_BASE / 构造参数），默认 http://127.0.0.1:11434。

实测口径（2026-10-27，qwen2.5vl:3b @RTX4070 8GB）：
    冷启动（含模型加载）first_token 12s / total 15.1s；热查询 2.8s ×3 稳定。
    ⇒ default_timeout_sec=75（覆盖冷启动 + 余量；热态实际远快于此）。

Ollama /api/chat 的图像走**顶层 images 数组**（base64，不带 data: 前缀）——
OpenAI 式 content parts 会 400（实测踩过）。

隐私：请求只发本机 Ollama 服务；不落盘、不打图；错误一律 RuntimeError → locate_safe 判 unobservable。
"""
from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request

__all__ = ["ENV_BASE", "ENV_MODEL", "ENV_KEEP", "OllamaBackend", "build_from_env"]

ENV_BASE = "OLLAMA_BASE"
ENV_MODEL = "OLLAMA_VISION_MODEL"
ENV_KEEP = "OLLAMA_KEEP_ALIVE"

DEFAULT_BASE = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5vl:3b"
DEFAULT_TIMEOUT_SEC = 75.0   # 覆盖冷启动 ~15s + 大图慢机余量
ANCHOR_WH = (1920, 1080)     # Ollama qwen 视觉预处理的名义坐标系（标定实验 2026-10-27）
PROMPT = ("找出图片中「%s」的位置，给出边界框（0-1 归一化坐标，[x1,y1,x2,y2] 格式）。只输出 JSON，不要解释。")


class OllamaBackend:
    """Ollama 本地视觉后端（qwen2.5vl 家族）。与 CloudBackend 同一协议。"""

    name = "ollama"
    default_timeout_sec = DEFAULT_TIMEOUT_SEC

    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 keep_alive: str | None = None, timeout_sec: float | None = None):
        self.model = model or os.environ.get(ENV_MODEL, DEFAULT_MODEL)
        self.base_url = (base_url or os.environ.get(ENV_BASE, DEFAULT_BASE)).rstrip("/")
        # keep_alive 驻留：治冷启动（DSH 点名"该治的是冷启动"）；默认 30 分钟
        self.keep_alive = keep_alive or os.environ.get(ENV_KEEP, "30m")
        self.timeout_sec = float(timeout_sec) if timeout_sec else DEFAULT_TIMEOUT_SEC

    def locate(self, image_bytes: bytes, target_name: str, window_rect=None) -> list[dict]:
        import base64
        # ★ 坐标系锚定（2026-10-27 标定实验）：Ollama 的 qwen2.5vl 预处理把任意图当
        #   **1920x1080** 处理，bbox_2d 是那个幻觉坐标系的值（自称"图是1920x1080"实测）。
        #   对策：先把图放大/缩放到 1920x1080 再喂 ⇒ 模型坐标系与真实像素 1:1，
        #   返回的 bbox_2d 直接可用（换回原尺寸由调用方按锚定比例反算，见下）。
        ANCHOR = ANCHOR_WH
        try:
            from PIL import Image as _PILImage
            src = _PILImage.open(io.BytesIO(image_bytes or b""))
            src.load()
            anchor_scale = (ANCHOR[0] / src.size[0], ANCHOR[1] / src.size[1])
            big = src.resize(ANCHOR, _PILImage.LANCZOS)
            _buf = io.BytesIO()
            big.save(_buf, format="PNG")
            b64 = base64.b64encode(_buf.getvalue()).decode("ascii")
            del _buf
        except Exception as exc:
            raise RuntimeError(f"图像预处理失败：{type(exc).__name__}: {exc}") from exc
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": self.keep_alive,
            "messages": [{"role": "user",
                          "content": PROMPT % target_name,
                          "images": [b64]}],
            "options": {"temperature": 0},
        }
        req = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Ollama 请求失败：{type(exc).__name__}: {exc}") from exc
        self.last_latency_ms = int((time.monotonic() - t0) * 1000)  # 面板/测试可读；本地无消费记账

        content = ""
        try:
            content = (body.get("message") or {}).get("content") or ""
        except Exception as exc:
            raise RuntimeError(f"回执形状不认识：{type(exc).__name__}") from exc
        cands = self._parse(content, list(ANCHOR_WH), target_name)
        # bbox_2d 在锚定空间（1920x1080）⇒ 反算回调用方给的 window_rect 坐标系
        out = []
        for c in cands:
            bx, by, bw, bh = c["bbox"]
            out.append({"label": c["label"],
                        "bbox": [int(round(bx / anchor_scale[0])),
                                 int(round(by / anchor_scale[1])),
                                 int(round(bw / anchor_scale[0])),
                                 int(round(bh / anchor_scale[1]))],
                        "confidence": c["confidence"]})
        return out

    def _parse(self, content: str, window_rect, target_name: str) -> list[dict]:
        """与 CloudBackend 同款三坐标口径容错（0-1 角点 / 0-1000 / 像素 / [x,y,w,h]）。"""
        import re
        text = (content or "").strip()
        # ★ Kimi 探针 04：对象形态 {"label":…,"bbox_2d":[…]} 与数组形态 [{…,"bbox_2d":…}]
        #   必须**同语义**——先整包直解 JSON（保住 bbox_2d 原生标记），
        #   失败才退化到正则搜数组（旧路径会丢标记 ⇒ 同数据两种结果）。
        items = None
        try:
            direct = json.loads(text)
        except Exception:
            direct = None
        if isinstance(direct, dict):
            hit_item = None
            for key in ("bbox_2d", "box_2d", "bbox"):
                v = direct.get(key)
                if isinstance(v, list) and len(v) == 4 \
                        and all(isinstance(t, (int, float)) for t in v):
                    hit_item = {"label": str(direct.get("label") or direct.get("name")
                                              or target_name),
                                key: v}
                    break
            items = [hit_item] if hit_item else []
        elif isinstance(direct, list):
            items = direct
        else:
            m = re.search(r"\[[\s\S]*\]", text)
            if not m:
                raise RuntimeError("回复里没有 JSON")
            try:
                items = json.loads(m.group(0))
            except ValueError as exc:
                raise RuntimeError(f"JSON 解不开：{exc}") from exc

        # 归一：纯数字四元组（无字段名）⇒ 当作一只框（键名留空，按口径阶梯判）
        if (isinstance(items, list) and len(items) == 4
                and all(isinstance(t, (int, float)) for t in items)):
            items = [{"bbox": items}]

        wr = list(window_rect) if window_rect else None
        if wr and len(wr) == 4:
            max_w, max_h = (wr[2] if wr[2] > 0 else None), (wr[3] if wr[3] > 0 else None)
        elif wr and len(wr) == 2:
            max_w, max_h = (wr[0] if wr[0] > 0 else None), (wr[1] if wr[1] > 0 else None)
        else:
            max_w = max_h = None

        out = []
        for it in items:
            if not isinstance(it, dict):
                raise RuntimeError("候选不是 dict")
            # qwen2.5-vl 原生 grounding 字段是 bbox_2d（**像素角点** [x1,y1,x2,y2]）；兼容 bbox/box_2d
            qwen_native = "bbox_2d" in it or "box_2d" in it
            raw_box = it.get("bbox_2d") or it.get("box_2d") or it.get("bbox")
            if not (isinstance(raw_box, (list, tuple)) and len(raw_box) == 4):
                raise RuntimeError("bbox 不是四元组")
            v = [float(t) for t in raw_box]
            label = str(it.get("label") or it.get("name") or target_name)
            if qwen_native:
                # 原生口径：像素角点，直接转 [x,y,w,h]（不做归一化换算）
                x, y, w, h = v[0], v[1], v[2] - v[0], v[3] - v[1]
            elif len(v) == 4 and v[2] > v[0] and v[3] > v[1]:
                # bbox 字段的角点序：按归一化阶梯换算
                x1, y1, x2, y2 = v
                if max_w and max_h and x2 <= 1.5 and y2 <= 1.5:
                    x, y, w, h = x1 * max_w, y1 * max_h, (x2 - x1) * max_w, (y2 - y1) * max_h
                elif max_w and max_h and x2 <= 1000 and y2 <= 1000 and (max_w > 1000 or max_h > 1000):
                    x, y, w, h = (x1 / 1000 * max_w, y1 / 1000 * max_h,
                                  (x2 - x1) / 1000 * max_w, (y2 - y1) / 1000 * max_h)
                else:
                    x, y, w, h = x1, y1, x2 - x1, y2 - y1
            else:
                x, y, w, h = v   # [x,y,w,h] 约定形态
            if w <= 0 or h <= 0:
                raise RuntimeError("bbox 宽高非正")
            out.append({"label": label,
                        "bbox": [int(round(x)), int(round(y)), int(round(w)), int(round(h))],
                        "confidence": 0.5})
        return out


def build_from_env():
    """env 装配：OLLAMA_VISION_MODEL 有值即启用（显式意愿）；否则 None。"""
    if not os.environ.get(ENV_MODEL):
        return None
    return OllamaBackend()
