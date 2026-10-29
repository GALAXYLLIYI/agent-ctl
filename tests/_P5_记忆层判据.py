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

断什么（13 组）：
    ① 三值映射 verdict_from_code 全表；② store 幂等 + sqlite_vec/numpy 两路径同序同分；
    ③ embedder 跨实例确定性 + L2；④ 合成 timeline → harvest：数量/结局/会话切分/游标幂等；
    ⑤ promote：时间断裂切段 + ANCHOR_N 强制锚点；⑥ merge 滞回：中间带不动、高相似合并出父段、锚点不被稀释；
    ⑦ compact 幂等（二跑零新增零合并）；⑧ 检索：针命中 + 段摘要命中带 source 标记；
    ⑨ distill：重复成功会话提炼 workflow、数字归一、last_verified 落库；
    ⑩ 敏感窗筛选：deny_window 命中的窗口身份不落 obs、留 win_filtered 标记、普通窗不受影响；
    ⑪ ctl_memory 工具接线：契约条目在 + core.TOOLS 有映射 + dispatch 三态（stats/query/未知 action）走通；
    ⑫ 混合时区（naive+aware）promote 不崩（Kimi 复核 P1-2 回归）；
    ⑬ 时间倒序入库（新 id 旧时间）：段成员 ep_ids 按时间序、检索命中晚写 episode（Kimi 复核 P1-1 回归）。

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
                            first_ep=e1, last_ep=e1, ep_ids=[e1], summary=SUM_A, n_ep=1)
        sB = st.add_segment(level=0, t_start="2026-10-26T10:01:00+08:00", t_end="2026-10-26T10:02:00+08:00",
                            first_ep=e2, last_ep=e2, ep_ids=[e2], summary=SUM_B, n_ep=1)
        sC = st.add_segment(level=0, t_start="2026-10-26T10:02:00+08:00", t_end="2026-10-26T10:03:00+08:00",
                            first_ep=e3, last_ep=e3, ep_ids=[e3], summary=SUM_C, n_ep=1)
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

        # v3（2026-10-29）：recall action + subject 参数
        from agentctl.memory.contract import Episode as _Ep, SUBJECT_AGENT as _SA, \
            SUBJECT_HUMAN as _SH
        from agentctl.memory.store import MemoryStore as _MS
        _st = _MS(os.path.join(tmp, "p5_tool.db"))
        try:
            _st.add_episode(_Ep(t="2026-10-29T11:00:00+08:00", kind="type",
                                outcome="success", obs="回测 突破 代理基线 结论",
                                action={"type": "type", "target": "t1"},
                                result="r", verify={"verdict": "success"},
                                session_id="s-agent-t1", subject=_SA, src_seq=None))
            _st.add_episode(_Ep(t="2026-10-29T11:01:00+08:00", kind="click",
                                outcome="success", obs="回测 突破 人工复盘 笔记",
                                action={"type": "click", "target": "t2"},
                                result="r", verify={"verdict": "success"},
                                session_id="s-human-t2", subject=_SH, src_seq=None))
        finally:
            _st.close()
        r5 = core_mod.dispatch("ctl_memory", {"action": "recall", "text": "回测 突破 代理基线"},
                               actor="判据")
        assert r5.get("ok") is True, r5
        rec = r5["data"]["结果"]
        assert rec["recalled"] and rec["hits"], f"recall 应命中: {rec}"
        assert all(h.get("subject") == _SA for h in rec["hits"]), "recall 默认只出 agent 仓"
        assert rec.get("block"), "recall 必须带可出示 block"
        r6 = core_mod.dispatch("ctl_memory", {"action": "recall", "text": "回测 突破",
                                              "subject": "human"}, actor="判据")
        assert r6.get("ok") is True
        rec6 = r6["data"]["结果"]
        assert all(h.get("subject") == _SH for h in rec6["hits"]), "human 范围混入 agent"
        r7 = core_mod.dispatch("ctl_memory", {"action": "recall", "text": "回测 突破",
                                              "subject": "robot"}, actor="判据")
        assert r7.get("ok") is not True, "非法 subject 必须拒"

        # v4（DSH 验收 P0-1/P2-1 回归）：段层 scope + 空串拒绝，走 dispatch 真路径
        from agentctl.memory.compact import Compactor as _Cp
        _st2 = _MS(os.path.join(tmp, "p5_tool.db"))
        try:
            _Cp(_st2).compact()      # 造出带 subject 的段
        finally:
            _st2.close()
        r8 = core_mod.dispatch("ctl_memory", {"action": "query", "text": "回测 突破",
                                              "subject": "human"}, actor="判据")
        assert r8.get("ok") is True, r8
        bad8 = [h for h in r8["data"]["结果"]["hits"]
                if (h.get("subject") or "mixed") != "human"]
        assert not bad8, f"段层泄漏: {[(h.get('source'), h.get('subject')) for h in bad8]}"
        r9 = core_mod.dispatch("ctl_memory", {"action": "query", "text": "回测",
                                              "subject": ""}, actor="判据")
        assert r9.get("ok") is not True, "subject 空串必须拒（P2-1）"
    finally:
        os.environ.pop("AGENTCTL_MEMORY_DB", None)


