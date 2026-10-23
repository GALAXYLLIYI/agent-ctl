# -*- coding: utf-8 -*-
r"""M19P2 判据 · 同族第四/第五处：「没测出来」不许写成「确认没变」（act.py 两处读回）

★ 本刀要修的缺陷（**改前代码里真实存在**）：

  X1  `act.py` `do_type` 的**元素段 · UIA 读回**（改前 `act.py:3453` 那一行
      `return 0 if (dry or ok) else 8`）：
      读回靠 `read_doc(hwnd)`；**读不到**时 `after` 拿不到内容 ⇒
      事实是「**没测出来**」，判词却落在「确认没变」那一支 ⇒
      **违反 `core.py:2881` 自己写的铁律**（「没测出来」与「没变」必须**分开**；
      unknown 永不当成功，也**不当「没变」**）。
  X2  `act.py` `do_drag` 的**选区读回**（改前 `act.py:3501` 那一行
      `return 0 if (dry or (sel is not None and len(sel) > 0)) else 8`）：
      `read_selection(hwnd)` 读失败 ⇒ 返回 None ⇒ 报 rc=8「确认没变」，
      事实是「没测出来」⇒ 同一条铁律。

★ 口径（**照 M19P 已落地的 `act._rc_from_changed` 复用，不新造**）：

    changed is True  ⇒ rc=0（变了 / 读到了且一致）
    changed is None  ⇒ rc=5（**没测出来**／判不了）—— `core.py` 的 `_map_rc` 把它
                        映成 code=5（CODE_UNSTABLE）⇒ agent 会重新观测/重试
    changed is False ⇒ rc=8（确认没变 / 读到了但不一致）—— code=8（CODE_NO_CHANGE）
    dry=True         ⇒ rc=0（**dry 分支不动**）

  ★「读失败」与「读到但不一致」的**区分在调用点做**（`_rc_from_changed` 本身不用扩）：
    `read_doc` / `read_selection` 返回 None ⇒ **读失败** ⇒ 喂 None（没测出来）；
    返回字符串 ⇒ **读到了** ⇒ 再比一致不一致（True / False）。

★ 本判据**不碰真 GUI**（铁律）：
  `read_doc` / `read_selection` / `check_target` / `require_no_modal` /
  `ensure_foreground` / `send_text` / `drag` / `log` / `gate.require` 全部打桩；
  动作后的等待旋钮 `AGENTCTL_SETTLE_MS=0`（不许真睡）；
  闸门的 AUTHDIR 指到 %TEMP%（判据不许在真仓 `_auth\` 里留痕迹）。

★ 本判据是**缺陷驱动**的（与 M19' 同款思路）：
  · **改前跑 ⇒ 必须红**（缺陷在）—— 这证明判据有牙；
  · 改后跑 ⇒ 必须绿；
  · `--inject`（把口径改回「None⇒8」）⇒ 必须红 ⇒ 证明判据抓得住这个病。

★ 本判据默认跑**三处**（真源 / 产物 / live）⇒ 三处不一致会直接报红 ——
  「三处同步」这件事因此也归判据管，而不是靠人记得跑生成器。

用法：
    python -X utf8 tests\_P3_M19P2_同族判据.py             # 正跑（期望 exit=0）
    python -X utf8 tests\_P3_M19P2_同族判据.py --inject    # 注入自证（期望 exit=1）
    python -X utf8 tests\_P3_M19P2_同族判据.py --act <路径> # 只跑某一份 act.py

退出码：
    0 = 全绿 ｜ 1 = 有红（缺陷仍在 / 注入被逮到）｜ 2 = 注入没让行为退化（判据没牙）
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile

# ★ 判据要 import 三份 act.py ⇒ 顺手会在它们旁边生成 __pycache__\*.pyc。
#   ⇒ 落笔前先关掉字节码写入 ⇒ 判据**零足迹**（跑完不留任何副产物）。
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ── 判据自用的临时家：闸门日志、注入副本都落这儿 ⇒ 真仓不留痕迹 ────────────
_TMP = tempfile.mkdtemp(prefix="_M19P2_判据_")
os.environ["AGENTCTL_AUTHDIR"] = os.path.join(_TMP, "_auth")
os.environ["AGENTCTL_SETTLE_MS"] = "0"          # 动作后不许真睡
try:
    os.makedirs(os.environ["AGENTCTL_AUTHDIR"], exist_ok=True)
except OSError:
    pass

SRC_ACT = os.path.join(ROOT, "legacy_tools", "_src", "act.py")      # 真源
PRODUCT_ACT = os.path.join(ROOT, "legacy_tools", "act.py")          # 产物（生成物）
FAKE_HWND = 424242
FAKE_INFO = {"hwnd": FAKE_HWND, "title": "靶场记事本", "class": "Notepad",
             "process": "notepad.exe", "rect": [0, 0, 800, 600], "foreground": True}

# ★ 注入段：把「没测出来」重新写成「确认没变」（**只落在 %TEMP% 的副本上**）
INJECT_OLD_RC = '''

# ===== 以下为 M19P2 注入段（跑红自证专用，**勿入产品代码**）=====
def _rc_from_changed(changed, dry=False):          # noqa: F811  故意覆盖真版
    """注入的缺陷：把「没测出来」（changed is None）当成「确认没变」⇒ rc=8。

    ★ 这正是 M19P 之前的口径（`return 0 if (dry or changed is True) else 8` 一类），
      也是 M19P2 这两处漏网的病根：读不回来 ⇒ 说成「确认没变」。
    """
    if dry:
        return 0
    return 0 if changed is True else 8
'''


# ══════════════════════════════════════════════════════════════════════════
# 一 · 定位「三处 act.py」
# ══════════════════════════════════════════════════════════════════════════
def live_act_path():
    """live（真在跑的那份）—— 指针文件 `build\\local_legacy_home.txt`，**不写死盘符**。"""
    ptr = os.path.join(ROOT, "build", "local_legacy_home.txt")
    try:
        with open(ptr, "r", encoding="utf-8") as f:
            home = f.read().strip()
    except OSError:
        return None
    if not home:
        return None
    p = os.path.join(home.replace("/", os.sep), "act.py")
    return p if os.path.isfile(p) else None


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def three_copies():
    """三处：真源 / 产物 / live。返回 [(角色, 路径)]（不存在的跳过）。"""
    out = []
    for role, p in (("真源", SRC_ACT), ("产物", PRODUCT_ACT), ("live", live_act_path())):
        if p and os.path.isfile(p):
            out.append((role, p))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 二 · 打桩（★ 本判据不许碰真 GUI）
# ══════════════════════════════════════════════════════════════════════════
def _stub_read_doc(before_text, after_text, after_fail):
    """`read_doc` 桩：第 1 次（动作前）给 `before`；第 2 次（动作后读回）按 `after_fail` 定。"""
    st = {"n": 0}

    def _f(hwnd):
        st["n"] += 1
        if st["n"] == 1:
            return (None, before_text, "ValuePattern")
        if after_fail:
            return (None, None, "读不到文本：判据桩注入的读失败")
        return (None, after_text, "ValuePattern")

    _f.st = st
    return _f


def _stub_read_selection(sel, fail):
    """`read_selection` 桩：读失败 ⇒ None；读到了 ⇒ 给 `sel`（可能是空串）。"""

    def _f(hwnd):
        return None if fail else sel

    return _f


def _run_with_stubs(mod, body):
    """在打桩环境里跑 `body()`；返回 (rc, logs, exc)。异常不吞 ⇒ 记成 exc。"""
    logs = []
    old = {}
    gate_old = mod.gate.require
    try:
        old["check_target"] = mod.check_target
        old["require_no_modal"] = mod.require_no_modal
        old["ensure_foreground"] = mod.ensure_foreground
        old["log"] = mod.log
        mod.check_target = lambda hwnd: (dict(FAKE_INFO), None)
        mod.require_no_modal = lambda hwnd, what: None
        mod.ensure_foreground = lambda hwnd, what, dry=False: None
        mod.log = lambda m: logs.append(str(m))
        mod.gate.require = lambda *a, **k: None
        rc, exc = body(), None
    except Exception as e:                        # 崩了也是一条红（不许当成功）
        rc, exc = None, "%s: %s" % (type(e).__name__, e)
    finally:
        for k, v in old.items():
            setattr(mod, k, v)
        mod.gate.require = gate_old
    return rc, logs, exc


def run_type(mod, *, before="", after=None, fail=False, dry=False, text="靶字甲"):
    """跑一次 `do_type`（元素段）。返回 (rc, logs, sent, exc)。"""
    sent = []

    def _body():
        mod.read_doc = _stub_read_doc(before, after, fail)
        mod.send_text = lambda t, **kw: sent.append(t)
        return mod.do_type(FAKE_HWND, text, dry=dry)

    rc, logs, exc = _run_with_stubs(mod, _body)
    return rc, logs, sent, exc


def run_drag(mod, *, sel="", fail=False, dry=False):
    """跑一次 `do_drag`。返回 (rc, logs, dragged, exc)。"""
    dragged = []

    def _body():
        mod.read_selection = _stub_read_selection(sel, fail)
        mod.drag = lambda frm, to: dragged.append((frm, to))
        return mod.do_drag(FAKE_HWND, (10, 10), (100, 40), dry=dry)

    rc, logs, exc = _run_with_stubs(mod, _body)
    return rc, logs, dragged, exc


# ══════════════════════════════════════════════════════════════════════════
# 三 · 断言组
# ══════════════════════════════════════════════════════════════════════════
def battery(mod):
    """跑完整套断言。返回 [(标签, 期望, 实得, 通过?, 说明)]。"""
    out = []

    def add(label, want, got, note=""):
        out.append((label, want, got, got == want, note))

    # ── X1 · do_type 元素段 · UIA 读回 ──────────────────────────────────
    rc, logs, sent, exc = run_type(mod, after="", fail=True)
    add("X1 读回失败（没测出来）⇒ rc=5（不许说「确认没变」）", 5,
        rc if exc is None else "EXC " + exc,
        "read_doc 读回返回 None" + ("（★改前在这里崩：_norm_nl(None)）」" if exc else ""))

    rc, logs, sent, exc = run_type(mod, after="靶字甲")
    add("X1 读到且一致 ⇒ rc=0", 0, rc if exc is None else "EXC " + exc)

    rc, logs, sent, exc = run_type(mod, after="别的字")
    add("X1 读到但不一致 ⇒ rc=8（确认没变）", 8, rc if exc is None else "EXC " + exc)

    # ★ 「仅换行表示不同」= **同一批换行**的写法不同（\r\n vs \n），不是尾部多一个 \n。
    rc, logs, sent, exc = run_type(mod, text="靶字甲\n靶字乙",
                                   after="靶字甲\r\n靶字乙")
    add("X1 读到但仅换行表示不同 ⇒ rc=0（既有语义不许丢）", 0,
        rc if exc is None else "EXC " + exc)

    rc, logs, sent, exc = run_type(mod, after="")
    add("X1 读到但是空内容（读到了≠没变）⇒ rc=8", 8,
        rc if exc is None else "EXC " + exc)

    rc, logs, sent, exc = run_type(mod, after="靶字甲", dry=True)
    add("X1 dry（演练）⇒ rc=0（dry 分支不许动）", 0, rc if exc is None else "EXC " + exc)
    add("X1 dry 时不许真的把字送出去", True, (len(sent) == 0), "送出 %d 次" % len(sent))

    # ── X2 · do_drag · 选区读回 ────────────────────────────────────────
    rc, logs, dragged, exc = run_drag(mod, sel="", fail=True)
    add("X2 选区读回失败（没测出来）⇒ rc=5（不许说「确认没变」）", 5,
        rc if exc is None else "EXC " + exc,
        "read_selection 返回 None")

    rc, logs, dragged, exc = run_drag(mod, sel="选中三个字")
    add("X2 读到且非空 ⇒ rc=0", 0, rc if exc is None else "EXC " + exc)

    rc, logs, dragged, exc = run_drag(mod, sel="")
    add("X2 读到但是空选区 ⇒ rc=8（读到了，就是没选到）", 8,
        rc if exc is None else "EXC " + exc)

    rc, logs, dragged, exc = run_drag(mod, sel="选中三个字", dry=True)
    add("X2 dry（演练）⇒ rc=0（dry 分支不许动）", 0, rc if exc is None else "EXC " + exc)
    add("X2 dry 时不许真的拖", True, (len(dragged) == 0), "拖了 %d 次" % len(dragged))

    # ── 文案诚实：rc=5 必须说「没测出来」，rc=8 不许说「没测出来」──────
    rc, logs, sent, exc = run_type(mod, after="", fail=True)
    txt = "\n".join(logs)
    add("X1 rc=5 的回执里必须写「没测出来」", True, ("没测出来" in txt))
    add("X1 rc=5 的回执里不许出现「确认没变」", True, ("确认没变" not in txt))

    rc, logs, dragged, exc = run_drag(mod, sel="", fail=True)
    txt = "\n".join(logs)
    add("X2 rc=5 的回执里必须写「没测出来」", True, ("没测出来" in txt))
    add("X2 rc=5 的回执里不许出现「确认没变」", True, ("确认没变" not in txt))

    rc, logs, sent, exc = run_type(mod, after="别的字")
    txt = "\n".join(logs)
    add("X1 rc=8 的回执里不许写「没测出来」（那是真量到了）", True, ("没测出来" not in txt))

    rc, logs, dragged, exc = run_drag(mod, sel="")
    txt = "\n".join(logs)
    add("X2 rc=8 的回执里不许写「没测出来」（那是真量到了）", True, ("没测出来" not in txt))

    return out


def run_battery(mod):
    """跑断言组，返回 (rows, 关键行)。"""
    rows = battery(mod)
    key = {}
    for label, want, got, ok, note in rows:
        if label.startswith("X1 读回失败"):
            key["X1_fail"] = (got, ok)
        if label.startswith("X2 选区读回失败"):
            key["X2_fail"] = (got, ok)
    return rows, key


# ══════════════════════════════════════════════════════════════════════════
# 四 · 加载与注入
# ══════════════════════════════════════════════════════════════════════════
def load_act(path, name="act_under_test"):
    """按**路径**加载一份 act.py（三处各能各测，注入副本也能测）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_shadow(act_path):
    """把 act.py 所在目录**只读复制**到 %TEMP%，并在副本的 act.py 尾部注入旧口径。

    ★ 现役一个字节都不动（照 `tests\\_inject.py` 的铁律）。
    """
    base = tempfile.mkdtemp(prefix="_M19P2_注入_")
    src_dir = os.path.dirname(os.path.abspath(act_path))
    for fn in os.listdir(src_dir):
        if fn.startswith("__"):
            continue
        if fn.endswith(".py") or fn.endswith(".json"):
            shutil.copyfile(os.path.join(src_dir, fn), os.path.join(base, fn))
    os.makedirs(os.path.join(base, "_auth"), exist_ok=True)
    dst = os.path.join(base, os.path.basename(act_path))
    with open(dst, "a", encoding="utf-8", newline="\n") as f:
        f.write(INJECT_OLD_RC)
    return dst, base


