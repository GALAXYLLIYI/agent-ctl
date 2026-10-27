# -*- coding: utf-8 -*-
r"""vision_backend_cloud.py —— 视觉兜底的**云后端**（OpenAI 兼容 chat 接口 · 纯标准库）

定位（视觉兜底_接口_20261025.md 的"真后端"位）：
    vision_assist.py 保持零网络协议层；本模块是被它调用的后端之一，
    经 `locate_safe(backend=CloudBackend(...))` 进入，三态纪律原样继承。

★ 四道隐私/钱包闸（缺一不发）：
    1. 双钥匙：AGENTCTL_VISION_ASSIST=1 且 AGENTCTL_VISION_BACKEND=cloud（都由人手设）
    2. 密钥文件制：AGENTCTL_VISION_KEY_FILE（默认 %LOCALAPPDATA%\AgentCtl\_vision_key）
       —— 每次调用时读，永不进日志/命令行/代码（presubmit 扫字面量，本模块里没有任何密钥）
    3. 敏感窗拒绝发送在**调用侧**做（复用 authstate.deny_window）—— 本模块只管"图来了之后"
    4. 日预算闸：AGENTCTL_VISION_DAILY_BUDGET（默认 ¥2.0/天，按状态文件累计的估算消费）

★ 面板可见性：每次调用把 {model, n_calls, tokens, est_cost, last_latency_ms, day}
    写进 AGENTCTL_VISION_STATE_FILE（默认 %LOCALAPPDATA%\AgentCtl\vision_state.json），
    悬浮窗只读该文件展示"连的什么模型、消费多少"。图像本身**永不落盘**。

★ 免费档说明：glm-4v-flash 在 BigModel 当前标价 ¥0（厂商定价，可能变）——
    token 用量从 API 回执**实测**，价格表标注"约"，面板显示真实累计；价格变了预算闸兜底。

★ base_url 可覆盖（AGENTCTL_VISION_BASE_URL）：判据用本地假 HTTP 服务喂四种剧本；
    将来指回 http://127.0.0.1:11434/v1/chat/completions 即 Ollama 的 OpenAI 兼容端 —— 同一适配器两用。
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date

__all__ = ["ENV_BACKEND", "ENV_KEY_FILE", "ENV_MODEL", "ENV_BASE_URL",
           "ENV_BUDGET", "ENV_STATE_FILE", "PRICE_TABLE",
           "CloudBackend", "build_from_env"]

ENV_BACKEND = "AGENTCTL_VISION_BACKEND"
ENV_KEY_FILE = "AGENTCTL_VISION_KEY_FILE"
ENV_MODEL = "AGENTCTL_VISION_MODEL"
ENV_BASE_URL = "AGENTCTL_VISION_BASE_URL"
ENV_TIMEOUT = "AGENTCTL_VISION_TIMEOUT_SEC"
ENV_BUDGET = "AGENTCTL_VISION_DAILY_BUDGET"
ENV_STATE_FILE = "AGENTCTL_VISION_STATE_FILE"

DEFAULT_MODEL = "glm-4v-flash"
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_TIMEOUT_SEC = 8.0
DEFAULT_DAILY_BUDGET = 2.0
_MIN_KEY_LEN = 16

# 每 1K token 估价（¥，进/出）。约数，仅面板展示与预算闸用；Unknown ⇒ cost 记 -1。
PRICE_TABLE = {
    "glm-4v-flash": (0.0, 0.0),
    "glm-4v-plus": (0.004, 0.004),
}

_JSON_ARRAY = re.compile(r"\[[\s\S]*\]")


def _app_dir() -> str:
    return os.environ.get("AGENTCTL_APP_DIR",
                          os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                                       "AgentCtl"))


def default_key_file() -> str:
    return os.environ.get(ENV_KEY_FILE, os.path.join(_app_dir(), "_vision_key"))


def default_state_file() -> str:
    return os.environ.get(ENV_STATE_FILE, os.path.join(_app_dir(), "vision_state.json"))


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def read_key(path: str) -> str:
    """读密钥文件。每次调用都重读（支持换 key 不重启）。空/过短 ⇒ RuntimeError（→ unobservable）。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            key = (f.read() or "").strip()
    except OSError as exc:
        raise RuntimeError(f"密钥文件读不了（{path}）：{type(exc).__name__}") from exc
    if len(key) < _MIN_KEY_LEN:
        raise RuntimeError(f"密钥文件无效（长度<{_MIN_KEY_LEN} 或为空）：{path}")
    return key


