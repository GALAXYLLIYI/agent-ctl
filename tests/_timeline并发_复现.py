# -*- coding: utf-8 -*-
r"""tests\_timeline并发_复现.py —— timeline **多进程并发写** 的复现脚本（M3 · 2026-10-21）

治的病（见 docs\设计_timeline并发写_20261021.md）：
    真源 src\agentctl\store.py 的写路径是「**进程内**锁 + 构造时读一次链尾」：
      · store.py 的 `threading.RLock` 只挡**同进程**的线程，跨进程完全无效；
      · `_load_head()` 只在 `Store.__init__` 里调一次 ⇒ 每个进程拿着**自己的**链尾快照；
      · 真源的应用形态恰恰是**短命进程**（每次 MCP/HTTP 调用起一个进程），
        「读链尾 → 干活（截图/OCR/UI 自动化，几十到几百毫秒）→ 追加」这个窗口里，
        别的进程早就把链尾推走了 ⇒ **seq 撞车 + 哈希链分叉**。
      · 索引表 `events.seq` 是 PRIMARY KEY + `INSERT OR REPLACE`
        ⇒ 撞上的两条里**后写的覆盖先写的** ⇒ SQLite 索引静默少条。

本脚本做的事（**只读真源，不改真源一个字节**）：
    起 N 个**真子进程**，每个进程独立构造一个写者、各写 M 条事件，
    然后把四个数摆在一起对照：
        ① jsonl 行数   ② 写入总数   ③ 断链处数   ④ SQLite 行数

★ 为什么用**真子进程**而不是线程：
    线程共享 `Store._lock`，撞不出这个病；只有**独立进程**才有各自的 `_head` 快照。
    这不是模拟 —— 真源线上就是这么跑的。

用法：
    python -X utf8 tests\_timeline并发_复现.py                 # 真源（默认）⇒ 今天必然红
    python -X utf8 tests\_timeline并发_复现.py --writer=参考   # 参考实现 ⇒ 绿（证明可修）
    python -X utf8 tests\_timeline并发_复现.py --keep           # 留沙箱，自己翻 jsonl
    python -X utf8 tests\_timeline并发_复现.py --json          # 机器可读输出

退出码：0 = 三条契约同时成立（绿）｜1 = 有契约不成立（红）｜2 = 环境不满足（验不了）

★ 沙箱隔离：本脚本**只在临时目录**里造 timeline，**绝不碰** `work\timeline\`（真源现场）。
  真源的现场体检是**另一条**（tests\_P2_timeline并发_判据.py 的 C 组），只读不写。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agentctl import store as tl_store            # noqa: E402  真源（**只读复用**）

GENESIS = tl_store.GENESIS

# ══════════════════════════════════════════════════════════════════════
# ① 跨进程文件锁（修法 A 的零件）
# ══════════════════════════════════════════════════════════════════════
class FileLock:
    """跨进程排他锁 —— **OS 级**锁，进程一死锁自动释放（不留死锁文件）。

    Windows 用 msvcrt.locking(LK_NBLCK) 锁 1 个字节：
      · 抢不到就抛 OSError ⇒ 调用方自旋重试；
      · ★ 进程被 kill / 崩溃 ⇒ 句柄随进程关闭 ⇒ 锁**自动**释放（这是选它
        不用「O_EXCL 锁文件」的唯一理由：后者会留下死锁文件）。
    POSIX 用 fcntl.flock（同样的语义）。

    ★ 为什么不用 threading.Lock / RLock：那两个只在本进程内有效。
      本项目的病正是「多进程」——进程内锁再多也没用。
    """

    def __init__(self, path: str, timeout: float = 60.0, poll: float = 0.002):
        self.path = path
        self.timeout = timeout
        self.poll = poll
        self._fh = None

    def _try_lock(self) -> bool:
        self._fh = open(self.path, "a+b")
        self._fh.seek(0)
        try:
            self._fh.write(b"\0")     # 保证至少有 1 个字节可锁
            self._fh.flush()
            self._fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def _unlock(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            if self._try_lock():
                return self
            if time.time() > deadline:
                raise TimeoutError("等锁超时（%ss）：%s" % (self.timeout, self.path))
            time.sleep(self.poll)

    def __exit__(self, *exc):
        self._unlock()
        return False


# ══════════════════════════════════════════════════════════════════════
# ② 参考实现（修法 A：跨进程文件锁 + 锁内重读链尾）
# ══════════════════════════════════════════════════════════════════════
class RefStore(tl_store.Store):
    """**修法 A 的可执行原型** —— 与真源只差两件事：

      1. 追加前拿**跨进程文件锁**（真源只有进程内 RLock）；
      2. **锁内重读链尾**（真源只在构造时读一次 ⇒ 拿到的是快照，会过期）。

    ★ 记账逻辑（记录格式、哈希算法、jsonl 写法、SQLite 写入）**全部继承真源**，
      一行都没抄 —— 所以本原型的链与真源的链**逐字同构**，可直接对拍。
      生产修法应当把这两步**塞进 store.append 里**（单一真源），而不是另写一个类。

    ★ 这是**设计稿 §3 修法 A** 的落地形态，不是最终提交形态。
    """

    LOCK_NAME = ".timeline-write.lock"

    def __init__(self, root: str, *, fsync_every: int = 1):
        super().__init__(root, fsync_every=fsync_every)
        self.lock_path = os.path.join(root, self.LOCK_NAME)

    # ── 读链尾（按**文件顺序**的链尾，不是 max(seq)）──
    def read_tail(self) -> tuple[int, str]:
        """返回 (链尾 seq, 链尾 hash)。

        ★ 关键细节：真源的 `_load_head()` 取的是 **max(seq)** 那一行的 hash。
          一旦链分过叉，max(seq) 未必是文件里的最后一行 ⇒ 真源会从**错误的位置**续链。
          这里按**文件顺序的链尾**取（= 最后一份文件里的最后一条有效记录），
          才是「链」的真正尾巴。
        """
        files = sorted(self._jsonl_files())
        for path in reversed(files):
            last = None
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue          # 半行/坏行：跳过，落到上一条
                        last = r
            except OSError:
                continue
            if last is not None:
                try:
                    return int(last.get("seq", 0)), str(last.get("hash", GENESIS))
                except Exception:
                    continue
        return 0, GENESIS

    def append(self, kind: str, **kw) -> dict:
        """跨进程串行化的一笔追加。"""
        with FileLock(self.lock_path):
            self._head = self.read_tail()       # ★ 锁内重读 ⇒ 拿到的是**此刻**的链尾
            return super().append(kind, **kw)   # ★ 记账逻辑全部走真源


class BrokenRefStore(RefStore):
    """**注入缺陷版**：拿掉跨进程锁（其余与 RefStore 完全一样）。

    ⇒ 它复现的正是真源今天的病：锁没了，两个进程各自拿着**过期**的链尾快照，
      写进同一个 seq、挂到同一条 prev_hash 上 ⇒ 链分叉 + 索引覆盖丢条。

    ★ 这个类**只**给判据用（--inject 自证 + 判别力反证），
      绝不许被当成修法。
    """

    def append(self, kind: str, **kw) -> dict:
        self._head = self.read_tail()           # ★ 没有锁 ⇒ 读到的随时可能已经过期
        return super(RefStore, self).append(kind, **kw)


WRITERS = {
    "真源": tl_store.Store,        # src\agentctl\store.py 的 Store（**不改**）
    "参考": RefStore,              # 修法 A 原型（跨进程文件锁 + 锁内重读链尾）
    "缺陷": BrokenRefStore,        # 注入缺陷版（无跨进程锁）—— 只给判据用
}


# ══════════════════════════════════════════════════════════════════════
# ③ 测量（**真源与参考实现共用同一把尺子**）
# ══════════════════════════════════════════════════════════════════════
def measure(root: str) -> dict:
    """量一个 timeline 目录，产出四项对照所需的全部数字。

    ★ 这把尺子对**真源**和**参考实现**是同一份 —— 否则「参考实现能过」
      可能只是尺子偏了。判据的 A 组（参考）与 B 组（真源）都调它。
    """
    files = sorted(glob.glob(os.path.join(root, "timeline-*.jsonl")))
    lines = records = 0
    bad_lines = 0
    breaks = []
    seq_gaps = []
    seq_seen = {}
    dup_seq = 0
    prev = GENESIS
    expect_seq = 1

    for path in files:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                lines += 1
                try:
                    r = json.loads(line)
                except Exception:
                    records += 1          # 坏行：占位但不可解析
                    bad_lines += 1        # ★ 并发追加把记录**撕裂/拼接**了
                    continue
                records += 1
                sq = r.get("seq")
                if sq in seq_seen:
                    dup_seq += 1
                else:
                    seq_seen[sq] = (os.path.basename(path), lineno)
                got = r.get("hash")
                body = {k: v for k, v in r.items() if k != "hash"}
                want = tl_store.compute_hash(prev, body)
                if r.get("prev_hash") != prev or got != want:
                    breaks.append({"file": os.path.basename(path), "line": lineno,
                                   "seq": sq, "kind": r.get("kind")})
                if sq != expect_seq:
                    seq_gaps.append({"file": os.path.basename(path), "line": lineno,
                                     "seq": sq, "expect": expect_seq})
                prev = got
                expect_seq = (sq + 1) if isinstance(sq, int) else expect_seq + 1

    db = os.path.join(root, "timeline.db")
    n_events = n_frames = None
    try:
        con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
        n_events = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        try:
            n_frames = con.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
        except sqlite3.Error:
            n_frames = 0
        con.close()
    except Exception:
        pass

    return {
        "jsonl_files": [os.path.basename(p) for p in files],
        "jsonl_lines": lines,
        "jsonl_records": records,
        "bad_lines": bad_lines,
        "distinct_seq": len(seq_seen),
        "dup_seq": dup_seq,
        "chain_breaks": len(breaks),
        "seq_gaps": len(seq_gaps),
        "breaks": breaks[:20],
        "seq_gap_samples": seq_gaps[:20],
        "sqlite_events": n_events,
        "sqlite_frames": n_frames,
    }


def contracts(m: dict, written_total: int) -> dict:
    """三条契约的判定。

    (a) jsonl 行数 == 写入总数 **且没有撕裂行**
        —— 只比行数不够：一条被两个进程**拼起来/撕开**的坏行也会占一行，
           行数对上了但事件已经没了 ⇒ 必须另查「不可解析行数 == 0」。
    (b) 哈希链全通（且 seq 连续）
    (c) sqlite 行数 == jsonl 行数
    """
    a_ok = (m["jsonl_lines"] == written_total and m.get("bad_lines", 0) == 0)
    b_ok = (m["chain_breaks"] == 0 and m["seq_gaps"] == 0)
    c_ok = (m["sqlite_events"] is not None and m["sqlite_events"] == m["jsonl_lines"])
    return {
        "a": {"ok": a_ok, "want": written_total, "got": m["jsonl_lines"],
              "text": "jsonl 行数 == 写入总数（撕裂行 %s）" % m.get("bad_lines", 0)},
        "b": {"ok": b_ok, "want": 0, "got": m["chain_breaks"],
              "text": "哈希链全通（断链 %s 处 / seq 不连续 %s 处）"
                      % (m["chain_breaks"], m["seq_gaps"])},
        "c": {"ok": c_ok, "want": m["jsonl_lines"], "got": m["sqlite_events"],
              "text": "sqlite 行数 == jsonl 行数"},
        "all_ok": a_ok and b_ok and c_ok,
    }


# ══════════════════════════════════════════════════════════════════════
# ④ 并发跑（真子进程 + 起跑栅栏）
# ══════════════════════════════════════════════════════════════════════
def _barrier_wait(barrier_dir: str, n: int, timeout: float = 120.0) -> bool:
    """栅栏：等 N 个进程都「准备好了」再一起开跑。

    ★ 为什么必须有栅栏：真源的病是 **TOCTOU**（读链尾 → 干活 → 追加）。
      没有栅栏时，子进程可能一个跑得快、一个跑得慢，**碰巧**不撞上 ⇒ 复现不稳定。
      栅栏把所有进程钉在「都读到了同一个旧链尾」的那一刻 ⇒ 撞车**必然**发生。
      —— 这不是造假：线上正是「大家一起干活的窗口」里撞的。
    """
    os.makedirs(barrier_dir, exist_ok=True)
    with open(os.path.join(barrier_dir, "ready-%d" % os.getpid()), "w", encoding="utf-8") as f:
        f.write("1")
    deadline = time.time() + timeout
    while True:
        try:
            names = os.listdir(barrier_dir)
        except OSError:
            names = []
        if sum(1 for x in names if x.startswith("ready-")) >= n:
            return True
        if time.time() > deadline:
            return False
        time.sleep(0.005)


def _worker(root: str, n: int, processes: int, each: int, writer: str, barrier: str) -> dict:
    """子进程体：构造写者 → 过栅栏 → 各写 `each` 条。

    ★ `n` = 本进程序号（只用于留痕）；`processes` = **总进程数**（栅栏要用它）。
      早先写错了：把序号当成了总进程数 ⇒ 栅栏退化成「阶梯」（第 k 个进程等 k 个就绪）
      ⇒ 撞车变成部分发生、复现不稳定。**栅栏必须是等齐 `processes` 个才一起开跑。**
    """
    cls = WRITERS[writer]
    rec = {"attempted": each, "returned": 0, "errors": 0, "error_samples": [],
           "pid": os.getpid(), "writer": writer, "index": n}
    # 构造写者（真源在这一步读一次链尾快照）
    store_obj = None
    last_err = None
    for _ in range(60):
        try:
            store_obj = cls(root)
            break
        except Exception as e:               # 库被锁住：起步阶段重试（不掩盖追加期的问题）
            last_err = "%s: %s" % (type(e).__name__, e)
            time.sleep(0.05)
    if store_obj is None:
        rec["errors"] = each
        rec["error_samples"].append("构造写者失败：%s" % last_err)
        return rec

    _barrier_wait(barrier, processes)           # ★ 一起开跑（等齐 processes 个）

    for i in range(each):
        try:
            store_obj.append("repro", actor="repro", tool="ctl_repro",
                             payload={"pid": os.getpid(), "i": i})
            rec["returned"] += 1
        except Exception as e:
            rec["errors"] += 1
            if len(rec["error_samples"]) < 5:
                rec["error_samples"].append("%s: %s" % (type(e).__name__, e))
    return rec


def run_scenario(root: str, *, processes: int = 6, each: int = 40,
                 writer: str = "真源", keep: bool = False, timeout: float = 300.0) -> dict:
    """起 `processes` 个子进程各写 `each` 条，返回测量结果 + 写入总数。

    ★ 沙箱必须先清空 —— 复现的前提是「从空链开始」。
    """
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    barrier = os.path.join(root, "_barrier")
    res_dir = os.path.join(root, "_results")
    os.makedirs(res_dir, exist_ok=True)

    # 先把库建好（真源的 Store.__init__ 会建表）—— 建表不属于被测的并发写路径
    tl_store.Store(root)

    procs = []
    for i in range(processes):
        cmd = [sys.executable, "-X", "utf8", os.path.abspath(__file__),
               "--worker", "--root", root, "--n", str(i), "--each", str(each),
               "--processes", str(processes),
               "--writer", writer, "--barrier", barrier, "--result",
               os.path.join(res_dir, "r%02d.json" % i)]
        procs.append((i, subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True,
                                          encoding="utf-8", errors="replace")))
    dead = time.time() + timeout
    for i, p in procs:
        while p.poll() is None and time.time() < dead:
            time.sleep(0.02)
        if p.poll() is None:
            p.kill()
            p.wait()

    worker_reports = []
    for i in range(processes):
        path = os.path.join(res_dir, "r%02d.json" % i)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    worker_reports.append(json.load(f))
            except Exception:
                pass
    # 兜底：子进程崩了没写结果文件 ⇒ 它写的条数只能按 0 记（少算也算丢）
    returned = sum(w.get("returned", 0) for w in worker_reports)
    attempted = processes * each
    errors = sum(w.get("errors", 0) for w in worker_reports)

    m = measure(root)
    written_total = returned
    res = {
        "writer": writer,
        "processes": processes,
        "each": each,
        "root": root,
        "attempted": attempted,
        "written_total": written_total,
        "append_errors": errors,
        "worker_reports": worker_reports,
        "measure": m,
        "contracts": contracts(m, written_total),
    }
    if not keep:
        shutil.rmtree(root, ignore_errors=True)
    return res


# ══════════════════════════════════════════════════════════════════════
# ⑤ 输出
# ══════════════════════════════════════════════════════════════════════
def _print_report(res: dict, verbose: bool = True) -> None:
    m = res["measure"]
    c = res["contracts"]
    bar = "═" * 68
    print(bar)
    print("timeline 并发写 · 复现脚本（M3）")
    print(bar)
    print("  写者     : %s" % res["writer"])
    print("  场景     : %d 个真子进程 × 每进程 %d 条 = %d 次 append"
          % (res["processes"], res["each"], res["attempted"]))
    print("  沙箱     : %s" % res["root"])
    print()
    print("── 四项对照 ─────────────────────────────────────────────")
    print("  ① jsonl 行数     = %s" % m["jsonl_lines"])
    print("  ② 写入总数       = %s   （尝试 %s / 成功返回 %s / 追加异常 %s）"
          % (res["written_total"], res["attempted"], res["written_total"],
             res["append_errors"]))
    print("  ③ 断链处数       = %s   （另：seq 不连续 %s 处 / 重复 seq %s 条 / 不同 seq %s 个）"
          % (m["chain_breaks"], m["seq_gaps"], m["dup_seq"], m["distinct_seq"]))
    print("  ④ sqlite 行数    = %s" % m["sqlite_events"])
    print("  ★ 撕裂行         = %s   （不可解析的 jsonl 行 ⇒ 记录被并发写**拼坏/撕坏**）"
          % m.get("bad_lines", 0))
    print()
    print("── 三条契约 ─────────────────────────────────────────────")
    for k in ("a", "b", "c"):
        it = c[k]
        print("  (%s) %-42s %s   期望=%s 实得=%s"
              % (k, it["text"], "绿" if it["ok"] else "红", it["want"], it["got"]))
    print()
    if verbose:
        if m["breaks"]:
            print("  断链样例（前 5）：")
            for b in m["breaks"][:5]:
                print("    %s line=%s seq=%s kind=%s"
                      % (b["file"], b["line"], b["seq"], b["kind"]))
        errs = []
        for w in res["worker_reports"]:
            errs.extend(w.get("error_samples") or [])
        if errs:
            print("  追加异常样例（前 5）：")
            for e in errs[:5]:
                print("    %s" % e)
    print()
    if c["all_ok"]:
        print("结论：【绿】并发写不丢事件、哈希链不断、SQLite 与 jsonl 一致。")
    else:
        bad = [k for k in ("a", "b", "c") if not c[k]["ok"]]
        print("结论：【红】并发写下契约 %s 不成立 —— "
              "（真源 store.py 未修：进程内锁 + 构造时读链尾 ⇒ seq 撞车 / 链分叉 / 索引覆盖丢条）"
              % "、".join(bad))
    print(bar)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="timeline 多进程并发写复现脚本")
    ap.add_argument("--processes", type=int, default=6, help="并发进程数 N（默认 6）")
    ap.add_argument("--each", type=int, default=40, help="每进程写多少条 M（默认 40）")
    ap.add_argument("--writer", default="真源", choices=list(WRITERS.keys()),
                    help="写者：真源（默认）/ 参考（修法 A 原型）/ 缺陷（注入用）")
    ap.add_argument("--sandbox", default=None, help="沙箱目录（默认：临时目录）")
    ap.add_argument("--keep", action="store_true", help="跑完保留沙箱")
    ap.add_argument("--json", action="store_true", help="只打机器可读 JSON")
    ap.add_argument("--quiet", action="store_true", help="不打详细报告")
    # ── 子进程体参数（内部用）──
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--root", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--n", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--barrier", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--result", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    # ── 子进程体 ──
    if args.worker:
        rec = _worker(args.root, args.n, args.processes, args.each, args.writer, args.barrier)
        try:
            with open(args.result, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
        except Exception:
            pass
        return 0

    sandbox = args.sandbox or tempfile.mkdtemp(prefix="agentctl_timeline_repro_")
    try:
        res = run_scenario(sandbox, processes=args.processes, each=args.each,
                           writer=args.writer, keep=args.keep)
    except Exception as e:
        print("结论：【验不了】复现脚本跑不起来：%s: %s" % (type(e).__name__, e), flush=True)
        return 2

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif not args.quiet:
        _print_report(res)
    return 0 if res["contracts"]["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
