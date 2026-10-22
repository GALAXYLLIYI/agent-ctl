# -*- coding: utf-8 -*-
r"""tests\_P3_M18b_store连接复用_判据.py —— M18-b 刀3a：`store.py` **连接复用 + 链尾倒读**

治的病（M17-C2 地图 s4｜裁决_M17 §四·刀3）：
    ① `src\agentctl\store.py` 的 `_conn()` **每条事件**都 `sqlite3.connect()` 一次，
       并且每次都重设 `PRAGMA journal_mode=WAL` —— 而 WAL 是**写进库文件头的持久设置**
       ⇒ 每条事件都在付「建连 + 两次 PRAGMA 往返」的纯浪费。
       修法：**每线程一条长连接**（`threading.local`）＋ PRAGMA 只在**建连时**设一次。
    ② `_read_tail()` **每条事件**都把当天整份 JSONL 逐行读一遍 + 每行 `json.loads`
       （今天 6 MB ⇒ 写 N 条累计 O(N²)）。修法：从文件尾部倒读，只解析最后若干行；
       末尾半行/坏行跳过；找不到完整行再回退全量（M8「按文件顺序取最后一条」的
       正确性语义**一个都不许丢**）。

本判据只钉产品语义**没被改坏**（不是量速度）：
  A｜事件一条不少 + 字段逐条相等 + 真相源（jsonl）一条不少 + **连接复用确实生效**
  B｜哈希链不断（独立复算，不借被验的代码）＋ 帧（证据文件）可复核
  D｜证据仍可复核：`rebuild_index()` 重建后行数/hash 与 jsonl 全对得上
  E｜★ **审计同步落盘后才回执**：`append()` 返回的那一刻，用**全新连接 + 新 fd**
     必须已经读得到这条事件（不许后台线程延迟写）
  F｜**文件句柄不增长**：1000 条事件前后对比；`close()` 后回到开跑基线
  G｜多线程（每线程一条长连接）＋ `close()` 后重建 ＋ **真·进程重启**后重连续链
  S｜静态不变式：fsync / 同步提交 / 无后台批量写 / WAL 只设一次（只读源码，不跑秒表）
  H｜★ `_read_tail()` **等价性**：空目录 / 只有空文件 / 末尾半行 / 中间坏行 / 跨 3 个文件
     —— 新旧实现返回值逐例完全相同
  I｜★ 坏行/半行**不误判**：末尾半行 ⇒ 返回上一条完整记录（不是半行内容，也不是 0）
  K｜★ 规模效应（1 MB / 6 MB / 30 MB）：**只在 `--window` 且令牌文件到位时才跑**
     —— 主控明确禁止现在跑秒表，默认只打印「待窗口」，不参与绿/红/验不了计数

--inject（**必须红**，证明本判据有判别力）：
  `--inject=drop`     丢掉第 3 条事件的索引写入（模拟「事件丢一条」）
  `--inject=chain`    把第 5 条落地记录的 `prev_hash` 改成假值（模拟「链断」）
  `--inject=halfline` 把末尾半行**硬当完整记录**（模拟「读到半行当完整行」）
  ⇒ 正常跑 exit=0；注入跑必须 exit=1。

退出码：0 绿 / 1 红 / 2 验不了（**验不了不许读成绿**）

★ 不测量耗时（除 `--window` 的 K 组外）：派单 §四「测量窗口令牌」没发 ⇒
  本判据默认**没有任何秒表断言**，只断言语义与不变式
  （跑得再慢也不许红，跑得再快也不许绿）。
★ 靶场：全部落在 `tempfile` 沙箱里，跑完 `close()` + `rmtree`；系统临时目录按
  **本次唯一前缀**核对残留（不误伤别的进程的文件）；桌面零产物。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from agentctl.store import GENESIS, Store, canonical, compute_hash   # noqa: E402

GREEN, RED, SKIP, NOTES = [], [], [], []
INJECT = ""                                  # "" / "drop" / "chain" / "halfline"
WINDOW = False                               # --window：只在主控令牌到位后跑 K 组秒表
N_EVENTS = 200                               # A 组：事件条数
TAG = "agentctl_m18b_%d" % os.getpid()       # 本次沙箱唯一前缀（残留核对只认它）


# ══════════════════════════════════════════════════════════════════════
# ⑥ 报告口径（与仓库里其它判据一致：绿/红/验不了三态，验不了不许读成绿）
# ══════════════════════════════════════════════════════════════════════
def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def note(text):
    NOTES.append(text)
    print("[注] %s" % text, flush=True)


def finish():
    print("\n" + "=" * 70, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    for n in NOTES:
        print("[注] %s" % n, flush=True)
    if SKIP:
        print("结论：【验不了】—— 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】连接复用没守住语义（详见上面每一条红）", flush=True)
        if INJECT:
            print("★ 本次是注入模式（--inject=%s）⇒ **红是预期结果**：判据抓得住这个缺陷。" % INJECT,
                  flush=True)
        return 1
    print("结论：【绿】事件一条不少 / 哈希链不断 / 句柄不增长 / 返回即已可读；"
          "连接复用生效；_read_tail 尾部倒读与老实现逐例等价（半行不误判）。",
          flush=True)
    if INJECT:
        print("★ 警告：注入模式竟然全绿 ⇒ 本判据**没有判别力**，必须返工！", flush=True)
    return 0


# ══════════════════════════════════════════════════════════════════════
# 注入版（只给 --inject 自证用；**绝不**是修法，也不被产品代码引用）
# ══════════════════════════════════════════════════════════════════════
class _DropStore(Store):
    """模拟「事件丢一条」：第 3 条事件的索引写入被吞掉（真相源与索引对不上）。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._inj_n = 0
        self._inj_hit = False

    def _execute_write(self, sql, params):
        self._inj_n += 1
        if not self._inj_hit and self._inj_n == 3:
            self._inj_hit = True
            print("[注入] 丢掉第 3 条事件的索引写入（模拟『事件丢一条』）", flush=True)
            return None
        return super()._execute_write(sql, params)