def _price(model: str):
    return PRICE_TABLE.get(model)


def _state_path() -> str:
    return default_state_file()


def _state_update(model: str, tokens_in: int, tokens_out: int,
                  est_cost: float, latency_ms: int, found: bool) -> None:
    """按日累计写状态文件（面板只读它）。写坏不抛 —— 状态文件故障不许影响识别主路。"""
    try:
        path = _state_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        today = date.today().isoformat()
        st = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                st = json.load(f)
            if st.get("day") != today:
                st = {}
        except Exception:
            st = {}
        st.update({
            "day": today,
            "backend": "cloud",
            "model": model,
            "n_calls": int(st.get("n_calls", 0)) + 1,
            "tokens_in": int(st.get("tokens_in", 0)) + int(tokens_in),
            "tokens_out": int(st.get("tokens_out", 0)) + int(tokens_out),
            "est_cost_today": round(float(st.get("est_cost_today", 0.0)) + max(0.0, est_cost), 4),
            "last_found": bool(found),
            "last_latency_ms": int(latency_ms),
            "last_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass  # 面板展示是副产物，主路不许被它绊倒


def _budget_check(model: str) -> None:
    """日预算闸——**fail-closed**（外部探针 19）：
    · 模型不在价格表（价格未知）⇒ 无法核算 ⇒ 拒发；
    · 状态文件不可读/损坏 ⇒ 消费无从对账 ⇒ 拒发；
    · 今日估算 ≥ 预算 ⇒ 拒发。
    全部 RuntimeError → locate_safe 判 unobservable（flash 免费档在表内，正常放行）。"""
    if model not in PRICE_TABLE:
        raise RuntimeError(
            f"模型 {model!r} 无价格记录 ⇒ 预算闸无法核算 ⇒ fail-closed 拒发"
            f"（可加入 PRICE_TABLE 或换已核算模型）")
    price = PRICE_TABLE[model]
    path = _state_path()
    st = {}
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        st = {}
    except Exception as exc:
        raise RuntimeError(f"预算状态不可读（{path}）：{exc} ⇒ fail-closed 拒发") from exc
    if st.get("day") == date.today().isoformat():
        budget = _env_float(ENV_BUDGET, DEFAULT_DAILY_BUDGET)
        spent = float(st.get("est_cost_today", 0) or 0)
        if spent >= budget:
            raise RuntimeError(
                f"日预算已用尽（今日 ≈¥{spent} ≥ ¥{budget}）；"
                f"调 {ENV_BUDGET} 可调")
    price = price  # (free 档 (0,0) 天然放行)


_PROMPT = "找出图片中「%s」的位置，给出边界框（0-1 归一化坐标，[x1,y1,x2,y2] 格式）。"


class CloudBackend:
    """OpenAI 兼容 chat 接口的视觉后端（默认智谱 glm-4v-flash，免费档）。"""

    name = "cloud"
    # 云热态实测 ~1.0s（glm-4v-flash 905ms 级）；给足余量但远小于本地冷态。
    default_timeout_sec = 8.0

    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 key_file: str | None = None, timeout_sec: float | None = None):
        self.model = model or os.environ.get(ENV_MODEL, DEFAULT_MODEL)
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL)).strip()
        self.key_file = key_file or default_key_file()
        self.timeout_sec = float(timeout_sec or _env_float(ENV_TIMEOUT, DEFAULT_TIMEOUT_SEC))

    # ── Backend 协议 ────────────────────────────────────────────────────────
    def locate(self, image_bytes: bytes, target_name: str, window_rect=None) -> list[dict]:
        model = self.model
        _budget_check(model)  # 预算尽 ⇒ RuntimeError → locate_safe 判 unobservable
        key = read_key(self.key_file)

        b64 = base64.b64encode(image_bytes or b"").decode("ascii")
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": "data:image/png;base64," + b64}},
                    {"type": "text", "text": _PROMPT % target_name},
                ],
            }],
        }
        req = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"网络不可达：{exc.reason}") from exc
        latency_ms = int((time.monotonic() - t0) * 1000)

        try:
            content = body["choices"][0]["message"]["content"] or ""
            usage = body.get("usage") or {}
            tokens_in = int(usage.get("prompt_tokens", 0) or 0)
            tokens_out = int(usage.get("completion_tokens", 0) or 0)
        except Exception as exc:
            raise RuntimeError(f"回执形状不认识：{type(exc).__name__}") from exc

        cands = self._parse(content, window_rect, target_name)
        price = _price(model)
        est = 0.0
        if price is not None:
            est = (tokens_in / 1000.0) * price[0] + (tokens_out / 1000.0) * price[1]
        _state_update(model, tokens_in, tokens_out, est, latency_ms, bool(cands))
        return cands

    def _parse(self, content: str, window_rect, target_name: str = "目标") -> list[dict]:
        """从回复里抠出候选。抠不出/不合法 ⇒ RuntimeError（→ unobservable，永不当"没有"）。

        实测模型形态（glm-4v-flash，V4 问法）：可能回
            对象 {"save_button":[0.1,0.3,0.4,0.35]} / 纯数组 [0.1,...] / 数组套 dict / []。
        """
        text = (content or "").replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "")
        m = _JSON_ARRAY.search(text)
        if not m:
            raise RuntimeError("回复里没有 JSON 数组")
        try:
            items = json.loads(m.group(0))
        except ValueError as exc:
            raise RuntimeError(f"JSON 解不开：{exc}") from exc

        # 归一形态：dict → 取第一个 4 数字列表值；纯数字列表 → 整体当一只框
        if isinstance(items, dict):
            items = next((v for v in items.values()
                          if isinstance(v, list) and len(v) == 4
                          and all(isinstance(t, (int, float)) for t in v)), [])
        if (isinstance(items, list) and len(items) == 4
                and all(isinstance(t, (int, float)) for t in items)):
            items = [{"label": target_name, "bbox": items, "confidence": 0.5}]
        if not isinstance(items, list):
            raise RuntimeError("JSON 不是数组")

        wr = list(window_rect) if window_rect else None
        # 容错：调用方可能传 [x,y,w,h]（4 元）或 [w,h]（2 元）——形状不对不该 IndexError，
        # 拿不到宽高就不做归一化换算（候选照常返回，坐标口径在 detail 里由上层把关）。
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
            bbox = it.get("bbox")
            label = str(it.get("label") or it.get("name") or "").strip()
            if not label:
                raise RuntimeError("候选缺 label")
            try:
                conf = float(it.get("confidence", 0.5))
            except (TypeError, ValueError):
                raise RuntimeError("confidence 不是数字")
            if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
                raise RuntimeError("bbox 不是四元组")
            v = [float(t) for t in bbox]
            # 模型自由发挥的三种坐标口径，全接：
            #   ① 0-1 归一化角点 [x1,y1,x2,y2]（V4 实测 glm-4v-flash 的输出形态）
            #   ② 0-1000 归一化角点（GLM grounding 原生惯例）
            #   ③ [x,y,w,h]（提示词约定形态）
            if len(v) == 4 and v[2] > v[0] and v[3] > v[1]:
                x1, y1, x2, y2 = v  # 角点序
                if max_w and max_h and x2 <= 1.5 and y2 <= 1.5:      # 0-1 角点
                    x1, x2, w = x1 * max_w, x2 * max_w, (x2 - x1) * max_w
                    y1, y2, h = y1 * max_h, y2 * max_h, (y2 - y1) * max_h
                    x, y = x1, y1
                elif max_w and max_h and x2 <= 1000 and y2 <= 1000 \
                        and (max_w > 1000 or max_h > 1000):          # 0-1000 角点（长图才可判）
                    x1, x2, w = x1 / 1000.0 * max_w, x2 / 1000.0 * max_w, (x2 - x1) / 1000.0 * max_w
                    y1, y2, h = y1 / 1000.0 * max_h, y2 / 1000.0 * max_h, (y2 - y1) / 1000.0 * max_h
                    x, y = x1, y1
                else:                                                # 像素角点
                    x, y, w, h = x1, y1, x2 - x1, y2 - y1
            else:
                x, y, w, h = v                                       # [x,y,w,h]
            if w <= 0 or h <= 0:
                raise RuntimeError("bbox 宽高非正")
            out.append({"label": label, "bbox": [int(round(x)), int(round(y)),
                                                 int(round(w)), int(round(h))],
                        "confidence": max(0.0, min(1.0, conf))})
        return out


def build_from_env():
    """env 组装入口：BACKEND=cloud 才返回实例，否则 None（locate_safe ⇒ "没有可用后端"）。"""
    if os.environ.get(ENV_BACKEND) != "cloud":
        return None
    return CloudBackend()