# ── ⑫ 混合时区（Kimi 复核 P1-2 回归）───────────────────────────────────────

def t_mixed_timezone(tmp: str) -> None:
    db = os.path.join(tmp, "p5_tz.db")
    with MemoryStore(db) as st:
        # aware（harvest 写 timeline 带 +08:00）与 naive（agent_loop build_episode 本地时间）混存
        st.add_episode(mk_ep(1, "混合时区甲", t="2026-10-26T10:00:00+08:00"))
        st.add_episode(mk_ep(2, "混合时区乙", t="2026-10-26T10:00:30"))        # naive
        st.add_episode(mk_ep(3, "混合时区丙", t="2026-10-26T10:01:00+08:00"))
        from agentctl.memory.compact import Compactor, _gap_sec

        # 混合对直接相减必须已归一成有限秒数（旧代码此处抛 TypeError）
        g = _gap_sec("2026-10-26T10:00:30", "2026-10-26T10:01:00+08:00")
        assert g == 30.0, f"混合时区 gap 应=30s，实 {g}"
        stat = Compactor(st).promote()   # 全程不许崩
        segs = list(st.iter_segments(live_only=True, level=0))
        assert stat["segments_created"] == len(segs) and segs, "混合时区下必须正常成段"
        for s in segs:
            assert not (s["first_ep"] <= 1 <= s["last_ep"] and s["first_ep"] == s["last_ep"]), "sanity"
        assert st.get_meta("promoted_until") == "3", "三条全部入段"


# ── ⑬ 时间倒序入库（Kimi 复核 P1-1 回归：新 id 旧时间）──────────────────────

def t_time_inversion(tmp: str) -> None:
    db = os.path.join(tmp, "p5_inv.db")
    with MemoryStore(db) as st:
        # 按写入顺序：id1(10:00) → id2(10:05) → 晚写者 id3(10:02)、id4(10:03)
        # 按时间排序后段成员应为 [1,3,4,2] —— 旧代码区间 {1,2} 会漏 3、4
        st.add_episode(mk_ep(1, "季度报表数据 打开表格", t="2026-10-26T10:00:00+08:00"))
        st.add_episode(mk_ep(2, "季度报表数据 保存表格", t="2026-10-26T10:05:00+08:00"))
        st.add_episode(mk_ep(3, "德尔特库存清单 导出明细", t="2026-10-26T10:02:00+08:00"))
        st.add_episode(mk_ep(4, "德尔特库存清单 核对数量", t="2026-10-26T10:03:00+08:00"))
        from agentctl.memory.compact import Compactor
        from agentctl.memory.retrieve import Retriever

        Compactor(st).compact()
        for s in st.iter_segments(live_only=True):
            assert s["n_ep"] == len(json.loads(s["ep_ids"] or "[]")), \
                f"段 {s['id']} n_ep 与 ep_ids 成员数不一致（区间口径复发）"
        seg_sets = [json.loads(s["ep_ids"]) for s in st.iter_segments(live_only=True)]
        assert [3, 4] in seg_sets, \
            f"晚写者(id3,id4)必须按时间序同段显式落库，实 {seg_sets}"
        out = Retriever(st).query("德尔特 库存清单 明细", k=5)
        ep_ids_hit = [h["id"] for h in out["hits"] if h["source"] == "episode"]
        assert 3 in ep_ids_hit[:3], f"晚写 episode(id3) 必须命中前 3，实 {ep_ids_hit}"


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


# ── ⑭ v3/v4 主体分仓（2026-10-29：subject 列 + scope 检索 + 新近回退）───────────
# DSH 验收 P0-1/P1-3 修复后的重构版：
#   · 互斥断言覆盖**全部 hit 类型**（不得用 source=='episode' 过滤——过滤器与 bug 位置
#     重合正是上次漏掉段泄漏的原因）
#   · 新近回退用例先造出段（候选集非空）再写新条目——回退链路必须真的在链路上
#   · 注入自检：RECENT_FALLBACK_N=0 时该用例必须变红（判据活性的固定自证）