# ══════════════════════════════════════════════════════════════════════════
# 五 · 主流程
# ══════════════════════════════════════════════════════════════════════════
def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for label, want, got, ok, note in rows:
        print("  %-56s 期望=%-6s 实得=%-22s %s%s"
              % (label, want, got, "PASS" if ok else "FAIL",
                 ("  · " + note) if note else ""), flush=True)


def do_normal(targets):
    fails = []
    for role, path in targets:
        print("\n[被测] %-4s %s" % (role, path), flush=True)
        print("       MD5=%s  %d 字节"
              % (md5_of(path), os.path.getsize(path)), flush=True)
        mod = load_act(path, name="act_under_test_%s" % role)
        rows, _ = run_battery(mod)
        show_rows("%s · X1/X2 三态断言" % role, rows)
        bad = [r for r in rows if not r[3]]
        fails.extend(["%s：%s" % (role, r[0]) for r in bad])
    print()
    print("=" * 78, flush=True)
    if fails:
        print("【红】失败 %d 项 -> %s" % (len(fails), fails), flush=True)
        print("★ 判据是活的（能抓住这个缺陷）", flush=True)
        return 1
    print("【绿】全过（%d 份 act.py × %d 条断言）" % (len(targets), len(rows)),
          flush=True)
    return 0


