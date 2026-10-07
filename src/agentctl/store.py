# -*- coding: utf-8 -*-
r"""store.py —— 时间线的【真相源】与哈希链

设计依据（详细设计 §4 / §22 / §24.5）：
  · **jsonl 是真相源**（追加式纯文本，任何工具都能独立复算）；
  · **SQLite 只是可重建的索引**（坏了能从 jsonl 重建，反过来不行）；
  · **哈希链**：hash = sha256(prev_hash ‖ 规范化字段)；任何一处改动都会让后续全不匹配；
  · ★ 诚实边界：本机链**只防"事后局部改"**，防不了"有磁盘权限者整体重算" ——
    那需要**离开本机的锚点**（另见 anchors）。这句必须写进文档，不许含糊成"防篡改"。
  · 事件与帧**共链**（同一个 prev_hash 序列），这样"某次动作"与"它的前后帧"天然可对齐。

用法：
    from agentctl.store import Store
    st = Store(r"...\timeline")
    e = st.append("act", actor="DSH", tool="ctl_click", payload={"hwnd": 1234, "name": "保存"})
    st.verify()          # -> (ok, first_bad)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sqlite3
import threading
import time

SCHEMA_VERSION = 1
GENESIS = "0" * 64


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def canonical(obj) -> str:
    """规范化序列化 —— 链的计算必须与平台/字典序无关，否则跨机复算会不一致。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash: str, body: dict) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(b"\n")
    h.update(canonical(body).encode("utf-8"))
    return h.hexdigest()


