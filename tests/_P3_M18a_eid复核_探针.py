# -*- coding: utf-8 -*-
"""M18-a 探针：对给定 feat 跑一次 core._eid_recheck（模式由环境变量/argv 切）。"""
import json, os, sys
sys.path.insert(0, r"<工作区>\Documents\agent-ctl\src")
hwnd = int(sys.argv[1]); feat = json.loads(sys.argv[2])
need_keys = sys.argv[3] == "1"; inject = len(sys.argv) > 4 and sys.argv[4] == "inject"
from agentctl import core
if inject:
    # 注入缺陷：劣化定位——「同类型同名即命中，忽略矩形」。
    # 这是精确定位最要命的漏法：同名/矩形漂移全部漏检（rect 不匹配也放行）。
    from agentctl import uia_tree
    def _degraded(h, feat_, roles):
        nodes, _t = uia_tree.walk(h, roles=tuple(roles or ("all",)),
                                  max_depth=25, include_offscreen=True)
        for n in nodes:
            if (n.get("type") == feat_.get("type")
                    and (n.get("name") or "") == (feat_.get("name") or "")):
                return [(n, "（注入：同名即命中，忽略矩形）")]
        return []
    core._eid_locate_fast = _degraded
try:
    cur, why, rctx = core._eid_recheck(hwnd, feat, {"roles": ["all"], "max_depth": 25},
                                       need_keys=need_keys)
except Exception as e:
    print(json.dumps({"档": "未知", "错误": "%s: %s" % (type(e).__name__, e)},
                     ensure_ascii=False)); sys.exit(0)
if cur is None:
    print(json.dumps({"档": "不在原位", "why": (why or "")[:160]}, ensure_ascii=False))
    sys.exit(0)
uid = ""
if need_keys:
    uid = (rctx or {}).get("uid") or core._screen_uid_at(rctx, cur)
print(json.dumps({"档": "通过", "center": cur.get("center"), "rect": cur.get("rect"),
                  "enabled": cur.get("enabled"), "uid": uid},
                 ensure_ascii=False))