def t_subject_v3(tmp: str) -> None:
    import agentctl.memory.retrieve as _ret_mod
    from agentctl.memory.compact import Compactor
    from agentctl.memory.retrieve import Retriever

    db = os.path.join(tmp, "v3.db")
    st = MemoryStore(db)

    # 1) 非法 subject 拒收
    try:
        Episode(t="2026-10-29T10:00:00+08:00", kind="click", outcome="success",
                subject="robot").validate()
        raise AssertionError("非法 subject 未被拒收")
    except ValueError:
        pass

    # 2) 双主体写入 + 先 compact 造出段（v4：段带 subject）
    def put(seq, obs, subject):
        st.add_episode(Episode(
            t=f"2026-10-29T10:{seq:02d}:00+08:00", kind="type", outcome="success",
            obs=obs, action={"type": "type", "target": f"t{seq}"},
            result="r", verify={"verdict": "success"},
            session_id=f"s-{subject}-{seq}", subject=subject, src_seq=None))

    put(1, "回测 突破 策略 IS OOS 双正", C.SUBJECT_AGENT)
    put(2, "回测 突破 策略 人工复盘 笔记", C.SUBJECT_HUMAN)
    put(3, "突破 参数 稳健带 扫描 结论", C.SUBJECT_AGENT)
    Compactor(st).compact()
    seg_subjects = {s.get("subject") for s in st.iter_segments(live_only=True)}
    assert seg_subjects <= {"agent", "human", "mixed"} and seg_subjects, \
        f"段缺 subject 标注: {seg_subjects}"

    def assert_scope(res, subj):
        bad = [h for h in res["hits"]
               if (h.get("subject") or "mixed") != subj]
        assert not bad, f"{subj} 范围混入: {[(h.get('source'), h.get('subject')) for h in bad]}"

    ra = Retriever(st).query("回测 突破 策略", k=6, subject=C.SUBJECT_AGENT)
    rh = Retriever(st).query("回测 突破 策略", k=6, subject=C.SUBJECT_HUMAN)
    assert ra["hits"] and rh["hits"], "scope 检索两边都不该为空"
    assert_scope(ra, C.SUBJECT_AGENT)
    assert_scope(rh, C.SUBJECT_HUMAN)
    # 段命中必须真实存在且同主体（否则互斥断言可能在空集上 vacuous）
    assert any(h.get("source") == "segment-summary" for h in ra["hits"]), \
        "agent 范围应能命中同主体段摘要"
    assert any(h.get("source") == "segment-summary" for h in rh["hits"]), \
        "human 范围应能命中同主体段摘要"

    # 3) 新近回退：先有段（候选集非空），再写新条目，不 promote 查询必须命中 id=4
    #    k=2 刻意小于候选数（3）：粗筛不会放开全库，回退是唯一能带到 id=4 的路径
    put(4, "跨品种 期货 趋势 栖息地 结论", C.SUBJECT_AGENT)
    found_with_fb = False
    for _ in range(4):
        rr = Retriever(st).query("期货 趋势 栖息地", k=2, subject=C.SUBJECT_AGENT)
        if any(h.get("source") == "episode" and h.get("id") == 4 for h in rr["hits"]):
            found_with_fb = True
            break
    assert found_with_fb, "新近回退失效：候选集非空时未 promote 的新写入检索不到"
    # 注入自检：关掉回退必须变红（判据活性）
    saved_n = _ret_mod.RECENT_FALLBACK_N
    try:
        _ret_mod.RECENT_FALLBACK_N = 0
        rr0 = Retriever(st).query("期货 趋势 栖息地", k=2, subject=C.SUBJECT_AGENT)
        assert not any(h.get("source") == "episode" and h.get("id") == 4
                       for h in rr0["hits"]), "注入自检失败：关掉回退仍命中（判据没咬合）"
    finally:
        _ret_mod.RECENT_FALLBACK_N = saved_n

    # 4) compact 后幂等仍在
    Compactor(st).promote()
    st.close()

    # 5) v2 老库迁移：无 subject 列 + src_seq 行 → 自动补列回填 human
    legacy = os.path.join(tmp, "legacy_v2.db")
    con = sqlite3.connect(legacy)
    con.executescript(
        "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);"
        "CREATE TABLE episodes("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, t TEXT NOT NULL, src_seq INTEGER,"
        "kind TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', screen_version INTEGER,"
        "obs TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '{}',"
        "result TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL,"
        "verify TEXT NOT NULL DEFAULT '{}', text TEXT NOT NULL DEFAULT '', embed BLOB,"
        "UNIQUE(src_seq, t));"
        "INSERT INTO episodes(t, src_seq, kind, session_id, obs, outcome) VALUES"
        "('2026-10-01T09:00:00+08:00', 7, 'click', 's-old', '桌面 点击', 'success'),"
        "('2026-10-01T09:05:00+08:00', NULL, 'type', 's-old2', '代理 经验', 'success');"
    )
    con.commit()
    con.close()
    st2 = MemoryStore(legacy)
    cols = [r[1] for r in st2.conn.execute("PRAGMA table_info(episodes)").fetchall()]
    assert "subject" in cols, "v2 老库迁移未补 subject 列"
    assert st2.get_meta("schema_version") == str(C.SCHEMA_VERSION)
    human_n = st2.conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE subject='human'").fetchone()[0]
    agent_n = st2.conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE subject='agent'").fetchone()[0]
    assert (human_n, agent_n) == (1, 1), f"回填口径错: human={human_n} agent={agent_n}"
    # 旧代码路径兼容：显式列 INSERT 不带 subject 也能落库（DEFAULT 兜底）
    st2.conn.execute(
        "INSERT INTO episodes(t, src_seq, kind, session_id, obs, outcome) VALUES"
        "('2026-10-01T09:10:00+08:00', 9, 'click', 's-old3', '旧代码直插', 'success')")
    st2.conn.commit()
    st2.close()
    # 口径自愈（DSH P1-2）：旧式直插的 src_seq 行重开库后必须被纠正为 human
    st3 = MemoryStore(legacy)
    try:
        drift = st3.conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE src_seq IS NOT NULL AND subject<>'human'"
        ).fetchone()[0]
        assert drift == 0, f"口径自愈失效：{drift} 行带 src_seq 却不是 human"
    finally:
        st3.close()


