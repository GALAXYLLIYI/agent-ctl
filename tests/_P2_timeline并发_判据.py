# -*- coding: utf-8 -*-
r"""tests\_P2_timeline并发_判据.py —— 第 43 条判据：**timeline 多进程并发写不许丢事件**

治的病（派单 M3 · 2026-10-21）：
    真源 src\agentctl\store.py 的写路径只有**进程内** `threading.RLock`，
    而 `_load_head()` 只在 `Store.__init__` 里调一次 ⇒ 每个进程拿着**自己的**链尾快照。
    真源的应用形态恰恰是**短命进程**（每次 MCP/HTTP 调用起一个进程），
    「读链尾 → 干活（截图/OCR/UI 自动化）→ 追加」这个窗口里别的进程早把链尾推走了：
      · **seq 撞车** ⇒ `events.seq` 是 PRIMARY KEY + `INSERT OR REPLACE`
        ⇒ 后写的**覆盖**先写的 ⇒ SQLite 索引静默少条；
      · **哈希链分叉** ⇒ 两条记录挂到同一条 prev_hash 上 ⇒ 链断；
      · **jsonl 追加被撕裂** ⇒ 两个进程各 seek 到同一 EOF 后写 ⇒ 记录被拼坏/覆盖
        ⇒ **真相源自己**丢事件（这条最狠：不是索引少，是源文件少）。
    本机现场实测：jsonl 3376 行 / SQLite 3338 行 ⇒ **索引少 38 条**；**21 处断链**。

================================ 四组断言 ====================================
A组｜**规格可满足性**（判定面·绿）
  ① 用**参考实现**（修法 A 原型：跨进程文件锁 + 锁内重读链尾）跑并发写，
     三条契约必须**同时成立**：
       (a) jsonl 行数 == 写入总数（且无撕裂行）
       (b) 哈希链全通（断链 0 处 / seq 不连续 0 处）
       (c) sqlite 行数 == jsonl 行数
     ⇒ 证明这三条**不是互相打架的空话**（是**可同时满足**的规格）。

B组｜**真源实测**（判定面·绿·但内容是「病还在」）
  ② 真源 store.py 在同一并发场景下，三条契约**不可能同时成立**。
     ★ 这条是**钉子**：它证明 A 组的断言**有判别力**（对真会坏的实现就会红），
       同时钉住「真源还是坏的」。
     ★ 真源修好之后本组会转红 —— 那不是出错，是**提醒**：
       「口径该翻过来了（把本组从『必须不成立』改成『必须成立』），
         本条即可登记进 run_all.CRITERIA」。真源改动另开 DSH 单。

C组｜**真源现场体检**（判定面·绿·只读 work\timeline）
  ③ 现场四数必须**如实报出**，且必须**互相自洽**：
       jsonl 行数 − sqlite 行数 == 重复 seq 条数
     （本机实测 3376 − 3338 = 38 = 重复 seq 38 ⇒ 等式成立 ⇒ 坐实
       「少掉的那 38 条**就是**被 `INSERT OR REPLACE` 覆盖掉的那 38 条」）
  ④ 断链处数必须 **> 0**（今天 21 处）—— 现场有断链就必须报出来，不许报成 0。
  ⑤ 本组**只读**：跑完前后现场的（文件数/字节/mtime）必须一模一样。

D组｜**交付物与边界**（判定面·绿）
  ⑥ 四件交付物在位（复现脚本 / 设计稿 / 本判据 / M2 挪进来的两件）。
  ⑦ **真源不许被本单改过**（`git diff HEAD -- src/agentctl/store.py` 必须为空）。

================================ --inject（必红）================================
`--inject` 把 A 组的写者**换成「缺陷实现」**（拿掉跨进程文件锁的那个）——
    ⇒ 它模拟的是「只做了一半的修法」：链照样分叉、索引照样覆盖。
    ⇒ A 组三条断言必须**同时转红** ⇒ 本条整体 rc=1。
⇒ 证明本判据**不是恒绿**：它真的抓得住「并发写坏」。

退出码：0 绿 / 1 红 / 2 验不了（**验不了不许读成绿**）

★ 为什么本条**不接 run_all**（NOT_WIRED，理由写在 tests\_元判据_判据接线.py）：
  C 组读的是**本机 work\timeline 真源现场**（不入库，换台机器读不到）
  ⇒ 那台机器上本条整条 rc=2 ⇒ 按本仓纪律（见 _滚动按键判据.py 的先例）
  「含验不了项的判据不接 run_all」。真源修好、C 组改成读沙箱后即可登记。
"""
from __future__ import annotations

