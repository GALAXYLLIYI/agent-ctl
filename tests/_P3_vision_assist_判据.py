# -*- coding: utf-8 -*-
r"""tests\_P3_vision_assist_判据.py —— 视觉兜底接口（vision_assist）的判据

★ 测的是哪一刀
  `src\agentctl\vision_assist.py`（派单 M1）：给「UIA 拿不到时」的兜底留一个**接口位**。
  本单**不接模型**：只定协议 + 给一个假后端（不读图、不联网）。

★ 为什么值得单独立判据
  这类「接口位」最容易被后来人接上真模型，然后**悄悄变成**"用户的屏幕被送出去了"。
  ⇒ 判据把三条红线**钉成机器可跑的断言**：
     ① 默认关闭 ⇒ unobservable（而且**图根本没被交给任何后端**）；
     ② 永不当成功（超时 / 异常 / 候选不合规 ⇒ 一律 unobservable）；
     ③ 零网络、零模型、只用标准库、不 import core/gate。

★ 四跑法（与本仓约定一致；期望退出码写在这里）
    python -X utf8 tests\_P3_vision_assist_判据.py             # 正跑（期望 exit=0）
    python -X utf8 tests\_P3_vision_assist_判据.py --inject    # 注入自证（期望 exit=1）
    python -X utf8 tests\_P3_vision_assist_判据.py --nosucharg # 未知参数被忽略 ⇒ 与正跑一致
  退出码：0 = 全绿 ｜ 1 = 有红 ｜ 2 = 环境不满足（模块不在仓内）

★ `--inject` 怎么注入（**只改一处缝**）
  把模块里**所有不确定路径的唯一出口** `_unobservable()` 换成"返回 ok:True"的版本
  ⇒ 「没测出来」就会谎报成功 ⇒ 判据必须当场变红。
  ★ 之所以能只改一处：模块把失败出口做成了**单点函数**（这也是它的设计优点）。

★ 本判据**全程离线、零真 GUI**：不截图、不输入、不联网；`image_bytes` 一律传假 bytes。
"""
from __future__ import annotations

import argparse
import ast
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
MOD_PATH = os.path.join(SRC, "agentctl", "vision_assist.py")

verdicts = []


def rec(title, ok, detail=""):
    verdicts.append((title, bool(ok), detail))
    print("  [%s] %s%s" % ("绿" if ok else "红", title,
                           ("  ← %s" % detail) if detail else ""), flush=True)


def out(msg=""):
    print(msg, flush=True)


# ★ 名单：**网络** 与 **模型/推理框架**（形态级，禁止"换个别名绕过去"）
FORBIDDEN_IMPORTS = (
    # 网络
    "requests", "urllib", "urllib3", "http", "httpx", "socket", "ssl", "ftplib",
    "smtplib", "telnetlib", "aiohttp", "websockets", "asyncio", "xmlrpc",
    # 模型 / 推理框架
    "openai", "ollama", "anthropic", "transformers", "torch", "tensorflow",
    "keras", "onnxruntime", "onnx", "cv2", "llama_cpp", "timm", "diffusers",
)
# ★ 本模块**不许**依赖的仓内模块（会把它拖进 core/gate 的重依赖里）
FORBIDDEN_LOCAL = ("core", "gate")


