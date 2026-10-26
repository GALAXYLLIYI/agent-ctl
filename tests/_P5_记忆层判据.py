# -*- coding: utf-8 -*-
r"""tests\_P5_记忆层判据.py —— 记忆层 v0 集成判据（记忆层刀 · 2026-10-26 · 第 65 条）

治的病（上游设计稿 §6 实测对账的三块空白之一：episode 的 outcome 没写判定契约）：
    · episode 必须带三值结局（success / failed / unobservable + blocked），
      code=8「确认没变」**不许判成功**；code=5「没测出来」必须 unobservable —— 本仓三态纪律在记忆层的落点。
    · timeline → episode 的提炼必须**幂等可续收**（同一原始事件流，两个索引）。
    · 巩固双阈值滞回：相似度 >0.85 才合并、<0.65 才切分，**中间带必须维持现状**（防抖动）；
      周期锚点不许被合并稀释。
    · 检索必须命中且带出处（episode 命中 vs 段摘要命中要分得清）。
    · workflow 提炼：重复成功会话入库、数字归一、过期可辨。

断什么（11 组）：
    ① 三值映射 verdict_from_code 全表；② store 幂等 + sqlite_vec/numpy 两路径同序同分；
    ③ embedder 跨实例确定性 + L2；④ 合成 timeline → harvest：数量/结局/会话切分/游标幂等；
    ⑤ promote：时间断裂切段 + ANCHOR_N 强制锚点；⑥ merge 滞回：中间带不动、高相似合并出父段、锚点不被稀释；
    ⑦ compact 幂等（二跑零新增零合并）；⑧ 检索：针命中 + 段摘要命中带 source 标记；
    ⑨ distill：重复成功会话提炼 workflow、数字归一、last_verified 落库；
    ⑩ 敏感窗筛选：deny_window 命中的窗口身份不落 obs、留 win_filtered 标记、普通窗不受影响；
    ⑪ ctl_memory 工具接线：契约条目在 + core.TOOLS 有映射 + dispatch 三态（stats/query/未知 action）走通。

--inject（自证判据是活的，注入后**必须变红**）：
    把 contract.verdict_from_code 换成「code=8 也判 success」的谎言版，
    跑 ① 的断言 ⇒ 必须抓红。进程内打补丁 + finally 还原。

边界：纯逻辑 + %TEMP% 临时库；不碰桌面、不发 ctl_* 动作、不需要通行证。
退出码：0 = 全绿；1 = 有红（逐条点名）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import agentctl.memory.contract as C  # noqa: E402
from agentctl.memory.contract import Episode  # noqa: E402
from agentctl.memory.embedder import HashNGramEmbedder  # noqa: E402
from agentctl.memory.store import MemoryStore  # noqa: E402

FAILS: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"  ok  {name}")
    except Exception as exc:  # noqa: BLE001
        FAILS.append(f"{name}: {exc!r}")
        print(f"  RED {name}: {exc!r}")


# ── 合成数据工厂 ────────────────────────────────────────────────────────────

def mk_ep(seq: int, obs: str, kind: str = "click", outcome: str = "success",
          target: str = "按钮", t: str | None = None) -> Episode:
    return Episode(
        t=t or f"2026-10-26T10:{(seq // 60) % 60:02d}:{seq % 60:02d}+08:00",
        kind=kind,
        outcome=outcome,
        obs=obs,
        action={"type": kind, "target": target},
        result="ok=1 code=0",
        verify={"verdict": "success"},
        session_id="s-test",
        src_seq=seq,
    )


TIMELINE_DDL = """
CREATE TABLE events(
  seq INTEGER NOT NULL, kind TEXT NOT NULL, t_mono REAL NOT NULL,
  t_wall TEXT NOT NULL, actor TEXT, tool TEXT, ok INTEGER, code INTEGER,
  payload TEXT, session TEXT, UNIQUE(seq));