import glob
import hashlib
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TIMELINE_DIR = os.path.join(ROOT, "work", "timeline")
REPRO = os.path.join(HERE, "_timeline并发_复现.py")
DESIGN = os.path.join(ROOT, "docs", "设计_timeline并发写_20261021.md")
SELF = os.path.abspath(__file__)
M2_PROBE = os.path.join(HERE, "_attr_probe.py")
M2_PROBE_OUT = os.path.join(HERE, "fixtures", "attr_probe_out.txt")
TRUE_SOURCE = os.path.join(ROOT, "src", "agentctl", "store.py")

GREEN, RED, SKIP, NOTES = [], [], [], []
INJECT = False


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def note(text):
    NOTES.append(text)
    print("[注] %s" % text, flush=True)


def finish():
    print("\n" + "=" * 70, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】timeline 并发写判据没守住（详见上面每一条红）", flush=True)
        return 1
    print("结论：【绿】并发写三条契约可同时满足；真源现状已如实报出。", flush=True)
    print("★ 注意：本条**判定面是规格**。真源 store.py 仍是坏的 ——", flush=True)
    print("  「真源未修」这件事在 B 组如实报出，**修法与迁移路径见**", flush=True)
    print("  docs\\设计_timeline并发写_20261021.md（真源改动另开 DSH 单）。", flush=True)
    return 0


# ── 载入复现脚本（同一把尺子：真源与参考实现共用）────────────────────────
def _load_repro():
    spec = importlib.util.spec_from_file_location("_tl_repro", REPRO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════
# C 组｜真源现场体检（只读）
# ══════════════════════════════════════════════════════════════════════
def _site_snapshot() -> dict:
    """现场的 (文件数 / 总字节 / 各文件 mtime) —— 用来证明本组**只读**。"""
    snap = {}
    for p in sorted(glob.glob(os.path.join(TIMELINE_DIR, "*"))):
        if os.path.isfile(p):
            st = os.stat(p)
            snap[os.path.basename(p)] = (st.st_size, int(st.st_mtime))
    return snap


def _stable_measure(mod, retries: int = 6):
    """量现场。★ 现场是**活的**（应用正在写）⇒ 必须量两遍确认期间没变，
    否则读到的 jsonl 行数与 sqlite 行数会来自不同时刻 ⇒ 等式必然对不上。"""
    for _ in range(retries):
        before = mod.measure(TIMELINE_DIR)
        mid = _site_snapshot()
        time.sleep(0.05)
        after = mod.measure(TIMELINE_DIR)
        if (before["jsonl_lines"] == after["jsonl_lines"]
                and before["sqlite_events"] == after["sqlite_events"]):
            return before, mid
        time.sleep(0.3)
    return None, None


def _git_clean(rel: str):
    """(ok, detail)。ok=None 表示**验不了**（本机不是 git 仓 / git 不可用）。"""
    try:
        p = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel],
                           cwd=ROOT, capture_output=True, timeout=30)
    except Exception as e:
        return None, "git 不可用：%s: %s" % (type(e).__name__, e)
    if p.returncode == 0:
        return True, "工作区与 HEAD 一致（真源未被本单改动）"
    if p.returncode == 1:
        try:
            diff = subprocess.run(["git", "diff", "--stat", "HEAD", "--", rel],
                                  cwd=ROOT, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=30)
            return False, "★ 真源有未提交改动：%s" % (diff.stdout.strip() or "见 git diff")
        except Exception:
            return False, "★ 真源有未提交改动"
    return None, "git diff 失败（rc=%s）" % p.returncode


