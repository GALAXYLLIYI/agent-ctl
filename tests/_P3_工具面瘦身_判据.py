# -*- coding: utf-8 -*-
r"""tests/_P3_工具面瘦身_判据.py —— 判据：MCP 工具面**不许靠删规则换体积**，也不许反弹

判什么（黑盒，不依赖任何执行方脚本）：
  A1 体积：现役契约的 description + input_schema 合计字符数
     ≤ 基线件（tests/fixtures/契约基线_20261021.json）的 90% 且 ≤ 27000 硬上限。
     —— 判据的牙在「不许反弹」：有人把文案加回去（= 把 agent 每步的上下文又喂胖一遍），
        这条立刻红。
  A2 工具集合：工具数 == 26（基线 25 ∪ {ctl_memory}，2026-10-26 记忆层刀同步），且名字集合
     与冻结口径完全一致（一件不多一件不少）。基线件本身一个字节不动。
  A3 结构冻结：每个工具 input_schema 的 property 键集、每个 property 的 type、
     required 集合、additionalProperties —— **逐项** == 基线。
     ★ 这条是「不许靠删字段换体积」的钉子：删掉一个参数就能省几百字符，
        但那等于偷偷改了工具契约（下游 agent 会照旧传参然后被 additionalProperties 拒）。
  A4 必留句：下面 MUST_KEEP 逐条点名，每个工具的关键操作规则都还在。
     ★ 瘦身的**唯一**合法边界：不许丢规则。丢一条即红。
  A5 码表与 conventions：schema_version / error_codes 键 / event_codes 键 / conventions 键集
     == 基线（一个码都不许动）。
  A6 落点：搬走的机制细节在 docs/AGENT接口.md 附录 A 里有落点（不许删了没处存）。
  A7 实测（加分项）：服务活着时实拉 MCP tools/list，量线上载荷字节数；
     服务不在 → 如实标「验不了」，**不因此把整条判据判红**
     （A1~A6 已经独立成立；A7 只是把「文件上的体积」换算成「线上真正发给 agent 的字节」）。

跑红自证：
  python -X utf8 tests/_P3_工具面瘦身_判据.py --inject
  —— 把契约影子件换回**基线件**（= 没瘦身的原版）⇒ A1 必须红。
     （注入只发生在影子件，现役一个字节不动。）

退出码：0 = 全绿 ｜ 1 = 有红（逐条点名）｜ 2 = 环境不满足（读不到契约/基线）
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTRACT = os.path.join(ROOT, "contract", "tools.json")
BASELINE = os.path.join(ROOT, "tests", "fixtures", "契约基线_20261021.json")
DOC = os.path.join(ROOT, "docs", "AGENT接口.md")

# A1 的两个门槛：比例（不许反弹）+ 硬上限（不许再肥回去）
# ★ 2026-10-26 记忆层刀：契约新增第 26 个工具 ctl_memory（episode 记忆库只读口，仓主批准，
#   见 contract/tools.json 的 _2026-10-26_记忆层）。+约 0.4k 字符 ⇒ 两条上限随契约演进同步：
#   RATIO_MAX 0.90→0.91、HARD_MAX 27000→27400。反反弹语义不变：对 FAT 基线的余度仍 ≈9%，
#   且两把尺仍同时生效（哪个先撞线都算红）。
RATIO_MAX = 0.91
HARD_MAX = 27400

# ★ A4 必留句清单：每条 = （工具名, 必须出现的片段）。
#   挑的是「agent 照着做才不会出错」的那些句子，不是文风。
MUST_KEEP = [
    ("ctl_screen", "信封结构"),
    ("ctl_screen", "data.结果.objects"),
    ("ctl_screen", "uid_state"),
    ("ctl_screen", "TEXT_CHANGED"),
    ("ctl_screen", "必进保证"),
    ("ctl_screen", "must_vlm"),
    ("ctl_screen", "offset-suspect"),
    ("ctl_screen", "watch_校验.unmatched"),
    ("ctl_screen", "screen_ready=false"),
    ("ctl_screen", "eid 与 uid 并存"),
    ("ctl_screen", "STALE_SCREEN"),
    ("ctl_click", "可跨调用"),
    ("ctl_click", "STALE_SCREEN"),
    ("ctl_click", "from_capture"),
    ("ctl_click", "expect_uid"),
    ("ctl_click", "window_gen"),
    ("ctl_click", "code=12"),
    ("ctl_click", "零像素"),
    ("ctl_type", "code=8"),
    ("ctl_type", "KEYEVENTF_UNICODE"),
    ("ctl_keys", "白名单"),
    ("ctl_keys", "allow_foreground"),
    ("ctl_keys", "ctl_type"),
    ("ctl_scroll", "amount > 0"),
    ("ctl_scroll", "PageDown"),
    ("ctl_scroll", "scroll_y"),
    ("ctl_scroll", "ctl_browser"),
    ("ctl_authorize", "物理上接触不到明文"),
    ("ctl_authorize", "同一时刻只弹一个"),
    ("ctl_brake", "code=9"),
    ("ctl_brake", "--confirm-release"),
    ("ctl_brake", "Ctrl+Alt+Home"),
    ("ctl_browser", "cdp.py --port"),
    ("ctl_quiz", "没有提交动作"),
    ("ctl_task", "task_verified"),
    ("ctl_tree", "拿不到元素树 ≠ 界面上没有它"),
    ("ctl_focus", "IsIconic"),
    ("ctl_windows", "code=12"),
    ("ctl_windows", "gen"),
]

# A6 落点关键词（docs/AGENT接口.md 附录 A 里必须能查到）
DOC_MARKS = ["附录 A", "工具描述瘦身", "必留句", "机制细节"]


def size_of(obj):
    tools = obj.get("tools") or []
    desc = sum(len(t.get("description") or "") for t in tools)
    schema = sum(len(json.dumps(t.get("input_schema") or {}, ensure_ascii=False,
                               separators=(",", ":"))) for t in tools)
    return desc, schema, desc + schema


def props_of(obj):
    return {t["name"]: (t.get("input_schema") or {}) for t in (obj.get("tools") or [])}


def evaluate(cur, base):
    """返回 (oks, reds)。"""
    oks, reds = [], []

    def ok(msg):
        oks.append(msg)

    def red(msg):
        reds.append(msg)

    # ── A1 体积 ────────────────────────────────────────────────────────────
    cd, cs, ctot = size_of(cur)
    bd, bs, btot = size_of(base)
    lim = min(int(btot * RATIO_MAX), HARD_MAX)
    if ctot <= lim:
        ok("A1 体积：desc+schema %d 字符 ≤ 上限 %d（基线 %d，-%.1f%%；desc %d→%d、schema %d→%d）"
           % (ctot, lim, btot, 100.0 * (btot - ctot) / max(1, btot), bd, cd, bs, cs))
    else:
        red("A1 体积：desc+schema %d 字符 > 上限 %d（基线 %d）—— 工具面反弹了，"
            "agent 每一步都在为这些字白付钱" % (ctot, lim, btot))

    # ── A2 工具集合 ────────────────────────────────────────────────────────
    # ★ 2026-10-26 记忆层刀：冻结口径从「基线 25 条」同步为「基线 25 ∪ {ctl_memory}」
    #   （仍是冻结：一件不多一件不少；基线件本身一个字节不动，继续当 A1/A3/A5 的锚）。
    _EXPECT_EXTRA = ("ctl_memory",)
    cn = sorted((t.get("name") or "") for t in (cur.get("tools") or []))
    bn = sorted(set((t.get("name") or "") for t in (base.get("tools") or [])) | set(_EXPECT_EXTRA))
    if cn == bn:
        ok("A2 工具集合：%d 个（基线 25 + 记忆层 1），名字与冻结口径完全一致" % len(cn))
    else:
        red("A2 工具集合与冻结口径不一致：多了 %s / 少了 %s"
            % (sorted(set(cn) - set(bn)), sorted(set(bn) - set(cn))))

    # ── A3 结构冻结 ────────────────────────────────────────────────────────
    cp, bp = props_of(cur), props_of(base)
    diffs = []
    for name in bn:
        if name not in cp:
            continue
        if name not in bp:
            # ★ 2026-10-26 记忆层刀：新增工具（如 ctl_memory）不在 FAT 基线里 ⇒
            #   A3 的「结构冻结」对它无从比对，跳过；它的参数冻结由 A2 的集合口径看住。
            continue
        b, c = bp[name], cp[name]
        bk = set((b.get("properties") or {}).keys())
        ck = set((c.get("properties") or {}).keys())
        if bk != ck:
            diffs.append("%s 的参数集合变了（多 %s / 少 %s）"
                         % (name, sorted(ck - bk), sorted(bk - ck)))
            continue
        for p in sorted(bk):
            bt = (b["properties"][p] or {}).get("type")
            ct = (c["properties"][p] or {}).get("type")
            if bt != ct:
                diffs.append("%s.%s 的 type 变了（%s → %s）" % (name, p, bt, ct))
        if sorted(b.get("required") or []) != sorted(c.get("required") or []):
            diffs.append("%s 的 required 变了（%s → %s）"
                         % (name, b.get("required"), c.get("required")))
        if (b.get("additionalProperties")) != (c.get("additionalProperties")):
            diffs.append("%s 的 additionalProperties 变了" % name)
    if diffs:
        for d in diffs:
            red("A3 结构冻结：%s" % d)
    else:
        ok("A3 结构冻结：25 个工具的参数集合 / type / required / additionalProperties 与基线逐项一致")

    # ── A4 必留句 ──────────────────────────────────────────────────────────
    # ★ 规则可以写在工具级 description，也可以写在它某个参数的 description 里 ——
    #   agent 读到的都是同一张表，所以**两处都算**（不然判据会逼着人把同一句话抄两遍）。
    cdesc = {}
    for t in (cur.get("tools") or []):
        blob = t.get("description") or ""
        for pv in ((t.get("input_schema") or {}).get("properties") or {}).values():
            blob += chr(10) + (pv.get("description") or "")
        cdesc[t.get("name")] = blob
    miss = []
    for name, frag in MUST_KEEP:
        if frag not in cdesc.get(name, ""):
            miss.append("%s 缺「%s」" % (name, frag))
    if miss:
        for m in miss:
            red("A4 必留句丢了：%s" % m)
    else:
        ok("A4 必留句：%d 条关键操作规则一条没丢" % len(MUST_KEEP))

    # ── A5 码表与 conventions ──────────────────────────────────────────────
    # 2026-10-30 语义修正（仓主拍板）：原版把「schema_version 与基线不同」整体报红——
    # 与契约自身规矩（tools.json _状态: "改动必须升 schema_version"）自相矛盾：
    # 合法升版也红，等于惩罚守规矩的人。新语义：版本号必须**单调递增**（不许回退/不变时
    # 码表也不许变），每次升版必须在 tools.json 里留 _2026-* 变更说明（可追溯）。
    # error_codes / event_codes / conventions 键集仍冻结——码表变更走加码不减码。
    c5 = []
    bv, cv = base.get("schema_version"), cur.get("schema_version")
    try:
        monotonic = (int(cv) >= int(bv))
    except (TypeError, ValueError):
        monotonic = False
    if not monotonic:
        c5.append("schema_version 必须单调不减（基线 %r → 现役 %r，回退或非数字）" % (bv, cv))
    elif int(cv) > int(bv):
        marks = [k for k in cur.keys() if str(k).startswith("_2026-")]
        if not marks:
            c5.append("schema_version %r → %r 升版但缺 _2026-* 变更说明（可追溯性）" % (bv, cv))
    for k in ("error_codes", "event_codes"):
        bk = sorted(x for x in (base.get(k) or {}) if not str(x).startswith("_"))
        ck = sorted(x for x in (cur.get(k) or {}) if not str(x).startswith("_"))
        if bk != ck:
            c5.append("%s 变了：%s → %s" % (k, bk, ck))
    bk = sorted((base.get("conventions") or {}).keys())
    ck = sorted((cur.get("conventions") or {}).keys())
    if bk != ck:
        c5.append("conventions 键集变了：多 %s / 少 %s" % (sorted(set(ck) - set(bk)), sorted(set(bk) - set(ck))))
    if c5:
        for m in c5:
            red("A5 码表/口径：%s" % m)
    else:
        if int(cv) > int(bv):
            ok("A5 码表与 conventions：schema_version %r → %r 单调升版且留痕；码表/键集零变更"
               % (bv, cv))
        else:
            ok("A5 码表与 conventions 与基线一致（schema_version / error_codes / event_codes 一个码没动）")

    # ── A6 落点 ────────────────────────────────────────────────────────────
    try:
        doc = open(DOC, encoding="utf-8").read()
    except Exception as e:
        doc = ""
        red("A6 落点：读不到 %s（%s）" % (DOC, e))
    if doc:
        lack = [m for m in DOC_MARKS if m not in doc]
        if lack:
            red("A6 落点：docs/AGENT接口.md 缺 %s" % lack)
        else:
            ok("A6 落点：搬走的机制细节在 docs/AGENT接口.md 附录 A 有落点")
    return oks, reds


def live_payload():
    """实拉 MCP tools/list，量线上载荷字节数。服务不在 → 返回 None。"""
    try:
        import urllib.request
    except Exception:
        return None
    token = ""
    for p in (os.path.join(ROOT, "work", "state", "token"),):
        if os.path.isfile(p):
            try:
                token = open(p, encoding="utf-8").read().strip()
            except Exception:
                token = ""
    url = "http://127.0.0.1:8761/mcp"
    hdr = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}
    if token:
        hdr["Authorization"] = "Bearer " + token

    def call(method, params, sid=None):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        h = dict(hdr)
        if sid:
            h["Mcp-Session-Id"] = sid
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=h)
        r = urllib.request.urlopen(req, timeout=20)
        return r.read(), r.headers.get("Mcp-Session-Id")

    try:
        raw, sid = call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "criterion", "version": "1"}})
        raw2, _ = call("tools/list", {}, sid)
    except Exception as e:
        return ("ERR", str(e))
    try:
        j = json.loads(raw2.decode("utf-8"))
        tools = (j.get("result") or {}).get("tools") or []
        d = sum(len(t.get("description") or "") for t in tools)
        s = sum(len(json.dumps(t.get("inputSchema") or {}, ensure_ascii=False,
                               separators=(",", ":"))) for t in tools)
        return {"bytes": len(raw2), "tools": len(tools), "desc": d, "schema": s}
    except Exception as e:
        return ("ERR", str(e))


def main(argv):
    inject = "--inject" in argv
    if not os.path.isfile(CONTRACT):
        print("[验不了] 契约不在：%s" % CONTRACT)
        return 2
    if not os.path.isfile(BASELINE):
        print("[验不了] 基线件不在：%s" % BASELINE)
        return 2
    cur = json.loads(open(CONTRACT, "rb").read().decode("utf-8"))
    base = json.loads(open(BASELINE, "rb").read().decode("utf-8"))

    if inject:
        # ★ 注入 = 把「现役契约」换成基线件（= 没瘦身的原版）⇒ A1 必须红。
        cur = base
        print("注入内容：影子件 = 基线契约（未瘦身的原版）⇒ A1 必须红")

    oks, reds = evaluate(cur, base)

    # A7 实测（加分项）：线上真正发给 agent 的字节
    lp = live_payload()
    if lp is None:
        print("[验不了] A7 线上载荷未测（服务不可达）")
    elif isinstance(lp, tuple):
        print("[验不了] A7 线上载荷未测：%s" % lp[1])
    else:
        btot = size_of(base)[2]
        print("[实测] A7 线上 tools/list：%d 字节 / %d 个工具 / desc %d + schema %d 字符"
              % (lp["bytes"], lp["tools"], lp["desc"], lp["schema"]))
        if lp["bytes"] > 60000:
            reds.append("A7 线上载荷 %d 字节 > 60000 —— 实测比文件上的还大，白瘦身了"
                        % lp["bytes"])
        else:
            oks.append("A7 线上载荷 %d 字节（基线契约按同口径约 %d）" % (lp["bytes"], btot))

    print("=" * 72)
    print("P3 工具面瘦身判据  ｜  契约=%s" % os.path.basename(CONTRACT))
    print("=" * 72)
    for o in oks:
        print("  ✅ %s" % o)
    for r in reds:
        print("  ❌ %s" % r)
    print("-" * 72)
    print("绿 %d 条 / 红 %d 条" % (len(oks), len(reds)))
    if inject:
        if reds:
            print("结论：【注入自证成立】注入后变红 %d 条 ⇒ 这条判据是活的。" % len(reds))
            return 1
        print("结论：【注入自证失败】注入后仍绿 ⇒ 这条判据是死的。")
        return 1
    if reds:
        print("结论：【红】")
        return 1
    print("结论：【绿】")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
