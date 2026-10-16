# -*- coding: utf-8 -*-
r"""Codex 独立黑盒判据 · 2026-10-15

查的不是「四窗自己说绿了没」，而是查四窗之间没人管的接缝：
契约（唯一真相源）与代码实际行为是否脱节。

设计依据 = AGENT准则「甲. 开工前黑盒判据」三条：
  1) 能指到具体 文件:行 / 命令
  2) 派单方（Codex）能独立重跑，不依赖任何执行方的脚本
  3) 能跑红 —— --inject 往契约副本注入不一致，证明判据是活的

本脚本只读：不写契约、不写产品代码。
判红时给非零退出码（否则挂门禁等于没门 —— 参见
work\_T1_退回_W2_判据退出码.md 的教训）。

用法：
  python -X utf8 tests\_codex_接缝判据.py            # 验真契约
  python -X utf8 tests\_codex_接缝判据.py --inject   # 注入缺陷，自证能跑红

── 判据改错史（留档，别再犯）────────────────────────────
v1 的 J2「契约每个入参代码都读得到」与 J3「cli.py 不得出现 ctl_ 字面量」
  都**假红**，已作废（2026-10-15 实测证伪）：
    · J2 误报 ctl_shot.region/scale/format/quality/inline 等 —— 这些是
      **透传给下游 legacy 子进程**的（t_shot → _perceive(args, extra)），
      core.py 里本来就没有 args.get 痕迹。改成只查 required。
    · J3 误报 17 个工具名 —— cli.py:32-38 是 **ALIAS 短名别名表**，
      cli.py:165 还有「ctl_」拼接兜底，两者都是 CLI 必需机制；
      W4 承诺的只是「**参数表**从契约生成」，管的是 help 那部分。
      改成查「别名表是否指向契约外的幽灵工具」。
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
CONTRACT = os.path.join(ROOT, "contract", "tools.json")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

RESULTS = []
_ACCEPTED = None


def check(cid, desc, ok, detail=""):
    RESULTS.append((cid, desc, bool(ok), detail))


def accepted_args():
    """代码里真正被读过的入参名集合（静态扫 args.get / args[...]）。"""
    global _ACCEPTED
    if _ACCEPTED is None:
        blob = ""
        d = os.path.join(SRC, "agentctl")
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py"):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    blob += f.read()
        keys = set()
        keys |= set(re.findall(r'args\.get\(\s*["\x27]([a-zA-Z_][a-zA-Z0-9_]*)', blob))
        keys |= set(re.findall(r'args\[\s*["\x27]([a-zA-Z_][a-zA-Z0-9_]*)', blob))
        keys |= set(re.findall(r'\.get\(\s*["\x27]([a-zA-Z_][a-zA-Z0-9_]*)', blob))
        _ACCEPTED = keys
    return _ACCEPTED


def contract_tools(obj):
    ts = obj.get("tools") or []
    if isinstance(ts, dict):
        return dict(ts)
    return {t.get("name"): (t or {}) for t in ts if isinstance(t, dict)}


def schema_of(spec):
    return spec.get("input_schema") or spec.get("inputSchema") or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true",
                    help="往契约副本注入缺陷，证明本判据能跑红（不改真契约）")
    args = ap.parse_args()

    with open(CONTRACT, encoding="utf-8") as f:
        contract = json.load(f)
    ctools = contract_tools(contract)

    if args.inject:
        # 注入1：契约少一个工具（代码里有）⇒ J1 红
        ctools.pop("ctl_browser", None)
        # 注入2：契约多一个幽灵工具 ⇒ J1 红；ALIAS 里的 browser 也变孤儿 ⇒ J3 红
        ctools["ctl_幽灵工具"] = {"name": "ctl_幽灵工具",
                                  "input_schema": {"properties": {}}}
        # 注入3：必填一个代码根本不认的字段 ⇒ J2 红
        ctools["ctl_status"] = dict(ctools.get("ctl_status") or {})
        ctools["ctl_status"]["input_schema"] = {
            "properties": {}, "required": ["绝不该必填的字段"]}

    from agentctl import core
    code_tools = set(core.TOOLS)
    acc = accepted_args()

    only_code = sorted(code_tools - set(ctools))
    only_contract = sorted(set(ctools) - code_tools)
    check("J1", "契约工具名单与 core.TOOLS 双向一致（不多不少）",
          not only_code and not only_contract,
          "只在代码里：%s / 只在契约里：%s" % (only_code or "无", only_contract or "无"))

    missing_req = []
    for name, spec in sorted(ctools.items()):
        for p in (schema_of(spec).get("required") or []):
            if p not in acc:
                missing_req.append("%s.%s" % (name, p))
    check("J2", "契约 required 字段代码里都读得到（不会漏传后无提示）",
          not missing_req, "代码里读不到的 required：%s" % (missing_req or "无"))

    with open(os.path.join(SRC, "agentctl", "cli.py"), encoding="utf-8") as f:
        cli_src = f.read()
    m_alias = re.search(r"ALIAS\s*=\s*\{(.*?)\}", cli_src, re.S)
    alias_vals = set(re.findall(r'"(ctl_[a-z_]+)"', m_alias.group(1))) if m_alias else set()
    ghost = sorted(alias_vals - set(ctools))
    check("J3", "ALIAS 别名表指向的工具都在契约里（不指幽灵）",
          not ghost, "指向契约外工具的别名：%s" % (ghost or "无"))

    ver = contract.get("schema_version")
    m = re.search(r"schema_version\s*(\d+)\s*(?:→|->)\s*(\d+)",
                  str(contract.get("_2026-10-14_T1窗1") or ""))
    check("J4", "契约 changelog 声明的目标版本 == 实际 schema_version",
          bool(m) and int(m.group(2)) == ver,
          "schema_version=%s / changelog 说=%s" % (ver, m.group(2) if m else "没解析到"))

    red = len([1 for r in RESULTS if not r[2]])
    print("=" * 78)
    print("Codex 接缝判据 · 模式 %s"
          % ("自证跑红（注入缺陷）" if args.inject else "验真契约"))
    print("契约 %s" % CONTRACT)
    print("-" * 78)
    for cid, desc, ok, detail in RESULTS:
        print("[%s] %s  %s" % ("绿" if ok else "红", cid, desc))
        if detail:
            print("        %s" % detail)
    print("-" * 78)
    print("判据 %d 条：绿 %d，红 %d" % (len(RESULTS), len(RESULTS) - red, red))
    print("结论：%s" % ("全绿" if not red else "有红"))
    if args.inject and not red:
        print("★★ 注入缺陷后仍全绿 ⇒ 本判据是死的，不能拿来发成绩")
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
