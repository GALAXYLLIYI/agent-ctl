# -*- coding: utf-8 -*-
r"""S3 验收-w1：闸门并发原子性 worker
被 s3_accept_gate.py 用 subprocess 反复拉起，每次尝试消费一次通行证。
"""
import os
import sys

sys.path.insert(0, os.environ["AGENTCTL_SRC"])
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agentctl import gate  # noqa: E402

actor = sys.argv[1] if len(sys.argv) > 1 else "w"
try:
    gate.require("B", f"worker-{actor}")
    print("OK")
    raise SystemExit(0)
except gate.GateDenied as e:
    print("DENIED", e.code, str(e)[:60])
    raise SystemExit(1)