# ── ⑮ v5 intake 去重合并（2026-10-29 晚 · 派单 Phase 1）─────────────────────
# 治的病：真库 95% 是同一条观测的循环写回（一条「30×15 复核」重复 1459 次）。
# 验收口径（派单）：同 obs 写两次 ⇒ 一行 recurrences=2；obs 变 ⇒ 新行；outcome 变 ⇒ 新行；
# 另加：归一化去重、subject 不许跨仓合并、结论演进 + first_t 留档、source 口径自愈。

def t_intake_dedup(tmp: str) -> None:
    import agentctl.memory.intake as I
    from agentctl.memory.recall import recall as _recall

    db = os.path.join(tmp, "v5_intake.db")
    st = MemoryStore(db)
    cred = {"file": "docs/lessons_jsonl_v1.md", "anchor": "§3"}
    kw = dict(kind="click", obs="回测 突破 策略 结论甲", outcome=C.OUTCOME_SUCCESS,
              session_id="s-20261029-dayloop", actor=C.ACTOR_DAYLOOP,
              verify={"cred": dict(cred)})

    r1 = I.record_experience(st, result="首轮：通过", **kw)
    assert r1["created"] is True and r1["recurrences"] == 1, r1
    r2 = I.record_experience(st, result="二轮：复核通过", **kw)
    assert r2["created"] is False and r2["recurrences"] == 2, r2
    assert st.count_episodes() == 1, f"同 obs 写两次应只 1 行，实 {st.count_episodes()}"
    row = st.conn.execute("SELECT * FROM episodes").fetchone()
    assert row["result"] == "二轮：复核通过", f"结论必须演进到最新，实 {row['result']!r}"
    assert row["recurrences"] == 2 and row["dedup_key"], dict(row)
    v = json.loads(row["verify"])
    assert v["cred"]["first_t"] == row["t"], "首次时间必须留在 verify.cred.first_t"
    assert v["cred"]["file"] == cred["file"], "最新出处必须留在 cred.file"

    # 归一化：全角空格 / 大小写 / 多余空白都不算新观测
    r3 = I.record_experience(st, result="三轮", **{**kw, "obs": "回测　突破  策略 结论甲"})
    assert r3["created"] is False and r3["recurrences"] == 3, r3
    assert st.count_episodes() == 1, "归一化去重失效（全角空格被当成新观测）"

    # obs 变 ⇒ 新行
    r4 = I.record_experience(st, result="乙", **{**kw, "obs": "回测 突破 策略 结论乙"})
    assert r4["created"] is True and st.count_episodes() == 2
    # outcome 变 ⇒ 新行（先 failed 后 success 是新信息）
    r5 = I.record_experience(st, result="先失败", **{**kw, "outcome": C.OUTCOME_FAILED})
    assert r5["created"] is True and st.count_episodes() == 3
    # 分仓不许合并：同一 obs，human 主体另起一行
    r6 = I.record_experience(st, result="人工复盘",
                             **{**kw, "session_id": "s-human-1",
                                "actor": C.ACTOR_HUMAN})
    assert r6["created"] is True and st.count_episodes() == 4, "跨主体被合并了"

    # 注入自检（组内）：关掉去重 ⇒ 同 obs 必须真的多一行（开关 load-bearing）
    saved = I.DEDUP_ENABLED
    try:
        I.DEDUP_ENABLED = False
        r7 = I.record_experience(st, result="去重关了", **kw)
        assert r7["created"] is True and st.count_episodes() == 5, \
            "关掉去重后仍被合并 —— DEDUP_ENABLED 没接线"
    finally:
        I.DEDUP_ENABLED = saved
    # 再开：命中 id 最小那条继续累加
    r8 = I.record_experience(st, result="重开去重", **kw)
    assert r8["created"] is False and r8["id"] == r1["id"], (r8["id"], r1["id"])

    # recurrences 在召回里可读（×N）——用一个没有同前缀兄弟行的观测，
    # 否则 recall 的 obs 去重会把展示吃掉（同 obs 的 failed/success 两行只出一行）
    for _i in range(5):
        I.record_experience(st, kind="lesson", obs="累计 五轮 复核 结论丙",
                            outcome=C.OUTCOME_SUCCESS, result="复核通过",
                            actor=C.ACTOR_DSH, verify={"cred": dict(cred)})
    row = st.conn.execute(
        "SELECT recurrences FROM episodes WHERE obs LIKE '累计 五轮%'").fetchone()
    assert int(row["recurrences"]) == 5, f"五次写回必须合并成 recurrences=5，实 {dict(row)}"
    rc = _recall(st, "累计 五轮 复核 结论丙", k=3)
    assert rc["hits"] and rc["hits"][0]["recurrences"] == 5, rc["hits"]
    assert "×5" in rc["block"], rc["block"]

    # source / actor 口径自愈：旧码直插（带 src_seq、不带 source/actor）重开库必须纠正
    st.conn.execute(
        "INSERT INTO episodes(t, src_seq, kind, session_id, obs, outcome, subject) VALUES"
        "('2026-10-29T15:00:00+08:00', 9001, 'click', 's-old', '旧码直插 带序号',"
        " 'success', 'agent')")
    st.conn.commit()
    n_before = st.count_episodes()
    st.close()
    st2 = MemoryStore(db)
    try:
        row = st2.conn.execute(
            "SELECT source, actor, recurrences, dedup_key FROM episodes WHERE src_seq = 9001"
        ).fetchone()
        assert row["source"] == C.SOURCE_TIMELINE, f"source 自愈失效: {row['source']}"
        assert row["actor"] == C.ACTOR_HUMAN, f"actor 自愈失效: {row['actor']}"
        assert row["recurrences"] == 1, "旧式直插应落 DEFAULT 1"
        assert row["dedup_key"], "缺键行必须被补上 dedup_key"
        assert st2.count_episodes() == n_before
    finally:
        st2.close()


