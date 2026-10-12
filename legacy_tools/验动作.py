# -*- coding: utf-8 -*-
r"""验动作.py —— 动作的「内层溯源 + 三值判定」

解决什么问题（2026-10-02 用户两轮追问的落点）
--------------------------------------------------------------------------------
GUI 层的动作**没有返回值**（点一下只是把一条消息塞进队列），所以"动作报成功"
根本不等于"事情发生了"。外层（截图）**必要但不充分** —— 有些成果屏幕上根本看不见。

★ 学术界（VAGEN, arXiv 2602.00575）把这条叫「部分状态可观测」：
  关键证据藏在 **latent state**（文件属性、后台进程）里，必须【主动去查】。
  它的 Stage 3 用的工具就是：execute shell / execute python / computer use。
  本脚本 = 把那一步固化下来，而且是**只读**的（不改环境，可反复做）。

三段式对照（同一套 snapsho 前后比）
--------------------------------------------------------------------------------
    python 验动作.py snap --tag before --paths "<路径1>;<路径2>" --expect "file-exists:<路径1>/待生成.txt"
        ↓  （在这里做动作）
    python 验动作.py check --tag after

三值判定（照官方 verify_state 的口径）
--------------------------------------------------------------------------------
    satisfied     期望全部满足
    unsatisfied   有期望不满足
    unknown       ★ 有变化但无法判定 / 判不了  —— **unknown 永不当成功**

★ 三条【回执可信度】硬规矩（2026-10-12 实测换来，必须写进流程）
--------------------------------------------------------------------------------
  1) **background 投递的动作，回执一律不作数。**
     实测：对 Chromium/Electron 窗口（窗口类 Chrome_WidgetWin_1）用 background 方式点击，
     接口返回「已投递成功」，而**控件毫无反应**（连点两次，两张截图逐字节相同）。
     ⇒ 判成没成，只看**目标状态变了没有**，不看接口回没回成功。
  2) **读窗口之前，先确认它 on-screen。**
     实测：最小化 / 移出可见区的窗口，UIA 树会塌成 0 个节点，看上去像「这个应用没开无障碍」，
     其实只是没在屏上。⇒ 先恢复再读，否则会把「读不到」误判成「不存在」。
  3) **坐标只许来自放大取景，不许目测。**
     实测：目测发送按钮 (1431,867)，连点两次全废；放大后量出真实位置，一次命中。
     目测误差 60+ 像素，正好落在按钮之外。

期望谓词（machine-checkable，不用 NLP）
--------------------------------------------------------------------------------
    file-exists:<路径>        文件/目录存在
    file-absent:<路径>        不存在
    file-changed:<路径>       mtime 或 size 变了
    file-created:<路径>       之前不存在、现在存在
    file-deleted:<路径>       之前存在、现在不存在
    file-contains:<路径>|<子串>  文件内容里含有该子串（★2026-10-10 新增，见下）
    dir-count-up:<目录>       目录里文件数增加了
    proc-exists:<进程名>      进程在
    proc-gone:<进程名>        进程不在了
    window-exists:<标题片段>  窗口在
    window-gone:<标题片段>    窗口不在了
    window-restored:<标题片段> 窗口从「最小化」变回正常（★2026-10-04 新增）
    window-moved:<标题片段>    窗口位置/大小变了（★2026-10-04 新增）
    any-change                只要有变化就算满足（"动作生效了没"）
    no-change                 期望什么都没变（防误伤）

★ 快照可比性硬校验（2026-10-10 新增，T9-R A4 第一步 b）
--------------------------------------------------------------------------------
    check 现在会**先校验前后两张快照的路径集是否一致**：
      不一致 → 显式报错、退出码 5，**拒绝判定**（不再静默降级成 unknown）。
    为什么：2026-10-10 实测——驱动只 snap 了后一张、check 又没传 --before，
    于是回退到 2026-10-04 的旧默认快照（4 条无关路径）；
    file-changed 拿不到同一路径、返回 None，账本上留下 3 条**冤枉的 unknown**。
    路径集对不上时，任何 file-* 结论都不可信，所以宁可不判。
    退出码：0 satisfied / 3 unsatisfied / 4 unknown / **5 快照不可比（判定无效）**
    放宽开关：--allow-path-mismatch（显式用了才放宽，且会打醒目警告、账本留 note）

★ 只读承诺：snapshot 只读系统状态，**不写任何被观测的东西**，也不改剪贴板。
"""
import argparse
import ctypes
import ctypes.wintypes   # ★2026-10-04 补：光 import ctypes 不会让 ctypes.wintypes 可用；
                        #   缺这一行会让 snap_windows 抛 AttributeError，被 except Exception 静默吞掉，
                        #   结果是【窗口数变成 0】——我第一版就踩了，当场跑快照才发现。
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 账本目录：默认 = 本脚本所在仓库根下的 work/verify/（★ 不再依赖开发机绝对路径）
#   AGENTCTL_VERIFY_WORK 可覆盖 —— 换机器、CI、以及负例测试的沙箱隔离都靠它。
_HERE = os.path.dirname(os.path.abspath(__file__))
WORK = (os.environ.get("AGENTCTL_VERIFY_WORK")
        or os.path.join(os.path.dirname(_HERE), "work", "verify"))