def module_imports(path):
    """用 AST 取模块的 import 名（不靠正则 ⇒ docstring/注释里的字样不算）。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.append(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names.append("<相对 import>")
            elif node.module:
                names.append(node.module.split(".")[0])
    return names


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="P3-vision_assist 判据 · 视觉兜底接口（默认关闭 / 零网络 / 永不当成功）")
    ap.add_argument("--inject", action="store_true",
                    help="注入自证：把「不确定 → unobservable」的出口改成 ok:True（期望 exit=1）")
    a, _unknown = ap.parse_known_args(argv)      # ★ 未知参数静默忽略（与本仓约定一致）

    out("=" * 78)
    out("P3-vision_assist 判据 · 视觉兜底接口（默认关闭 / 零网络 / 永不当成功）")
    out("=" * 78)
    out("[口径] 默认关闭 ⇒ unobservable，且**图根本没被交给任何后端**")
    out("[口径] 永不当成功：超时 / 异常 / 候选不合规 ⇒ 一律 unobservable")
    out("[口径] 三态分开：找到了 / 有效观测到「画面里没有」/ 没测出来（后两者不许混）")
    out("[口径] 零网络、零模型、只用标准库；不 import core/gate")
    out("[模式] %s" % ("注入自证（--inject）" if a.inject else "正跑"))
    out()

    if not os.path.isfile(MOD_PATH):
        out("[红] 模块不在仓内：%s —— 这条接口根本没落地。" % MOD_PATH)
        return 2
    if SRC not in sys.path:
        sys.path.insert(0, SRC)

    # ★ 先把开关摘掉再导入：保证「默认关闭」这一格是**诚实的**（不靠调用者的环境）
    os.environ.pop("AGENTCTL_VISION_ASSIST", None)
    try:
        import agentctl.vision_assist as va
    except Exception as e:
        out("[红] 模块导入失败：%s: %s" % (type(e).__name__, e))
        return 2
    importlib.reload(va)                        # 确保 ENABLED 是「摘掉 env 后」的初值

    out("---- ① 默认关闭（不许把图交出去）----")

    def _inject_seam():
        """把「所有不确定路径的**唯一出口**」换成谎报成功的版本。

        ★ 要打**两次**：下面那次 `importlib.reload(va)` 会把模块**重新执行** ⇒ 注入被清掉。
          只打一次的话，"默认关闭"那条路就不会被注到 ⇒ 注入的杀伤面小一半。
        """
        def _bad_unobservable(detail):
            return {"ok": True, "found": True, "reason": "",
                    "detail": "注入：把「没测出来」谎报成成功", "candidates": []}
        va._unobservable = _bad_unobservable

    if a.inject:
        _inject_seam()
        out("  （已注入①：_unobservable ⇒ 返回 ok:True）")

    class _Spy(va.Backend):
        """记录「有没有被调用」的探针后端（不记 image_bytes）。"""

        def __init__(self):
            self.calls = 0

        def locate(self, image_bytes, target_name, window_rect=None):
            self.calls += 1
            return []

    spy = _Spy()
    r = va.locate_safe(b"\x00fake", "保存", (0, 0, 100, 100), backend=spy)
    rec("默认关闭 ⇒ unobservable", r.get("ok") is False and r.get("reason") == "unobservable",
        "ok=%s reason=%s" % (r.get("ok"), r.get("reason")))
    rec("默认关闭 ⇒ 后端一次都没被调用（不把图交出去）", spy.calls == 0,
        "调用次数=%d" % spy.calls)
    rec("默认关闭 ⇒ ENABLED 为 False", va.ENABLED is False, "ENABLED=%r" % (va.ENABLED,))

    out()
    out("---- ② 静态红线（零网络 / 零模型 / 只用标准库）----")
    imports = module_imports(MOD_PATH)
    hit = sorted(set(imports) & set(FORBIDDEN_IMPORTS))
    rec("模块没有 import 任何网络/模型依赖", not hit, "命中：%s" % ("、".join(hit) if hit else "无"))
    nonstd = sorted(set(imports) - set(getattr(sys, "stdlib_module_names", ())))
    rec("模块只用标准库", not nonstd, "非标准库：%s" % ("、".join(nonstd) if nonstd else "无"))
    localhit = sorted(set(imports) & set(FORBIDDEN_LOCAL))
    rec("模块不 import core / gate", not localhit, "命中：%s" % ("、".join(localhit) if localhit else "无"))
    with open(MOD_PATH, encoding="utf-8") as fh:
        src_text = fh.read()
    rec("源码里没有 http(s):// 字面量（结构上不可能有网络端点）",
        ("http://" not in src_text) and ("https://" not in src_text))

    out()
    out("---- ③ 开启后的行为（真开 + 假后端）----")
    os.environ["AGENTCTL_VISION_ASSIST"] = "1"
    importlib.reload(va)                        # ★ 开关在导入时读一次 ⇒ 靠 reload 翻转
    rec("开启后 ENABLED 为 True", va.ENABLED is True, "ENABLED=%r" % (va.ENABLED,))

    # ★ 注入：把「所有不确定路径的唯一出口」换成谎报成功的版本
    if a.inject:
        _inject_seam()        # ★ reload 把注入清掉了 ⇒ 这里要再打一次
        out("  （已注入②：reload 之后重新打）")

    cands = [{"label": "保存", "bbox": [10, 20, 30, 40], "confidence": 0.9}]
    b = va.FakeBackend({"保存": cands})
    r = va.locate_safe(b"\x00fake", "保存", (0, 0, 100, 100), backend=b)
    rec("开启 + 假后端 ⇒ 找到候选", r.get("ok") is True and r.get("found") is True,
        "ok=%s found=%s" % (r.get("ok"), r.get("found")))
    got = (r.get("candidates") or [{}])[0]
    rec("候选字段齐全（label/bbox/confidence）",
        got.get("label") == "保存" and list(got.get("bbox") or []) == [10, 20, 30, 40]
        and abs(float(got.get("confidence") or 0) - 0.9) < 1e-6,
        "候选=%s" % got)
    rec("假后端**没读图**（只记调用，不碰 image_bytes）", len(b.calls) == 1,
        "调用记录=%s" % b.calls)

    r = va.locate_safe(b"\x00fake", "没有这个", (0, 0, 100, 100),
                       backend=va.FakeBackend({"保存": cands}))
    rec("后端说「画面里没有」⇒ 有效观测（ok=True/found=False），**不是**没测出来",
        r.get("ok") is True and r.get("found") is False and not r.get("candidates"),
        "ok=%s found=%s reason=%s" % (r.get("ok"), r.get("found"), r.get("reason")))

    r = va.locate_safe(b"\x00fake", "保存", (0, 0, 100, 100),
                       backend=va.FakeBackend(mode="raise"))
    rec("后端异常 ⇒ unobservable（永不当成功）",
        r.get("ok") is False and r.get("reason") == "unobservable",
        "ok=%s reason=%s detail=%s" % (r.get("ok"), r.get("reason"), r.get("detail")))

    r = va.locate_safe(b"\x00fake", "保存", (0, 0, 100, 100),
                       backend=va.FakeBackend(mode="timeout", delay_sec=0.5),
                       timeout_sec=0.05)
    rec("后端超时 ⇒ unobservable（永不当成功）",
        r.get("ok") is False and r.get("reason") == "unobservable",
        "ok=%s reason=%s detail=%s" % (r.get("ok"), r.get("reason"), r.get("detail")))

    class _BadCand(va.Backend):
        def locate(self, image_bytes, target_name, window_rect=None):
            return [{"label": "保存", "bbox": [0, 0, 0, 10], "confidence": 0.5}]

    r = va.locate_safe(b"\x00fake", "保存", (0, 0, 100, 100), backend=_BadCand())
    rec("候选不合规（bbox 宽高非正）⇒ unobservable（不放行半吊子坐标）",
        r.get("ok") is False and r.get("reason") == "unobservable",
        "ok=%s detail=%s" % (r.get("ok"), r.get("detail")))

    out()
    bad = [t for t, ok, _ in verdicts if not ok]
    if bad:
        out("【红】%d/%d 条不成立：" % (len(bad), len(verdicts)))
        for t in bad:
            out("      - %s" % t)
        return 1
    out("【绿】%d/%d 条全成立：默认关闭、不把图交出去、永不当成功、零网络零模型。"
        % (len(verdicts), len(verdicts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