"""


def build_synthetic_timeline(db_path: str, rows: list[tuple]) -> None:
    con = sqlite3.connect(db_path)
    con.executescript(TIMELINE_DDL)
    con.executemany(
        "INSERT INTO events(seq, kind, t_mono, t_wall, actor, tool, ok, code, payload, session)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


def ev(seq: int, kind: str, t_wall: str, ok: int, code: int, payload: dict | None = None):
    return (seq, kind, 1000.0 + seq, t_wall, "judged", f"ctl_{kind}", ok, code,
            json.dumps(payload or {}, ensure_ascii=False), "")


# ── ① 三值映射 ──────────────────────────────────────────────────────────────

def t_verdict_table() -> None:
    assert C.verdict_from_code(1, 0) == ("success", "success")
    assert C.verdict_from_code(1, 8) == ("failed", "failed"), "code=8 确认没变不许判成功"
    assert C.verdict_from_code(0, 5) == ("unobservable", "unobservable"), "code=5 没测出来"
    assert C.verdict_from_code(0, 12) == ("failed", "failed")
    assert C.verdict_from_code(None, None) == ("unobservable", "unobservable"), "看不清不判成功"
    assert C.verdict_from_code(None, 0) != ("success", "success"), "ok 缺失不许判成功"


# ── ② store 幂等 + 双路径 ───────────────────────────────────────────────────

def t_store_idempotent_and_paths(tmp: str) -> None:
    db = os.path.join(tmp, "p5_store.db")
    with MemoryStore(db) as st:
        a = st.add_episode(mk_ep(1, "Excel 保存按钮对话框"))
        b = st.add_episode(mk_ep(1, "Excel 保存按钮对话框（重复）"))
        assert a == b and st.count_episodes() == 1, "同 src_seq 必须幂等"
        assert st.use_sqlite_vec is True, "本机装了 sqlite-vec，默认应启用影子表"
    with MemoryStore(db, use_sqlite_vec=False) as stn, MemoryStore(db, use_sqlite_vec=True) as stv:
        q = HashNGramEmbedder().embed("excel 保存 对话框")
        hv, hn = stv.search_episodes(q, k=5), stn.search_episodes(q, k=5)
        assert [r["id"] for r, _ in hv] == [r["id"] for r, _ in hn], "两路径必须同序"
        assert all(abs(x - y) < 1e-4 for (_, x), (_, y) in zip(hv, hn)), "两路径必须同分"


# ── ③ embedder 确定性 ───────────────────────────────────────────────────────

def t_embedder_deterministic() -> None:
    e1, e2 = HashNGramEmbedder(), HashNGramEmbedder()
    v1, v2 = e1.embed("打开 Excel 表格"), e2.embed("打开 Excel 表格")
    assert v1 == v2, "跨实例必须逐位相等（禁 python hash()）"
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6, "必须 L2 归一"
    assert e1.embed("") == [0.0] * C.EMBED_DIM, "空文本 ⇒ 全零"


# ── ④ 合成 timeline → harvest ───────────────────────────────────────────────

def t_harvest_synthetic(tmp: str) -> None:
    tdb = os.path.join(tmp, "timeline.db")
    rows = [
        ev(1, "click", "2026-10-26T10:00:00+08:00", 1, 0, {"xy": [10, 20], "win": {"exe": "wps.exe", "cls": "OpusApp"}}),
        ev(2, "type", "2026-10-26T10:00:05+08:00", 1, 0, {"text": "季度报表数据", "mode": "focus"}),
        ev(3, "shot", "2026-10-26T10:00:06+08:00", 1, 0, {}),  # 观测类：不收
        ev(4, "click", "2026-10-26T10:00:10+08:00", 1, 8, {}),  # 确认没变
        ev(5, "scroll", "2026-10-26T10:00:20+08:00", 0, 5, {"amount": -100}),  # 没测出来
        ev(6, "click", "2026-10-26T10:05:00+08:00", 0, 3, {}),  # 非零非5非8
        ev(7, "click", "2026-10-26T12:00:00+08:00", 1, 0, {}),  # 隔 2 小时 ⇒ 新会话
        ev(8, "action_blocked", "2026-10-26T12:00:05+08:00", 0, 10, {"tool": "ctl_click", "reason": "急停"}),
    ]
    build_synthetic_timeline(tdb, rows)

    from agentctl.memory.harvest import TimelineHarvester

    with MemoryStore(os.path.join(tmp, "p5_h.db")) as st:
        hv = TimelineHarvester(st, timeline_dir=tmp)
        try:
            assert hv.pending_count() == 7, "观测类(shot)不收 ⇒ 7 条待收"
            stat = hv.harvest()
            assert stat["added"] == 7, f"应收 7 条，实 {stat}"
            n_after = st.count_episodes()
            stat2 = hv.harvest()
            assert st.count_episodes() == n_after and stat2["added"] == 0, "重跑必须零新增"
            eps = {r["src_seq"]: r for r in st.iter_episode_rows()}
            assert 3 not in eps, "shot 不该成 episode"
            assert eps[4]["outcome"] == "failed", "code=8 ⇒ failed"
            assert eps[5]["outcome"] == "unobservable", "code=5 ⇒ unobservable"
            assert eps[8]["outcome"] == "blocked", "action_blocked ⇒ blocked"
            assert eps[1]["session_id"] == eps[6]["session_id"], "10:00-10:05 同会话"
            assert eps[7]["session_id"] != eps[1]["session_id"], "隔 2 小时必须切会话"
            assert "季度报表" in eps[2]["text"], "type 的 text 必须进检索文本"
            assert st.get_meta("harvest_cursor_seq") == "8", "游标必须到 8"
        finally:
            hv.close()


# ── ⑤⑥⑦ 巩固：promote 边界 / merge 滞回 / 幂等 ─────────────────────────────

def _seed_topics(st: MemoryStore) -> None:
    """两条话题线 + 中间时间断裂。seq=1..38：Excel(1..14) / 断裂 / 浏览器(15..28) / Excel2(29..38)。"""
    t0 = 10 * 3600  # 10:00:00 秒偏移

    def t_of(offset_sec: int) -> str:
        h, rem = divmod(t0 + offset_sec, 3600)
        m, s = divmod(rem, 60)
        return f"2026-10-26T{h:02d}:{m:02d}:{s:02d}+08:00"

    seq = 0
    for i in range(14):  # Excel 线，每条隔 30s
        seq += 1
        st.add_episode(mk_ep(seq, f"excel 表格单元格输入报表数字 {i}", t=t_of(i * 30)))
    for i in range(14):  # 断裂 2 小时后，浏览器线
        seq += 1
        st.add_episode(mk_ep(seq, f"浏览器网页搜索下载文件 {i}", kind="browser", t=t_of(7200 + i * 30)))
    for i in range(9):  # 再回 Excel（相似但时间断裂已切段）
        seq += 1
        st.add_episode(mk_ep(seq, f"excel 表格单元格输入报表数字 续{i}", t=t_of(14400 + i * 30)))


def t_promote_boundaries(tmp: str) -> None:
    db = os.path.join(tmp, "p5_compact.db")
    with MemoryStore(db) as st:
        _seed_topics(st)
        from agentctl.memory.compact import Compactor

        c = Compactor(st)
        stat = c.promote()
        segs = list(st.iter_segments(live_only=True, level=0))
        assert stat["segments_created"] == len(segs) and segs, "promote 必须建段"
        # 14 条连续 → 必在 ANCHOR_N=20 之前由话题连续性保持；2 小时断裂 ⇒ 至少切出 3 段
        assert len(segs) >= 3, f"时间断裂必须切段，实 {len(segs)} 段"
        # 段内不跨断裂：任一段的 first_ep..last_ep 不得横跨 seq 14→15 的断裂
        for s in segs:
            assert not (s["first_ep"] <= 14 and s["last_ep"] >= 15), f"段 {s['id']} 跨了时间断裂"
        # 38 条里必有 ANCHOR_N 锚点：没有任何段的 n_ep 超过 ANCHOR_N
        assert all(s["n_ep"] <= C.ANCHOR_N for s in segs), "锚点失守：段超过 ANCHOR_N 条"
        assert st.get_meta("promoted_until") is not None


def _cos(a: str, b: str) -> float:
    va, vb = HashNGramEmbedder().embed(a), HashNGramEmbedder().embed(b)
    return sum(x * y for x, y in zip(va, vb))


def t_merge_hysteresis(tmp: str) -> None:
    db = os.path.join(tmp, "p5_merge.db")
    # 文本对按 hash-bigram 口径精算（阈值区间是硬的，数据必须真落进去）：
    #   sim(A,B) = 8/9  ≈ 0.889 > MERGE_SIM(0.85)   → 必须合并
    #   sim(B,C) = 6/√72 ≈ 0.707 ∈ [SPLIT_SIM, MERGE_SIM] → 滞回中间带，必须不动
    SUM_A = "excel 报表单元格输入数字"
    SUM_B = "excel 报表单元格输入数子"
    SUM_C = "excel 报表单格输入数字"
    with MemoryStore(db) as st:
        # 手工铺 3 个 level0 段：A–B 高相似必须合并；B–C 中间带必须维持现状
        e1 = st.add_episode(mk_ep(1, "excel 操作一"))
        e2 = st.add_episode(mk_ep(2, "excel 操作二"))
        e3 = st.add_episode(mk_ep(3, "excel 操作三"))
        sA = st.add_segment(level=0, t_start="2026-10-26T10:00:00+08:00", t_end="2026-10-26T10:01:00+08:00",
                            first_ep=e1, last_ep=e1, summary=SUM_A, n_ep=1)
        sB = st.add_segment(level=0, t_start="2026-10-26T10:01:00+08:00", t_end="2026-10-26T10:02:00+08:00",
                            first_ep=e2, last_ep=e2, summary=SUM_B, n_ep=1)
        sC = st.add_segment(level=0, t_start="2026-10-26T10:02:00+08:00", t_end="2026-10-26T10:03:00+08:00",
                            first_ep=e3, last_ep=e3, summary=SUM_C, n_ep=1)
        from agentctl.memory.compact import Compactor

        simAB = _cos(SUM_A, SUM_B)
        assert simAB > C.MERGE_SIM, f"测试数据失效：simAB={simAB:.3f} 应 >{C.MERGE_SIM}（先修数据再谈判据）"
        simBC = _cos(SUM_B, SUM_C)
        assert C.SPLIT_SIM <= simBC <= C.MERGE_SIM, \
            f"测试数据失效：simBC={simBC:.3f} 应落在滞回中间带 [{C.SPLIT_SIM},{C.MERGE_SIM}]"
        stat = Compactor(st).merge()
        segs = {s["id"]: s for s in st.iter_segments(live_only=False, level=0)}
        assert segs[sA]["merged_into"] and segs[sB]["merged_into"], "高相似对必须合并"
        assert not segs[sC]["merged_into"], \
            f"中间带(sim={simBC:.2f})不许被裹挟合并（0.85 阈值滞回失守）"
        assert stat["parents_created"] >= 1


def t_compact_idempotent(tmp: str) -> None:
    db = os.path.join(tmp, "p5_idem.db")
    with MemoryStore(db) as st:
        _seed_topics(st)
        from agentctl.memory.compact import Compactor

        st1 = Compactor(st).compact()
        n_seg1 = len(list(st.iter_segments(live_only=False)))
        st2 = Compactor(st).compact()
        n_seg2 = len(list(st.iter_segments(live_only=False)))
        assert st2["segments_created"] == 0, f"二跑必须零新建，实 {st2}"
        assert st2["parents_created"] == 0, f"二跑必须零合并，实 {st2}"
        assert n_seg2 == n_seg1, "二跑段总数不许变"


# ── ⑧ 检索 ──────────────────────────────────────────────────────────────────

def t_retrieve(tmp: str) -> None:
    db = os.path.join(tmp, "p5_query.db")
    with MemoryStore(db) as st:
        _seed_topics(st)
        from agentctl.memory.compact import Compactor
        from agentctl.memory.retrieve import Retriever

        Compactor(st).compact()
        out = Retriever(st).query("excel 表格报表输入", k=5)
        hits = out["hits"]
        assert hits, "必须命中"
        ep_hits = [h for h in hits if h["source"] == "episode"]
        assert ep_hits and "报表" in json.dumps(hits[:3], ensure_ascii=False), "针必须进前 3"
        assert all("score" in h and h["score"] >= 0 for h in hits), "分数非负"
        assert all(h.get("t") for h in ep_hits), "episode 命中必须带时间出处"
        sum_hits = [h for h in hits if h["source"] == "segment-summary"]
        if sum_hits:
            assert all(h.get("segment_id") for h in sum_hits), "段摘要命中必须带 segment_id（出处）"
        # 粗下钻证据：检索结果须说明候选来自哪些段（candidate_ids 即粗选段覆盖的 ep 集合）
        assert "candidate_ids" in out, "必须带粗选出处（candidate_ids）"


# ── ⑨ 提炼 ──────────────────────────────────────────────────────────────────

def t_distill(tmp: str) -> None:
    db = os.path.join(tmp, "p5_distill.db")
    with MemoryStore(db) as st:
        # 会话 A：4 条全成功 → 必须提炼；会话 B：1 条失败 → 不足 min_actions，不提炼
        for i in range(4):
            ep = mk_ep(100 + i, f"导出报表第{i}份", kind="click", outcome="success",
                       target=f"导出按钮2026-{i}", t=f"2026-10-26T11:0{i}:00+08:00")
            ep.session_id = "s-a"
            st.add_episode(ep)
        ep = mk_ep(200, "失败动作", outcome="failed", t="2026-10-26T12:00:00+08:00")
        ep.session_id = "s-b"
        st.add_episode(ep)
        from agentctl.memory.distill import distill_workflows

        stat = distill_workflows(st)
        assert stat["workflows_upserted"] >= 1, f"成功会话必须提炼出 workflow：{stat}"
        wfs = list(st.conn.execute("SELECT * FROM workflows"))
        assert wfs and wfs[0]["last_verified"].startswith("2026-10-26"), "last_verified 必须落库"
        sig = wfs[0]["signature"]
        assert "#" in sig and "2026" not in sig, f"数字必须归一：{sig}"
        # 幂等：同会话二跑不许新增行（upsert 走 signature 唯一键）
        stat2 = distill_workflows(st)
        assert stat2["workflows_upserted"] >= 1
        assert list(st.conn.execute("SELECT COUNT(*) FROM workflows"))[0][0] == len(wfs), \
            "同签名不许重复入库"


# ── ⑩ 敏感窗筛选（harvest 层；设计稿 §5.2 隐私边界）────────────────────────

def t_sensitive_filter(tmp: str) -> None:
    # ★ harvester 固定读 <目录>\timeline.db ⇒ 用独立子目录，避免与 ④ 组的合成库串台
    sens_dir = os.path.join(tmp, "sens_tl")
    os.makedirs(sens_dir, exist_ok=True)
    tdb = os.path.join(sens_dir, "timeline.db")
    rows = [
        # 终端族（authstate 五张表 TERMINAL）⇒ 窗口身份必须被滤掉
        ev(1, "type", "2026-10-26T10:00:00+08:00", 1, 0,
           {"text": "whoami", "win": {"exe": "cmd.exe", "cls": "ConsoleWindowClass"}}),
        # 普通窗 ⇒ 身份保留
        ev(2, "type", "2026-10-26T10:00:10+08:00", 1, 0,
           {"text": "普通记事", "win": {"exe": "notepad.exe", "cls": "Notepad"}}),
    ]
    build_synthetic_timeline(tdb, rows)

    from agentctl.memory.harvest import TimelineHarvester

    with MemoryStore(os.path.join(tmp, "p5_sens.db")) as st:
        hv = TimelineHarvester(st, timeline_dir=sens_dir)
        try:
            hv.harvest()
            eps = {r["src_seq"]: r for r in st.iter_episode_rows()}
            v1 = Episode.from_db_row(eps[1]).verify   # verify 列在原始行是 JSON 串
            v2 = Episode.from_db_row(eps[2]).verify
            assert "win=" not in eps[1]["obs"], "敏感窗身份不许落 obs"
            assert v1.get("win_filtered"), "命中必须留 win_filtered 标记（不许静默）"
            assert "whoami" in eps[1]["text"], "动作语义保留（滤的是窗口身份，不是动作记录）"
            assert "win=notepad.exe|Notepad" in eps[2]["obs"], "普通窗口身份必须保留"
            assert not v2.get("win_filtered"), "普通窗不得误滤"
        finally:
            hv.close()


# ── ⑪ ctl_memory 工具接线（契约 + core 分发 + 三态行为）────────────────────

def t_tool_wiring(tmp: str) -> None:
    import json as _json

    tj = _json.load(open(os.path.join(ROOT, "contract", "tools.json"), encoding="utf-8"))
    entries = {t.get("name"): t for t in tj.get("tools") or []}
    e = entries.get("ctl_memory")
    assert e, "契约缺 ctl_memory 条目"
    assert e["input_schema"].get("additionalProperties") is False
    assert e.get("annotations", {}).get("readOnlyHint") is True, "记忆查询必须标只读"

    from agentctl import core as core_mod

    assert "ctl_memory" in core_mod.TOOLS, "core.TOOLS 缺 ctl_memory 映射"

    os.environ["AGENTCTL_MEMORY_DB"] = os.path.join(tmp, "p5_tool.db")
    try:
        r = core_mod.dispatch("ctl_memory", {"action": "stats"}, actor="判据")
        assert r.get("ok") is True and r.get("data", {}).get("episodes") == 0, r
        r2 = core_mod.dispatch("ctl_memory", {"action": "query", "text": "测试检索"}, actor="判据")
        assert r2.get("ok") is True and r2["data"]["结果"]["hits"] == [], r2
        r3 = core_mod.dispatch("ctl_memory", {"action": "bogus"}, actor="判据")
        assert r3.get("ok") is not True, "未知 action 必须拒"
        r4 = core_mod.dispatch("ctl_memory", {"action": "query"}, actor="判据")
        assert r4.get("ok") is not True, "query 缺 text 必须拒"
    finally:
        os.environ.pop("AGENTCTL_MEMORY_DB", None)


# ── --inject：判据是活的（注入谎言映射必须变红）─────────────────────────────

def t_inject_self() -> None:
    """在「code=8 也判 success」的谎言映射下，① 组断言必须抓红。"""
    saved = C.verdict_from_code

    def lying(ok, code):
        return ("success", "success")

    C.verdict_from_code = lying
    try:
        try:
            t_verdict_table()
        except AssertionError:
            return  # 抓红 = 判据活着 ✓
        raise AssertionError("注入谎言后 ① 组竟仍全绿 —— 判据是死的")
    finally:
        C.verdict_from_code = saved


# ── 入口 ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="注入自证：判据必须变红")
    args = ap.parse_args()
    if args.inject:
        t_inject_self()
        print("inject 自证：谎言被抓住（判据活着）")
        return 0

    tmp = tempfile.mkdtemp(prefix="p5_mem_")
    print("== 记忆层 v0 集成判据 ==")
    print(f"  temp = {tmp}")
    check("① 三值映射全表", t_verdict_table)
    check("② store 幂等 + 双路径一致", lambda: t_store_idempotent_and_paths(tmp))
    check("③ embedder 确定性", t_embedder_deterministic)
    check("④ 合成 timeline → harvest", lambda: t_harvest_synthetic(tmp))
    check("⑤ promote 边界与锚点", lambda: t_promote_boundaries(tmp))
    check("⑥ merge 双阈值滞回", lambda: t_merge_hysteresis(tmp))
    check("⑦ compact 幂等", lambda: t_compact_idempotent(tmp))
    check("⑧ 检索命中与出处", lambda: t_retrieve(tmp))
    check("⑨ workflow 提炼与数字归一", lambda: t_distill(tmp))
    check("⑩ 敏感窗筛选", lambda: t_sensitive_filter(tmp))
    check("⑪ ctl_memory 工具接线", lambda: t_tool_wiring(tmp))
    if FAILS:
        print(f"RED x{len(FAILS)}")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