LEDGER = os.path.join(WORK, "tier_ledger.jsonl")     # ★ S6 档位账本（只追加）
MAX_FILES = 4000          # 单次扫描的文件上限，防止扫盘扫到天荒地老
TIERS = ["A", "B", "C", "D"]
CLEAN_NEEDED = 20         # 铁律 11⑥：B→C、C→D 的"连续 20 次干净成功"


# ────────────────────── S6 档位账本（长期成本）──────────────────────
def ledger_append(action, tier, verdict, expect, note=""):
    """把一次动作的判定结果追加进账本。**只追加，不改已有行**。"""
    os.makedirs(WORK, exist_ok=True)
    rec = {
        "t": round(time.time(), 3),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": action or "(未指名)",
        "tier": (tier or "?").upper(),
        "verdict": verdict,
        "expect": expect or "",
        "note": note[:200],
    }
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def ledger_read():
    out = []
    if not os.path.exists(LEDGER):
        return out
    with open(LEDGER, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def ledger_status():
    """算每个档位的【连续干净成功】次数。
    规则（写死，不许自己放水）：
      satisfied   → 连续 +1
      unsatisfied → 连续【归零】（动作没生效，谈不上干净）
      unknown     → 不计数、也不归零（未知不是成功也不是失败，但不能推进进度）
    """
    recs = ledger_read()
    st = {t: {"streak": 0, "total": 0, "ok": 0, "bad": 0, "unk": 0} for t in TIERS}
    for r in recs:
        t = (r.get("tier") or "?").upper()
        if t not in st:
            continue
        s = st[t]
        s["total"] += 1
        v = r.get("verdict")
        if v == "satisfied":
            s["ok"] += 1
            s["streak"] += 1
        elif v == "unsatisfied":
            s["bad"] += 1
            s["streak"] = 0
        else:
            s["unk"] += 1
            # ★ 刻意不动 streak —— 未知既不奖励也不惩罚
    return st, len(recs)


def do_ledger(args):
    st, n = ledger_status()
    print("=" * 74)
    print(f"  S6 档位账本　{LEDGER}")
    print(f"  记录 {n} 条｜门槛：连续 {CLEAN_NEEDED} 次 satisfied")
    print("=" * 74)
    if not n:
        print("  （还没有记录。动作后跑 `check --action <名> --tier <档>` 就会记账）")
        return 0
    print()
    print(f"  {'档':<4}{'连续干净':<12}{'总次数':<9}{'成':<7}{'败':<7}{'未知':<7}状态")
    unlocked = True
    for t in TIERS:
        s = st[t]
        if t == "A":
            state = "（只读，无需门槛）"
        elif not unlocked:
            state = "未解锁（上一档未达标）"
        elif s["streak"] >= CLEAN_NEEDED:
            state = f"★ 已达门槛 {s['streak']}/{CLEAN_NEEDED}（升档仍由用户拍板）"
        else:
            unlocked = False
            state = f"累计中 {s['streak']}/{CLEAN_NEEDED}"
            if t != "A":
                pass
        print(f"  {t:<4}{s['streak']:<12}{s['total']:<9}{s['ok']:<7}{s['bad']:<7}{s['unk']:<7}{state}")
        if t != "A" and s["streak"] < CLEAN_NEEDED:
            unlocked = False
    print()
    print(f"  最近 5 条：")
    for r in ledger_read()[-5:]:
        print(f"    {r['ts']}  [{r['tier']}] {r['action'][:34]:<34} {r['verdict']}")
    return 0



# ────────────────────────── 采集 ──────────────────────────
def snap_procs():
    """进程快照。★ 排除【自己这一支】（验动作.py / pwsh / cmd）——
    否则每次快照都会看到"自己新增、自己消失"，纯噪声。
    （2026-10-02 实测：第一次对比就报"进程 新增1 消失1"，全是我自己。）"""
    # ★2026-10-08 修（ZCode，P1 前置自检两轮发现）：
    #   原来按【自己这一支 + 祖先链的 PID】排除进程 —— 但终端由资源管理器拉起时，
    #   explorer.exe 就在祖先链里，于是每张快照都缺 explorer（实测 298/299 进程、
    #   tasklist 里 explorer 在跑，快照却匹配 0），proc-exists:explorer.exe 永远误判。
    #   修法：不再按祖先 PID 整链排除，只保留 noisy 名单 —— 2026-10-02 那个
    #   「跑一次验动作自己冒出来又消失」的噪声源（python/pwsh/cmd/conhost）
    #   本来就全在 noisy 里，遮得住；explorer/终端这类稳定祖先恢复可观测。
    #   备份：work\_bak归档\tools_验动作.py.bak_修snap排除祖先名_20261008_171604
    noisy = {"powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe", "python.exe"}
    out = {}
    for p in psutil.process_iter(["pid", "name"]):
        try:
            pid, name = p.info["pid"], p.info["name"] or ""
            if name.lower() in noisy:
                continue
            out[f"{name}#{pid}"] = name
        except Exception:
            pass
    return out


def snap_windows():
    """纯 ctypes 枚举顶层窗口（零依赖）。"""
    user32 = ctypes.windll.user32
    out = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
    def cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            # ★ 2026-10-04 补：原来只记 title+pid，导致**纯 GUI 状态变化完全测不到**
            #   （实测：把最小化的窗口恢复，标题/句柄/PID 全不变 → 快照前后一模一样 →
            #     报 satisfied，但那是蒙对的；改用 any-change 更会被无关的 svchost 退出骗到）
            #   现在补记几何与可见性状态，让 window-restored / window-moved 有据可判。
            rc = ctypes.wintypes.RECT()
            has_rect = bool(user32.GetWindowRect(hwnd, ctypes.byref(rc)))
            out[f"{int(hwnd)}"] = {
                "title": buf.value,
                "pid": int(pid.value),
                "visible": bool(user32.IsWindowVisible(hwnd)),
                "iconic": bool(user32.IsIconic(hwnd)),
                "zoomed": bool(user32.IsZoomed(hwnd)),
                "rect": [rc.left, rc.top, rc.right, rc.bottom] if has_rect else None,
            }
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(cb, None)
    except Exception:
        pass
    return out


def snap_files(paths):
    """只看【指定的】路径 —— 不扫盘。记录 size+mtime（便宜）与存在性。"""
    out, n = {}, 0
    for raw in paths:
        p = raw.strip()
        if not p:
            continue
        if os.path.isfile(p):
            try:
                st = os.stat(p)
                out[p] = {"kind": "file", "size": st.st_size, "mtime": round(st.st_mtime, 3)}
            except Exception:
                out[p] = {"kind": "unreadable"}
        elif os.path.isdir(p):
            out.setdefault(p, {"kind": "dir", "fileCount": 0})
            cnt = 0
            for root, _dirs, files in os.walk(p):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        st = os.stat(fp)
                        out[fp] = {"kind": "file", "size": st.st_size, "mtime": round(st.st_mtime, 3)}
                        cnt += 1
                    except Exception:
                        pass
                    n += 1
                    if n > MAX_FILES:
                        break
                if n > MAX_FILES:
                    break
            out[p] = {"kind": "dir", "fileCount": cnt}
        else:
            out[p] = {"kind": "absent"}       # ★ 明确记"不存在"，file-created 才判得了
    return out


def do_snap(args):
    os.makedirs(WORK, exist_ok=True)
    paths = [x for x in (args.paths or "").split(";") if x.strip()]
    data = {
        "tag": args.tag,
        "time": time.time(),
        "timeStr": time.strftime("%Y-%m-%d %H:%M:%S"),
        "expect": args.expect or "",
        "procs": snap_procs(),
        "windows": snap_windows(),
        "files": snap_files(paths),
        "paths": paths,
    }
    dst = os.path.join(WORK, f"{args.tag}.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"  ✅ 快照 [{args.tag}] → {dst}")
    print(f"     时间 {data['timeStr']}｜进程 {len(data['procs'])}｜窗口 {len(data['windows'])}"
          f"｜文件项 {len(data['files'])}")
    if args.expect:
        print(f"     期望: {args.expect}")
    return 0


# ────────────────────────── 对比 ──────────────────────────
def diff(a, b, label):
    """返回 (新增, 消失, 变化) 三个列表"""
    ka, kb = set(a), set(b)
    added = sorted(kb - ka)
    gone = sorted(ka - kb)
    changed = []
    for k in sorted(ka & kb):
        if a[k] != b[k]:
            changed.append(k)
    return added, gone, changed


# ────────────────────────── 谓词 ──────────────────────────
def eval_pred(pred, before, after, d):
    """返回 (结果, 说明)；结果 ∈ {True, False, None}（None = 判不了 = unknown）"""
    f_b, f_a = before["files"], after["files"]
    p_b, p_a = before["procs"], after["procs"]
    w_b, w_a = before["windows"], after["windows"]

    if ":" in pred:
        kind, arg = pred.split(":", 1)
    else:
        kind, arg = pred, ""
    kind = kind.strip().lower()
    arg = arg.strip()

    if kind == "file-exists":
        # ★ 存在性【直接问文件系统】，不用快照 —— 快照只保证"被我盯到的路径"，
        #   不存在的路径压根不会出现在快照里，用快照判会误报 unknown
        #   （2026-10-02 实测踩到这个：查一个不存在的文件得到 unknown 而不是 unsatisfied）
        try:
            return os.path.exists(arg), f"{arg} → {'存在' if os.path.exists(arg) else '不存在'}"
        except Exception as ex:
            return None, f"查询失败 {type(ex).__name__}"
    if kind == "file-absent":
        try:
            ex_ = os.path.exists(arg)
            return (not ex_), f"{arg} → {'存在' if ex_ else '不存在'}"
        except Exception as ex:
            return None, f"查询失败 {type(ex).__name__}"
    if kind == "file-contains":
        # ★2026-10-10 新增（T9-R A4 第一步 b）：
        #   原来谓词表里没有 file-contains，于是「_t9_notepad.txt 里应当出现【训练靶】字样」
        #   这类期望**根本没法表达** —— 驱动只能把它写成不认识的形式，
        #   check 返回 None，账本 2026-10-10 08:33:33 那条 unknown 就死在这里。
        #   语义：file-contains:<路径>|<子串> —— 子串出现在文件内容里 → satisfied。
        #   与 file-exists 同策：**直接读文件**，不靠快照（快照只记 size/mtime，不含内容）。
        if "|" not in arg:
            return None, "file-contains 用法：file-contains:<路径>|<子串>（缺 | 分隔符）"
        path, needle = arg.split("|", 1)
        path, needle = path.strip(), needle
        if not path:
            return None, "file-contains 缺路径"
        if not needle:
            return None, "file-contains 的子串为空，判不了"
        if not os.path.isfile(path):
            return False, f"{path} 不是文件（或不存在）→ 不可能含「{needle[:20]}」"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception as ex:
            return None, f"读 {path} 失败 {type(ex).__name__}: {ex}"
        hit = needle in txt
        return hit, f"{path}（{len(txt)} 字）{'含' if hit else '不含'}「{needle[:30]}」"
    if kind == "file-changed":
        if arg in f_b and arg in f_a:
            ok = f_b[arg] != f_a[arg]
            return ok, f"{arg}: {f_b[arg]} → {f_a[arg]}"
        # ★ 说清是**哪一侧**缺，别只丢一句「没同时出现」（那种模糊话查不出根因）
        miss = []
        if arg not in f_b:
            miss.append("before")
        if arg not in f_a:
            miss.append("after")
        return None, (f"{arg} 没出现在 {'、'.join(miss)} 快照里"
                      f"（before 盯了 {len(f_b)} 项 / after 盯了 {len(f_a)} 项）")
    if kind == "file-created":
        was = f_b.get(arg, {}).get("kind") == "absent" or arg not in f_b
        now = f_a.get(arg, {}).get("kind") not in (None, "absent")
        if arg not in f_a:
            return None, f"{arg} 不在 after 快照里"
        return (was and now), f"before={f_b.get(arg, {}).get('kind')} after={f_a.get(arg, {}).get('kind')}"
    if kind == "file-deleted":
        was = f_b.get(arg, {}).get("kind") not in (None, "absent")
        now = f_a.get(arg, {}).get("kind") == "absent" or arg not in f_a
        return (was and now), f"before={f_b.get(arg, {}).get('kind')} after={f_a.get(arg, {}).get('kind')}"
    if kind == "dir-count-up":
        nb = f_b.get(arg, {}).get("fileCount")
        na = f_a.get(arg, {}).get("fileCount")
        if nb is None or na is None:
            return None, f"{arg} 不是被盯的目录"
        return na > nb, f"{arg} 文件数 {nb} → {na}"
    if kind == "proc-exists":
        hit = [k for k, v in p_a.items() if arg.lower() in str(v).lower()]
        return (len(hit) > 0), f"匹配 {len(hit)} 个进程"
    if kind == "proc-gone":
        was = [k for k, v in p_b.items() if arg.lower() in str(v).lower()]
        now = [k for k, v in p_a.items() if arg.lower() in str(v).lower()]
        return (len(was) > 0 and len(now) == 0), f"before={len(was)} after={len(now)}"
    if kind == "window-exists":
        hit = [v for v in w_a.values() if arg.lower() in str(v.get("title", "")).lower()]
        return (len(hit) > 0), f"匹配 {len(hit)} 个窗口"
    if kind == "window-gone":
        was = [v for v in w_b.values() if arg.lower() in str(v.get("title", "")).lower()]
        now = [v for v in w_a.values() if arg.lower() in str(v.get("title", "")).lower()]
        return (len(was) > 0 and len(now) == 0), f"before={len(was)} after={len(now)}"
    if kind == "window-restored":
        was = [v for v in w_b.values() if arg.lower() in str(v.get("title", "")).lower()]
        now = [v for v in w_a.values() if arg.lower() in str(v.get("title", "")).lower()]
        if not was or not now:
            return None, f"两次快照没同时匹配到「{arg}」"
        b = any(v.get("iconic") for v in was)
        a = any(v.get("iconic") for v in now)
        return (b and not a), f"最小化 {b} → {a}"
    if kind == "window-moved":
        was = [v for v in w_b.values() if arg.lower() in str(v.get("title", "")).lower()]
        now = [v for v in w_a.values() if arg.lower() in str(v.get("title", "")).lower()]
        if not was or not now:
            return None, f"两次快照没同时匹配到「{arg}」"
        rb, ra = was[0].get("rect"), now[0].get("rect")
        if rb is None or ra is None:
            return None, "快照里没有 rect（旧快照无此字段）"
        return (rb != ra), f"rect {rb} → {ra}"
    if kind == "any-change":
        return d["total"] > 0, f"总变化 {d['total']} 项"
    if kind == "no-change":
        return d["total"] == 0, f"总变化 {d['total']} 项"
    return None, f"不认识的谓词「{kind}」"


# ── M3：退出码必须带判定，否则自动化会把 unknown 当成功 ──
# 以前 satisfied / unsatisfied / unknown **全部 return 0**。
# 调用方一句 `验动作.py check ... && echo 完成` 就会把 unknown 打印成「完成」——
# 判据形同虚设。退出码约定（写死，别自己改）：
EXIT_SATISFIED = 0
EXIT_UNSATISFIED = 3
EXIT_UNKNOWN = 4
# ★2026-10-10 新增（T9-R A4 第一步 b）：前后快照路径集不一致 ⇒ 判定无效，独立退出码。
#   绝不允许这种情况静默退化成 unknown —— 那正是 2026-10-10 三条冤枉 unknown 的成因。
EXIT_MISMATCH = 5


def _record(args, before, after, verdict, expect):
    """记账（只有显式给了 --action 才记，免得误记）。抽出来是为了让
    「没写谓词」那条早退路径**也**能记账 —— 早退不是不记账的理由。"""
    if not getattr(args, "action", None):
        return None
    tier = getattr(args, "tier", None) or "?"
    note = f"{args.before}→{args.tag}"
    if getattr(args, "allow_path_mismatch", False):
        note += "｜⚠快照路径集不一致但被显式放宽"
    rec = ledger_append(args.action, tier, verdict, expect, note=note)
    st, _n = ledger_status()
    s = st.get(tier.upper(), {"streak": 0})
    print(f"  → 已记账：[{tier}] {args.action} = {verdict}"
          f"　该档连续干净 {s['streak']}/{CLEAN_NEEDED}")
    return rec


def _declared_paths(snap):
    """快照里声明的路径集（snap 时传给 --paths 的），归一化成 set。"""
    ps = snap.get("paths")
    if ps is None:
        return None                      # 旧快照没这字段 → 视为不可比
    return {os.path.normcase(os.path.normpath(str(p).strip()))
            for p in ps if str(p).strip()}


def compare_snapshots(before, after):
    """★2026-10-10 新增（T9-R A4 第一步 b）：校验两张快照是否**可比**。

    返回 (ok, 说明)。ok=False 时调用方必须**报错退出**，不许继续判 ——
    路径集对不上，任何 file-* 谓词的结论都不可信。
    """
    pb, pa = _declared_paths(before), _declared_paths(after)
    if pb is None or pa is None:
        return False, ("快照缺 'paths' 字段（多半是很旧的快照）—— 无法确认两次盯的是同一批路径："
                       f"before={before.get('tag')} after={after.get('tag')}")
    if pb != pa:
        only_b = sorted(pb - pa)
        only_a = sorted(pa - pb)
        return False, ("前后快照盯的路径集不一致："
                       f"仅 before 有 {only_b or '无'}；仅 after 有 {only_a or '无'}"
                       f"（before 共 {len(pb)} 条 / after 共 {len(pa)} 条）")
    if not pb:
        return True, "两张快照都没盯文件路径（只有进程/窗口）—— 可比"
    return True, f"路径集一致（{len(pb)} 条）"


def do_check(args):
    bp = os.path.join(WORK, f"{args.before}.json")
    ap = os.path.join(WORK, f"{args.tag}.json")
    if not os.path.exists(bp):
        print(f"  ❌ 找不到基准快照 {bp}（先跑 snap --tag {args.before}）")
        return 2
    before = json.load(open(bp, encoding="utf-8"))
    after = json.load(open(ap, encoding="utf-8"))

    # ★2026-10-10 新增（T9-R A4 第一步 b）：先校验「两张快照可比吗」。
    #   不可比 ⇒ 显式报错 + 退出码 5 + 不记账，**不许静默降级成 unknown**。
    ok_cmp, why_cmp = compare_snapshots(before, after)
    if not ok_cmp and not getattr(args, "allow_path_mismatch", False):
        print("=" * 74)
        print("  ⛔ 判定无效：前后快照不可比（拒绝判定）")
        print(f"     {why_cmp}")
        print("=" * 74)
        print("  为什么拒绝：路径集对不上时，file-* 谓词拿不到同一路径，结论一律不可信 ——")
        print("             继续判只会产出「冤枉的 unknown」（2026-10-10 就栽在这）。")
        print("  正确做法：**前后都用同一组 --paths** 重拍快照：")
        print(f"     python 验动作.py snap --tag {args.before} --paths \"<与后面相同>\" --expect \"...\"")
        print("     …做动作…")
        print(f"     python 验动作.py snap --tag {args.tag} --paths \"<同上>\"")
        print(f"     python 验动作.py check --tag {args.tag} --before {args.before} --action … --tier …")
        print()
        print("  本次**未记账**（判定无效，不该污染账本）。退出码 5。")
        return EXIT_MISMATCH
    if not ok_cmp:
        print("  ⚠️⚠️ --allow-path-mismatch 已放宽：" + why_cmp)
        print("       本次判定建立在**不可比**的快照上 —— 结论仅供参考，不许当成功。")

    pa, pg, pc = diff(before["procs"], after["procs"], "进程")
    wa, wg, wc = diff(before["windows"], after["windows"], "窗口")
    fa, fg, fc = diff(before["files"], after["files"], "文件")
    # 窗口用标题比更有意义
    wt_b = {k: v.get("title") for k, v in before["windows"].items()}
    wt_a = {k: v.get("title") for k, v in after["windows"].items()}
    wt_new = [v for k, v in wt_a.items() if k not in wt_b]
    wt_gone = [v for k, v in wt_b.items() if k not in wt_a]
    # ★2026-10-04 补：窗口的「状态变化」（移动/最小化/可见性）原来完全不计入，
    #   导致报告一边打「窗口 新增 0 / 消失 0」、一边又判出「最小化 True → False」，自相矛盾。
    #   现在单独统计并计入 total。
    _STATE = ("visible", "iconic", "zoomed", "rect")
    _wb, _wa = before["windows"], after["windows"]
    wt_state = []
    for k in set(_wb) & set(_wa):
        b, a = _wb.get(k, {}), _wa.get(k, {})
        if any(b.get(f) != a.get(f) for f in _STATE):
            wt_state.append({
                "title": a.get("title"),
                "iconic": (b.get("iconic"), a.get("iconic")),
                "rect": (b.get("rect"), a.get("rect")),
            })

    d = {
        "proc_added": pa, "proc_gone": pg,
        "win_added": wt_new, "win_gone": wt_gone,
        "win_state": wt_state,
        "file_added": fa, "file_gone": fg, "file_changed": fc,
        "total": len(pa) + len(pg) + len(wt_new) + len(wt_gone) + len(wt_state)
                 + len(fa) + len(fg) + len(fc),
    }

    print("=" * 74)
    print(f"  动作验证　{args.before} → {args.tag}")
    print(f"  {before['timeStr']}   →   {after['timeStr']}"
          f"　（间隔 {after['time'] - before['time']:.1f} 秒）")
    print("=" * 74)
    print()
    print("【系统侧（内层）实际发生了什么】")
    print(f"  进程  新增 {len(pa)} / 消失 {len(pg)}" + (f"   新增: {pa[:5]}" if pa else "")
          + (f"   消失: {pg[:5]}" if pg else ""))
    print(f"  窗口  新增 {len(wt_new)} / 消失 {len(wt_gone)}")
    for t in wt_new[:6]:
        print(f"          + {t}")
    for t in wt_gone[:6]:
        print(f"          - {t}")
    print(f"  窗口状态变化 {len(wt_state)} 处（移动/最小化/可见性）")
    for c in wt_state[:6]:
        print(f"          ~ {c['title']}  最小化 {c['iconic'][0]}→{c['iconic'][1]}"
              f"  rect {c['rect'][0]}→{c['rect'][1]}")
    print(f"  文件  新增 {len(fa)} / 消失 {len(fg)} / 变化 {len(fc)}")
    for p in fa[:6]:
        print(f"          + {p}")
    for p in fg[:6]:
        print(f"          - {p}")
    for p in fc[:6]:
        print(f"          ~ {p}")
    print()

    exps = [e.strip() for e in (after.get("expect") or before.get("expect") or "").replace(",", ";").split(";") if e.strip()]
    if not exps:
        print("【判定】unknown —— 没写期望谓词，所以只能说「有变化」，不能说「成了」。")
        print("        ★ 这正是「有变化 ≠ 我要的变化」。下次加 --expect。")
        print()
        print("=" * 74)
        print("  结论：unknown（未声明期望）")
        print("=" * 74)
        # ★★ M3 修：这里原来直接 return 0，等于**没记账**。
        #    后果：一个 agent 可以连做 20 个「不可验证」的动作，账本上一条都没有 ——
        #    动作在审计轨迹里凭空消失，比记成 unknown 更糟。
        #    现在照样记一笔 unknown（ledger_status 对 unknown 既不推进也不归零）。
        _record(args, before, after, "unknown", "")
        return EXIT_UNKNOWN

    print("【期望对照】")
    results = []
    for e in exps:
        try:
            r, why = eval_pred(e, before, after, d)
        except Exception as ex:
            r, why = None, f"判定异常 {type(ex).__name__}: {ex}"
        results.append(r)
        mark = {True: "✅", False: "❌", None: "❓"}[r]
        word = {True: "satisfied", False: "unsatisfied", None: "unknown"}[r]
        print(f"  {mark} {e}")
        print(f"       {word} —— {why}")
    print()

    if any(r is False for r in results):
        verdict = "unsatisfied"
    elif any(r is None for r in results):
        verdict = "unknown"
    else:
        verdict = "satisfied"

    print("=" * 74)
    print(f"  结论：{verdict}")
    if verdict == "unknown":
        print("        ★ unknown 永不当成功 —— 要继续找证据，或报告用户。")
    print("=" * 74)

    # ★ S6：记账（只有显式给了 --action 才记，免得误记）
    #   ★2026-10-12 修：判定那段取的是 after.get("expect") or before.get("expect")，
    #   而记账只取 after —— 于是「--expect 只写在 before 快照上」时，
    #   判定用了谓词、账本却写空：审计轨迹证明不了当时声称过什么。
    #   记账口径必须与判定口径**一致**，否则账本不是证据。
    _record(args, before, after, verdict,
            after.get("expect") or before.get("expect") or "")
    # ★ M3：退出码必须反映判定。unknown 绝不能返回 0。
    return {
        "satisfied": EXIT_SATISFIED,
        "unsatisfied": EXIT_UNSATISFIED,
        "unknown": EXIT_UNKNOWN,
    }[verdict]


# ────────────────────────── 入口 ──────────────────────────
def main():
    ap = argparse.ArgumentParser(description="动作的内层溯源与三值判定")
    sub = ap.add_subparsers(dest="cmd")

    s1 = sub.add_parser("snap", help="拍快照")
    s1.add_argument("--tag", required=True)
    s1.add_argument("--paths", default="", help="分号分隔的要盯的路径（只看这些，不扫盘）")
    s1.add_argument("--expect", default="", help="期望谓词，分号分隔")
    s1.set_defaults(func=do_snap)

    s2 = sub.add_parser("check", help="与基准快照对比并判定")
    s2.add_argument("--tag", default="after")
    s2.add_argument("--before", default="before")
    s2.add_argument("--action", default="", help="★ 给了就记进档位账本（动作名）")
    s2.add_argument("--tier", default="", help="动作档位 A/B/C/D")
    # ★2026-10-10 新增：默认严格 —— 前后快照路径集不一致就报错退出(5)。
    #   只有显式加这个开关才放宽，且会打醒目警告 + 账本 note 留痕。
    s2.add_argument("--allow-path-mismatch", action="store_true",
                    help="★放宽：允许前后快照路径集不一致（不推荐，会被记进账本 note）")
    s2.set_defaults(func=do_check)

    s3 = sub.add_parser("ledger", help="★ 看档位账本（连续干净成功次数）")
    s3.set_defaults(func=do_ledger)

    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help()
        return 1
    return a.func(a)


try:
    import psutil
except ImportError:
    print("  需要 psutil：pip install psutil")
    sys.exit(3)

if __name__ == "__main__":
    raise SystemExit(main())