class Store:
    def __init__(self, root: str, *, fsync_every: int = 1):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.frames_dir = os.path.join(root, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self.db_path = os.path.join(root, "timeline.db")
        self.anchor_path = os.path.join(root, "anchors.jsonl")
        self._lock = threading.RLock()
        self._fsync_every = max(1, fsync_every)
        self._since_fsync = 0
        self._init_db()
        self._head = self._load_head()

    # ────────────────────────── 内部 ──────────────────────────
    def _today_jsonl(self) -> str:
        return os.path.join(self.root, f"timeline-{_dt.date.today():%Y%m%d}.jsonl")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")   # §4.3：批量提交，不赌单条 fsync 的延迟
        return c

    def _init_db(self):
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS events(
                  seq      INTEGER PRIMARY KEY,
                  kind     TEXT NOT NULL,
                  t_mono   REAL NOT NULL,
                  t_wall   TEXT NOT NULL,
                  actor    TEXT, tool TEXT,
                  ok       INTEGER, code INTEGER,
                  payload  TEXT,
                  prev_hash TEXT, hash TEXT NOT NULL,
                  session  TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_ev_twall ON events(t_wall);
                CREATE INDEX IF NOT EXISTS ix_ev_kind  ON events(kind);
                CREATE INDEX IF NOT EXISTS ix_ev_frame ON events(tool);

                CREATE TABLE IF NOT EXISTS frames(
                  seq      INTEGER PRIMARY KEY,
                  t_mono   REAL NOT NULL,
                  t_wall   TEXT NOT NULL,
                  path     TEXT, sha256 TEXT, bytes INTEGER,
                  w INTEGER, h INTEGER,
                  trigger  TEXT, state TEXT,
                  prev_hash TEXT, hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_fr_twall ON frames(t_wall);
                CREATE INDEX IF NOT EXISTS ix_fr_trig  ON frames(trigger);

                CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
                """
            )
            c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version',?)",
                      (str(SCHEMA_VERSION),))

    def _load_head(self) -> tuple[int, str]:
        """从 jsonl 读链尾（**以 jsonl 为准**，不是 SQLite）。"""
        last_seq, last_hash = 0, GENESIS
        for path in sorted(self._jsonl_files()):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        if int(r.get("seq", 0)) > last_seq:
                            last_seq, last_hash = int(r["seq"]), r.get("hash", last_hash)
            except OSError:
                continue
        return last_seq, last_hash

    def _jsonl_files(self):
        return [os.path.join(self.root, f) for f in os.listdir(self.root)
                if f.startswith("timeline-") and f.endswith(".jsonl")]

    def _append_jsonl(self, rec: dict):
        path = self._today_jsonl()
        line = canonical(rec) + "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            if self._since_fsync % self._fsync_every == 0:
                f.flush()
                os.fsync(f.fileno())
        self._since_fsync += 1

    # ────────────────────────── 对外 ──────────────────────────
    @property
    def head(self) -> tuple[int, str]:
        return self._head

    def append(self, kind: str, *, actor: str = "", tool: str = "",
               payload: dict | None = None, ok: bool | None = None,
               code: int | None = None, session: str = "",
               t_mono: float | None = None, t_wall: str | None = None) -> dict:
        """追加一条事件。**这是唯一的写入口** —— 绕过它写库＝链断。"""
        with self._lock:
            seq = self._head[0] + 1
            prev = self._head[1]
            body = {
                "v": SCHEMA_VERSION,
                "seq": seq,
                "kind": kind,
                "t_mono": round(t_mono if t_mono is not None else time.perf_counter(), 6),
                "t_wall": t_wall or _now_iso(),
                "actor": actor, "tool": tool,
                "ok": ok, "code": code,
                "payload": payload or {},
                "session": session,
                "prev_hash": prev,
            }
            body["hash"] = compute_hash(prev, {k: v for k, v in body.items() if k != "hash"})
            self._append_jsonl(body)
            with self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO events(seq,kind,t_mono,t_wall,actor,tool,ok,code,"
                    "payload,prev_hash,hash,session) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (seq, kind, body["t_mono"], body["t_wall"], actor, tool,
                     None if ok is None else int(ok), code,
                     canonical(payload or {}), prev, body["hash"], session))
            self._head = (seq, body["hash"])
            return body

    def append_frame(self, path: str, *, trigger: str = "manual", state: str = "present",
                     t_mono: float | None = None, t_wall: str | None = None,
                     w: int = 0, h: int = 0) -> dict:
        """登记一个帧。**内容哈希进链** —— 这样"这张图是不是后来换的"可以被验。

        ⚠️ 2026-10-02 验收测试抓到的 bug：传入相对路径时，原来直接拿它去 os.path.exists()，
           而当时的工作目录不是时间线目录 → 哈希算成空串 → 整条基线就是红的。
           **修法：先按 self.root 解析成绝对路径再算哈希。**
        """
        with self._lock:
            seq = self._head[0] + 1
            prev = self._head[1]
            full = path if os.path.isabs(path) else os.path.join(self.root, path)
            sha, nbytes = _sha256_file(full) if os.path.exists(full) else ("", 0)
            body = {
                "v": SCHEMA_VERSION, "seq": seq, "kind": "frame",
                "t_mono": round(t_mono if t_mono is not None else time.perf_counter(), 6),
                "t_wall": t_wall or _now_iso(),
                "path": os.path.relpath(full, self.root).replace("\\", "/"),
                "sha256": sha, "bytes": nbytes, "w": w, "h": h,
                "trigger": trigger, "state": state,
                "prev_hash": prev,
            }
            body["hash"] = compute_hash(prev, {k: v for k, v in body.items() if k != "hash"})
            self._append_jsonl(body)
            with self._conn() as c:
                c.execute("INSERT OR REPLACE INTO frames(seq,t_mono,t_wall,path,sha256,bytes,w,h,"
                          "trigger,state,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (seq, body["t_mono"], body["t_wall"], body["path"], sha, nbytes, w, h,
                           trigger, state, prev, body["hash"]))
            self._head = (seq, body["hash"])
            return body

    # ── 查询（走 SQLite 索引；坏了可 rebuild_index 重建）──
    def events(self, *, limit: int = 100, kind: str | None = None,
               since_wall: str | None = None) -> list[dict]:
        q = "SELECT seq,kind,t_mono,t_wall,actor,tool,ok,code,payload,prev_hash,hash FROM events"
        w, a = [], []
        if kind:
            w.append("kind=?"); a.append(kind)
        if since_wall:
            w.append("t_wall>=?"); a.append(since_wall)
        if w:
            q += " WHERE " + " AND ".join(w)
        q += " ORDER BY seq DESC LIMIT ?"; a.append(limit)
        with self._conn() as c:
            return [dict(zip(("seq", "kind", "t_mono", "t_wall", "actor", "tool",
                              "ok", "code", "payload", "prev_hash", "hash"), r))
                    for r in c.execute(q, a)]

    def frames(self, *, limit: int = 2000, since_wall: str | None = None,
               until_wall: str | None = None, trigger: str | None = None,
               include_restricted: bool = False) -> list[dict]:
        """查帧（供出片用）。默认**不返回受限帧**（敏感窗口区间的帧）。"""
        q = ("SELECT seq,t_mono,t_wall,path,sha256,bytes,w,h,trigger,state "
             "FROM frames")
        w, a = [], []
        if since_wall:
            w.append("t_wall>=?"); a.append(since_wall)
        if until_wall:
            w.append("t_wall<=?"); a.append(until_wall)
        if trigger:
            w.append("trigger=?"); a.append(trigger)
        if not include_restricted:
            w.append("(state IS NULL OR state!='restricted')")
        if w:
            q += " WHERE " + " AND ".join(w)
        q += " ORDER BY seq ASC LIMIT ?"; a.append(limit)
        with self._conn() as c:
            return [dict(zip(("seq", "t_mono", "t_wall", "path", "sha256", "bytes",
                              "w", "h", "trigger", "state"), r))
                    for r in c.execute(q, a)]

    def frame_abs(self, rel_path: str) -> str:
        return rel_path if os.path.isabs(rel_path) else os.path.join(self.root, rel_path)

    def rebuild_index(self) -> int:
        """从 jsonl 重建 SQLite 索引 —— 用来证明"jsonl 才是真相源"。"""
        with self._conn() as c:
            c.execute("DELETE FROM events"); c.execute("DELETE FROM frames")
        n = 0
        for path in sorted(self._jsonl_files()):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    with self._conn() as c:
                        if r.get("kind") == "frame":
                            c.execute("INSERT OR REPLACE INTO frames(seq,t_mono,t_wall,path,sha256,"
                                      "bytes,w,h,trigger,state,prev_hash,hash) "
                                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (r["seq"], r["t_mono"], r["t_wall"], r.get("path"), r.get("sha256"),
                                       r.get("bytes"), r.get("w"), r.get("h"), r.get("trigger"),
                                       r.get("state"), r.get("prev_hash"), r["hash"]))
                        else:
                            c.execute("INSERT OR REPLACE INTO events(seq,kind,t_mono,t_wall,actor,tool,"
                                      "ok,code,payload,prev_hash,hash,session) "
                                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (r["seq"], r["kind"], r["t_mono"], r["t_wall"], r.get("actor", ""),
                                       r.get("tool", ""), r.get("ok"), r.get("code"),
                                       canonical(r.get("payload") or {}), r.get("prev_hash"), r["hash"],
                                       r.get("session", "")))
                    n += 1
        self._head = self._load_head()
        return n

    # ── 就地验证（★ 正式验证请用独立验证器 verify\agentctl_verify.py）──
    def verify(self, *, check_frames: bool = True) -> tuple[bool, dict | None, dict]:
        """返回 (ok, first_bad, stats)。**这里只是自检**——同一份代码写和验不算独立。"""
        prev = GENESIS
        expect_seq = 1
        n_ev = n_fr = 0
        for path in sorted(self._jsonl_files()):
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception as e:
                        return False, {"file": os.path.basename(path), "line": lineno,
                                       "reason": f"JSON 解析失败: {e}"}, {}
                    got = r.get("hash")
                    body = {k: v for k, v in r.items() if k != "hash"}
                    want = compute_hash(prev, body)
                    if r.get("prev_hash") != prev or got != want:
                        return False, {"file": os.path.basename(path), "line": lineno,
                                       "seq": r.get("seq"), "kind": r.get("kind"),
                                       "reason": "链断（prev_hash 或 hash 不符）",
                                       "expected_hash": want, "actual_hash": got,
                                       "expected_prev": prev, "actual_prev": r.get("prev_hash")}, {}
                    if r.get("seq") != expect_seq:
                        return False, {"file": os.path.basename(path), "line": lineno,
                                       "seq": r.get("seq"), "reason": f"序号不连续（应为 {expect_seq}）"}, {}
                    if check_frames and r.get("kind") == "frame":
                        n_fr += 1
                        fp = os.path.join(self.root, r.get("path") or "")
                        if r.get("state") == "present":
                            if not os.path.exists(fp):
                                return False, {"seq": r["seq"], "reason": "帧文件缺失", "path": fp}, {}
                            sha, _ = _sha256_file(fp)
                            if sha != r.get("sha256"):
                                return False, {"seq": r["seq"], "reason": "帧内容被改（sha256 不符）",
                                               "path": fp, "expected_hash": r.get("sha256"),
                                               "actual_hash": sha}, {}
                    else:
                        n_ev += 1
                    prev, expect_seq = got, expect_seq + 1
        return True, None, {"events": n_ev, "frames": n_fr, "last_hash": prev}

    # ── 带外锚点（对抗"整链重算"的唯一手段）──
    def write_anchor(self, note: str = "") -> dict:
        ok, bad, stats = self.verify(check_frames=False)
        rec = {"t_wall": _now_iso(), "t_mono": round(time.perf_counter(), 6),
               "head_seq": self._head[0], "head_hash": self._head[1],
               "chain_ok": ok, "stats": stats, "note": note}
        with open(self.anchor_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(canonical(rec) + "\n")
            f.flush(); os.fsync(f.fileno())
        return rec


def _sha256_file(path: str, chunk: int = 1 << 20) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b); n += len(b)
    return h.hexdigest(), n