# ── ⑯ 教训 JSONL 适配器（Phase 2）──────────────────────────────────────────
# 治的病：三宿主的交付结论/翻车教训全在会话与报告里，没有摄入通路。
# 验收口径：断点续收（同文件重跑零新增）、出处缺失+success 必须拒、actor 映射正确。

def t_lessons_ingest(tmp: str) -> None:
    import agentctl.memory.intake as I

    db = os.path.join(tmp, "v5_lessons.db")
    st = MemoryStore(db)
    path = os.path.join(tmp, "lessons_demo.jsonl")
    good_success = {
        "t": "2026-10-29T19:00:00+08:00", "actor": "dsh", "kind": "lesson",
        "obs": "验收发现段层分仓失效：段摘要没过主体闸", "outcome": "success",
        "result": "修复：段按成员一致性落 subject + mixed 不进单仓",
        "verify": {"cred": {"file": "验收报告_v3_20261029.md", "anchor": "P0-1"}},
    }
    good_unobs = {
        "t": "2026-10-29T19:01:00+08:00", "actor": "zcode", "kind": "pitfall",
        "obs": "疑似写读耦合：不 promote 就查不到新写入", "outcome": "unobservable",
        "result": "未定论：样本不足，先记下", "verify": {},
    }
    good_failed = {
        "t": "2026-10-29T19:02:00+08:00", "actor": "codex", "kind": "delivery",
        "obs": "7B SFT 评测 100% 但 gold 取自 failed 动作", "outcome": "failed",
        "result": "结论不可用：判据与目标不一致",
        "verify": {"cred": {"file": "验收报告_20261028.md", "anchor": "P0-1"}},
    }
    bad_no_cred = {
        "t": "2026-10-29T19:03:00+08:00", "actor": "dsh", "kind": "lesson",
        "obs": "没有出处也敢标成功", "outcome": "success", "result": "应当被拒",
        "verify": {},
    }
    bad_actor = {
        "t": "2026-10-29T19:04:00+08:00", "actor": "robot", "kind": "lesson",
        "obs": "非法宿主", "outcome": "failed", "result": "应当被拒",
        "verify": {"cred": {"file": "x.md"}},
    }
    with open(path, "w", encoding="utf-8") as f:
        for d in (good_success, good_unobs, good_failed, bad_no_cred, bad_actor):
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    out = I.ingest_lessons(st, path)
    assert out["added"] == 3, f"应入 3 条，实 {out}"
    assert out["rejected"] == 2, f"应拒 2 条（无出处 success / 非法 actor），实 {out}"
    assert {e["line"] for e in out["errors"]} == {4, 5}, out["errors"]
    assert any("出处" in e["error"] for e in out["errors"]), "无出处必须明确拒收理由"
    assert out["promoted"], "摄入必须以 promote 收尾（写读耦合）"
    assert st.count_episodes() == 3
    # actor → subject 映射
    got = {r["actor"]: r["subject"] for r in st.conn.execute(
        "SELECT actor, subject FROM episodes")}
    assert got.get(C.ACTOR_DSH) == C.SUBJECT_AGENT, got
    assert got.get(C.ACTOR_CODEX) == C.SUBJECT_AGENT, got
    assert got.get(C.ACTOR_ZCODE) == C.SUBJECT_AGENT, got
    # source 必须是 session（与 timeline/manual 区分开）
    srcs = {r["source"] for r in st.conn.execute("SELECT source FROM episodes")}
    assert srcs == {C.SOURCE_SESSION}, srcs
    # 可检索 + 段已落（promote 生效）
    from agentctl.memory.retrieve import Retriever
    rq = Retriever(st).query("段层 分仓 失效 主体 闸", k=5, subject=C.SUBJECT_AGENT)
    assert any(h.get("source") == "episode" for h in rq["hits"]), "教训应可检索"
    assert list(st.iter_segments(live_only=True)), "promote 应产出段"

    # 断点续收：同文件重跑零新增零拒绝
    out2 = I.ingest_lessons(st, path)
    assert (out2["added"], out2["merged"], out2["rejected"]) == (0, 0, 0), out2
    assert st.count_episodes() == 3
    # 追加一行再跑 ⇒ 只吃新增那一行
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({**good_success, "obs": "新增教训：段层分仓修复后复测"},
                           ensure_ascii=False) + "\n")
    out3 = I.ingest_lessons(st, path)
    assert out3["added"] == 1, out3
    assert st.count_episodes() == 4
    st.close()


