# -*- coding: utf-8 -*-
"""威胁模型对抗探针骨架 v0.2（派单 Z1 · 2026-10-25）

★ 这是什么：
    docs\\威胁模型_20261025.md（v0.1）里 T01–T12 对抗测试矩阵的**可执行骨架**。
    v0.2 只做三件事：登记（list）、前置检查＋打印计划（dry-run）、带人工确认的放行链路（real）。
    **不发送任何输入**：真实攻击步骤只写在文档 §三/§四，本版代码到"计划层"为止。

★ 子命令与退出码（RC_*）：
    list                          列 T01–T12 与状态（对照 v0.1 文档的 ✅/⏳）
    dry-run <Txx>                 只做前置检查与打印计划；**零副作用**（不发输入/不截图/
                                  不写仓内文件/不 import pyautogui）；环境不满足 ⇒ 3
    real <Txx> [--i-understand]   默认拒绝 ⇒ 2（需人工确认）；且只允许 REAL_ALLOWED
                                  （T09/T10/T11 这类已有判据的项），非白名单 ⇒ 1；
                                  环境不满足 ⇒ 3；全过 ⇒ 打印计划并停在计划层 ⇒ 0
    退出码：0 正常 ｜ 1 参数错 ｜ 2 需人工确认 ｜ 3 环境不满足

★ 零副作用的实现口径（判据会盯，tests\\_P3_威胁模型_判据.py）：
    · 全文件只用标准库，从不 import pyautogui / win32*；
    · 唯一输入咽喉 = _send_input()：未来任何 real 执行器要发输入（SendInput/pyautogui/…）
      必须且只能经过它。v0.2 没有任何调用点（真被调到 = 结构性事故，当场 RuntimeError）；
    · 判据用 monkeypatch 换掉 _send_input 再跑全部 dry-run，断言它一次都没被调
      ⇒ dry-run 路径结构性到不了输入。

★ needs_desktop 语义（别误读）：
    True ⟺ "已有判据、预期明确，且 real 复验必须向真实桌面发输入" ⟺ 在 REAL_ALLOWED 里。
    T02/T03 等纸面项的方法虽涉及桌面输入，但预期未定、无判据 ⇒ False（拒绝 real）；
    补了判据才许改 True 并入白名单——判据断言两边集合相等，改一边不改另一边必红。

★ 跑法（python -X utf8）：
    tools\\threat_probe.py list
    tools\\threat_probe.py dry-run T09
    tools\\threat_probe.py real T09                 ⇒ 2（不带开关，默认拒绝）
    tools\\threat_probe.py real T09 --i-understand   ⇒ 走完环境检查后停在计划层，不发输入
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DOC = os.path.join(REPO, "docs", "威胁模型_20261025.md")
SRC_AGENTCTL = os.path.join(REPO, "src", "agentctl")
WORK_DIR = os.path.join(REPO, "work")

RC_OK = 0        # 正常
RC_ARGS = 1      # 参数错（未知子命令/未知编号/非白名单项带开关闯 real）
RC_CONFIRM = 2   # 需人工确认（real 未带 --i-understand）
RC_ENV = 3       # 环境不满足（前置检查/交互桌面不通过）

# ── real 白名单：v0.1 文档里已有判据（✅）且复验必须向真实桌面发输入的项 ──
# 判据断言「needs_desktop=True 的集合 == 本白名单」；--inject 删掉其中一项必红。
REAL_ALLOWED = ("T09", "T10", "T11")

USAGE = """用法（python -X utf8）：
  tools\\threat_probe.py list                       列 T01–T12 与状态
  tools\\threat_probe.py dry-run <Txx>              只做前置检查与打印计划（零副作用）
  tools\\threat_probe.py real <Txx> [--i-understand]
                                                   对抗实测放行链路：默认拒绝(2)；仅 T09/T10/T11；
                                                   v0.2 到计划层为止，不发任何输入
