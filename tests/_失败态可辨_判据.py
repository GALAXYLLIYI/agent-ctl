# -*- coding: utf-8 -*-
r"""tests\_失败态可辨_判据.py —— 第 31 条判据：**失败态不许长得像「什么都没拿到」**。

治的病（不是某一个 bug，是 2026-10-19 窗口D 实跑挖出来的**一类**洞）：
    今天那四个洞（D1 MCP actor / D2 组合键打乱字符 / D3 双账本 / D4 通行证计数不归零）
    共同的形状不是「通道不够」，是 **「成功与失败长得一模一样」**：
      · MCP 写动作**恒定**报 code=10「没带 actor」—— 而真因是 actor 没被传下去，
        文案说的原因与真实原因**不是一回事**；
      · 乱字符和粘贴**都让画面变化** ⇒ 像素判据读成「成功」；
      · 新证一出生就报「次数已用尽」⇒ 弹窗上用户批的次数说了不算。
    这类洞最贵的地方在于：**它们不会被任何失败告警抓到**。
    红灯只抓「知道自己错了」，抓不到「伪装成对的」⇒ **绿灯比没闸门更危险**。

    ⇒ 本条就是框架 §⑧ 提的那条元结论的**可执行版本**：把「失败态与成功态在证据上
    可区分」从建议提成判据。它验的是映射层的**结构性质**，不是某一个具体 bug
    —— 具体 bug 修完了，这条还守着。

被测对象：src\agentctl\core.py::_map_rc（**纯逻辑函数**，不碰闸门、不碰屏幕）
  ⇒ 与第 30 条同款：**用户不在场也能复跑**。

四条断言：
  ① **复读禁令**（主断言）：失败回执的 warning 不许**严格等于**「老脚本返回码 N」。
     复读输入 = 零信息增量 = 失败态与「什么都没拿到」在证据上不可区分。
  ② **同码不同因**：落到同一契约码的不同 rc，warning 必须两两不同。
     （今天 D1 的形状：恒定 ⇒ 恒定的话 ⇒ agent 只能猜）
  ③ **证据非空**：失败回执的 data 里至少有一个**非空、非 None** 的定位字段。
  ④ **契约对账**：本判据写的 RC_MAP 必须与真源实际落码一致（防两边悄悄分叉）。
  ⑤ **全仓复读禁令**（★ 2026-10-19 补，静态）：真源里**一处都不许**再出现裸复读
     模板 f"老脚本返回码 {rc}" —— 它不只藏在 _map_rc 的 warn 表里：ctl_perceive 的
     兜底分支（core.py 约 1045 行）也有一模一样的复读，而 2026-10-13 的 S3-B2
     「未知 rc 不许静默兜底」**只修了 _map_rc 那条路** ⇒ 同一刀砍了一半。
     ①②③ 动态查「已知 rc 的行为」，⑤ 静态查「模板还在不在」，两者互补。

  ★ **注入即红**（--inject）：把 warning 全部退化成复读 ⇒ ① 红；模拟「源码里多了一处复读」⇒ ⑤ 红。

★ 诚实边界（必须写在这里，不许含糊）：
  · 本条**不验「某个 rc 的业务语义对不对」**。act.py 的 rc 产出点没有集中登记处，
    判据不猜、不硬编 —— 它只断言「不许复读」和「同码不同因要说不同话」，
    这两条不需要知道 rc 到底是什么就成立。
  · 本条**不能证明失败态对人可读**，只证明它**不复读**。
    「这句话 Agent 看完知道下一步干什么」属人工判断，不在本条口径内。
  · 断言①对**严格相等**生效，形如「老脚本返回码 4（详见 README）」的
    半复读**抓不到** —— 这是有意收窄：判据第一条必须窄而确定，
    宽而模糊的判据第一次翻车就会连累整条红线。

退出码：0=绿；1=红；2=验不了（找不到真源 src\agentctl\core.py）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# _map_rc 里那张 rc→契约码 的映射表。**照抄 src\agentctl\core.py 的 m**，
# 故意写成字面量而不是 import 后反射：这样 rc 表变了本判据会「跑不动」而不是
# 「跟着一起变绿」—— 判据必须比被测对象更严（框架 §⑧ 元判据那条教训）。
RC_MAP = {
    3: "CODE_DENY_WHITELIST", 7: "CODE_NEEDS_ASK", 9: "CODE_UNAUTH",
    4: "CODE_NO_CTRL", 5: "CODE_UNSTABLE", 6: "CODE_UNSTABLE",
    8: "CODE_NO_CHANGE", 127: "CODE_NO_CTRL",
}

GREEN, RED, SKIP = [], [], []


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"),
                              name, str(detail)[:220]), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def _find_src():
    r"""定位 src\agentctl\core.py。**不许把盘符写死**（tests\_M_可移植性判据.py 的红线）。"""
    cands = []
    env = os.environ.get("AGENTCTL_SRC")
    if env:
        cands.append(env)
    cands.append(os.path.join(ROOT, "src"))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "agentctl", "core.py")):
            return c
    return ""


def _meaningful(data):
    r"""data 里有没有一个**非空、非 None** 的定位字段。
    注意：占位符也算「有」，本条只管「有没有」，不管「准不准」——
    「准不准」是断言②（不同因不同话）管的，两条分工不许混。
    """
    for k, v in (data or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return "%s=%s" % (k, str(v)[:40])
    return ""


def main():
    inject = "--inject" in sys.argv
    src = _find_src()
    if not src:
        rec("前置·找到真源 src/agentctl/core.py", False, "候选目录都没找到", skip=True)
        return 2
    sys.path.insert(0, src)
    from agentctl import core, gate

    # ⑤ 的被测文本：读真源源码本身（静态）。--inject 时往里塞一处复读，模拟
    # 「有人在别处又写了一个复读模板」—— 断言⑤ 必须当场红。
    core_path = os.path.join(src, "agentctl", "core.py")
    try:
        SRC_TEXT = open(core_path, encoding="utf-8").read()
    except OSError as e:
        SRC_TEXT = ""
        rec("⑤ 读真源源码", False, e, skip=True)
    if inject:
        SRC_TEXT = SRC_TEXT + '\n    _inj_probe = f"老脚本返回码 {rc}"\n'
        print("（--inject：源码已注入一处复读模板 ⇒ 期望 ⑤ 红）", flush=True)

    _echo_tpl = re.compile(r"老脚本返回码\s*\{rc\}")
    _hits = [i + 1 for i, ln in enumerate(SRC_TEXT.splitlines())
             if _echo_tpl.search(ln) and not ln.lstrip().startswith("#")]
    rec('⑤ 真源里不许再出现裸复读模板 f"老脚本返回码 {rc}"', len(_hits) == 0,
        ("命中 %d 行：%s" % (len(_hits), _hits[:8])) if _hits else "0 处 ⇒ 复读这条路已封死")

    real = core._map_rc
    if inject:
        # 注入缺陷：把每个失败回执的 warning **退化成纯复读**。
        # 这正是 2026-10-19 D1 的病根形态（恒定一句话、与真因无关）——
        # 断言①如果抓不住它，说明这条判据本身是死的，必须跟着一起红。
        def _broken(rc, out, err):
            env = dict(real(rc, out, err))
            if rc != 0:
                env["warning"] = "老脚本返回码 %d" % rc
            return env
        core._map_rc = _broken
        print("（--inject：warning 已全部退化成纯复读 ⇒ 期望 ① 红、判据红）", flush=True)

    try:
        seen = {}   # 契约码 → {rc: warning}，供断言②用
        for rc in sorted(RC_MAP):
            env = core._map_rc(rc, "stdout-text", "stderr-text")
            code = env.get("code")
            warn = (env.get("warning") or "").strip()
            seen.setdefault(code, {})[rc] = warn

            # ① 复读禁令（本条主断言）
            echo = "老脚本返回码 %d" % rc
            rec("① rc=%d 的 warning 不许复读输入" % rc, warn != echo,
                ("复读了：%r（零信息增量，与「什么都没拿到」不可区分）" % warn)
                if warn == echo else repr(warn[:60]))

            # ③ 证据非空
            ev = _meaningful(env.get("data"))
            rec("③ rc=%d 失败回执带非空定位字段" % rc, bool(ev),
                ev or "data 全是空值/None ⇒ 失败态定位不到原因")

            # ④ 契约对账（本判据的 RC_MAP vs 真源实际落码）
            want = getattr(gate, RC_MAP[rc], None)
            rec("④ rc=%d 真源落码 == %s" % (rc, RC_MAP[rc]), want is not None and want == code,
                "本判据写 %s=%s，真源落 code=%s" % (RC_MAP[rc], want, code))

        # ② 同码不同因必须不同话
        for code in sorted(seen, key=lambda c: (c is None, c)):
            rcs = seen[code]
            if len(rcs) < 2:
                continue
            uniq = set(rcs.values())
            rec("② 契约码 code=%s 下 %d 个 rc 的 warning 必须可区分" % (code, len(rcs)),
                len(uniq) == len(rcs),
                "%d 个 rc 只说出 %d 句话：%s" % (len(rcs), len(uniq),
                                                dict((r, w[:26]) for r, w in rcs.items())))
    finally:
        core._map_rc = real

    if RED:
        print("\n红 %d：%s" % (len(RED), "；".join(RED)), flush=True)
        return 1
    print("\n全绿 %d" % len(GREEN), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
