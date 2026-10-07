# -*- coding: utf-8 -*-
r"""recorder.py —— 采集与环形留存（详细设计 §5.3 / §22 / §23）

★ 分层（§22.2 定的）：
    L0 事件流     每次动作一条（由 core/store 负责）
    L1 结构快照   **控件树 gzip ＋ 窗口元数据**（每 N 秒，变化才存）—— 可查询，"那一刻有没有『保存』按钮"
    L2 视觉锚点   全屏帧 0.25 缩放（每 60 秒）
    L2' 动作帧    动作前后各 1 张，0.5 缩放
    L3 片段录像   按需（clip.py）

★ 环形留存的三条硬规矩（§5.3）：
    1. **`trigger ∈ {action, opening, witness}` 的帧永不清理**；
    2. 先到上限（字节数 / 小时数）就先删最旧的**可清理**帧；
    3. 清理要**留痕**（写一条事件），不许偷偷删。

★ 与闸门的关系（铁律 11）：**真正抓屏必须过闸门**。
   本模块把"抓屏"和"决策逻辑"分开：
     · 决策逻辑（指纹/变化判定/留存计划）是**纯函数**，可以随便测、不需要授权；
     · `capture_screen()` 一进来就 `gate.require_any(...)` —— 没有通行证直接抛 GateDenied。
"""
from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import json
import os
import time

