# -*- coding: utf-8 -*-
r"""python -m agentctl.memory —— 记忆层 v0 CLI（拉取式，无守护进程）

    python -m agentctl.memory harvest [--limit N] [--timeline DIR]
    python -m agentctl.memory compact
    python -m agentctl.memory query "文本" [--k 5]
    python -m agentctl.memory stats

约定：
    · episode 库路径：AGENTCTL_MEMORY_DB 或 <仓根>/work/memory/episodes.db
    · 输出一律 JSON（UTF-8），退出码：0 成功；2 用法/环境问题
    · 本 CLI 只做编排；口径全部在 contract.py 与各模块
"""
from __future__ import annotations

import argparse
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import agentctl.memory as mem


def _emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agentctl.memory", description="记忆层 v0 CLI")
    ap.add_argument("--db", default=None, help="episode 库路径（默认 AGENTCTL_MEMORY_DB 或仓内 work/memory）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_h = sub.add_parser("harvest", help="timeline.db → episodes（增量幂等）")
    p_h.add_argument("--limit", type=int, default=None)
    p_h.add_argument("--timeline", default=None, help="timeline 目录（默认 AGENTCTL_TIMELINE 或仓内 work/timeline）")

    p_c = sub.add_parser("compact", help="巩固：promote + merge（幂等）")
    p_c.add_argument(
        "--rebuild-segments", action="store_true",
        help="作废全部段并从 episodes 全量重 promote（段口径升级/区间损坏时用）",
    )

    p_d = sub.add_parser("distill", help="经验提炼：成功会话 → workflows（幂等 upsert）")

    p_q = sub.add_parser("query", help="检索 episodes + segments + workflows")
    p_q.add_argument("text")
    p_q.add_argument("--k", type=int, default=5)

    p_rl = sub.add_parser(
        "record-lessons", help="教训 JSONL → episodes（去重合并 + 断点续收 + promote 收尾）"
    )
    p_rl.add_argument("file", help="教训 JSONL 路径（格式见 docs/lessons_jsonl_v1.md）")
    p_rl.add_argument("--no-promote", action="store_true", help="跳过 promote 收尾（调试用）")
    p_rl.add_argument("--cursor", default=None, help="游标文件路径（默认 <file>.cursor）")

    sub.add_parser("stats", help="库内计数与游标")

    args = ap.parse_args(argv)

    store = mem.open_store(args.db)
    try:
        if args.cmd == "harvest":
            from agentctl.memory.harvest import TimelineHarvester

            hv = TimelineHarvester(store, timeline_dir=args.timeline)
            try:
                _emit(hv.harvest(limit=args.limit))
            finally:
                hv.close()
        elif args.cmd == "compact":
            from agentctl.memory.compact import Compactor

            if getattr(args, "rebuild_segments", False):
                store.conn.execute("DELETE FROM segments")
                store.set_meta("promoted_until", "0")
                print("旧段已作废，promoted_until 归零，全量重建…", file=sys.stderr)
            _emit(Compactor(store).compact())
        elif args.cmd == "distill":
            from agentctl.memory.distill import distill_workflows

            _emit(distill_workflows(store))
        elif args.cmd == "query":
            from agentctl.memory.retrieve import Retriever

            _emit(Retriever(store).query(args.text, k=args.k))
        elif args.cmd == "record-lessons":
            from agentctl.memory.intake import ingest_lessons

            _emit(ingest_lessons(
                store, args.file,
                promote=not getattr(args, "no_promote", False),
                cursor_path=args.cursor,
            ))
        elif args.cmd == "stats":
            rows = list(store.iter_episode_rows())
            by_outcome: dict[str, int] = {}
            by_kind: dict[str, int] = {}
            for r in rows:
                by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
                by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
            segs = list(store.iter_segments(live_only=False))
            wf = store.get_meta("workflow_count")
            _emit(
                {
                    "db": args.db or mem.default_db_path(),
                    "memory_version": mem.MEMORY_VERSION,
                    "episodes": len(rows),
                    "by_outcome": by_outcome,
                    "by_kind": by_kind,
                    "segments_total": len(segs),
                    "segments_live": len([s for s in segs if not s.get("merged_into")]),
                    "workflows_meta": wf,
                    "harvest_cursor_seq": store.get_meta("harvest_cursor_seq", "0"),
                    "promoted_until": store.get_meta("promoted_until", "0"),
                }
            )
        else:
            ap.error(f"未知命令: {args.cmd}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