# ── ⑰ 主体归因 actor（Phase 3）─────────────────────────────────────────────
# 治的病：「谁写的」没有归因字段；分仓保持二元（人的不可被代理改写）。
# 验收口径：actor 校验拒非法值、映射表正确、存量回填口径抽查、机器仓内跨宿主可见。

def t_actor_attribution(tmp: str) -> None:
    import agentctl.memory.intake as I

    # 1) 词表校验 + 映射表
    try:
        Episode(t="2026-10-29T20:00:00+08:00", kind="click", outcome="success",
                actor="robot").validate()
        raise AssertionError("非法 actor 未被拒收")
    except ValueError:
        pass
    for actor in C.VALID_ACTORS:
        expect = C.SUBJECT_HUMAN if actor == C.ACTOR_HUMAN else C.SUBJECT_AGENT
        assert C.subject_for_actor(actor) == expect, (actor, C.subject_for_actor(actor))
    # 一致性双向：agent 绑 human 仓、human 绑 agent 仓都必须拒
    st = MemoryStore(os.path.join(tmp, "v5_actor.db"))
    for bad in ((C.ACTOR_ZCODE, C.SUBJECT_HUMAN), (C.ACTOR_HUMAN, C.SUBJECT_AGENT)):
        try:
            I.record_experience(st, kind="lesson", obs="映射不一致", outcome="failed",
                                result="x", actor=bad[0], subject=bad[1])
            raise AssertionError(f"subject/actor 不一致未被拒: {bad}")
        except ValueError:
            pass

    # 2) 机器仓内跨宿主互相可见（这正是扩展的目的：ZCode 踩的坑 DSH 该看到）
    cred = {"file": "docs/lessons_jsonl_v1.md"}
    I.record_experience(st, kind="lesson", obs="跨宿主 归因 可见性 甲",
                        outcome="success", result="ZCode 的结论", actor=C.ACTOR_ZCODE,
                        verify={"cred": dict(cred)})
    I.record_experience(st, kind="lesson", obs="跨宿主 归因 可见性 乙",
                        outcome="success", result="Codex 的结论", actor=C.ACTOR_CODEX,
                        verify={"cred": dict(cred)})
    from agentctl.memory.recall import recall as _recall
    rc = _recall(st, "跨宿主 归因 可见性", k=5)
    actors = {h["actor"] for h in rc["hits"]}
    assert actors >= {C.ACTOR_ZCODE, C.ACTOR_CODEX}, actors
    assert "[" + C.ACTOR_ZCODE + "]" in rc["block"], rc["block"]
    st.close()

    # 3) 存量回填口径（宁可 unknown 不许猜）
    legacy = os.path.join(tmp, "v5_actor_legacy.db")
    con = sqlite3.connect(legacy)
    con.executescript(
        "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);"
        "CREATE TABLE episodes("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, t TEXT NOT NULL, src_seq INTEGER,"
        "kind TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '',"
        "subject TEXT NOT NULL DEFAULT 'agent', screen_version INTEGER,"
        "obs TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '{}',"
        "result TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL,"
        "verify TEXT NOT NULL DEFAULT '{}', text TEXT NOT NULL DEFAULT '', embed BLOB,"
        "recurrences INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT 'manual',"
        "dedup_key TEXT NOT NULL DEFAULT '', UNIQUE(src_seq, t));"
        "INSERT INTO episodes(t, src_seq, kind, session_id, obs, outcome, subject) VALUES"
        "('2026-10-29T09:00:00+08:00', 11, 'click', 's-20261029-dayloop', '桌面 点击', 'success', 'human'),"
        "('2026-10-29T09:01:00+08:00', NULL, 'click', 's-20261028-nightloop', '夜间 经验', 'success', 'agent'),"
        "('2026-10-29T09:02:00+08:00', NULL, 'click', 's-zcode-recall-1', 'ZCode 经验', 'success', 'agent'),"
        "('2026-10-29T09:03:00+08:00', NULL, 'click', 's-agent-forecast-verify', '无宿主名 会话', 'success', 'agent'),"
        "('2026-10-29T09:04:00+08:00', NULL, 'click', 's-20261012-084533', '别的 会话', 'success', 'agent');"
    )
    con.commit()
    con.close()
    st2 = MemoryStore(legacy)
    try:
        # src_seq 优先于 session 提示：人的时间线不许被归因成 loop
        assert st2.conn.execute(
            "SELECT actor FROM episodes WHERE id=1").fetchone()[0] == C.ACTOR_HUMAN
        assert st2.conn.execute(
            "SELECT actor FROM episodes WHERE id=2").fetchone()[0] == C.ACTOR_NIGHTLOOP
        assert st2.conn.execute(
            "SELECT actor FROM episodes WHERE id=3").fetchone()[0] == C.ACTOR_ZCODE
        # 认不出的必须 unknown（宁可不猜）
        for i in (4, 5):
            got = st2.conn.execute(
                f"SELECT actor FROM episodes WHERE id={i}").fetchone()[0]
            assert got == C.ACTOR_UNKNOWN, f"id{i} 猜错了: {got}"
        assert str(st2.get_meta("v5_actor_backfill") or "").strip(), "迁移摘要缺失"
        assert st2.get_meta("schema_version") == str(C.SCHEMA_VERSION)
    finally:
        st2.close()



