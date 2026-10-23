# -*- coding: utf-8 -*-
r"""M19P3 判据 · act.py「谎报成功」三条：F3 日志诚实度 / F5 read 落码 / F7 读失败分因

★ 本刀修的三条（**改前代码里真实存在**）：

  F3  `do_keys`：三态已经算出来（`changed`），但 `changed is None` 时日志的 `ev` 是**空串**
      ⇒ 读者看不出「判不出来」⇒ 假成功连日志都不自曝。
      ★ **rc 维持 0**（主控 2026-10-23 拍板）：act 的 rc 只表示「输入送没送到」，
        画面结论由 **core** 判（core.py 的 `_picture_verdict` 用自己的尺子重判）⇒
        act 若自己下画面结论，会**掐死 core 已有的画面结论**（core.py:2895 `if not ok: return r`）。
        本刀**只改文案**，不动 rc、不动其它日志行。

  F5  `main` 的 `read`：`read_doc` 失败（txt is None）仍 `return 0`
      ⇒ 调用方拿到「读取成功、字符数为 null」的自相矛盾回执。
      ⇒ 改为按 `read_doc` 的**四种失败原因**各落各的码（不许压成一个）：
         未获授权⇒9 / 窗口不响应⇒5 / **真的**没有文本区⇒4 / 读不到文本⇒5。

  F7  `do_type` 前置读：`before is None` 一律落 rc=4「找不到文本区」
      ⇒ 把真因（未获授权 / 窗口不响应 / 读不到文本）掩盖成另一个错
      ⇒ code=4 的处方（契约 §2.1「重新 ctl_windows + ctl_tree 看现状」）
        对「窗口挂了」「取不到值」**都是错处方** ⇒ agent 会照着绕圈。
      ⇒ 改为分因落码，且**读失败不许**走进「没有文本区 ⇒ 网页表单模式盲打」那条路。

★ 本判据**离线打桩**（不许真 GUI）：`do_keys` / `do_type` / `main` 的 read 全部把
  `check_target` / `gate.require` / `gate.require_any` / `ensure_foreground` /
  `require_no_modal` / `read_doc` / `read_selection` / `evidence` / `_img_fp` /
  `send_vk` / `send_text` / `log` 换成桩。
  等待旋钮 `AGENTCTL_SETTLE_MS=0`；闸门 AUTHDIR 指到 %TEMP%（判据不在真仓留痕迹）；
  `sys.dont_write_bytecode = True`（判据**零足迹**）。

★ 四跑法：
    ① 正跑      python -X utf8 tests\_P3_M19P3_谎报成功三条_判据.py               ⇒ 期望 exit=0
    ② 注入      python -X utf8 tests\_P3_M19P3_谎报成功三条_判据.py --inject      ⇒ 期望 exit=1（必须红）
    ③ 产品模式  python -X utf8 tests\_P3_M19P3_谎报成功三条_判据.py --scope product ⇒ 期望 exit=0
    ④ 未知参数  python -X utf8 tests\_P3_M19P3_谎报成功三条_判据.py --未知参数     ⇒ 期望 exit=0
  ★ ③ 产品模式：产物 `legacy_tools\act.py` 由**主控统一重生成**（本单不许跑 gen_legacy）⇒
    本单交卷时产物**尚未含** M19P3 ⇒ 该路打印「【验不了】产物尚未重生成」并按 0 收工
    （**不是假绿**：摘要里会显式写「验不了 1 路」，等主控重生成后补跑）。

★ 退出码：0=全绿 ｜ 1=有红（缺陷仍在 / 注入被逮到）｜ 2=注入没让行为退化（判据没牙）

★ 范围（主控写权约定）：本单只许改**真源** `legacy_tools\_src\act.py` ＋ 本判据；
  产物 / live / 台账 / 复核脚本 **不许碰**（由主控统一重生成与补跑）。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile

# ★ 判据零足迹：不写字节码（import act 会在它旁边生成 __pycache__\*.pyc）
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ── 判据自用的临时家：闸门日志、注入副本都落这儿 ⇒ 真仓不留痕迹 ────────────
_TMP = tempfile.mkdtemp(prefix="_M19P3_判据_")
os.environ["AGENTCTL_AUTHDIR"] = os.path.join(_TMP, "_auth")
os.environ["AGENTCTL_SETTLE_MS"] = "0"          # 动作后不许真睡
try:
    os.makedirs(os.environ["AGENTCTL_AUTHDIR"], exist_ok=True)
except OSError:
    pass

SRC_ACT = os.path.join(ROOT, "legacy_tools", "_src", "act.py")   # 真源（本单的唯一产品面）
PRODUCT_ACT = os.path.join(ROOT, "legacy_tools", "act.py")       # 产物（主控统一重生成）

# ★ `import gate` 的落点：任何一份 act.py 副本的目录里可能没有 gate.py ⇒
#   把真源目录放进 sys.path，保证副本也能 import 到同一份 gate。
sys.path.insert(0, os.path.dirname(SRC_ACT))

FAKE_HWND = 424242
FAKE_INFO = {"hwnd": FAKE_HWND, "title": "靶场记事本", "class": "Notepad",
             "process": "notepad.exe", "rect": [0, 0, 800, 600], "foreground": True}

# read_doc 的四种失败原因（**四因不压一码**，见 act.py `_read_fail_rc`）
HOW_UNAUTH = "未获授权"
HOW_HUNG = "目标窗口不响应（IsHungAppWindow=1），已拒绝读文档"
HOW_NO_CTRL = "找不到文本编辑区"
HOW_UNREADABLE = "读不到文本：某个 COM 异常"


# ══════════════════════════════════════════════════════════════════════════
# 一 · 打桩（★ 离线，不许真 GUI）
# ══════════════════════════════════════════════════════════════════════════
def _patched(mod, overrides):
    old = {k: getattr(mod, k) for k in overrides}
    for k, v in overrides.items():
        setattr(mod, k, v)
    return old


def _restore(mod, old):
    for k, v in old.items():
        setattr(mod, k, v)


def _base_stubs(mod, logs):
    """所有路径共用的桩。"""
    ov = {
        "check_target": lambda hwnd: (dict(FAKE_INFO), None),
        "require_no_modal": lambda hwnd, what: None,
        "ensure_foreground": lambda hwnd, what, dry=False: None,
        "evidence": lambda hwnd, tag: {"文件": "stub.png"},
        "log": lambda m: logs.append(str(m)),
    }
    old = _patched(mod, ov)
    old["__gate_require__"] = mod.gate.require
    old["__gate_require_any__"] = mod.gate.require_any
    mod.gate.require = lambda *a, **k: None
    mod.gate.require_any = lambda *a, **k: None
    return old


def _unpatch(mod, old):
    for k in [k for k in old if k.startswith("__")]:
        attr = k.strip("_")
        setattr(mod.gate, attr, old[k])
    _restore(mod, {k: v for k, v in old.items() if not k.startswith("__")})


def run_keys(mod, fp_seq):
    """跑一次 `do_keys`。`fp_seq` = [前指纹, 后指纹]（None = 取不到指纹 ⇒ changed=None）。"""
    logs, sent = [], []
    old = _base_stubs(mod, logs)
    try:
        seq = list(fp_seq)

        def _fp(_path):
            return seq.pop(0) if seq else None

        _patched(mod, {"_img_fp": _fp,
                       "send_vk": lambda vk, down: sent.append((vk, down))})
        rc = mod.do_keys(FAKE_HWND, vk=0x41, times=1)
        exc = None
    except Exception as e:
        rc, exc = None, "%s: %s" % (type(e).__name__, e)
    finally:
        _restore(mod, {k: v for k, v in old.items() if not k.startswith("__")})
        _unpatch(mod, old)
    return rc, logs, sent, exc


def run_type(mod, *, how=None, before="", force_send=False, dry=False, text="靶字甲"):
    """跑一次 `do_type`。

    · `how` 非 None ⇒ `read_doc` **失败**，返回 (None, None, how)（模拟四种失败原因）
    · `how` 为 None ⇒ `read_doc` 成功，返回 (None, before, "ValuePattern")
    """
    logs, sent = [], []
    old = _base_stubs(mod, logs)
    try:
        _patched(mod, {
            "read_doc": (lambda hwnd: (None, None, how)) if how is not None
                        else (lambda hwnd: (None, before, "ValuePattern")),
            "send_text": lambda t, **kw: sent.append(t),
            "_img_fp": lambda p: "fp",
        })
        rc = mod.do_type(FAKE_HWND, text, dry=dry, force_send=force_send)
        exc = None
    except Exception as e:
        rc, exc = None, "%s: %s" % (type(e).__name__, e)
    finally:
        _restore(mod, {k: v for k, v in old.items() if not k.startswith("__")})
        _unpatch(mod, old)
    return rc, logs, sent, exc


def run_read(mod, *, how=None, txt="abc"):
    """跑 `main` 的 `read` 动作（改 sys.argv 走真路径）。返回 (rc, stdout, exc)。"""
    logs = []
    old = _base_stubs(mod, logs)
    argv_old = sys.argv
    buf = io.StringIO()
    try:
        _patched(mod, {
            "read_doc": (lambda hwnd: (None, None, how)) if how is not None
                        else (lambda hwnd: (None, txt, "ValuePattern")),
            "read_selection": lambda hwnd: "",
        })
        sys.argv = ["act.py", "read", "--hwnd", str(FAKE_HWND)]
        with contextlib.redirect_stdout(buf):
            rc = mod.main()
        exc = None
    except SystemExit as e:
        rc, exc = None, "SystemExit: %s" % e
    except Exception as e:
        rc, exc = None, "%s: %s" % (type(e).__name__, e)
    finally:
        sys.argv = argv_old
        _restore(mod, {k: v for k, v in old.items() if not k.startswith("__")})
        _unpatch(mod, old)
    return rc, buf.getvalue(), exc


# ══════════════════════════════════════════════════════════════════════════
# 二 · 断言组
# ══════════════════════════════════════════════════════════════════════════
def battery(mod):
    """跑完整套断言。返回 [(断言 id, 说明, 期望, 实得, 通过?)]。"""
    out = []

    def add(aid, desc, want, got):
        out.append((aid, desc, want, got, got == want))

    # ── F3 · do_keys 的日志诚实度（rc 维持 0）──────────────────────────
    rc, logs, sent, exc = run_keys(mod, [None, "fp"])
    txt = "\n".join(logs)
    add("F3-1", "changed=None ⇒ 日志必须明说「没测出来」（改前是空串）",
        True, "没测出来" in txt)
    add("F3-2", "changed=None ⇒ 不许把「判不出来」说成「页面无变化」",
        True, ("页面无变化" not in txt and "页面已变化" not in txt))
    add("F3-3", "★ rc 维持 0（主控拍板：画面结论归 core）—— changed=None 也不许改 rc",
        0, rc if exc is None else "EXC " + exc)

    rc, logs, sent, exc = run_keys(mod, ["fp1", "fp2"])
    txt = "\n".join(logs)
    add("F3-4", "changed=True ⇒ 日志仍写「页面已变化」（既有文案不许变）",
        True, "页面已变化" in txt)
    add("F3-5", "changed=True ⇒ rc=0", 0, rc if exc is None else "EXC " + exc)

    rc, logs, sent, exc = run_keys(mod, ["fp1", "fp1"])
    txt = "\n".join(logs)
    add("F3-6", "changed=False ⇒ 日志仍写「页面无变化」（既有文案不许变）",
        True, "页面无变化" in txt)
    add("F3-7", "changed=False ⇒ rc=0（**不许**改成 8：那是 core 的尺子）",
        0, rc if exc is None else "EXC " + exc)

    # ── F5 · main 的 read：读失败不许报成功，四因各落各码 ────────────────
    rc, sout, exc = run_read(mod, how=HOW_UNREADABLE)
    add("F5-1", "read_doc「读不到文本」⇒ rc=5（没测出来），不许 0",
        5, rc if exc is None else "EXC " + exc)
    rc, sout, exc = run_read(mod, how=HOW_UNAUTH)
    add("F5-2", "read_doc「未获授权」⇒ rc=9（没通行证）",
        9, rc if exc is None else "EXC " + exc)
    rc, sout, exc = run_read(mod, how=HOW_HUNG)
    add("F5-3", "read_doc「窗口不响应」⇒ rc=5（不稳定，稍后重试）",
        5, rc if exc is None else "EXC " + exc)
    rc, sout, exc = run_read(mod, how=HOW_NO_CTRL)
    add("F5-4", "read_doc「真的没有文本区」⇒ rc=4（既有语义不许变）",
        4, rc if exc is None else "EXC " + exc)
    rc, sout, exc = run_read(mod, txt="abc")
    add("F5-5", "读到内容 ⇒ rc=0（既有行为不许变）",
        0, rc if exc is None else "EXC " + exc)
    rc, sout, exc = run_read(mod, txt="")
    add("F5-6", "读到**空**内容 ⇒ rc=0（空文档是合法结果，不许当失败）",
        0, rc if exc is None else "EXC " + exc)

    # ── F7 · do_type 前置读：读失败与「真的没有文本区」分开 ─────────────
    rc, logs, sent, exc = run_type(mod, how=HOW_UNREADABLE)
    add("F7-1", "前置读「读不到文本」⇒ rc=5（不许是 4「找不到文本区」）",
        5, rc if exc is None else "EXC " + exc)
    rc, logs, sent, exc = run_type(mod, how=HOW_UNAUTH)
    add("F7-2", "前置读「未获授权」⇒ rc=9",
        9, rc if exc is None else "EXC " + exc)
    rc, logs, sent, exc = run_type(mod, how=HOW_HUNG)
    add("F7-3", "前置读「窗口不响应」⇒ rc=5",
        5, rc if exc is None else "EXC " + exc)
    rc, logs, sent, exc = run_type(mod, how=HOW_NO_CTRL)
    add("F7-4", "前置读「真的没有文本区」⇒ rc=4（既有语义不许变）",
        4, rc if exc is None else "EXC " + exc)
    rc, logs, sent, exc = run_type(mod, how=HOW_UNREADABLE, force_send=True)
    add("F7-5", "读失败 + --force-send ⇒ **不许**走进网页表单模式盲打（不许真发键）",
        0, len(sent))
    add("F7-6", "读失败 + --force-send ⇒ rc 仍是对应的失败码（不许变 0）",
        5, rc if exc is None else "EXC " + exc)
    rc, logs, sent, exc = run_type(mod, how=HOW_NO_CTRL, force_send=True)
    add("F7-7", "「真的没有文本区」+ --force-send ⇒ 仍走网页表单模式（既有行为不许变）",
        1, len(sent))

    return out


# ══════════════════════════════════════════════════════════════════════════
# 三 · 注入矩阵（每条断言都有对应的注入对照：改坏 ⇒ 必须报警）
# ══════════════════════════════════════════════════════════════════════════
_EV_NEW = ('    ev = ("　⚠⚠ 判不出来（指纹取不到）＝**没测出来**" if changed is None\n'
           '          else ("　✔ 页面已变化" if changed else "　⚠ 页面无变化"))\n')
_EV_OLD = ('    ev = "" if changed is None else ("　✔ 页面已变化" if changed else "　⚠ 页面无变化")\n')
_EV_AS_NOCHANGE = ('    ev = ("　⚠ 页面无变化" if changed is None\n'
                   '          else ("　✔ 页面已变化" if changed else "　⚠ 页面无变化"))\n')
_EV_AS_CHANGED = ('    ev = ("　⚠⚠ 判不出来（指纹取不到）＝**没测出来**" if changed is None\n'
                   '          else ("　✔ 有变化" if changed else "　⚠ 页面无变化"))\n')
_EV_AS_UNCHANGED = ('    ev = ("　⚠⚠ 判不出来（指纹取不到）＝**没测出来**" if changed is None\n'
                    '          else ("　✔ 页面已变化" if changed else "　⚠ 没变化"))\n')

_KEYS_RC = ('        f"　前图={before.get(\'文件\') or \'无\'} 后图={after.get(\'文件\') or \'无\'}" + ev)\n'
            '    return 0\n')

_READ_OK = ('            return _rd_rc\n'
            '        return 0\n')

INJECTIONS = (
    ("INJ-1", "F3 的 ev 改回空串（判不出来不说）",
     _EV_NEW, _EV_OLD, ("F3-1",)),
    ("INJ-2", "do_keys 的 rc 改成 5（越过主控拍板：画面结论归 core）",
     _KEYS_RC, _KEYS_RC.replace("    return 0\n", "    return 5\n"), ("F3-3", "F3-5", "F3-7")),
    ("INJ-3", "F5 读失败改回无条件 return 0（谎报成功）",
     "            return _rd_rc\n", "            return 0\n", ("F5-1", "F5-2", "F5-3", "F5-4")),
    ("INJ-4", "F5 读到内容也报 5（把成功打成失败）",
     _READ_OK, _READ_OK.replace("        return 0\n", "        return 5\n"), ("F5-5", "F5-6")),
    ("INJ-5", "F7 不分因（读失败一律当「没有文本区」）",
     '        if _kind != "no_ctrl":\n', "        if False:\n",
     ("F7-1", "F7-2", "F7-3", "F7-5", "F7-6")),
    ("INJ-6", "F7 把「真的没有文本区」也改成 rc=5",
     '        return _tell_next(4, f"打不了字：{how}", hwnd,\n',
     '        return _tell_next(5, f"打不了字：{how}", hwnd,\n', ("F7-4",)),
    ("INJ-7", "F7 禁掉网页表单模式（真没有文本区也不许盲打）",
     "    if before is None and force_send:\n", "    if False:\n", ("F7-7",)),
    ("INJ-8", "F3 把「判不出来」说成「页面无变化」（把没测出来说成没变）",
     _EV_NEW, _EV_AS_NOCHANGE, ("F3-2",)),
    ("INJ-9", "F3 改坏「页面已变化」的文案",
     _EV_NEW, _EV_AS_CHANGED, ("F3-4",)),
    ("INJ-10", "F3 改坏「页面无变化」的文案",
     _EV_NEW, _EV_AS_UNCHANGED, ("F3-6",)),
)


# ══════════════════════════════════════════════════════════════════════════
# 四 · 加载 / 注入副本
# ══════════════════════════════════════════════════════════════════════════
def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def load_act(path, name):
    """按**路径**加载一份 act.py（真源/产物/注入副本都能测）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_injected(src_path, old_text, new_text):
    """把真源复制到 %TEMP% 并注入一处缺陷 ⇒ **现役一个字节不动**。"""
    base = tempfile.mkdtemp(prefix="_M19P3_注入_")
    dst = os.path.join(base, "act.py")
    shutil.copyfile(src_path, dst)
    with io.open(dst, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    n = text.count(old_text)
    if n != 1:
        shutil.rmtree(base, ignore_errors=True)
        raise AssertionError("注入锚点出现 %d 次（要求唯一）⇒ 注入失效" % n)
    text = text.replace(old_text, new_text, 1)
    with io.open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return dst, base


# ══════════════════════════════════════════════════════════════════════════
# 五 · 主流程
# ══════════════════════════════════════════════════════════════════════════
def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for aid, desc, want, got, ok in rows:
        print("  %-6s %-62s 期望=%-8s 实得=%-18s %s"
              % (aid, desc, want, got, "PASS" if ok else "FAIL"), flush=True)


def do_normal(scope, act_path=None):
    targets = []
    if act_path:
        targets.append(("指定", os.path.abspath(act_path)))
    elif scope == "product":
        targets.append(("产物", PRODUCT_ACT))
    elif scope == "all":
        targets.append(("真源", SRC_ACT))
        targets.append(("产物", PRODUCT_ACT))
    else:
        targets.append(("真源", SRC_ACT))

    fails, unverifiable = [], []
    for role, path in targets:
        if not os.path.isfile(path):
            print("[被测] %-4s %s —— **文件不在** ⇒ 验不了" % (role, path), flush=True)
            unverifiable.append("%s：文件不在" % path)
            continue
        print("\n[被测] %-4s %s" % (role, path), flush=True)
        print("       MD5=%s  %d 字节" % (md5_of(path), os.path.getsize(path)), flush=True)
        # ★ 产物与真源不同步 ⇒ 该路**验不了**（不是假绿）：本单不许跑 gen_legacy，
        #   产物由主控统一重生成后补跑。
        if role == "产物" and md5_of(path) != md5_of(SRC_ACT):
            print("       【验不了】产物尚未重生成（与真源 MD5 不同）⇒ "
                  "等主控统一重生成后补跑本路", flush=True)
            unverifiable.append("产物：尚未重生成（等主控统一重生成后补跑）")
            continue
        mod = load_act(path, "act_under_test_%s" % role)
        rows = battery(mod)
        show_rows("%s · F3/F5/F7 断言" % role, rows)
        fails.extend(["%s：%s %s" % (role, aid, desc) for aid, desc, w, g, ok in rows if not ok])

    print()
    print("=" * 78, flush=True)
    if unverifiable:
        print("【验不了】%d 路：%s" % (len(unverifiable), unverifiable), flush=True)
    if fails:
        print("【红】失败 %d 项 -> %s" % (len(fails), fails), flush=True)
        print("★ 判据是活的（能抓住这个缺陷）", flush=True)
        return 1
    print("【绿】全过%s" % ("（有 %d 路验不了，等主控重生成后补跑）" % len(unverifiable)
                           if unverifiable else ""), flush=True)
    return 0


def do_inject():
    """注入自证：每处注入都必须让**它对应的断言**转红 ⇒ 判据有证明力。"""
    print("注入只发生在 %TEMP% 的副本上，现役一个字节不动。\n", flush=True)
    all_caught, problems = True, []
    for iid, desc, old_text, new_text, targets in INJECTIONS:
        try:
            dst, base = make_injected(SRC_ACT, old_text, new_text)
        except AssertionError as e:
            all_caught = False
            problems.append("%s 注入失败：%s" % (iid, e))
            print("【%s】%s\n     ★ 注入失败：%s" % (iid, desc, e), flush=True)
            continue
        try:
            mod = load_act(dst, "act_injected_%s" % iid.replace("-", "_"))
            rows = battery(mod)
            by_id = {r[0]: r for r in rows}
            missed = [t for t in targets
                      if t not in by_id or by_id[t][4]]      # 目标断言必须**红**
            extra = [r[0] for r in rows if not r[4] and r[0] not in targets]
            if missed:
                all_caught = False
                problems.append("%s 目标断言没红：%s" % (iid, missed))
            print("【%s】%s" % (iid, desc), flush=True)
            print("     目标断言 %s ⇒ %s" % (list(targets),
                                       "全红 ✔（改坏被逮到）" if not missed
                                       else "★ 没红：%s" % missed), flush=True)
            if extra:
                print("     （附带转红：%s —— 同一处缺陷的连带影响，正常）" % extra, flush=True)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    print()
    print("=" * 78, flush=True)
    if all_caught:
        print("【红】%d 处注入**全被逮到** ⇒ 判据有证明力 ⇒ exit=1"
              % len(INJECTIONS), flush=True)
        return 1
    print("【✗崩溃】有注入**没**让行为退化（%s）⇒ 判据没牙 ⇒ exit=2"
          % "；".join(problems), flush=True)
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红，exit=1）")
    ap.add_argument("--scope", default="src", choices=("src", "product", "all"),
                    help="src=真源（本单范围）｜ product=产物（等主控重生成）｜ all=两路")
    ap.add_argument("--act", default=None, help="跑指定的一份 act.py（如改前快照）")
    args, _unknown = ap.parse_known_args()      # ★ 未知参数**静默忽略**（不许假红）

    print("== M19P3 判据 · act.py「谎报成功」三条（F3 日志 / F5 read 落码 / F7 读失败分因） ==")
    print("[口径] F3：rc 维持 0（画面结论归 core）＋ 日志必须说清「没测出来」")
    print("[口径] F5：读失败不许报成功；四因各落各码 9/5/4/5")
    print("[口径] F7：读失败与「真的没有文本区」分开；读失败不许盲打")
    print("[模式] %s" % ("注入自证（--inject）" if args.inject
                          else ("指定：%s" % args.act if args.act else "正跑：%s" % args.scope)))
    try:
        return do_inject() if args.inject else do_normal(args.scope, args.act)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