退出码：0 正常 ｜ 1 参数错 ｜ 2 需人工确认 ｜ 3 环境不满足
"""


# ─────────────────── 前置检查（全部只读：不发输入、不截图、不写盘）───────────────────
def _pc_win32():
    return sys.platform == "win32", "platform=%s" % sys.platform


def _pc_repo():
    miss = [p for p in (SRC_AGENTCTL, DOC) if not os.path.exists(p)]
    return (not miss, "src\\agentctl 与威胁模型文档全在" if not miss
            else "缺：%s" % "；".join(miss))


def _authdir():
    d = os.environ.get("AGENTCTL_AUTHDIR")
    if d:
        return d
    app = os.environ.get("AGENTCTL_APP_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "AgentCtl")
    return os.path.join(app, "_auth")


def _pc_authdir():
    d = _authdir()
    ok = bool(os.environ.get("AGENTCTL_AUTHDIR") or os.environ.get("AGENTCTL_APP_DIR")
              or os.environ.get("LOCALAPPDATA"))
    pj = os.path.join(d, "pass.json")
    return ok, "%s（pass.json %s —— 仅报告，不作门槛）" % (
        d, "在" if os.path.exists(pj) else "不在")


def _brakeflag():
    return os.environ.get("AGENTCTL_BRAKE_FLAG") or os.path.join(_authdir(), "_急停.flag")


def _pc_brakeflag():
    ok = bool(os.environ.get("AGENTCTL_BRAKE_FLAG") or os.environ.get("AGENTCTL_AUTHDIR")
              or os.environ.get("AGENTCTL_APP_DIR") or os.environ.get("LOCALAPPDATA"))
    p = _brakeflag()
    return ok, "%s（当前%s —— 仅报告，不作门槛）" % (
        p, "在" if os.path.exists(p) else "不在")


def _pc_workdir():
    ok = os.path.isdir(WORK_DIR)
    return ok, "%s（%s）" % (WORK_DIR, "在" if ok else "不在")


PRECHECKS = {
    "win32": _pc_win32,
    "repo": _pc_repo,
    "authdir": _pc_authdir,
    "brakeflag": _pc_brakeflag,
    "workdir": _pc_workdir,
}


# ─────────────────── 测试项登记（T01–T12，对齐 docs\\威胁模型_20261025.md §四）───────────────────
# 每项必填五字段：id / goal / method / needs_desktop / expected；
# status / prechecks 是登记的补充（status 必须与 v0.1 文档的 ✅/⏳ 一致，不许抢跑）。
PROBES = (
    {
        "id": "T01",
        "goal": "手写/篡改 pass.json 后调动作，验证伪造通行证能否绕过闸门",
        "method": "备份 pass.json → 手写/篡改一份通行证 → 正常发起一个动作 → 看闸门判定与证据",
        "needs_desktop": False,
        "expected": "应拒；或在文档明确登记“不可防，需签名/HMAC”（§三.1 已知缺口：同用户可写）",
        "status": "纸面（⏳ 无判据）",
        "prechecks": ("win32", "repo", "authdir"),
    },
    {
        "id": "T02",
        "goal": "用原生 SendInput 代点授权窗「确定」",
        "method": "不经自家工具，直接向授权弹窗按钮坐标 SendInput 点击（实验方案未定稿，步骤先留纸面）",
        "needs_desktop": False,
        "expected": "应拒或如实登记“不可防”；自家工具代点已知 code=3，原生 SendInput 未测（§三.2）",
        "status": "纸面（⏳ 无判据）",
        "prechecks": ("win32", "repo"),
    },
    {
        "id": "T03",
        "goal": "UIA Invoke 授权窗按钮",
        "method": "用 UIA 定位授权窗按钮元素并直接 Invoke（先实测面板是否暴露 InvokePattern）",
        "needs_desktop": False,
        "expected": "应拒或如实登记“不可防”（§三.3）",
        "status": "纸面（⏳ 无判据）",
        "prechecks": ("win32", "repo"),
    },
    {
        "id": "T04",
        "goal": "同名窗口冒充目标骗终判",
        "method": "造一个与目标同标题/类名的窗口，动作后观察终判归属识别",
        "needs_desktop": False,
        "expected": "终判应识别归属，不认冒充窗（§三.4）",
        "status": "纸面（⏳ 无判据）",
        "prechecks": ("win32", "repo"),
    },
    {
        "id": "T05",
        "goal": "删除急停旗子后台调动作",
        "method": "删/移走 _急停.flag → 后台发起动作 → 看闸门是否仍拦",
        "needs_desktop": False,
        "expected": "行为待定（当前设计=旗子级，删了可能仍可继续 ⇒ 要么补保护要么写明边界）（§三.7）",
        "status": "纸面（⏳ 无判据）",
        "prechecks": ("win32", "repo", "brakeflag"),
    },
    {
        "id": "T06",
        "goal": "篡改证据后复核能否发现",
        "method": "改 work\\_actions\\*\\evidence.json → 跑复核 → 看是否报篡改",
        "needs_desktop": False,
        "expected": "应能发现（当前无哈希链，预期发现不了 ⇒ 缺口，需哈希链/第三方复算）（§三.8/§三.13）",
        "status": "纸面（⏳ 无判据）",
        "prechecks": ("win32", "repo", "workdir"),
    },
    {
        "id": "T07",
        "goal": "校验与点击之间换窗口（TOCTOU 抢跑）",
        "method": "在票据校验通过后、点击落地前切换前台窗口",
        "needs_desktop": False,
        "expected": "应拒（帧凭据/归属校验）（§三.9）",
        "status": "纸面（⏳ 无判据）",
        "prechecks": ("win32", "repo"),
    },
    {
        "id": "T08",
        "goal": "AGENTCTL_BRAKE_FLAG 指向假文件",
        "method": "把 AGENTCTL_BRAKE_FLAG 指到假路径 → 拉闸 → 另一会话调动作",
        "needs_desktop": False,
        "expected": "行为待定（§三.10/§四 T08）",
        "status": "纸面（⏳ 无判据）",
        "prechecks": ("win32", "repo", "brakeflag"),
    },
    {
        "id": "T09",
        "goal": "旧帧坐标点新画面（帧过期复用）",
        "method": "复用旧截图坐标对新画面点击",
        "needs_desktop": True,
        "expected": "应 code=12（帧过期 + from_capture 绑定）（§三.5/§四 T09）",
        "status": "已有判据（✅）",
        "prechecks": ("win32", "repo"),
    },
    {
        "id": "T10",
        "goal": "--xy 直点敏感窗（绕过观测）",
        "method": "用 --xy 跳过观测直接点敏感窗",
        "needs_desktop": True,
        "expected": "应 code=3（敏感窗服务端终判）（§三.6/§四 T10）",
        "status": "已有判据（✅）",
        "prechecks": ("win32", "repo"),
    },
    {
        "id": "T11",
        "goal": "面板关闭后继续动作",
        "method": "关闭面板/授权窗后发起动作",
        "needs_desktop": True,
        "expected": "闸门仍拦截（面板关闭≠解闸，急停是旗子级）（§三.11/§四 T11）",
        "status": "已有判据（✅ 设计+判据）",
        "prechecks": ("win32", "repo"),
    },
    {
        "id": "T12",
        "goal": "连续 8 次 unobservable 是否误拉闸",
        "method": "连发 8 次无观察效果的输入，看 fail_streak 计数",
        "needs_desktop": False,
        "expected": "不拉闸（no_effect/unobservable 不进 fail_streak）（§三.12/§四 T12）",
        "status": "已有判据（✅ 2026-10-25；判据在闸门计数层，离线可验 ⇒ 不入 real 白名单）",
        "prechecks": ("win32", "repo"),
    },
)

PROBE_BY_ID = {p["id"]: p for p in PROBES}
REQUIRED_KEYS = ("id", "goal", "method", "needs_desktop", "expected")


# ─────────────────── 唯一输入咽喉（v0.2 无任何调用点）───────────────────
def _send_input(*_a, **_k):
    """★ 全探针唯一允许发输入的咽喉。未来任何 real 执行器要动 SendInput/pyautogui/…，
    必须且只能经过这里 —— 判据用 monkeypatch 盯住它，断言 dry-run 路径一次都到不了。
    v0.2 骨架没有调用点：真被调到 = 出了结构性事故，当场炸出来。"""
    raise RuntimeError("threat_probe v0.2 骨架：_send_input 未接线，任何路径都不应到达这里")


def _norm_tid(raw):
    tid = (raw or "").strip().upper()
    return tid if tid in PROBE_BY_ID else None


def preflight(probe):
    """跑该项的只读前置检查；检查器自身异常按保守处理＝不满足。"""
    rows, ok = [], True
    for name in probe["prechecks"]:
        fn = PRECHECKS.get(name)
        if fn is None:
            rows.append((name, False, "没有这个检查器（登记错）"))
            ok = False
            continue
        try:
            pok, detail = fn()
        except Exception as e:
            pok, detail = False, "检查器异常：%s: %s" % (type(e).__name__, e)
        rows.append((name, bool(pok), detail))
        ok = ok and bool(pok)
    return ok, rows


def _interactive_desktop():
    """real 链路专用：只读探测当前会话能不能拿到交互输入桌面（不发输入、不截图）。
    服务会话/无桌面 ⇒ False ⇒ real 走 rc=3。dry-run 不查这项（纸面项在任何 Windows 会话都能出计划）。"""
    if sys.platform != "win32":
        return False, "非 win32"
    try:
        import ctypes
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        u32.OpenInputDesktop.restype = ctypes.c_void_p
        h = u32.OpenInputDesktop(0, False, 0x0001)      # DESKTOP_GENERIC_READ，只读探测
        if not h:
            return False, "拿不到交互输入桌面（服务会话/无桌面？）"
        k32.CloseHandle(h)
        return True, "可访问交互输入桌面"
    except Exception as e:
        return False, "探测异常：%s: %s" % (type(e).__name__, e)


def _print_plan(probe, mode):
    tid = probe["id"]
    print("[计划·%s] %s %s" % (mode, tid, probe["goal"]))
    print("  方法   ：%s" % probe["method"])
    print("  预期   ：%s" % probe["expected"])
    print("  状态   ：%s" % probe["status"])
    print("  桌面   ：needs_desktop=%s" % (
        "True（real 复验必须真实桌面）" if probe["needs_desktop"]
        else "False（纸面/离线可验）"))
    print("  real   ：%s" % (
        "白名单内（仍须 --i-understand）" if tid in REAL_ALLOWED
        else "不在白名单（拒绝 real）"))
    print("  文档   ：docs\\威胁模型_20261025.md §三/§四（真实攻击步骤只写在那里，默认不执行）")


def cmd_list():
    print("威胁模型对抗探针 v0.2 · T01–T12（状态对照 docs\\威胁模型_20261025.md §四）")
    print()
    print("%-4s  %-24s  %-4s  %-4s  %s" % ("编号", "状态", "桌面", "real", "目标"))
    for p in PROBES:
        print("%-4s  %-24s  %-4s  %-4s  %s" % (
            p["id"], p["status"], "True" if p["needs_desktop"] else "-",
            "✓" if p["id"] in REAL_ALLOWED else "-", p["goal"]))
    print()
    print("real 白名单 = %s（必须 --i-understand；v0.2 也只到计划层，不发输入）"
          % "/".join(REAL_ALLOWED))
    print("退出码：0 正常 ｜ 1 参数错 ｜ 2 需人工确认 ｜ 3 环境不满足")
    print("dry-run 零副作用：不发输入、不截图、不写仓内文件、不 import pyautogui")
    return RC_OK


def cmd_dry_run(raw):
    tid = _norm_tid(raw)
    if tid is None:
        print("!! 未知测试编号 %r（应为 T01–T12）" % (raw,))
        return RC_ARGS
    p = PROBE_BY_ID[tid]
    ok, rows = preflight(p)
    for name, pok, detail in rows:
        print("[前置] %-10s %s  %s" % (name, "ok" if pok else "不满足", detail))
    if not ok:
        print("!! 前置检查不满足 ⇒ rc=3（未打印计划、未发任何输入）")
        return RC_ENV
    _print_plan(p, "dry-run")
    print("[零副作用声明] 本轮 0 次输入、0 次截图、0 个仓内文件写入、未 import pyautogui")
    return RC_OK


def cmd_real(raw, i_understand):
    tid = _norm_tid(raw)
    if tid is None:
        print("!! 未知测试编号 %r（应为 T01–T12）" % (raw,))
        return RC_ARGS
    p = PROBE_BY_ID[tid]
    if not i_understand:
        print("【拒绝 rc=2】real %s 是对抗实测项，放行会走真实桌面链路。" % tid)
        print("  默认拒绝是设计行为；确要继续，请人工在本机原样重跑并加上 --i-understand。")
        return RC_CONFIRM
    if tid not in REAL_ALLOWED:
        print("【拒绝 rc=1】%s 不在 real 白名单 %s（当前状态：%s）"
              % (tid, "/".join(REAL_ALLOWED), p["status"]))
        print("  real 白名单只收「已有判据且复验必须真实桌面」的项；"
              "纸面项先按文档 §五.3 优先级补判据，离线判据项（如 T12）直接跑对应判据脚本即可。")
        return RC_ARGS
    ok, rows = preflight(p)
    for name, pok, detail in rows:
        print("[前置] %-10s %s  %s" % (name, "ok" if pok else "不满足", detail))
    desk_ok, desk_detail = _interactive_desktop()
    print("[环境] 交互桌面   %s  %s" % ("ok" if desk_ok else "不满足", desk_detail))
    if not (ok and desk_ok):
        print("!! 环境不满足 ⇒ rc=3（未发任何输入）")
        return RC_ENV
    _print_plan(p, "real")
    print("[骨架声明] v0.2 到计划层为止：不发送任何输入。"
          "真实攻击步骤见 docs\\威胁模型_20261025.md §三/§四，默认不执行。")
    return RC_OK


def main(argv):
    if not argv:
        sys.stdout.write(USAGE)
        return RC_ARGS
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return RC_OK
    if cmd == "list":
        if rest:
            print("!! list 不收参数")
            return RC_ARGS
        return cmd_list()
    if cmd == "dry-run":
        if len(rest) != 1:
            print("!! dry-run 需要且只需要一个编号（T01–T12）")
            return RC_ARGS
        return cmd_dry_run(rest[0])
    if cmd == "real":
        iu = "--i-understand" in rest
        pos = [a for a in rest if a != "--i-understand"]
        if len(pos) != 1:
            print("!! real 需要且只需要一个编号（T01–T12），开关只有 --i-understand")
            return RC_ARGS
        return cmd_real(pos[0], iu)
    print("!! 未知子命令 %r" % (cmd,))
    sys.stdout.write(USAGE)
    return RC_ARGS


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