# ── ⑱ v6 嵌入器换装（2026-10-30：bge-small 512 维 + 口径守门 + reembed）───────────

def t_embedder_v6(tmp: str) -> None:
    import math

    import agentctl.memory as mem_pkg
    from agentctl.memory.embedder import HashNGramEmbedder
    from agentctl.memory.embedder_bge import BGE_QUERY_PREFIX, BgeEmbedder

    assert C.EMBED_DIM == 512, f"v6 应为 512 维，得到 {C.EMBED_DIM}"

    # 1) bge 确定性 + 维度 + 查询前缀 + 语义分离（模型不在 ⇒ 判据红，不静默跳过）
    bge = BgeEmbedder()
    v1 = bge.embed("回测 突破 代理基线 结论")
    v2 = bge.embed("回测 突破 代理基线 结论")
    assert len(v1) == C.EMBED_DIM, f"bge 维度 {len(v1)} != {C.EMBED_DIM}"
    assert v1 == v2, "bge 同文本两次编码不一致（确定性破坏）"
    assert bge.embed_query("回测 突破") == bge.embed(BGE_QUERY_PREFIX + "回测 突破"), \
        "embed_query 与 前缀+embed 不等价"

    def _cos(a, b):
        na = math.sqrt(sum(x * x for x in a)) or 1e-12
        nb = math.sqrt(sum(x * x for x in b)) or 1e-12
        return sum(x * y for x, y in zip(a, b)) / (na * nb)

    same = _cos(bge.embed("写进数据库的新记录要马上能被查到"),
                bge.embed("刚写回去的新结论转头就能被搜到"))
    diff = _cos(bge.embed("写进数据库的新记录要马上能被查到"),
                bge.embed("收盘信号当天成交是未来函数"))
    assert same > diff, f"语义分离失败: same={same:.3f} <= diff={diff:.3f}"

    # 2) 口径守门：库=hash 时拿 bge 直开必须炸（豁免通道 allow_switch 除外）
    db = os.path.join(tmp, "v6.db")
    st = MemoryStore(db)
    st.add_episode(Episode(
        t="2026-10-30T09:00:00+08:00", kind="type", outcome="success",
        obs="回测 突破 代理基线 结论甲", action={"type": "type", "target": "t1"},
        result="r", verify={"verdict": "success"},
        session_id="s-v6-1", subject="agent", src_seq=None))
    st.close()
    try:
        MemoryStore(db, embedder=bge)
        raise AssertionError("口径守门失效：库=hash 却用 bge 无豁免开库成功")
    except RuntimeError as exc:
        assert "口径冲突" in str(exc), f"守门报错不明确: {exc}"

    # 3) reembed 换挡（豁免通道）：meta 换 bge、blob 全 512、同义改写可检
    st3 = MemoryStore(db, embedder=bge, allow_switch=True)
    try:
        res = st3.reembed_all(batch_size=8)
        assert res["episodes_reembedded"] == 1 and res["dim"] == C.EMBED_DIM
        assert "bge" in st3.get_meta("embedder")
        assert st3.get_meta("embed_dim") == str(C.EMBED_DIM)
        blob_len = st3.conn.execute(
            "SELECT length(embed) FROM episodes").fetchone()[0]
        assert blob_len == C.EMBED_DIM * 4, f"blob 长度 {blob_len} 不符 512 维"
        from agentctl.memory.retrieve import Retriever
        hits = [h for h in Retriever(st3).query(
            "刚写回去的新结论转头就能被搜到", k=3,
            subject="agent")["hits"] if h.get("source") == "episode"]
        assert hits, "reembed 后同义改写应命中"
        # 旧维废 blob 的精确不变量：**向量路径**跳过它（不崩、不贡献向量分）；
        # 关键词路径与 blob 无关，照常命中是正当行为
        st3.conn.execute("UPDATE episodes SET embed = ? WHERE id = 1", (b"12345678",))
        st3.conn.commit()
        st3._invalidate_matrix()
        qvec = st3.embedder.embed("回测 突破 代理基线")
        vec_hits = st3.search_episodes(qvec, k=5, candidate_ids=[1])
        assert vec_hits == [], "向量路径竟检回废维 blob（长度判废失效）"
        # 恢复合法 blob，向量路径应重新可见
        st3.reembed_all(batch_size=8)
        vec_hits2 = st3.search_episodes(qvec, k=5, candidate_ids=[1])
        assert vec_hits2 != [], "reembed 后向量路径未恢复"
    finally:
        st3.close()

    # 4) open_store 自动匹配：meta=bge 的库 ⇒ 开出来就是 bge 连接；反向（hash 开）仍炸
    st4 = mem_pkg.open_store(db)
    try:
        assert "bge" in str(getattr(st4.embedder, "NAME", "")), \
            "open_store 未按 meta 匹配 bge"
    finally:
        st4.close()
    try:
        MemoryStore(db, embedder=HashNGramEmbedder())
        raise AssertionError("口径守门失效：meta=bge 却用哈希开库成功")
    except RuntimeError:
        pass



