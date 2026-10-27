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
    e = st.append("act", actor="本地 agent", tool="ctl_click", payload={"hwnd": 1234, "name": "保存"})
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

#: M8（2026-10-22）：索引 schema 版本。
#: v2 = `seq` **不再**是 PRIMARY KEY（它是「并发写的化石」，重复是证据不是垃圾），
#:      改成普通列 + `UNIQUE(seq, hash)` ⇒ 同 seq 不同 hash 的两条**都装得下**。
#:      v1 = 老的 `seq INTEGER PRIMARY KEY` + `INSERT OR REPLACE` ⇒ 后写覆盖先写 ⇒ 索引少条。
INDEX_SCHEMA_VERSION = 2

#: 跨进程锁文件名（放在 timeline 根目录；**不是**历史文件，不参与任何统计）。
#: ★ 为什么**不叫** `.timeline-write.lock`：判据自带的参考实现
#:   `tests/_timeline并发_复现.py` 的 `RefStore` 用的就是那个名字，
#:   而它 `with FileLock(...)` 之后又调 `super().append()`（**真源的 append**）
#:   ⇒ 同进程对**同一个文件**套了两把锁 ⇒ Windows 的字节区间锁按句柄计，
#:   第二把拿不到 ⇒ **死锁**（实测：6 个 worker 全卡死，锁文件长到 80 KB）。
#:   ⇒ 本真源用**自己的**锁文件 ⇒ 两把锁锁的是**不同文件** ⇒ 不冲突。
LOCK_NAME = ".timeline-write.xlock"

#: 跨进程锁的**进程内登记表**（模块级 ⇒ 跨多个 Store 实例共享）。
#: 结构：{lock_path: [持有计数, 持有者线程 id]}
#: ★ 为什么必须**模块级**：同一个进程里可能有多个 Store 实例（不同 root 或同 root），
#:   嵌套调用时必须认得出「这把锁本进程已经拿着」⇒ 直接放行（可重入）。
#: ★ 为什么必须**可重入**：产品本就该可重入（将来嵌套调用是常态），
#:   不可重入的锁在嵌套时 = 自己等自己 = 死锁。
_LOCK_REGISTRY = {}
_LOCK_REGISTRY_GUARD = threading.Lock()

#: M18-b（2026-10-23）：**sqlite 连接复用**。
#: ★ 病（M17-C2 地图 s4）：老写法**每条事件**都 `sqlite3.connect()` 一次，
#:   并且每次都重设 `PRAGMA journal_mode=WAL` —— 连接建立 + 两次 PRAGMA 往返 × 事件数。
#:   而 `journal_mode=WAL` 是**写进库文件头的持久设置** ⇒ 每条事件重设纯属多余往返。
#: ★ 修法：**每线程一条长连接**（`threading.local`）。
#:   为什么线程私有、不是「全局一条 + 一把锁」：sqlite3 连接默认 `check_same_thread=True`，
#:   跨线程共用要么报错、要么把事务状态搅在一起；而读路径（`events()/frames()`）
#:   **不在写锁里**，共用一条连接就得给读也加锁 ⇒ 引入新的锁竞争。
#:   线程私有 = 零锁竞争，且每条连接仍由 sqlite 自己保证写原子（跨进程照旧靠 `CrossProcessLock`）。
#: ★ 连接失效（显式 `close()`、跨 fork 的 pid 变了、`database is locked`）⇒ **丢弃重建成新连接**，
#:   绝不把「没写成」吞成「写成了」（unknown 永不当成功）。
#: ★ **不碰 fsync / 不做后台批量写**：这条刀只省「建连 + 重设 PRAGMA」，
#:   `append()` 返回 = jsonl 已 fsync、索引已 commit（审计同步落盘语义一字不改）。
_DB_LOCK_RETRIES = 3            #: 遇到 `database is locked` / `busy` 的最多重试次数（有上限，不无限等）
_DB_LOCK_BACKOFF = 0.05         #: 第 n 次重试前 sleep n×0.05 秒