def do_inject(targets):
    """注入自证：把口径改回「None⇒8」，判据必须转红。"""
    role, path = targets[0]
    shadow, base = make_shadow(path)
    print("注入副本（现役不动）：%s" % shadow, flush=True)
    print("注入内容：_rc_from_changed 改回旧口径（changed is None ⇒ rc=8）\n", flush=True)
    mod = load_act(shadow, name="act_under_test_shadow")
    rows, key = run_battery(mod)
    show_rows("注入副本 · X1/X2 三态断言", rows)
    bad = [r for r in rows if not r[3]]
    print()
    print("=" * 78, flush=True)
    caught = []
    for k in ("X1_fail", "X2_fail"):
        got, ok = key.get(k, (None, True))
        if not ok:
            caught.append("%s 实得=%s（期望 5）" % (k, got))
    shutil.rmtree(base, ignore_errors=True)
    if caught:
        print("【红】注入被逮到（%s）⇒ exit=1" % "；".join(caught), flush=True)
        print("★ 判据有证明力：把口径改回「没测出来＝确认没变」，它当场就红。", flush=True)
        return 1
    if bad:
        print("【红】注入副本有 %d 条红，但**不是**两条「没测出来」那两条 ⇒ 判据口径可能变了"
              % len(bad), flush=True)
        return 1
    print("【✗崩溃】注入**没有**让行为退化 ⇒ 判据没牙 ⇒ exit=2", flush=True)
    return 2


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true",
                    help="只跑注入自证（必须红，exit=1）")
    ap.add_argument("--act", default=None,
                    help="只跑某一份 act.py（默认跑真源/产物/live 三处）")
    args = ap.parse_args()

    targets = three_copies()
    if args.act:
        targets = [("指定", os.path.abspath(args.act))]
        if not os.path.isfile(targets[0][1]):
            print("找不到：%s" % targets[0][1], flush=True)
            return 2
    if not targets:
        print("找不到任何 act.py（真源/产物/live 全不在）", flush=True)
        return 2

    print("== M19P2 判据 · 同族第四/第五处：「没测出来」不许写成「确认没变」 ==")
    print("[口径] act rc=0⇒成功 ｜ rc=5⇒code=5（没测出来，重试）｜ rc=8⇒code=8（确认没变）")
    print("[口径] 复用 act._rc_from_changed（True⇒0 / None⇒5 / False⇒8；dry⇒0）")
    print("[模式] %s" % ("注入自证（--inject）" if args.inject
                          else "正跑：%s" % "、".join(r for r, _ in targets)))
    try:
        return do_inject(targets) if args.inject else do_normal(targets)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