# ══════════════════════════════════════════════════════════════════════
def main() -> int:
    global INJECT
    INJECT = "--inject" in sys.argv

    print("=" * 70, flush=True)
    print("判据 43：timeline 多进程并发写不许丢事件（M3）", flush=True)
    print("=" * 70, flush=True)
    if INJECT:
        print("★ 注入：A 组的写者换成「缺陷实现」（拿掉跨进程文件锁）", flush=True)
        print("  ⇒ A 组三条契约必须同时转红 ⇒ 本条必须 rc=1", flush=True)

    if not os.path.isfile(REPRO):
        print("[红] 复现脚本不在：%s" % REPRO, flush=True)
        return 1
    mod = _load_repro()

    # ── A 组｜规格可满足性 ────────────────────────────────────────────
    print("\n── A组｜规格可满足性（参考实现 = 修法 A 原型）──", flush=True)
    a_writer = "缺陷" if INJECT else "参考"
    a_res = mod.run_scenario(os.path.join(mod.tempfile.gettempdir(),
                                          "agentctl_tl_judge_A_%d" % os.getpid()),
                              processes=6, each=40, writer=a_writer)
    a_c = a_res["contracts"]
    print("  写者=%s ｜ jsonl 行数=%s ｜ 写入总数=%s ｜ 断链=%s ｜ sqlite 行数=%s ｜ 撕裂行=%s"
          % (a_writer, a_res["measure"]["jsonl_lines"], a_res["written_total"],
             a_res["measure"]["chain_breaks"], a_res["measure"]["sqlite_events"],
             a_res["measure"].get("bad_lines", 0)), flush=True)
    for k, label in (("a", "① (a) jsonl 行数 == 写入总数（无撕裂行）"),
                     ("b", "② (b) 哈希链全通"),
                     ("c", "③ (c) sqlite 行数 == jsonl 行数")):
        rec(label, a_c[k]["ok"],
            "期望=%s 实得=%s" % (a_c[k]["want"], a_c[k]["got"]))
    rec("④ 三条契约**同时**成立（规格不是空话）", a_c["all_ok"],
        "三条全立=%s（修法 A 原型证明：跨进程文件锁 + 锁内重读链尾 ⇒ 可满足）"
        % a_c["all_ok"])
    if INJECT:
        note("注入自证：A 组此刻用的是**缺陷实现**，上面四条**必须全红** —— "
             "若还有一条是绿的，说明本判据抓不住『并发写坏』，本条就是死的。")

    # ── B 组｜真源实测（钉住「病还在」）───────────────────────────────
    print("\n── B组｜真源实测（真源 store.py 现状）──", flush=True)
    b_res = mod.run_scenario(os.path.join(mod.tempfile.gettempdir(),
                                          "agentctl_tl_judge_B_%d" % os.getpid()),
                              processes=6, each=40, writer="真源")
    b_c = b_res["contracts"]
    b_m = b_res["measure"]
    print("  真源 ｜ jsonl 行数=%s ｜ 写入总数=%s ｜ 断链=%s ｜ sqlite 行数=%s ｜ 撕裂行=%s"
          % (b_m["jsonl_lines"], b_res["written_total"], b_m["chain_breaks"],
             b_m["sqlite_events"], b_m.get("bad_lines", 0)), flush=True)
    for k, label in (("a", "⑤ 真源 (a) jsonl 行数 == 写入总数（无撕裂行）"),
                     ("b", "⑥ 真源 (b) 哈希链全通"),
                     ("c", "⑦ 真源 (c) sqlite 行数 == jsonl 行数")):
        rec(label + "（实况，如实报出）", True,
            "%s —— %s" % ("成立" if b_c[k]["ok"] else "**不成立**",
                          "期望=%s 实得=%s" % (b_c[k]["want"], b_c[k]["got"])))
    rec("⑧ 真源下三条契约**不可能同时成立**（钉子：病还在，且断言有判别力）",
        not b_c["all_ok"],
        "真源并发写实测：不成立的是 %s"
        % ("、".join(k for k in ("a", "b", "c") if not b_c[k]["ok"]) or "无"))
    print("  ★ 真源修好后本条会转红 —— 那不是出错，是提醒把本组口径翻过来。", flush=True)

    # ── C 组｜真源现场体检（只读）─────────────────────────────────────
    print("\n── C组｜真源现场体检（work/timeline，只读）──", flush=True)
    if not os.path.isdir(TIMELINE_DIR):
        rec("⑨ 真源现场体检", False,
            "现场不在：%s（不在版本库里，换台机器读不到 ⇒ 验不了）" % TIMELINE_DIR,
            skip=True)
    else:
        snap0 = _site_snapshot()
        m, _ = _stable_measure(mod)
        if m is None:
            rec("⑨ 真源现场体检", False,
                "现场正在被写入（两次测量不一致）⇒ 这一刻量不出稳定值 ⇒ 验不了",
                skip=True)
        else:
            print("  现场：jsonl 行数=%s ｜ 不同 seq=%s ｜ 重复 seq=%s ｜ 断链=%s 处 ｜ "
                  "seq 不连续=%s 处 ｜ sqlite 行数=%s ｜ 撕裂行=%s"
                  % (m["jsonl_lines"], m["distinct_seq"], m["dup_seq"],
                     m["chain_breaks"], m["seq_gaps"], m["sqlite_events"],
                     m.get("bad_lines", 0)), flush=True)
            eq = (m["jsonl_lines"] - m["sqlite_events"]) == m["dup_seq"]
            rec("⑨ 现场四数自洽：jsonl 行数 − sqlite 行数 == 重复 seq 条数", eq,
                "%s − %s = %s，重复 seq = %s ⇒ %s"
                % (m["jsonl_lines"], m["sqlite_events"],
                   m["jsonl_lines"] - m["sqlite_events"], m["dup_seq"],
                   "等式成立 ⇒ 坐实『少掉的那几条就是被 INSERT OR REPLACE 覆盖掉的』"
                   if eq else "★ 等式不成立 ⇒ 另有机制在丢事件（比断链更严重）"))
            rec("⑩ 断链处数必须 > 0（现场有断链就得报出来，不许报成 0）",
                m["chain_breaks"] > 0,
                "实测断链 %s 处（链尾 seq 不连续 %s 处）"
                % (m["chain_breaks"], m["seq_gaps"]))
            snap1 = _site_snapshot()
            rec("⑪ 本组**只读**现场（跑完前后文件/字节/mtime 一模一样）",
                snap0 == snap1,
                "现场 %d 个文件，前后快照%s" % (len(snap0),
                                            "一致" if snap0 == snap1 else "**不一致**"))

    # ── D 组｜交付物与边界 ────────────────────────────────────────────
    print("\n── D组｜交付物与边界 ──", flush=True)
    for pth, label in ((REPRO, "① 复现脚本 tests/_timeline并发_复现.py"),
                       (DESIGN, "② 设计稿 docs/设计_timeline并发写_20261021.md"),
                       (SELF, "③ 判据 tests/_P2_timeline并发_判据.py"),
                       (M2_PROBE, "④ M2 探针挪进库 tests/_attr_probe.py"),
                       (M2_PROBE_OUT, "⑤ M2 探针原始输出挪进库 tests/fixtures/attr_probe_out.txt")):
        rec("交付物在位：%s" % label, os.path.exists(pth),
            pth if os.path.exists(pth) else "缺：%s" % pth)

    ok, detail = _git_clean(os.path.relpath(TRUE_SOURCE, ROOT).replace("\\", "/"))
    if ok is None:
        note("真源未被改动这一项**未独立验证**（%s）—— 判据不因此转红，但也不许读成『已验』。"
             % detail)
    else:
        rec("⑥ 真源不许被本单改过（git diff HEAD 必须为空）", ok, detail)

    return finish()


if __name__ == "__main__":
    sys.exit(main())