class CrossProcessLock:
    """跨进程排他锁 —— **OS 级**锁，进程一死锁自动释放（不留死锁文件）。

    Windows 用 `msvcrt.locking(LK_NBLCK)` 锁 1 个字节；POSIX 用 `fcntl.flock`。

    ★ 为什么不用 `threading.Lock/RLock`：那两个**只在本进程内**有效。
      本项目的病正是「多进程」—— 进程内锁再多也没用
      （设计稿 §3 缺陷 1：真源 `store.py` 只有 `threading.RLock`）。
    ★ 为什么不用「O_EXCL 锁文件」：进程被 kill 时会**留下死锁文件**（死等）。
      OS 级锁随句柄关闭**自动**释放。
    ★ **可重入**（2026-10-22 返工）：同进程同路径再次进入 ⇒ 计数+1 ⇒ **直接放行**
      （不再做 msvcrt 锁 —— Windows 的字节区间锁按**句柄**计，同进程第二个句柄
      锁同一区间会失败 ⇒ 不做重入就是死锁）。退出时计数-1，**归零才真正解锁**。
    ★ **有上限**（2026-10-22 返工）：拿不到锁就**报错**，绝不无限等
      （铁律：unknown 永不当成功 ⇒ 等不到锁必须**失败**，不能悄悄当成「写成了」）。
      超时消息写清「等锁超时」，并带上路径与等待时长。
    ★ 锁文件**只当锁载体**：只在文件为空时补 1 个字节（保证有区间可锁），
      **绝不往里写数据**（旧写法每次重试都 append 一个字节 ⇒ 锁文件能长到 80 KB）。
    """

    #: 等锁的默认上限（秒）与重试间隔（秒）
    DEFAULT_TIMEOUT = 5.0
    DEFAULT_POLL = 0.05

    def __init__(self, path: str, timeout: float | None = None,
                 poll: float | None = None):
        self.path = path
        self.timeout = self.DEFAULT_TIMEOUT if timeout is None else float(timeout)
        self.poll = self.DEFAULT_POLL if poll is None else float(poll)
        self._fh = None
        self._reentered = False

    # ── 可重入判定（进程内）──
    def _enter_reentrant(self) -> bool:
        """若本进程已持有这把锁 ⇒ 计数+1 并直接放行。"""
        with _LOCK_REGISTRY_GUARD:
            ent = _LOCK_REGISTRY.get(self.path)
            if ent is None:
                _LOCK_REGISTRY[self.path] = [1, threading.get_ident()]
                return False          # ⇒ 真正去加 OS 级锁
            ent[0] += 1
            return True               # ⇒ 已持有，直接放行

    def _exit_reentrant(self) -> bool:
        """计数-1；归零 ⇒ 真正解锁。返回 True 表示「该真正解锁了」。"""
        with _LOCK_REGISTRY_GUARD:
            ent = _LOCK_REGISTRY.get(self.path)
            if ent is None:
                return True
            ent[0] -= 1
            if ent[0] > 0:
                return False
            _LOCK_REGISTRY.pop(self.path, None)
            return True

    def _try_lock(self) -> bool:
        self._fh = open(self.path, "a+b")
        try:
            # ★ 锁文件**只当锁载体**：只在**空文件**时补 1 个字节，
            #   绝不每次重试都 append（旧写法 ⇒ 锁文件长到 80 KB）。
            self._fh.seek(0, os.SEEK_END)
            if self._fh.tell() == 0:
                self._fh.write(b"\0")
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
            try:
                self._fh.close()
            except OSError:
                pass
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
        # ★ 可重入：本进程已持有这把锁 ⇒ 计数+1 ⇒ 直接放行（不再 msvcrt 锁）。
        #   Windows 的字节区间锁按**句柄**计 ⇒ 同进程第二个句柄锁同一区间会失败
        #   ⇒ 不做重入 = 嵌套调用必然死锁。
        if self._enter_reentrant():
            self._reentered = True
            return self
        self._reentered = False
        deadline = time.time() + self.timeout
        waited = 0.0
        while True:
            if self._try_lock():
                return self
            if time.time() > deadline:
                # ★ 拿不到锁 ⇒ **报错**，绝不无限等（铁律：unknown 永不当成功）。
                #   也把登记清掉，否则这把锁在本进程里会被永久标成「已持有」。
                with _LOCK_REGISTRY_GUARD:
                    _LOCK_REGISTRY.pop(self.path, None)
                raise TimeoutError(
                    "等锁超时：等 %.1f 秒仍拿不到跨进程写锁（%s）⇒ 本次写入**放弃**"
                    "（unknown 永不当成功）；若是别的进程正卡在临界区，稍后重试。"
                    % (self.timeout, self.path))
            time.sleep(self.poll)
            waited += self.poll

    def __exit__(self, *exc):
        # 退出：计数-1；**归零才真正解锁**。
        should_unlock = self._exit_reentrant()
        if should_unlock:
            self._unlock()
        return False


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
        self._lock = threading.RLock()          # 进程内（保留：同进程多线程）
        # ★ M8：跨进程排他锁 —— 缺陷 1/2/3 全部由它兜住（设计稿 §4 修法 A）。
        self._xlock = CrossProcessLock(os.path.join(root, LOCK_NAME))
        self._fsync_every = max(1, fsync_every)
        self._since_fsync = 0
        # ★ M18-b：每线程一条长连接（见模块顶部说明）。
        self._conn_tl = threading.local()      # 本线程的连接句柄
        self._conn_guard = threading.Lock()    # 只保护 _conn_owners 登记表
        self._conn_owners = []                 # 本实例开过的所有连接（close() 统一收口）
        self._conn_gen = 0                     # close() 后 +1 ⇒ 旧连接一律作废
        self._init_db()
        self._head = self._load_head()
        # ★ M8：当前纪元（epoch）。0 = 旧链（迁移前的老历史）；≥1 = 迁移后的新段。
        #   纪元**不另存状态文件** —— 它由 anchors.jsonl 里的「段锚」推导：
        #   有 epoch=0 的段锚 ⇒ 当前纪元 = 1；没有 ⇒ 当前纪元 = 0（全新链）。
        self._epoch = self._current_epoch()

    # ────────────────────────── 内部 ──────────────────────────
    def _today_jsonl(self) -> str:
        return os.path.join(self.root, f"timeline-{_dt.date.today():%Y%m%d}.jsonl")

    def _conn(self) -> sqlite3.Connection:
        """取**本线程**的长连接（没有/失效就建一条）。M18-b：不再每条事件新建。

        ★ `journal_mode` / `synchronous` **只在建连时**设一次：
          · `journal_mode=WAL` 是写进库文件头的**持久设置** ⇒ 每条事件重设 = 纯浪费；
          · `synchronous` 是**每连接**的设置 ⇒ 建连时设对即可（§4.3 语义不变）。
        ★ 连接的三条失效路径：① 被本实例 `close()` 关掉（gen 变了）
          ② 跨 fork（pid 变了）③ 被 `_execute_write` 判为坏连接丢掉
          ⇒ ①② 在这里立刻重建；③ 下一条事件重建。
        ★ `check_same_thread=False` **不是**为了跨线程共用：（本类只用线程私有的那一条）
          而是为了让 `close()/__del__` 能在别的线程里安全收口 —— 否则线程死了，
          连接没人关 ⇒ **文件句柄泄漏**（本刀判据第 6 条要断言的正是句柄不增长）。
        """
        pid = os.getpid()
        ent = getattr(self._conn_tl, "ent", None)
        if ent is not None:
            conn, gen, conn_pid = ent
            if conn is not None and gen == self._conn_gen and conn_pid == pid:
                return conn
            self._conn_tl.ent = None            # 失效：摘掉（交给 _drop_conn 关）
            self._drop_conn(conn)
        conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")     # ← 建连时一次（持久设置，不必每条事件重设）
        conn.execute("PRAGMA synchronous=NORMAL")   # ← 每连接一次：批量提交，不赌单条 fsync 的延迟
        self._conn_tl.ent = (conn, self._conn_gen, pid)
        with self._conn_guard:
            self._conn_owners.append(conn)
        return conn

    def _drop_conn(self, conn) -> None:
        """把一条（可能已坏的）连接从登记表摘掉并关掉；下次 `_conn()` 自然重建。"""
        if conn is None:
            return
        ent = getattr(self._conn_tl, "ent", None)
        if ent is not None and ent[0] is conn:
            self._conn_tl.ent = None
        with self._conn_guard:
            try:
                self._conn_owners.remove(conn)
            except ValueError:
                pass
        try:
            conn.close()
        except Exception:
            pass

    def _execute_write(self, sql: str, params: tuple) -> None:
        """写索引一条 —— **提交后才返回**（同步落盘；没有后台线程、没有批量刷）。

        ★ 与老写法 `with self._conn() as c: c.execute(...)` **完全同口径**：
          `with conn:` 成功即 commit、异常即 rollback ⇒ 「返回 = 此刻已可读」。
        ★ 重试只针对**可恢复**的两种：`database is locked`/`busy`（别的进程正在提交）
          与「连接已被关闭」（本实例 `close()` 或跨线程收口）。
          其它 `sqlite3.Error` 一律原样抛出 —— unknown 永不当成功。
        ★ 重试有上限（`_DB_LOCK_RETRIES`），且期间**持着跨进程写锁**，
          不会出现「两个写者都以为对方会重试」的活锁。
        """
        last = None
        for attempt in range(_DB_LOCK_RETRIES):
            conn = self._conn()
            try:
                with conn:
                    conn.execute(sql, params)
                return
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" not in msg and "busy" not in msg:
                    raise
                last = e
                if attempt + 1 >= _DB_LOCK_RETRIES:
                    self._drop_conn(conn)   # 连接可能已坏 ⇒ 丢掉，下条事件重建
                    raise
                time.sleep(_DB_LOCK_BACKOFF * (attempt + 1))
            except sqlite3.ProgrammingError as e:
                if "closed" not in str(e).lower():
                    raise
                last = e
                self._drop_conn(conn)
                if attempt + 1 >= _DB_LOCK_RETRIES:
                    raise
        raise last      # pragma: no cover —— 循环内已 raise，兜底保持「不静默成功」

    # ── M18-b：长连接的显式收口 ──
    def close(self) -> None:
        """关闭本实例开过的所有 sqlite 连接。

        ★ 只在「没有别的线程正在写」时调用（`__del__` 除外）。
        ★ 关掉之后本实例**仍可继续用**：`_conn()` 会重建新连接（gen+1 ⇒ 旧的全作废）。
        ★ 长连接的代价就是**文件句柄常驻** ⇒ 谁创建谁收口：进程退出、测试收尾、
          要删/搬 timeline 目录之前，都必须先 `close()`（Windows 上尤其：句柄没关删不掉）。
        """
        guard = getattr(self, "_conn_guard", None)
        if guard is None:
            return
        with guard:
            conns = self._conn_owners[:]
            self._conn_owners.clear()
            self._conn_gen += 1
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
        self._conn_tl = threading.local()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _init_db(self):
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS events(
                  seq      INTEGER NOT NULL,
                  kind     TEXT NOT NULL,
                  t_mono   REAL NOT NULL,
                  t_wall   TEXT NOT NULL,
                  actor    TEXT, tool TEXT,
                  ok       INTEGER, code INTEGER,
                  payload  TEXT,
                  prev_hash TEXT, hash TEXT NOT NULL,
                  session  TEXT,
                  UNIQUE(seq, hash)
                );
                CREATE INDEX IF NOT EXISTS ix_ev_twall ON events(t_wall);
                CREATE INDEX IF NOT EXISTS ix_ev_kind  ON events(kind);
                CREATE INDEX IF NOT EXISTS ix_ev_frame ON events(tool);

                CREATE TABLE IF NOT EXISTS frames(
                  seq      INTEGER NOT NULL,
                  t_mono   REAL NOT NULL,
                  t_wall   TEXT NOT NULL,
                  path     TEXT, sha256 TEXT, bytes INTEGER,
                  w INTEGER, h INTEGER,
                  trigger  TEXT, state TEXT,
                  prev_hash TEXT, hash TEXT NOT NULL,
                  UNIQUE(seq, hash)
                );
                CREATE INDEX IF NOT EXISTS ix_fr_twall ON frames(t_wall);
                CREATE INDEX IF NOT EXISTS ix_fr_trig  ON frames(trigger);

                CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
                """
            )
            c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version',?)",
                      (str(SCHEMA_VERSION),))
            self._migrate_index(c)

    # ── M8：索引 schema v1 → v2（seq 去主键，改 UNIQUE(seq, hash)）──
    def _migrate_index(self, c) -> bool:
        """把老库（`seq INTEGER PRIMARY KEY`）迁到 v2（seq 普通列 + UNIQUE(seq,hash)）。

        ★ 为什么必须迁（设计稿 §3 缺陷 3 / §6.6 候选乙）：
          `seq` 是主键 ⇒ SQLite **装不下**两条同 seq 的记录 ⇒ 索引**结构性地**少条。
          而「重复 seq」本身是**并发写的化石** —— 它是**证据，不是垃圾**
          ⇒ 真源 jsonl 是只追加的事实记录，**不许因为索引装不下就改历史**
          ⇒ 改索引的 schema，让它**装得下**。
        ★ 迁移的是**索引**（可重建的派生品），**不碰 jsonl**（历史）一个字节。
        ★ 幂等：迁完 `seq` 不再是主键 ⇒ 再调用直接返回 False。
        """
        try:
            cols = {r[1]: r for r in c.execute("PRAGMA table_info(events)")}
            seqcol = cols.get("seq")
            if not seqcol or not int(seqcol[5] or 0):        # pk=0 ⇒ 已是 v2
                c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('index_schema_version',?)",
                          (str(INDEX_SCHEMA_VERSION),))
                c.commit()
                return False
            c.executescript(
                """
                CREATE TABLE events_v2(
                  seq INTEGER NOT NULL, kind TEXT NOT NULL, t_mono REAL NOT NULL,
                  t_wall TEXT NOT NULL, actor TEXT, tool TEXT, ok INTEGER, code INTEGER,
                  payload TEXT, prev_hash TEXT, hash TEXT NOT NULL, session TEXT,
                  UNIQUE(seq, hash));
                INSERT OR IGNORE INTO events_v2
                  SELECT seq,kind,t_mono,t_wall,actor,tool,ok,code,payload,
                         prev_hash,hash,session FROM events;
                DROP TABLE events;
                ALTER TABLE events_v2 RENAME TO events;
                CREATE INDEX IF NOT EXISTS ix_ev_twall ON events(t_wall);
                CREATE INDEX IF NOT EXISTS ix_ev_kind  ON events(kind);
                CREATE INDEX IF NOT EXISTS ix_ev_frame ON events(tool);
                """)
            fcols = {r[1]: r for r in c.execute("PRAGMA table_info(frames)")}
            fseq = fcols.get("seq")
            if fseq and int(fseq[5] or 0):
                c.executescript(
                    """
                    CREATE TABLE frames_v2(
                      seq INTEGER NOT NULL, t_mono REAL NOT NULL, t_wall TEXT NOT NULL,
                      path TEXT, sha256 TEXT, bytes INTEGER, w INTEGER, h INTEGER,
                      trigger TEXT, state TEXT, prev_hash TEXT, hash TEXT NOT NULL,
                      UNIQUE(seq, hash));
                    INSERT OR IGNORE INTO frames_v2
                      SELECT seq,t_mono,t_wall,path,sha256,bytes,w,h,trigger,state,
                             prev_hash,hash FROM frames;
                    DROP TABLE frames;
                    ALTER TABLE frames_v2 RENAME TO frames;
                    CREATE INDEX IF NOT EXISTS ix_fr_twall ON frames(t_wall);
                    CREATE INDEX IF NOT EXISTS ix_fr_trig  ON frames(trigger);
                    """)
            c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('index_schema_version',?)",
                      (str(INDEX_SCHEMA_VERSION),))
            c.commit()
            return True
        except sqlite3.Error:
            return False

    #: M18-b 第二部分：从文件尾部倒读的**首块**大小（字节）。
    #: 只决定「先读多少」；找不到完整行就把块大小翻倍，直到读全文件。
    _TAIL_CHUNK = 64 * 1024

    @staticmethod
    def _last_record_fullscan(path: str):
        """老实现的**逐行读全文**——M18-b 的**回退路径**，语义是 M8 的正确性保证。

        ★ 只在倒读路径「读完全文件仍拿不到」或「半行里带非法 UTF-8」时才走这里。
        ★ 与老 `_read_tail` 的每行处理**逐字一致**：strip → 空行跳过 → json.loads
          失败跳过 → 取最后一条能解析的（不校验字段、不校验链）。
        ★ 不许删：文件末尾没有换行、整份文件是一行超长坏行等极端情况下，
          只有全量读才能给出与老实现完全相同的结果。
        """
        last = None
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except Exception:
                    continue            # 半行/坏行：跳过，落到上一条
        return last

    @classmethod
    def _last_record_in_file(cls, path: str):
        """取**文件顺序上的最后一条完整记录**（M18-b 第二部分：尾部倒读）。

        与老 `_read_tail` 的逐行全读**语义逐字等价**，只改读法：
          · 从 EOF 往前按 `_TAIL_CHUNK` 分块读，只解析尾部若干行 ⇒ 耗时不再随
            「文件里有多少条历史记录」线性增长（写 N 条不再累计读 N 遍全文）。
          · **完整性检查**：末尾可能是半行（进程被杀 / 写入被打断）⇒ 该行
            `json.loads` 失败 ⇒ 跳过，落到**上一条完整记录**（绝不把半行当完整行）。
          · **回退路径保留**：尾部一块都解析不出来 ⇒ 块大小翻倍直到覆盖全文；
            读完全文仍没有 ⇒ `_last_record_fullscan` 全量再扫一遍；
            半行带非法 UTF-8 ⇒ 也走 `_last_record_fullscan`（与老实现同样会抛错，
            不静默把坏字节当成「没有记录」）。
        """
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size <= 0:
            return None
        limit = min(size, cls._TAIL_CHUNK)
        while True:
            try:
                # read() 到 EOF（不是 read(limit)）：万一读的瞬间文件又长了，
                # 多出来的尾巴也在 buf 里 ⇒ 与老实现「一直读到 EOF」同口径。
                with open(path, "rb") as f:
                    f.seek(size - limit)
                    buf = f.read()
            except OSError:
                return None
            # 老实现用文本模式（universal newlines）⇒ \r\n / \r 都算换行；这里等价替换。
            lines = buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
            if limit < size:
                # 块是从文件中间切进来的 ⇒ 第一段是「半行」的尾巴，丢掉；
                # 下一轮块翻倍后会重新从更靠前的位置读，不会漏行。
                lines = lines[1:]
            for raw in reversed(lines):
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    return cls._last_record_fullscan(path)
                text = text.strip()
                if not text:
                    continue
                try:
                    return json.loads(text)
                except Exception:
                    continue            # 半行/坏行：跳过，落到上一条
            if limit >= size:
                # 整个文件倒读完了仍没有能解析的行 ⇒ 老实现兜底（逐字等价）。
                return cls._last_record_fullscan(path)
            limit = min(size, limit * 2)

    def _read_tail(self) -> tuple[int, str]:
        """读**文件顺序上的链尾**（不是 max(seq) 那一行）。

        ★ M8（设计稿 §5 第 3 条）：老口径 `_load_head()` 取的是 **max(seq)** 那一行的
          hash。一旦链分过叉（老链有 24 处断点），max(seq) 未必是文件里的最后一行
          ⇒ 真源会**从错误的位置续链**。
          这里按**文件顺序**取（最后一份文件里的最后一条有效记录）⇒ 才是链的真正尾巴。
        ★ M18-b 第二部分：**不再每事件全量重读 JSONL** —— 每份文件只从尾部倒读，
          找最后一条完整记录（见 `_last_record_in_file`）；跨文件回退、坏行/半行跳过、
          非 dict / seq 不可转 int 时继续找更早的文件、`GENESIS` 兜底，全部保持原语义。
        ★ 这是**读**链尾；写入方必须在**锁内**调它（见 append）。
        """
        last_seq, last_hash = 0, GENESIS
        for path in reversed(sorted(self._jsonl_files())):
            last = self._last_record_in_file(path)
            if last is None:
                continue
            try:
                return int(last.get("seq", 0)), str(last.get("hash", last_hash))
            except Exception:
                continue            # 末行不是 dict / seq 转不了 int ⇒ 找更早的文件（同老口径）
        return last_seq, last_hash

    def _load_head(self) -> tuple[int, str]:
        """从 jsonl 读链尾（**以 jsonl 为准**，不是 SQLite）。

        ★ M8：保留这个方法名与语义（外部有调用方），但内部**改成按文件顺序**
          读（见 `_read_tail`）⇒ 不再取 max(seq) 那一行。
        """
        return self._read_tail()

    def _jsonl_files(self):
        return [os.path.join(self.root, f) for f in os.listdir(self.root)
                if f.startswith("timeline-") and f.endswith(".jsonl")]

    def _append_jsonl(self, rec: dict):
        """追加一条记录到今天的 jsonl。

        ★ M8（设计稿 §3 缺陷 2）：老写法 `open(path,"a")` 在 Windows 上
          `O_APPEND` 的实现是「**先 seek 到 EOF，再写**」，**不是**原子的
          `FILE_APPEND_DATA` ⇒ 两个进程可以 seek 到**同一个** EOF，
          后写的**覆盖**先写的字节 ⇒ 记录被拼坏/撕坏 ⇒ **真相源自己丢事件**。
          ⇒ 这里改成：**一次 `os.write` 写完整行**（一次系统调用，撕裂窗口最小），
            再配合**跨进程锁**（临界区覆盖追加）⇒ 撕裂不再可能。
        """
        path = self._today_jsonl()
        line = (canonical(rec) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line)                 # ★ 一次写完整行
            if self._since_fsync % self._fsync_every == 0:
                os.fsync(fd)
        finally:
            os.close(fd)
        self._since_fsync += 1

    # ────────────────────────── 对外 ──────────────────────────
    @property
    def head(self) -> tuple[int, str]:
        return self._head

    def append(self, kind: str, *, actor: str = "", tool: str = "",
               payload: dict | None = None, ok: bool | None = None,
               code: int | None = None, session: str = "",
               t_mono: float | None = None, t_wall: str | None = None) -> dict:
        """追加一条事件。**这是唯一的写入口** —— 绕过它写库＝链断。

        ★ M8（设计稿 §3 缺陷 1/2/3 + §4 修法 A）：
          临界区 = 「**读链尾 → 算 seq → 追加 jsonl → 写索引**」整段，
          由一把**跨进程**排他锁包住 ⇒
            · 缺陷 1（进程内锁 + 构造时读链尾）→ 锁内**按文件顺序重读**链尾 ⇒ seq 不撞、链不分叉；
            · 缺陷 2（jsonl 追加非原子）→ 追加也在临界区里 + 单次 write 写完整行 ⇒ 不撕裂；
            · 缺陷 3（PRIMARY KEY + INSERT OR REPLACE）→ 索引改成 `UNIQUE(seq, hash)`
              且**用 INSERT OR IGNORE 追加**（不覆盖）⇒ 同 seq 不同 hash 的两条都装得下。
          ★ 进程内的 `threading.RLock` **保留**（同进程多线程仍要互斥）。
        ★ 链**跨纪元连续**：新纪元的第一条 `prev_hash` 指向上一段的链尾
          ⇒ 旧的独立验证器（`verify/agentctl_verify.py`）不用改也能验
          ⇒ 「段锚」负责的是「段与段之间对不对得上」。
        """
        with self._xlock:                      # ← 跨进程（先拿外层的，后拿进程内的）
            with self._lock:
                self._head = self._read_tail()  # ★ 锁内重读链尾（不是构造时的快照）
                seq = self._head[0] + 1        # ★ seq 在锁内分配 ⇒ 多进程不撞
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
                if self._epoch:
                    body["epoch"] = self._epoch      # ★ 纪元标记（旧记录没有 ⇒ 就是 epoch 0）
                body["hash"] = compute_hash(prev, {k: v for k, v in body.items() if k != "hash"})
                self._append_jsonl(body)
                # ★ M18-b：复用长连接写索引（提交后才返回；见 _execute_write）
                self._execute_write(
                    "INSERT OR IGNORE INTO events(seq,kind,t_mono,t_wall,actor,tool,ok,code,"
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
        # ★ M8：`append_frame` **必须**和 `append` 共用同一把锁
        #   （设计稿 §4 修法 A 的风险③：漏了它 ⇒ 帧链与事件链还会分叉）。
        with self._xlock:
            with self._lock:
                self._head = self._read_tail()  # ★ 锁内重读链尾
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
                if self._epoch:
                    body["epoch"] = self._epoch
                body["hash"] = compute_hash(prev, {k: v for k, v in body.items() if k != "hash"})
                self._append_jsonl(body)
                # ★ M18-b：复用长连接写索引（不覆盖，与 events 同口径）
                self._execute_write(
                    "INSERT OR IGNORE INTO frames(seq,t_mono,t_wall,path,sha256,bytes,w,h,"
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
                            c.execute("INSERT OR IGNORE INTO frames(seq,t_mono,t_wall,path,sha256,"
                                      "bytes,w,h,trigger,state,prev_hash,hash) "
                                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (r["seq"], r["t_mono"], r["t_wall"], r.get("path"), r.get("sha256"),
                                       r.get("bytes"), r.get("w"), r.get("h"), r.get("trigger"),
                                       r.get("state"), r.get("prev_hash"), r["hash"]))
                        else:
                            # ★ M8：OR IGNORE（**不是** OR REPLACE）⇒ 重建**不再覆盖**
                            #   ⇒ 历史上那 62 条「同 seq 不同 hash」的并发化石**都装得下**
                            #   ⇒ 重建后 sqlite 行数 == jsonl 行数（差额归零）。
                            c.execute("INSERT OR IGNORE INTO events(seq,kind,t_mono,t_wall,actor,tool,"
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
    def verify(self, *, check_frames: bool = True, scope: str = "epoch",
               from_event: int | None = None, to_event: int | None = None
               ) -> tuple[bool, dict | None, dict]:
        """返回 (ok, first_bad, stats)。**这里只是自检**——同一份代码写和验不算独立。

        ★ M8（设计稿 §6.3）：`scope` 两个取值 ——
          · "full" = 老口径：从 GENESIS 顺链验，**遇到第一个断点就停**
            （epoch 0 的段锚用它，如实写 chain_ok=False）。
          · "epoch"（默认）= **按纪元分段验**：只判**当前纪元**那一段，
            旧纪元的断点**如实计入统计但不判本纪元的红**
            ⇒ 「从修好那天起没再坏过」是**有意义的绿**。
            段与段之间**只看段锚对不对得上**（当前纪元第一条的 prev_hash
            必须等于上一段锚的 head_hash）。
        """
        if from_event is not None and to_event is not None \
                and int(from_event) > int(to_event):
            raise ValueError(
                f"verify 范围非法: from_event={from_event} > to_event={to_event}")
        ok, first_bad, stats = (
            self._verify_full(check_frames=check_frames) if scope == "full"
            else self._verify_epoch(check_frames=check_frames))
        # ★ Kimi 探针 22：调用方请求的范围必须**记账**——哈希链连续性校验仍须
        #   整段（跳过旧链点=校验作废），范围只约束报告口径（如实注明，不假装已滤）。
        if (from_event is not None or to_event is not None) and isinstance(stats, dict):
            stats["requested_range"] = {"from_event": from_event, "to_event": to_event,
                                        "note": "哈希链校验仍为整段；范围仅约束报告口径"}
        return ok, first_bad, stats

    def _verify_full(self, *, check_frames: bool = True) -> tuple[bool, dict | None, dict]:
        """老口径：从 GENESIS 顺链验，第一个断点即停。"""
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

    def _verify_epoch(self, *, check_frames: bool = True) -> tuple[bool, dict | None, dict]:
        """按纪元分段验（当前纪元必须全通；旧纪元的断点如实计入统计）。"""
        cur = self._epoch
        anchors = {int(a["epoch"]): a for a in self._anchors() if a.get("epoch") is not None}
        # ① 统计：各纪元的条数 + 旧纪元的断点数
        per_epoch = {}
        old_breaks = 0
        prev = GENESIS
        expect_seq = 1
        n_ev = n_fr = 0
        first_bad = None
        cur_prev = None          # 当前纪元第一条的 prev_hash
        cur_expect_seq = None
        cur_first = None
        for path in sorted(self._jsonl_files()):
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception as e:
                        if cur == 0:
                            return False, {"file": os.path.basename(path), "line": lineno,
                                           "reason": f"JSON 解析失败: {e}"}, {}
                        old_breaks += 1
                        continue
                    ep = int(r.get("epoch") or 0)
                    per_epoch[ep] = per_epoch.get(ep, 0) + 1
                    got = r.get("hash")
                    body = {k: v for k, v in r.items() if k != "hash"}
                    want = compute_hash(prev, body)
                    broken = (r.get("prev_hash") != prev or got != want
                              or (cur_expect_seq is not None and r.get("seq") != cur_expect_seq))
                    if ep != cur:
                        # ★ 旧纪元：断点**如实计数**，但**不判本纪元的红**
                        if broken:
                            old_breaks += 1
                        prev, expect_seq = got, (r.get("seq", 0) + 1)
                        if r.get("kind") == "frame":
                            n_fr += 1
                        else:
                            n_ev += 1
                        continue
                    # ── 当前纪元：必须全通 ──
                    if cur_prev is None:
                        cur_prev = r.get("prev_hash")
                        cur_first = {"file": os.path.basename(path), "line": lineno,
                                     "seq": r.get("seq")}
                        cur_expect_seq = r.get("seq")
                    if broken:
                        first_bad = {"file": os.path.basename(path), "line": lineno,
                                     "seq": r.get("seq"), "kind": r.get("kind"),
                                     "reason": "链断（prev_hash / hash / seq 不符）",
                                     "expected_hash": want, "actual_hash": got,
                                     "expected_prev": prev, "actual_prev": r.get("prev_hash")}
                        break
                    if check_frames and r.get("kind") == "frame":
                        n_fr += 1
                        fp = os.path.join(self.root, r.get("path") or "")
                        if r.get("state") == "present":
                            if not os.path.exists(fp):
                                first_bad = {"seq": r["seq"], "reason": "帧文件缺失", "path": fp}
                                break
                            sha, _ = _sha256_file(fp)
                            if sha != r.get("sha256"):
                                first_bad = {"seq": r["seq"], "reason": "帧内容被改（sha256 不符）",
                                             "path": fp, "expected_hash": r.get("sha256"),
                                             "actual_hash": sha}
                                break
                    else:
                        n_ev += 1
                    prev, expect_seq = got, (r.get("seq", 0) + 1)
                    # ★ 2026-10-22 修：当前纪元的 seq 期望值必须**逐条推进**。
                    #   原来只在第一条设 `cur_expect_seq = 首条的 seq` 就再没动过
                    #   ⇒ 第二条起必然「seq 不连续」⇒ 分段验**误报红**
                    #   （实测：expected_hash 与 actual_hash 完全相同，只有 seq 检查误伤）。
                    cur_expect_seq = (r.get("seq", 0) + 1)
                if first_bad:
                    break
        stats = {"events": n_ev, "frames": n_fr, "last_hash": prev,
                 "epoch": cur, "per_epoch": per_epoch,
                 "旧纪元断链": old_breaks,
                 "当前纪元起点": cur_first,
                 "口径": "scope=epoch：只判当前纪元（%d）那一段；旧纪元的断点如实计数、不判红" % cur}
        # ② 段锚校验：当前纪元第一条的 prev_hash 必须等于上一段锚的 head_hash
        if cur > 0 and cur_first and cur_prev:
            prev_anchor = anchors.get(cur - 1)
            if prev_anchor and prev_anchor.get("head_hash") and cur_prev != prev_anchor["head_hash"]:
                stats["段锚对不上"] = {"期望": prev_anchor.get("head_hash"), "实得": cur_prev}
                return False, {"reason": "段锚对不上（epoch %d 的起点不等于 epoch %d 的段锚 head_hash）"
                                         % (cur, cur - 1),
                               "file": cur_first.get("file"), "line": cur_first.get("line")}, stats
        if first_bad:
            return False, first_bad, stats
        return True, None, stats

    # ── 带外锚点（对抗"整链重算"的唯一手段）──
    def write_anchor(self, note: str = "") -> dict:
        ok, bad, stats = self.verify(check_frames=False, scope="full")
        rec = {"t_wall": _now_iso(), "t_mono": round(time.perf_counter(), 6),
               "head_seq": self._head[0], "head_hash": self._head[1],
               "chain_ok": ok, "stats": stats, "note": note,
               "epoch": self._epoch}
        with self.anchor_path, open(self.anchor_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(canonical(rec) + "\n")
            f.flush(); os.fsync(f.fileno())
        return rec

    # ══════════ M8：分段纪元（epoch）+ 段锚 ══════════
    # ── 纪元是怎么定的 ──
    #   **不另存状态文件**：纪元由 anchors.jsonl 里的「段锚」推导 ——
    #   有 epoch=0 的段锚 ⇒ 当前纪元 = 1；没有 ⇒ 当前纪元 = 0（全新链）。
    #   ⇒ 全新 timeline（沙箱/复现）**自动就是 epoch 0**，不会被平白多一个纪元。
    def _anchors(self) -> list[dict]:
        out = []
        try:
            with open(self.anchor_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except OSError:
            pass
        return out

    def _current_epoch(self) -> int:
        """当前纪元 = 已有段锚里的最大 epoch + 1（没有段锚 ⇒ 0）。"""
        eps = [int(a.get("epoch")) for a in self._anchors()
               if a.get("epoch") is not None]
        return (max(eps) + 1) if eps else 0

    def migrate_to_epoch1(self, note: str = "") -> dict:
        """**迁移**：把现有历史整体定为 epoch 0，写段锚，此后新记录进 epoch 1。

        ★ **不重算、不重写历史**：一个 jsonl 字节都不动；
          旧链的断点（如今天的 24 处）**如实留在 epoch 0 里**，
          `verify(scope="epoch")` 给出「**从修好那天起没再坏过**」的有意义的绿。
        ★ 幂等：已经迁过就直接返回（不重复写段锚）。
        ★ 段锚里 `chain_ok` **如实写**（旧链是断的 ⇒ False）—— 不粉饰。
        ★ **锁内重读链尾**（2026-10-22 修）：段锚的 head_hash 必须等于「迁移那一刻的
          **真链尾**」—— 否则新纪元第一条的 prev_hash（= 写它那一刻的链尾）
          与段锚对不上 ⇒ `_verify_epoch` 的「段锚对不上」会误报红。
          ★ 注意：这只保证「迁移那一瞬间」对得上。**迁移之后必须重启所有写者**
            （老进程的 `self._epoch` 是构造时的快照 ⇒ 它会继续写 epoch=0 的记录，
             把 epoch 1 的起点挤到后面 ⇒ 段锚照样对不上）。
        """
        with self._xlock:
            with self._lock:
                if self._epoch > 0:
                    return {"已迁移": True, "当前纪元": self._epoch,
                            "说明": "已有 epoch=0 的段锚 ⇒ 不重复迁移"}
                # ★ 锁内重读链尾（不是构造时的快照）
                self._head = self._read_tail()
                ok, bad, stats = self.verify(check_frames=False, scope="full")
                rec = {"t_wall": _now_iso(), "t_mono": round(time.perf_counter(), 6),
                       "head_seq": self._head[0], "head_hash": self._head[1],
                       "chain_ok": ok, "stats": stats,
                       "note": note or ("M8 迁移：旧链整体定为 epoch 0（**不重算、不重写**）；"
                                        "此后新记录进 epoch 1"),
                       "epoch": 0, "kind": "segment_anchor"}
                with open(self.anchor_path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(canonical(rec) + "\n")
                    f.flush(); os.fsync(f.fileno())
                self._epoch = 1
                return {"已迁移": True, "当前纪元": 1, "旧链链尾seq": rec["head_seq"],
                        "旧链chain_ok": ok, "段锚": rec}


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