class _ChainStore(Store):
    """模拟「链断」：第 5 条落地记录的 prev_hash 被改成假值。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._inj_n = 0
        self._inj_hit = False

    def _append_jsonl(self, rec):
        self._inj_n += 1
        if not self._inj_hit and self._inj_n == 5:
            self._inj_hit = True
            rec["prev_hash"] = "f" * 64
            print("[注入] 第 5 条落地记录的 prev_hash 改成假值（模拟『链断』）", flush=True)
        return super()._append_jsonl(rec)


class _HalfLineStore(Store):
    """模拟「读到半行当完整行」：只看最后一份文件的最后一行，不检查完整性。"""

    def _read_tail(self):
        files = sorted(self._jsonl_files())
        if not files:
            return 0, GENESIS
        try:
            with open(files[-1], "rb") as f:
                data = f.read()
        except OSError:
            return 0, GENESIS
        raw = data.rstrip(b"\n").rsplit(b"\n", 1)[-1].decode("utf-8", "replace")
        try:
            r = json.loads(raw)                 # 完整行 ⇒ 正常
            return int(r.get("seq", 0)), str(r.get("hash", GENESIS))
        except Exception:
            # 缺陷：半行解析不了就硬抠字段，**当成完整记录**（这正是要被抓住的 bug）
            import re
            m = re.search(r'"seq"\s*:\s*(\d+)', raw)
            h = re.search(r'"hash"\s*:\s*"([0-9a-f]*)', raw)
            print("[注入] 把末尾半行当完整记录：seq=%s hash=%s…"
                  % (m.group(1) if m else "?", (h.group(1) if h else "")[:12]), flush=True)
            return (int(m.group(1)) if m else 0,
                    h.group(1) if h else GENESIS)


def _mk_store(root):
    cls = {"drop": _DropStore, "chain": _ChainStore,
           "halfline": _HalfLineStore}.get(INJECT, Store)
    st = cls(root)
    return st


# ══════════════════════════════════════════════════════════════════════
# 尺子（与产品代码**同源**的 canonical/compute_hash，但链是**独立复算**）
# ══════════════════════════════════════════════════════════════════════
def _jsonl_files(root):
    return [os.path.join(root, f) for f in sorted(os.listdir(root))
            if f.startswith("timeline-") and f.endswith(".jsonl")]


def _jsonl_records(root):
    out = []
    for path in _jsonl_files(root):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def _chain_check(records):
    """按文件顺序独立复算哈希链。返回 (坏点列表, 链尾 hash)。"""
    prev, expect_seq, bad = GENESIS, 1, []
    for i, r in enumerate(records, 1):
        body = {k: v for k, v in r.items() if k != "hash"}
        want = compute_hash(prev, body)
        if r.get("prev_hash") != prev:
            bad.append((i, "prev_hash 不接上一条", r.get("seq")))
        if r.get("hash") != want:
            bad.append((i, "hash 与复算不符", r.get("seq")))
        if r.get("seq") != expect_seq:
            bad.append((i, "seq 不连续（应为 %d）" % expect_seq, r.get("seq")))
        prev = r.get("hash")
        expect_seq = (r.get("seq") or 0) + 1
    return bad, prev


def _last_jsonl_record(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    for line in reversed(data.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if line:
            try:
                return json.loads(line)
            except Exception:
                return None
    return None


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── M18-b 第二部分：`_read_tail` 的**老实现**（改前 store.py 的逐字副本，只当尺子）──
def _ref_read_tail(root):
    last_seq, last_hash = 0, GENESIS
    files = sorted(_jsonl_files(root))
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
                        continue        # 半行/坏行：跳过，落到上一条
                    last = r
        except OSError:
            continue
        if last is not None:
            try:
                return int(last.get("seq", 0)), str(last.get("hash", last_hash))
            except Exception:
                continue
    return last_seq, last_hash


def _rec_line(seq, prev):
    """造一条**能过链复算**的 jsonl 记录（用于等价性靶场）。"""
    body = {
        "v": 1, "seq": seq, "kind": "act",
        "t_mono": round(1000.0 + seq * 0.001, 6),
        "t_wall": "2026-10-23T00:%02d:00.000+08:00" % (seq % 60),
        "actor": "等价性", "tool": "ctl_click", "ok": True, "code": 0,
        "payload": {"i": seq}, "session": "s-等价",
        "prev_hash": prev,
    }
    body["hash"] = compute_hash(prev, body)
    return canonical(body)


def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _build_tail_cases(base):
    """造派单 §9.5-1 的 5 个状态；返回 {名字: (目录, 期望 (seq, hash))}。"""
    out = {}

    d = os.path.join(base, "tail_01_empty")
    os.makedirs(d)
    out["空目录"] = (d, (0, GENESIS))

    d = os.path.join(base, "tail_02_emptyfile")
    os.makedirs(d)
    open(os.path.join(d, "timeline-20260101.jsonl"), "wb").close()
    out["只有空文件"] = (d, (0, GENESIS))

    d = os.path.join(base, "tail_03_halfline")
    os.makedirs(d)
    r1 = _rec_line(1, GENESIS)
    h1 = json.loads(r1)["hash"]
    r2 = _rec_line(2, h1)
    h2 = json.loads(r2)["hash"]
    r3 = _rec_line(3, h2)
    h3 = json.loads(r3)["hash"]
    _write_text(os.path.join(d, "timeline-20260103.jsonl"),
                r1 + "\n" + r2 + "\n" + r3 + "\n"
                + '{"v":1,"seq":99,"hash":"deadbeef')          # 末尾半行（无换行）
    out["末尾半行"] = (d, (3, h3))

    d = os.path.join(base, "tail_04_badline")
    os.makedirs(d)
    r1 = _rec_line(1, GENESIS)
    h1 = json.loads(r1)["hash"]
    r2 = _rec_line(2, h1)
    h2 = json.loads(r2)["hash"]
    _write_text(os.path.join(d, "timeline-20260103.jsonl"),
                r1 + "\n" + "{这不是 JSON，坏行" + "\n" + r2 + "\n")
    out["中间坏行"] = (d, (2, h2))

    d = os.path.join(base, "tail_05_threefiles")
    os.makedirs(d)
    recs, prev = [], GENESIS
    for seq in range(1, 4):
        line = _rec_line(seq, prev)
        recs.append(line)
        prev = json.loads(line)["hash"]
    _write_text(os.path.join(d, "timeline-20260101.jsonl"), recs[0] + "\n" + recs[1] + "\n")
    _write_text(os.path.join(d, "timeline-20260102.jsonl"), recs[2] + "\n")
    # 最新一份文件只有坏行/空行 ⇒ 必须**回退**到 20260102（跨文件回退）
    _write_text(os.path.join(d, "timeline-20260103.jsonl"), "{broken\n\n")
    out["跨3文件（最新文件全坏）"] = (d, (3, json.loads(recs[2])["hash"]))

    return out


# ══════════════════════════════════════════════════════════════════════
# A｜事件一条不少 + 字段逐条相等 + 连接复用确实生效
# ══════════════════════════════════════════════════════════════════════
def group_a(st, root):
    bodies = []
    orig_connect = sqlite3.connect
    made = {"n": 0}

    def _counting_connect(*a, **kw):
        made["n"] += 1
        return orig_connect(*a, **kw)

    sqlite3.connect = _counting_connect          # 只统计：A 组窗口内新建了几条连接
    try:
        for i in range(N_EVENTS):
            bodies.append(st.append(
                "act", actor="判据", tool="ctl_click",
                payload={"i": i, "名": "项%d" % i, "nested": {"a": [i, i + 1]}},
                ok=(i % 3 != 0), code=(0 if i % 3 else 7), session="s-判据",
                t_mono=round(1000.0 + i * 0.001, 6),
                t_wall="2026-10-23T00:00:%02d.000+08:00" % (i % 60)))
    finally:
        sqlite3.connect = orig_connect

    rows = st.events(limit=N_EVENTS + 10)
    rec("A1 事件一条不少：写 %d 条 / SQLite 读回 %d 条" % (N_EVENTS, len(rows)),
        len(rows) == N_EVENTS, "差 %d 条" % (N_EVENTS - len(rows)))

    by_key = {(r["seq"], r["hash"]): r for r in rows}
    bad = []
    for b in bodies:
        r = by_key.get((b["seq"], b["hash"]))
        if r is None:
            bad.append((b["seq"], "缺行"))
            continue
        exp = {"kind": b["kind"], "t_mono": b["t_mono"], "t_wall": b["t_wall"],
               "actor": b["actor"], "tool": b["tool"],
               "ok": None if b["ok"] is None else int(b["ok"]),
               "code": b["code"], "payload": canonical(b["payload"]),
               "prev_hash": b["prev_hash"], "hash": b["hash"]}
        for k, v in exp.items():
            if r.get(k) != v:
                bad.append((b["seq"], k, v, r.get(k)))
    rec("A2 字段逐条相等（%d 条 × %d 字段）" % (len(bodies), 10),
        not bad, "不符 %d 处；样例 %r" % (len(bad), bad[:3]))

    recs = _jsonl_records(root)
    rec("A3 真相源一条不少：jsonl %d 行 / 写 %d 条" % (len(recs), N_EVENTS),
        len(recs) == N_EVENTS, "差 %d 行" % (N_EVENTS - len(recs)))
    diff = [i for i, (b, r) in enumerate(zip(bodies, recs), 1) if b != r]
    rec("A4 真相源字段逐条相等（返回体 == 落地行）", not diff,
        "第 %r 条起不一致（共 %d 条）" % (diff[:5], len(diff)))

    conn0 = st._conn()
    same = conn0 is st._conn()
    mode = conn0.execute("PRAGMA journal_mode").fetchone()[0]
    rec("A5 连接复用生效：%d 条事件期间新建连接 0 次（实得 %d）" % (N_EVENTS, made["n"]),
        made["n"] == 0, "本实例登记连接 %d 条" % len(st._conn_owners))
    rec("A6 同线程同一连接对象反复取回 + journal_mode 仍是持久 WAL",
        same and str(mode).lower() == "wal", "same=%s journal_mode=%s" % (same, mode))


# ══════════════════════════════════════════════════════════════════════
# B｜哈希链不断（独立复算）＋ 帧（证据文件）可复核
# ══════════════════════════════════════════════════════════════════════
def group_b(st, root):
    rel = "frames/evidence.png"
    full = os.path.join(root, "frames", "evidence.png")
    with open(full, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"M18b-evidence-payload")
    fr = st.append_frame(rel, trigger="manual", state="present", w=8, h=8,
                         t_mono=2000.0, t_wall="2026-10-23T00:01:00.000+08:00")

    recs = _jsonl_records(root)
    bad, last = _chain_check(recs)
    rec("B1 哈希链不断（独立复算 %d 条）" % len(recs), not bad,
        "断点 %d 处；样例 %r；链尾 %s" % (len(bad), bad[:3], last[:12]))
    rec("B2 链尾与 store.head 一致", st.head == (recs[-1]["seq"], recs[-1]["hash"]),
        "store.head=%r 文件链尾=%r" % (st.head, (recs[-1]["seq"], recs[-1]["hash"])))
    rec("B3 事件与帧同链（帧也在同一条 prev_hash 序列上）",
        fr["prev_hash"] == recs[-2]["hash"], "frame.prev=%s 前一条=%s"
        % (fr["prev_hash"][:12], recs[-2]["hash"][:12]))

    ok, first_bad, stats = st.verify(check_frames=True, scope="full")
    rec("B4 store.verify(scope=full, check_frames=True) 绿", ok,
        "first_bad=%r stats.events=%s stats.frames=%s"
        % (first_bad, stats.get("events"), stats.get("frames")))
    rec("B5 证据文件仍可复核（记录的 sha256 == 实算 sha256）",
        fr["sha256"] == _sha256(full) and bool(fr["sha256"]),
        "记录 %s… / 实算 %s…" % (fr["sha256"][:12], _sha256(full)[:12]))
    return fr


# ══════════════════════════════════════════════════════════════════════
# D｜证据仍可复核：从 jsonl 重建索引（sqlite 只是可重建的派生品）
# ══════════════════════════════════════════════════════════════════════
def group_d(st, root, n_events, n_frames):
    recs = _jsonl_records(root)
    n = st.rebuild_index()
    con = sqlite3.connect("file:" + st.db_path.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        cnt_ev = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        cnt_fr = con.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
        hashes_db = {r[0] for r in con.execute("SELECT hash FROM events")}
        hashes_fr = {r[0] for r in con.execute("SELECT hash FROM frames")}
    finally:
        con.close()
    hashes_jsonl = {r["hash"] for r in recs}
    rec("D1 rebuild 后 sqlite 行数 == jsonl 行数（%d / %d）" % (cnt_ev, n_events),
        n == len(recs) and cnt_ev == n_events and cnt_fr == n_frames,
        "rebuild 读 %d 行；events=%d frames=%d（应 %d/%d）"
        % (n, cnt_ev, cnt_fr, n_events, n_frames))
    rec("D2 索引里每条 hash 都能在 jsonl 里找到（OR IGNORE 不覆盖）",
        (hashes_db | hashes_fr) <= hashes_jsonl and len(hashes_db) == n_events,
        "索引独有 %d 条" % len((hashes_db | hashes_fr) - hashes_jsonl))
    bad, _ = _chain_check(_jsonl_records(root))
    rec("D3 rebuild 之后独立复算链仍全通", not bad, "断点 %d 处" % len(bad))


# ══════════════════════════════════════════════════════════════════════
# E｜★ 审计同步落盘后才回执（时序断言：返回 ⇒ 此刻已可读）
# ══════════════════════════════════════════════════════════════════════
def group_e(st, root):
    threads0 = threading.active_count()
    names0 = sorted(t.name for t in threading.enumerate())
    bad = []
    N = 20
    for i in range(N):
        b = st.append("act", actor="同步", tool="ctl_click", payload={"e": i})
        # ① **全新的** sqlite 连接（不是复用那条）此刻必须读得到 ⇒ 证明返回前已 commit
        con = sqlite3.connect("file:" + st.db_path.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            row = con.execute("SELECT seq, hash FROM events WHERE seq=? AND hash=?",
                              (b["seq"], b["hash"])).fetchone()
        finally:
            con.close()
        if row is None:
            bad.append((i, "sqlite 未提交"))
        # ② 真相源：另开一个 fd **重读文件**，最后一行必须是刚返回的那条
        last = _last_jsonl_record(st._today_jsonl())
        if not last or last.get("hash") != b["hash"]:
            bad.append((i, "jsonl 未落盘"))
        # ③ 不许有后台线程替它写（线程数/线程名都不许变）
        if threading.active_count() != threads0 or sorted(t.name for t in threading.enumerate()) != names0:
            bad.append((i, "线程集合变了（疑似后台批量写）"))
    rec("E1 append() 返回 ⇒ 事件**此刻**已可读（新连接 + 新 fd，%d 次）" % N,
        not bad, "不符 %d 处；样例 %r" % (len(bad), bad[:3]))
    rec("E2 写路径没有起后台线程（线程数 %d 前后不变）" % threads0,
        threading.active_count() == threads0 and sorted(t.name for t in threading.enumerate()) == names0,
        "现 %d 条线程" % threading.active_count())


# ══════════════════════════════════════════════════════════════════════
# H｜★ M18-b 第二部分：`_read_tail()` 等价性（5 个状态，新旧逐例对照）
# ══════════════════════════════════════════════════════════════════════
def group_h(base):
    cases = _build_tail_cases(base)
    table, mism_ref, mism_exp = [], [], []
    for name, (d, expect) in cases.items():
        st = _mk_store(d)
        try:
            new = st._read_tail()
        finally:
            st.close()
        old = _ref_read_tail(d)
        table.append((name, old, new, expect))
        if old != new:
            mism_ref.append((name, old, new))
        if new != expect:
            mism_exp.append((name, expect, new))
    for name, old, new, expect in table:
        print("      [H] %-22s 老=%r 新=%r 期望=%r %s"
              % (name, old, new, expect,
                 "✔" if (old == new == expect) else "✘"), flush=True)
    rec("H1 _read_tail 等价性：5 个状态「新实现 == 老实现（逐字副本）」",
        not mism_ref, "不符 %r" % (mism_ref,))
    rec("H2 _read_tail 等价性：5 个状态「新实现 == 人工构造的期望值」",
        not mism_exp, "不符 %r" % (mism_exp,))


# ══════════════════════════════════════════════════════════════════════
# I｜★ 坏行/半行不误判（半行 ⇒ 上一条完整记录；不是半行内容、不是 0）
# ══════════════════════════════════════════════════════════════════════
def group_i(base):
    cases = _build_tail_cases(os.path.join(base, "_i_again"))
    d, expect = cases["末尾半行"]
    st = _mk_store(d)
    try:
        got = st._read_tail()
    finally:
        st.close()
    rec("I1 末尾半行 ⇒ 返回上一条完整记录（期望 seq=%d；不是半行 seq=99、不是 0）"
        % expect[0], got == expect, "实得 %r" % (got,))

    d2, expect2 = cases["中间坏行"]
    st = _mk_store(d2)
    try:
        got2 = st._read_tail()
    finally:
        st.close()
    rec("I2 中间坏行 ⇒ 跳过坏行、仍取最后一条完整记录（期望 seq=%d）" % expect2[0],
        got2 == expect2, "实得 %r" % (got2,))


# ══════════════════════════════════════════════════════════════════════
# S｜静态不变式：fsync 未被削弱 / 同步提交 / 无后台批量写 / WAL 只设一次
# ══════════════════════════════════════════════════════════════════════
def group_s():
    path = os.path.join(SRC, "agentctl", "store.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines()

    def _body(defname):
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip().startswith("def %s(" % defname))
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].lstrip().startswith(("def ", "class "))), len(lines))
        return "\n".join(lines[start:end])

    ap = _body("_append_jsonl")
    ew = _body("_execute_write")
    s1 = "os.fsync(fd)" in ap
    s2 = "_since_fsync % self._fsync_every == 0" in ap and "_since_fsync += 1" in ap
    s3 = "with conn:" in ew and ".commit()" not in ew
    s4 = all(x not in src for x in ("threading.Thread(", "concurrent.futures", "asyncio"))
    n_wal = src.count('execute("PRAGMA journal_mode=WAL")')
    # 只数**真调用**（`conn = sqlite3.connect(...)`），不把注释/文档里的字样算进来
    import re as _re
    n_connect = len(_re.findall(r"^\s*\w+\s*=\s*sqlite3\.connect\(", src, _re.M))
    rec("S1 fsync 未被削弱：_append_jsonl 内仍有 os.fsync(fd)", s1,
        "" if s1 else "未找到 os.fsync(fd)")
    rec("S2 fsync 频率未改：`_since_fsync % _fsync_every == 0` 才 fsync，且每次 +1", s2,
        "" if s2 else "频率/计数代码缺失")
    rec("S3 写索引同步提交：_execute_write 仍靠 `with conn:` 退出即 commit（无延迟提交）", s3,
        "" if s3 else "`with conn:` 缺失或出现显式延迟 commit")
    rec("S4 没有后台批量写：store.py 无 threading.Thread / concurrent.futures / asyncio", s4,
        "" if s4 else "发现后台线程/异步痕迹")
    rec("S5 journal_mode=WAL 只在建连时设一次（源码里仅 1 处 execute）", n_wal == 1,
        "" if n_wal == 1 else "实得 %d 处" % n_wal)
    rec("S6 sqlite3.connect 真调用只出现在 _conn（不再每事件新建连接）", n_connect == 1,
        "" if n_connect == 1 else "实得 %d 处真调用" % n_connect)


# ══════════════════════════════════════════════════════════════════════
# G｜多线程 / close() 重建 / 真·进程重启后重连
# ══════════════════════════════════════════════════════════════════════
def group_g1(st, root):
    before = len(st.events(limit=100000))
    errs = []

    def worker(k):
        try:
            for i in range(50):
                st.append("act", actor="T%d" % k, tool="ctl_click", payload={"i": i})
        except Exception as e:                  # noqa: BLE001 —— 线程里的异常必须带回主线程
            errs.append("T%d: %r" % (k, e))

    ts = [threading.Thread(target=worker, args=(k,), name="m18b_w%d" % k) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    after = len(st.events(limit=100000))
    bad, _ = _chain_check(_jsonl_records(root))
    rec("G1 4 线程 × 50 条：一条不少（%d → %d）" % (before, after),
        not errs and after == before + 200, "异常 %r；差 %d" % (errs[:3], after - before - 200))
    rec("G1b 多线程后哈希链仍不断（独立复算）", not bad, "断点 %d 处" % len(bad))
    rec("G1c 每线程各自一条长连接（登记 %d 条：主线程 + 4）" % len(st._conn_owners),
        len(st._conn_owners) == 5, "实得 %d" % len(st._conn_owners))


def group_g2(st, root):
    n_before_close = len(st.events(limit=100000))
    # ① 连接被 close() 关掉之后，同一个 Store 必须能重建连接继续写（不是「关机了」）
    st.close()
    b = st.append("act", actor="close后", payload={"reconnect": 1})
    ok, first_bad, stats = st.verify(check_frames=True, scope="full")
    rec("G2 连接 close() 后重建并续链（seq=%s，verify=%s）" % (b["seq"], ok), ok,
        "first_bad=%r" % (first_bad,))
    rec("G2b close() 后事件仍一条不少（%d → %d）"
        % (n_before_close, len(st.events(limit=100000))),
        len(st.events(limit=100000)) == n_before_close + 1)

    # ② **真·进程重启**：子进程（新解释器）写 3 条后退出，父进程重开同一个 timeline
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "from agentctl.store import Store\n"
        "s = Store(r'%s')\n"
        "for i in range(3):\n"
        "    s.append('act', actor='子进程', tool='ctl_click', payload={'p': i})\n"
        "s.close()\n"
        "print('child-ok')\n" % (SRC, root))
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       env=env, capture_output=True, text=True, timeout=180)
    rec("G2c 子进程（新解释器）写 3 条并正常退出", p.returncode == 0,
        "rc=%s stderr=%s" % (p.returncode, (p.stderr or "")[-300:]))

    st2 = _mk_store(root)
    b2 = st2.append("act", actor="父进程", payload={"after_restart": 1})
    recs = _jsonl_records(root)
    bad, _ = _chain_check(recs)
    prev_rec = recs[-2] if len(recs) > 1 else None
    rec("G2d 进程重启后重连：新事件接在旧链尾上、链不断",
        not bad and prev_rec is not None and b2["prev_hash"] == prev_rec["hash"],
        "断点 %d 处；prev 期望 %s 实得 %s"
        % (len(bad), (prev_rec or {}).get("hash", "")[:12], b2["prev_hash"][:12]))
    return st2


# ══════════════════════════════════════════════════════════════════════
# F｜文件句柄不增长（1000 条事件前后对比）＋ close() 收口
# ══════════════════════════════════════════════════════════════════════
#: ★ 为什么放进**独立子进程**量：本进程里跑 A~G 已经用掉了 tempfile / 惰性 DLL 加载 /
#:   子进程创建等一次性内核对象 ⇒ 句柄计数会被这些**与 store 无关**的噪声污染
#:   （实测：同一条长连接开/关是 ±4，但 `tempfile.mkdtemp` 首次调用会一次多出 7 个）。
#:   子进程里只做「建连 → 写 1000 条 → 关」这一件事，尺子才干净。
FD_CHILD = r'''
import ctypes, gc, json, os, shutil, sqlite3, sys, tempfile
sys.path.insert(0, "__SRC__")
from agentctl.store import Store


def hc():
    if os.name == "nt":
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetProcessHandleCount.restype = wintypes.BOOL
        n = wintypes.DWORD(0)
        if not k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(n)):
            raise OSError(ctypes.get_last_error(), "GetProcessHandleCount")
        return int(n.value)
    return len(os.listdir("/proc/self/fd"))


tag = "agentctl_m18b_fd_" + str(os.getpid())
base = tempfile.mkdtemp(prefix=tag + "_")
root = os.path.join(base, "timeline")
out = {"ok": False}
try:
    # 先把「首次建 sqlite 连接」的一次性开销吃掉，基线才干净
    warm = sqlite3.connect(os.path.join(base, "warm.db"))
    warm.execute("PRAGMA journal_mode=WAL")
    warm.close()
    gc.collect()
    out["before_store"] = hc()

    st = Store(root)
    gc.collect()
    out["after_store"] = hc()

    for i in range(1000):
        st.append("act", actor="句柄", tool="ctl_click", payload={"i": i})
    gc.collect()
    out["after_1000"] = hc()

    ok, bad, stats = st.verify(check_frames=True, scope="full")
    out["verify_ok"] = bool(ok)
    out["verify_bad"] = bad
    out["events"] = st.head[0]

    st.close()
    gc.collect()
    out["after_close"] = hc()
    del st
    gc.collect()
    out["after_free"] = hc()        # ★ 连 Store 对象一起释放后的句柄数（threading 原语也归零）

    n = 0
    for p in sorted(os.listdir(root)):
        if p.startswith("timeline-") and p.endswith(".jsonl"):
            with open(os.path.join(root, p), encoding="utf-8") as fh:
                n += sum(1 for line in fh if line.strip())
    out["jsonl_lines"] = n
    out["db_still_there"] = os.path.exists(os.path.join(root, "timeline.db"))
    out["ok"] = True
finally:
    shutil.rmtree(base, ignore_errors=True)
    out["residue"] = [x for x in os.listdir(tempfile.gettempdir()) if x.startswith(tag)]
    print("@@FD@@" + json.dumps(out, ensure_ascii=False))
'''


def group_f(st):
    import subprocess as _sp
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["PYTHONIOENCODING"] = "utf-8"
    p = _sp.run([sys.executable, "-X", "utf8", "-c", FD_CHILD.replace("__SRC__", SRC)],
                env=env, capture_output=True, text=True, timeout=600)
    line = ""
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("@@FD@@"):
            line = ln[len("@@FD@@"):]
    if p.returncode != 0 or not line:
        rec("F0 句柄子进程跑完", False,
            "rc=%s stderr=%s" % (p.returncode, (p.stderr or "")[-500:]), skip=True)
        return
    try:
        d = json.loads(line)
    except Exception:
        rec("F0 句柄子进程输出可解析", False, line[:300], skip=True)
        return
    if not d.get("ok"):
        rec("F0 句柄子进程正常收尾", False, json.dumps(d, ensure_ascii=False), skip=True)
        return

    rec("F1 1000 条事件期间句柄不增长（建连后 %d → 写满 1000 条 %d）"
        % (d["after_store"], d["after_1000"]),
        d["after_1000"] <= d["after_store"] + 5,
        "超建连后基线 %d 个" % (d["after_1000"] - d["after_store"]))
    rec("F2 1000 条事件一条不少（events=%d / jsonl=%d）"
        % (d["events"], d["jsonl_lines"]),
        d["events"] == 1000 and d["jsonl_lines"] == 1000,
        "head_seq=%r" % d["events"])
    rec("F3 1000 条压完哈希链仍全通（子进程 verify full）",
        bool(d["verify_ok"]), "first_bad=%r" % (d.get("verify_bad"),))
    rec("F4 close()+释放 Store 后句柄回到建连前基线（%d → close %d → 释放 %d）"
        % (d["before_store"], d["after_close"], d["after_free"]),
        d["after_free"] <= d["before_store"] + 5,
        "超基线 %d 个" % (d["after_free"] - d["before_store"]))
    rec("F5 close() 之后证据文件仍在（长连接不靠「关掉才刷」）",
        bool(d["db_still_there"]) and d["jsonl_lines"] == 1000)
    rec("F6 句柄子进程的靶场无残留", d.get("residue") == [], "残留 %r" % (d.get("residue"),))


# ══════════════════════════════════════════════════════════════════════
# C｜临时目录不泄漏（沙箱残留 + sqlite 临时文件 + 系统临时目录）
# ══════════════════════════════════════════════════════════════════════
ALLOWED_ROOT = {".timeline-write.xlock", "frames", "timeline.db",
                "timeline.db-wal", "timeline.db-shm", "anchors.jsonl"}


def group_c(root, base):
    names = sorted(os.listdir(root)) if os.path.isdir(root) else []
    bad = [n for n in names if n not in ALLOWED_ROOT and not n.startswith("timeline-")]
    rec("C1 沙箱里没有残留临时文件（sqlite -journal / 半成品；实有 %r）" % names, not bad,
        "多出 %r" % bad)
    shutil.rmtree(base, ignore_errors=True)
    rec("C2 临时目录不泄漏：沙箱已删除（%s）" % base, not os.path.exists(base),
        "仍存在")
    left = [n for n in os.listdir(tempfile.gettempdir()) if n.startswith(TAG)]
    rec("C3 系统临时目录里没有本次前缀（%s）的残留" % TAG, not left, "残留 %r" % left)


# ══════════════════════════════════════════════════════════════════════
# K｜★ 规模效应（1 MB / 6 MB / 30 MB）—— **只在 --window + 令牌到位时跑**
#    ★ 主控明令：MiniMax C1 正在跑秒表，现在跑 perf_counter 会互相污染。
#      所以默认只打印「待窗口」，**不放进绿/红/验不了三态**（否则绿跑会变成 exit=2）。
# ══════════════════════════════════════════════════════════════════════
TOKEN_PATH = os.path.join(ROOT, "work", "_M18_动作面瘦身", "窗口令牌_M18b.txt")


def _pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def _prefill_today(root, target_bytes):
    """把「当天」jsonl 预填到约 target_bytes（记录都过链复算，末尾是完整行）。"""
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "timeline-%s.jsonl" % _dt.date.today().strftime("%Y%m%d"))
    prev, seq, size = GENESIS, 1, 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        while size < target_bytes:
            line = _rec_line(seq, prev) + "\n"
            f.write(line)
            size += len(line.encode("utf-8"))
            prev = json.loads(line)["hash"]
            seq += 1
    return path, seq - 1


def group_k(base):
    if not WINDOW:
        print("[待窗口] K1 规模效应 1 MB / 6 MB / 30 MB：未跑"
              "（未加 --window；主控禁止现在跑秒表）", flush=True)
        print("[待窗口] K2 _read_tail 单次耗时 n=20 p50/p95：未跑（同上）", flush=True)
        return
    if not os.path.exists(TOKEN_PATH):
        print("[待窗口] 令牌文件不存在：%s ⇒ 不跑秒表" % TOKEN_PATH, flush=True)
        return

    import time

    rows = []
    for label, target in (("1MB", 1 << 20), ("6MB", 6 << 20), ("30MB", 30 << 20)):
        d = os.path.join(base, "scale_" + label)
        path, n_pre = _prefill_today(d, target)
        st = _mk_store(d)
        try:
            n = 20
            evt, tail = [], []
            for i in range(n):
                t0 = time.perf_counter()
                st.append("act", actor="规模", tool="ctl_click", payload={"i": i})
                evt.append((time.perf_counter() - t0) * 1000.0)
            for _ in range(n):
                t0 = time.perf_counter()
                st._read_tail()
                tail.append((time.perf_counter() - t0) * 1000.0)
        finally:
            st.close()
        size = os.path.getsize(path)
        rows.append((label, n_pre, size, _pct(evt, 0.50), _pct(evt, 0.95),
                     _pct(tail, 0.50), _pct(tail, 0.95)))
        print("      [K] %-5s 预填 %d 行 / %.2f MB：每条 append p50=%.3f ms p95=%.3f ms；"
              "_read_tail p50=%.3f ms p95=%.3f ms（n=20）"
              % (label, n_pre, size / 1048576.0, rows[-1][3], rows[-1][4],
                 rows[-1][5], rows[-1][6]), flush=True)

    ratio = rows[-1][3] / rows[0][3] if rows[0][3] > 0 else float("inf")
    rec("K1 规模效应：每条事件写入耗时 30MB / 1MB = %.2f×（新实现应基本不随文件大小增长；"
        "老实现约 30×）" % ratio, ratio < 3.0, "三档 %r" % (rows,))
    slow = rows[-1][5] / rows[0][5] if rows[0][5] > 0 else float("inf")
    rec("K2 _read_tail 单次耗时 30MB / 1MB = %.2f×（应基本不变）" % slow,
        slow < 3.0, "三档 %r" % (rows,))


# ══════════════════════════════════════════════════════════════════════
def main(argv):
    global INJECT, WINDOW
    for a in argv[1:]:
        if a == "--inject":
            INJECT = "drop"
        elif a.startswith("--inject="):
            INJECT = a.split("=", 1)[1]
        elif a == "--window":
            WINDOW = True
    if INJECT not in ("", "drop", "chain", "halfline"):
        print("用法：python tests\\_P3_M18b_store连接复用_判据.py "
              "[--inject=drop|chain|halfline] [--window]", flush=True)
        return 2

    print("== M18-b 刀3a：store.py 连接复用 + _read_tail 尾部倒读判据"
          "（注入模式=%r；秒表=%s）==" % (INJECT or "无", "开" if WINDOW else "关（待窗口）"),
          flush=True)

    base = tempfile.mkdtemp(prefix=TAG + "_")
    root = os.path.join(base, "timeline")
    note("靶场：%s（tempfile 沙箱，跑完 close()+rmtree）" % base)
    st = None
    try:
        st = _mk_store(root)
        group_a(st, root)
        fr = group_b(st, root)
        group_d(st, root, N_EVENTS, 1)
        group_e(st, root)
        group_h(base)                   # M18-b 第二部分：_read_tail 等价性（5 例）
        group_i(base)                   # 半行/坏行不误判
        group_s()                       # 静态不变式（fsync / 同步提交 / 无后台写）
        group_g1(st, root)
        st2 = group_g2(st, root)
        st = st2
        group_f(st)                     # 句柄：在独立子进程里量（见 FD_CHILD 注释）
        group_k(base)                   # 规模效应：默认「待窗口」，--window 才跑秒表
        st.close()                      # 主沙箱收口：C 组要删目录
        group_c(root, base)
    except Exception:                          # noqa: BLE001 —— 判据本身跑不完也必须如实报红
        traceback.print_exc()
        rec("运行时异常：判据没跑完", False, "见上方 traceback")
    finally:
        try:
            if st is not None:
                st.close()
        except Exception:
            pass
        shutil.rmtree(base, ignore_errors=True)
        left = [n for n in os.listdir(tempfile.gettempdir()) if n.startswith(TAG)]
        if left:                               # 异常路径也要收干净（铁律 13）
            note("收尾仍残留：%r" % left)
    return finish()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