# ────────────────────────── 纯函数：可测、不需要授权 ──────────────────────────
def fingerprint(img, div: int = 8):
    """把一张 PIL 图压成"指纹"：灰度 + 1/div 缩略。

    本机实测（2560×1600）：指纹生成约 6.45 ms、比较 0.10 ms —— 一次"变没变"检查约 6.6 ms，
    1 fps 检查约占单核 0.66%。所以常态检查可以放心做。"""
    from PIL import Image                                   # noqa: PLC0415
    g = img.convert("L").resize((max(1, img.width // div), max(1, img.height // div)),
                                Image.BILINEAR)
    return g.tobytes(), g.size


def fp_diff(a, b) -> float:
    """两个指纹的平均绝对差（0~255）。不同尺寸直接判为"完全不同"。"""
    if not a or not b or a[1] != b[1]:
        return 255.0
    x, y = a[0], b[0]
    n = min(len(x), len(y))
    if n == 0:
        return 255.0
    s = 0
    for i in range(n):
        d = x[i] - y[i]
        s += d if d >= 0 else -d
    return s / n


def should_store(prev_fp, cur_fp, threshold: float = 2.0) -> tuple[bool, float]:
    """变化超过阈值才落盘（静止画面不重复占盘）。返回 (要不要存, 差值)。"""
    if prev_fp is None:
        return True, 255.0
    d = fp_diff(prev_fp, cur_fp)
    return d >= threshold, d


KEEP_TRIGGERS = ("action", "opening", "witness")


def retention_plan(frames: list[dict], *, now: float, max_bytes: int, max_hours: float,
                   protect: tuple = KEEP_TRIGGERS) -> dict:
    """算出该删哪些帧。**纯函数，不碰磁盘** —— 这样留存策略可以被独立测试。

    frames: [{"seq":, "bytes":, "t_wall_epoch":, "trigger":, "state":, "path":}]
    返回 {"delete": [...], "keep": n, "freed_bytes": n, "reason": "..."}
    """
    deletable = [f for f in frames
                 if f.get("trigger") not in protect and f.get("state") != "restricted"
                 and f.get("state") != "purged"]
    protected = [f for f in frames if f not in deletable]

    keep_bytes = sum(int(f.get("bytes") or 0) for f in protected)
    cur_bytes = keep_bytes + sum(int(f.get("bytes") or 0) for f in deletable)
    cutoff = now - max_hours * 3600

    delete, reasons = [], []
    # ① 超时的
    for f in deletable:
        t = f.get("t_wall_epoch")
        if t is not None and t < cutoff:
            delete.append(f)
    if delete:
        reasons.append(f"超时（>{max_hours:g} 小时）：{len(delete)} 张")

    rest = [f for f in deletable if f not in delete]
    freed = sum(int(f.get("bytes") or 0) for f in delete)
    # ② 仍然超容量的：从最旧的开始删
    if cur_bytes - freed > max_bytes:
        rest.sort(key=lambda f: f.get("t_wall_epoch") or 0)
        over = (cur_bytes - freed) - max_bytes
        extra = []
        for f in rest:
            if over <= 0:
                break
            extra.append(f)
            over -= int(f.get("bytes") or 0)
        if extra:
            reasons.append(f"超容量（>{max_bytes/1024/1024:.0f} MB）：再删最旧的 {len(extra)} 张")
        delete += extra
        freed += sum(int(f.get("bytes") or 0) for f in extra)

    return {"delete": delete, "keep": len(frames) - len(delete),
            "deleted": len(delete), "freed_bytes": freed,
            "protected": len(protected), "reason": "；".join(reasons) or "无需清理"}


# ────────────────────────── 采集（必须过闸门）──────────────────────────
class GateDeniedLike(Exception):
    pass


class Recorder:
    """采集器。真正的抓屏走 `gate.require_any` —— 没有通行证就抛异常，绝不偷偷抓。"""

    def __init__(self, store, root: str, *, gate_mod=None,
                 profile: str = "default", max_bytes: int = 8 * 1024**3,
                 max_hours: float = 72.0):
        self.store = store
        self.root = root
        self.frames_dir = os.path.join(root, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self.gate = gate_mod
        if self.gate is None:
            try:
                from agentctl import gate as _g                    # noqa: PLC0415
                self.gate = _g
            except Exception:
                self.gate = None
        self.profile = profile
        self.max_bytes = max_bytes
        self.max_hours = max_hours
        self._prev_fp = None
        self._last_L1 = 0.0
        self._last_L2 = 0.0
        self._session: dict | None = None          # 录制会话（见 start_session 的说明）
        self.frames_seen = 0
        self.frames_stored = 0
        self.bytes_stored = 0

    # ── 档位参数（§22.2 实测定的）──
    def params(self) -> dict:
        if self.profile == "light":
            return {"L1_interval": 10.0, "L2_interval": 180.0, "L2_scale": 0.25, "L2p_scale": 0.5}
        if self.profile == "action_only":
            return {"L1_interval": 0.0, "L2_interval": 0.0, "L2_scale": 0.25, "L2p_scale": 0.5}
        return {"L1_interval": 5.0, "L2_interval": 60.0, "L2_scale": 0.25, "L2p_scale": 0.5}

    # ── 授权：所有抓屏的唯一入口 ──
    #
    # ★★ 2026-10-02 真机跑出来的设计缺陷 + 修法：
    #   原来每 tick 都调 consuming 的 require()（1 次/秒），加上每 5 秒的结构快照再消耗两次
    #   → **A 级的 200 次额度 3 分多钟就烧光**（实测：169 tick 后报「本张通行证动作次数已用尽 200/200」）。
    #   A 级 200 次这个数本来是给"离散工具调用"定的，**不是给 1 fps 连续感知定的**。
    #
    #   修法：**录制会话只在开头消耗一次**；之后每一帧做**非消耗校验** ——
    #   刹车、时限、范围**照样每次都验**（这三条才是铁律 11 的实质），只是不再按帧记账。
    #   这样既没有放松闸门，也不会把额度烧在"看一眼"上。
    def start_session(self, note: str = "录制会话开始") -> str:
        """开一次录制会话：**这里消耗一次通行证动作数**，之后 tick 走非消耗校验。"""
        why = self._require(note)
        self._session = {"started": time.time(), "why": why, "ticks": 0}
        return why

    def end_session(self, note: str = "录制会话结束") -> dict:
        s = self._session
        self._session = None
        return {"ticks": (s or {}).get("ticks", 0),
                "duration_sec": round(time.time() - (s or {}).get("started", time.time()), 1)}

    def _require(self, note: str):
        """非消耗优先：有会话且仍有效（刹车松／未过期／范围含 A）就不消耗动作数。"""
        if self.gate is None:
            raise GateDeniedLike("没有可用的闸门模块，拒绝抓屏")
        if getattr(self, "_session", None):
            try:
                ok, why, _code = self.gate.check("A")
                if ok:
                    self._session["ticks"] += 1
                    return f"[会话内非消耗校验] {why}"
            except Exception:
                pass
            # 会话已失效（过期/急停/被消费）→ 落到下面重新申请，拿不到就抛
        scopes = getattr(self.gate, "PERCEIVE", ("A", "B", "C", "D"))
        try:
            return self.gate.require_any(scopes, note, count=False)   # 感知：不计数（2026-10-07）
        except Exception as e:
            raise GateDeniedLike(str(e)) from e

    def capture_screen(self, *, note: str = "录制抓屏"):
        """真正的抓屏。**必须先过闸门。** 返回 PIL 图。"""
        self._require(note)
        import mss                                              # noqa: PLC0415
        from PIL import Image                                   # noqa: PLC0415
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            raw = sct.grab(mon)
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def snapshot_ui(self, hwnd: int | None = None, note: str = "L1 结构快照") -> dict | None:
        """L1：控件树 gzip ＋ 窗口元数据。走老脚本（它自己也会过闸门）。"""
        self._require(note)
        from agentctl import core                                # noqa: PLC0415
        if hwnd:
            r = core.dispatch("ctl_tree", {"hwnd": int(hwnd), "max_nodes": 400},
                              actor="recorder")
        else:
            r = core.dispatch("ctl_windows", {"limit": 40}, actor="recorder")
        if not r.get("ok"):
            return {"ok": False, "why": r.get("warning"), "code": r.get("code")}
        blob = json.dumps(r.get("data"), ensure_ascii=False).encode("utf-8")
        gz = gzip.compress(blob)
        return {"ok": True, "raw_bytes": len(blob), "gz_bytes": len(gz),
                "gz_ratio": round(len(gz) / max(1, len(blob)), 4),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "blob": gz}

    # ── 存一张帧 ──
    def _save(self, img, *, trigger: str, scale: float, quality: int = 70,
              t_mono: float | None = None, t_wall: str | None = None) -> dict:
        from PIL import Image                                   # noqa: PLC0415
        w0, h0 = img.size
        if scale < 1.0:
            img = img.resize((max(1, int(w0 * scale)), max(1, int(h0 * scale))), Image.BILINEAR)
        name = f"{_dt.datetime.now():%Y%m%d_%H%M%S}_{int((t_mono or time.perf_counter())*1000)%100000}_{trigger}.jpg"
        rel = os.path.join("frames", name)
        fp = os.path.join(self.root, rel)
        img.save(fp, "JPEG", quality=quality, optimize=False)
        rec = self.store.append_frame(rel, trigger=trigger, t_mono=t_mono, t_wall=t_wall,
                                      w=img.width, h=img.height)
        self.frames_stored += 1
        self.bytes_stored += os.path.getsize(fp)
        return {**rec, "scale": scale}

    def _ensure_session_valid(self, note: str = "tick 校验"):
        """★ 每一拍都要过这道 —— **校验住在控制流里，不住在可替换的 I/O 方法里**。

        ★★ 2026-10-02 第三次修（集成测试抓出来的安全缺口）：
           原来闸门校验只写在 `capture_screen()` 内部。测试把 capture_screen 打桩后，
           **校验连着一块被替换掉了** → 失效的通行证、拉下的急停，tick() 照样跑。
           **这是真的防御纵深缺口：安全校验不能依赖某个可被替换的方法。**
           现在 tick() 每拍自己先验一遍（非消耗），拿不到就抛。
        """
        if not getattr(self, "_session", None):
            self.start_session(note)          # 无会话：消耗一次，拿证
            return
        if self.gate is None:
            raise GateDeniedLike("没有可用的闸门模块")
        ok, why, _code = self.gate.check("A")  # 非消耗：刹车／时限／范围
        if not ok:
            self._session = None               # 会话作废，下次必须重新拿证
            raise GateDeniedLike(f"录制会话已失效：{why}")

    # ── 主循环的一拍 ──
    def tick(self, *, now: float | None = None) -> dict:
        """一拍：验会话 → 抓一次 → 判变化 → 按分层决定存不存。**没通行证会抛 GateDeniedLike。**

        ★★ 2026-10-02 第二次修（集成漏洞）：
           上一版把"开会话"做成**需要调用方记得调 start_session()**，
           结果我自己的 10 分钟验收驱动忘了调 → 每 tick 照样消耗 → 额度又 3 分钟烧光。
           **教训：不能让调用方靠记性去满足安全前提。** 现在 tick() **第一次自动开会话**。
        """
        now = now if now is not None else time.time()
        self._ensure_session_valid("tick 校验")     # ★ 每拍都验（非消耗），与抓屏实现无关
        p = self.params()
        img = self.capture_screen(note="recorder.tick")
        self.frames_seen += 1
        fp = fingerprint(img)
        store_it, diff = should_store(self._prev_fp, fp, threshold=2.0)
        out = {"diff": round(diff, 3), "stored": False, "trigger": None}
        if store_it:
            self._prev_fp = fp
            if p["L2_interval"] and now - self._last_L2 >= p["L2_interval"]:
                self._save(img, trigger="heartbeat", scale=p["L2_scale"])
                self._last_L2 = now
                out.update(stored=True, trigger="heartbeat")
            elif now - self._last_L2 >= 1.0:
                self._save(img, trigger="change", scale=p["L2_scale"])
                self._last_L2 = now
                out.update(stored=True, trigger="change")
            if p["L1_interval"] and now - self._last_L1 >= p["L1_interval"]:
                snap = self.snapshot_ui(note="L1 结构快照")
                if snap and snap.get("ok"):
                    out["L1"] = {"gz_bytes": snap["gz_bytes"], "sha256": snap["sha256"][:16]}
                self._last_L1 = now
        return out

    def capture_for_action(self, *, pad_done: bool = True) -> dict:
        """动作帧：前后各 1 张（0.5 缩放）。动作前调用一次拿 before，动作后再调一次拿 after。"""
        p = self.params()
        before = self._save(self.capture_screen(note="action-before"), trigger="action",
                            scale=p["L2p_scale"])
        after = None
        if pad_done:
            time.sleep(0.25)
            after = self._save(self.capture_screen(note="action-after"), trigger="action",
                               scale=p["L2p_scale"])
        return {"before": before.get("seq"), "after": after.get("seq") if after else None}

    # ── 环形留存 ──
    def prune(self, *, now: float | None = None, dry_run: bool = False) -> dict:
        """按上限清理最旧的可清理帧；**动作/起手/见证帧永不删**；清理必须留痕。"""
        now = now if now is not None else time.time()
        rows = self.store.frames(limit=100000, include_restricted=True)
        metas = []
        for r in rows:
            try:
                t = _dt.datetime.fromisoformat(r["t_wall"]).timestamp()
            except Exception:
                t = None
            metas.append({"seq": r["seq"], "bytes": r["bytes"] or 0, "t_wall_epoch": t,
                          "trigger": r["trigger"], "state": r["state"], "path": r["path"]})
        plan = retention_plan(metas, now=now, max_bytes=self.max_bytes, max_hours=self.max_hours)
        freed = 0
        if not dry_run:
            for f in plan["delete"]:
                fp = os.path.join(self.root, f["path"])
                try:
                    if os.path.exists(fp):
                        freed += os.path.getsize(fp)
                        os.remove(fp)
                except OSError:
                    pass
            if plan["delete"]:
                self.store.append("retention_purge", actor="recorder", tool="prune",
                                  payload={"deleted": len(plan["delete"]),
                                           "freed_bytes": freed, "reason": plan["reason"]},
                                  ok=True, code=0)
        plan["freed_on_disk"] = freed
        return plan

    def status(self) -> dict:
        p = self.params()
        return {"profile": self.profile, "params": p,
                "max_bytes": self.max_bytes, "max_hours": self.max_hours,
                "frames_seen": self.frames_seen, "frames_stored": self.frames_stored,
                "bytes_stored": self.bytes_stored,
                "protected_triggers": list(KEEP_TRIGGERS)}