# ── 入口 ────────────────────────────────────────────────────────────────────

def t_inject_v5() -> list[str]:
    """v5 三组的注入自证：**把功能关掉，对应判据必须变红**（派单硬约束 4）。

    ⑮ 关 DEDUP_ENABLED     → 同 obs 写两次断言必红
    ⑯ 关 REQUIRE_PROVENANCE → 无出处 success 的拒收断言必红
    ⑰ 破坏 actor→subject 映射 → 归因断言必红
    返回被抓红的组名列表；任何一组没红就抛 AssertionError（判据是死的）。
    """
    import agentctl.memory.intake as I

    base = tempfile.mkdtemp(prefix="p5_inject_v5_")
    caught: list[str] = []

    def expect_red(tag: str, fn) -> None:
        try:
            fn()
        except AssertionError:
            caught.append(tag)
            return
        raise AssertionError(f"注入后 {tag} 仍全绿 —— 该组判据没咬合")

    saved_dedup = I.DEDUP_ENABLED
    I.DEDUP_ENABLED = False
    try:
        expect_red("⑮", lambda: t_intake_dedup(os.path.join(base, "a")))
    finally:
        I.DEDUP_ENABLED = saved_dedup

    saved_prov = I.REQUIRE_PROVENANCE
    I.REQUIRE_PROVENANCE = False
    try:
        expect_red("⑯", lambda: t_lessons_ingest(os.path.join(base, "b")))
    finally:
        I.REQUIRE_PROVENANCE = saved_prov

    saved_map = C.subject_for_actor
    C.subject_for_actor = lambda actor: C.SUBJECT_HUMAN  # noqa: ARG005
    try:
        expect_red("⑰", lambda: t_actor_attribution(os.path.join(base, "c")))
    finally:
        C.subject_for_actor = saved_map
    return caught


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="注入自证：判据必须变红")
    args = ap.parse_args()
    if args.inject:
        t_inject_self()
        print("inject 自证：① 谎言被抓住（判据活着）")
        caught = t_inject_v5()
        print(f"inject 自证：v5 三组注入全部抓红 {caught}")
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
    check("⑫ 混合时区 promote 不崩", lambda: t_mixed_timezone(tmp))
    check("⑬ 时间倒序入库检索命中", lambda: t_time_inversion(tmp))
    check("⑭ v3 主体分仓（迁移/范围/新近回退）", lambda: t_subject_v3(tmp))
    check("⑱ v6 嵌入器换装（bge/reembed/守门）", lambda: t_embedder_v6(tmp))
    check("⑮ v5 intake 去重合并（recurrences/source）", lambda: t_intake_dedup(tmp))
    check("⑯ v5 教训 JSONL（断点续收/出处强制/映射）", lambda: t_lessons_ingest(tmp))
    check("⑰ v5 主体归因 actor（词表/映射/回填）", lambda: t_actor_attribution(tmp))
    if FAILS:
        print(f"RED x{len(FAILS)}")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
