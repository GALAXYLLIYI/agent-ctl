# -*- coding: utf-8 -*-
"""守护脚本 guardian.py —— 秒级采样 + 三档判定 + 白名单无损处置 + 急停可中断。

用法:
  python -X utf8 guardian.py                # 观察模式（默认，绝不处置）
  python -X utf8 guardian.py --enforce      # 执行模式（按白名单清工作集）
  python -X utf8 guardian.py --replay       # 回放 09-16 卡死现场数据，校验阈值

纪律（对应《AGENT准则》与框架）：
  · 永不动用户窗口程序 / PowerShell（policy 里的"永不触碰"）
  · 处置前先查急停文件；存在即跳过并记录
  · 处置只做无损动作（清工作集 / 降优先级），v1 不结束进程
  · 自身开销写进 CSV 尾列，超过 1% CPU 自动降频
"""
import os, sys, json, time, csv, ctypes, ctypes.wintypes as wt, datetime, subprocess
import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
POLICY = os.path.join(HERE, "guardian_policy.json")
ENFORCE = "--enforce" in sys.argv
REPLAY = "--replay" in sys.argv


def _resolve(p):
    """展开 policy 里的路径：`<工作区>` 占位符 + `%ENV%` 环境变量。

    ★ 2026-10-17（用户裁：**不公开用户名**）：guardian_policy.json 是进仓文件，
      里面的「急停文件 / 日志目录」不能再把本机用户名写死。
      `<工作区>` = 本目录(legacy_tools)的上两级（= 仓根的上一级），可用
      `AGENTCTL_WORKSPACE` 覆盖；语义与原来那两条绝对路径**完全一致**。
    """
    ws = os.environ.get("AGENTCTL_WORKSPACE") or os.path.dirname(os.path.dirname(HERE))
    return os.path.expandvars(str(p)).replace("<工作区>", ws)


# ---------- 内存/提交 ----------
class MEMSTATUS(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def mem():
    m = MEMSTATUS(); m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m


def counters():
    """慢采样：零页表 / 硬故障 / 磁盘队列（走一次 PowerShell，慢通道用）"""
    ps = ("$c=Get-Counter -ErrorAction SilentlyContinue "
          "'\\Memory\\Free & Zero Page List Bytes','\\Memory\\Pages Input/sec',"
          "'\\PhysicalDisk(_Total)\\Current Disk Queue Length';"
          "$o=@{}; foreach($s in $c.CounterSamples){$o[$s.Path]=$s.CookedValue};"
          "$o | ConvertTo-Json -Compress")
    try:
        import subprocess
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=25,
                             creationflags=0x08000000).stdout.strip()
        d = json.loads(out) if out else {}
    except Exception:
        d = {}
    def g(k):
        for kk, vv in d.items():
            if k.lower() in kk.lower():
                return float(vv)
        return None
    z = g("zero page list")
    hf = g("pages input")
    dq = g("disk queue")
    return {"零页表MB": (z / 1048576) if z is not None else None,
            "硬故障每s": hf, "磁盘队列": dq}


TOP_N = 6


G = 1024 ** 3


def sysmem():
    """廉价通道：只问系统内存，微秒级，可 2 秒一次。"""
    m = mem()
    limit = m.ullTotalPageFile / G
    used = (m.ullTotalPageFile - m.ullAvailPageFile) / G
    return {"limit": limit, "used": used,
            "avail_phys_mb": m.ullAvailPhys / 1048576,
            "use_pct": used / limit * 100}


def proc_scan(prev, scan_every):
    """昂贵通道：遍历全部进程（这是唯一费 CPU 的一步），低频调用。"""
    procs, priv_sum, h_sum, h_max = [], 0.0, 0, None
    me = psutil.Process()
    for p in psutil.process_iter(['pid', 'name', 'memory_info', 'num_handles']):
        try:
            mi = p.info['memory_info']
            if not mi:
                continue
            priv = mi.private
            priv_sum += priv
            nh = p.info['num_handles'] or 0
            h_sum += nh
            if h_max is None or nh > h_max[3]:
                h_max = (p.info['name'] or "", p.info['pid'], nh, nh)
            procs.append({"pid": p.info['pid'], "name": p.info['name'] or "",
                          "priv": priv, "handles": nh, "cmd": ""})
        except Exception:
            continue
    procs.sort(key=lambda x: x["priv"], reverse=True)
    for x in procs[:TOP_N]:        # 只给 Top-N 取命令行（判定/白名单只需这些）
        try:
            x["cmd"] = " ".join(psutil.Process(x["pid"]).cmdline() or [])[:160]
        except Exception:
            x["cmd"] = ""
    try:
        me_cpu = me.cpu_percent(None)
        me_rss = me.memory_info().rss / 1048576
    except Exception:
        me_cpu, me_rss = 0.0, 0.0
    grow = []
    for x in procs[:TOP_N]:
        old = prev.get(x["pid"])
        if old is not None:
            d = (x["priv"] - old) / 1048576
            grow.append((x["name"], x["pid"], d / scan_every * 60))   # 统一成 MB/分钟
    return {"procs": procs, "priv_sum": priv_sum, "grow": grow,
            "me_cpu": me_cpu, "me_rss": me_rss,
            "handles_total": h_sum, "hmax": h_max[:3] if h_max else ("", 0, 0)}


_GROW_STREAK = {}          # pid → 连续超阈值的采样次数（防一次性尖峰误报）
BRAKE_SCRIPT = os.path.join(HERE, "brake.py")
_brake_state = {"pid": None, "fails": 0, "reported": False, "retired": False}


def _brake_daemon_pid():
    """找 brake.py --daemon 的 PID。只对少数 python 进程取命令行，很便宜。"""
    for p in psutil.process_iter(['pid', 'name']):
        try:
            if (p.info['name'] or '').lower() not in ('pythonw.exe', 'python.exe'):
                continue
            cmd = " ".join(psutil.Process(p.info['pid']).cmdline() or [])
            if 'brake.py' in cmd and '--daemon' in cmd:
                return p.info['pid']
        except Exception:
            continue
    return None


def ensure_brake(log):
    """刹车守护在不在？不在就拉起来。返回一条告警文本（拉不起来时）或 None。

    为什么让守护脚本管刹车：热键必须有个活进程持有，进程没了热键就**静默失效**——
    而"静默失效的刹车"比"没有刹车"更危险，因为你会以为它还在。
    """
    # ★ 窗Q2（2026-10-16）：老线 brake.py --daemon 已退役（打印去向 + rc=2，不再常驻）。
    #   这里必须**停手**：否则每个采样周期都会再 Popen 一次那个立刻退出的进程，而
    #   Popen 对 rc=2 **不抛异常** —— 于是日志每轮都写「已重新拉起、热键恢复可用」，
    #   实际上没有任何进程持有 Ctrl+Alt+Home。那比退役前更坏：不是哑，是骗。
    if _brake_state.get("retired"):
        return ("老线刹车守护已退役：热键 Ctrl+Alt+Home 需另起新线 brake-daemon"
                "（桌面 bat 急停仍然可用）")
    pid = _brake_state["pid"]
    if pid and psutil.pid_exists(pid):
        return None
    found = _brake_daemon_pid()
    if found:
        _brake_state["pid"], _brake_state["fails"], _brake_state["reported"] = found, 0, False
        return None
    try:
        p = subprocess.Popen([sys.executable, "-X", "utf8", BRAKE_SCRIPT, "--daemon"],
                             cwd=HERE, creationflags=0x08000000)      # CREATE_NO_WINDOW
        # ★ 窗Q2：Popen 成功 != 进程活着。退役版的 --daemon **立刻 rc=2 退出**，
        #   而 Popen 对此不抛异常 —— 不确认存活就会把「没人在按热键」记成
        #   「热键恢复可用」。等一小会儿确认它真活下来了，再决定要不要记 pid。
        try:
            p.wait(timeout=0.8)
        except Exception:
            pass
        if p.poll() is not None:
            _brake_state["pid"], _brake_state["fails"], _brake_state["reported"] = None, 0, True
            _brake_state["retired"] = True
            log("❌ 老线 brake.py --daemon 已退役（rc=%s），本守护不再拉它 —— "
                "热键 Ctrl+Alt+Home 需由新线常驻：AgentCtl.exe brake-daemon" % p.returncode)
            return ("老线刹车守护已退役：热键 Ctrl+Alt+Home 需另起新线 brake-daemon"
                    "（桌面 bat 急停仍然可用）")
        _brake_state["pid"], _brake_state["fails"], _brake_state["reported"] = p.pid, 0, False
        log(f"🛡 刹车守护不在，已重新拉起（PID {p.pid}）——热键 Ctrl+Alt+Home 恢复可用")
        return None
    except Exception as e:
        _brake_state["fails"] += 1
        if not _brake_state["reported"]:
            log(f"❌ 刹车守护拉不起来（第 {_brake_state['fails']} 次）：{e}")
            _brake_state["reported"] = True
        if _brake_state["fails"] >= 3:
            return f"刹车守护连续 {_brake_state['fails']} 次拉不起来（热键失效，桌面 bat 仍可用）"
        return None


def grade(s, pol, slow):
    th = pol["阈值"]
    hits, level = [], 0            # 0 正常 1 注意 2 偏高 3 危险

    def low(key, val, ths):
        nonlocal level
        if val is None:
            return
        for i, t in enumerate(ths):
            if val < t:
                level = max(level, i + 1)
                hits.append(f"{key}={val:.0f} < {t}")

    def high(key, val, ths):
        nonlocal level
        if val is None:
            return
        for i, t in enumerate(ths):
            if val > t:
                level = max(level, i + 1)
                hits.append(f"{key}={val:.0f} > {t}")

    low("可用物理MB", s["avail_phys_mb"], th["可用物理MB"])
    # 零页表：实测噪声极大（同一分钟可在 11~162 MB 间跳），不做判据，仅进 CSV 留档
    high("未归属差额GB", s["gap"], th["未归属差额GB"])
    high("全系统句柄数", s.get("handles_total"), th.get("全系统句柄数", [9999999]))
    # 句柄泄漏：09-17 那次 22 GB 差额的真凶就是它（steam.exe 449k 句柄），单看内存永远看不到
    hn, hp, hc = s.get("hmax") or ("", 0, 0)
    hth = th.get("单进程句柄数", [])
    if hth and hc > hth[0]:
        for i, t in enumerate(hth):
            if hc > t:
                level = max(level, i + 1)
        hits.append(f"{hn}({hp})句柄 {hc} 个")
    high("未归属差额增速GB每h", s.get("gap_rate"), th.get("未归属差额增速GB每小时", [9999]))
    high("提交使用率%", s["use_pct"], th["提交使用率百分比"])
    ths = th["单进程增速MB每分"]
    seen = set()
    for nm, pid, d in s["grow"]:
        if d > ths[0]:
            _GROW_STREAK[pid] = _GROW_STREAK.get(pid, 0) + 1
        else:
            _GROW_STREAK.pop(pid, None)
            continue
        seen.add(pid)
        if _GROW_STREAK[pid] < 2:        # 一次性尖峰不算：要**连续两次采样**都超才算
            continue
        for i, t in enumerate(ths):
            if d > t:
                level = max(level, i + 1)
        hits.append(f"{nm}({pid})增速 {d:.0f}MB/分（连续{_GROW_STREAK[pid]}次）")
    for pid in [p for p in _GROW_STREAK if p not in seen]:
        _GROW_STREAK.pop(pid, None)
    return level, hits


LVL = {0: "🟢正常", 1: "🟡注意", 2: "🟠偏高", 3: "🔴危险"}


def protected(pol, name):
    return name.lower() in {x.lower() for x in pol["永不触碰"]}


def whitelisted(pol, name, cmd):
    for w in pol["白名单"]:
        if w["进程名"].lower() == name.lower() and w["命令行含"].lower() in cmd.lower():
            return w
    return None


def do_enforce(pol, procs):
    act = []
    for x in procs[:TOP_N]:
        if protected(pol, x["name"]):
            continue
        w = whitelisted(pol, x["name"], x["cmd"])
        if not w:
            continue
        if "清工作集" in w.get("动作", []):
            try:
                h = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, x["pid"])
                if h:
                    ctypes.windll.psapi.EmptyWorkingSet(h)
                    ctypes.windll.kernel32.CloseHandle(h)
                    act.append(f"清工作集 {x['name']}({x['pid']})")
            except Exception as e:
                act.append(f"清工作集失败 {x['name']}({x['pid']}): {e}")
    return act


def replay():
    """回放 09-16 卡死现场：提交 52.37/57.20、差额 43 GB、零页表 4 MB、可用物理低"""
    pol = json.load(open(POLICY, encoding="utf-8"))
    fake = {"limit": 57.20, "used": 52.37, "gap": 43.0,
            "avail_phys_mb": 400, "use_pct": 52.37 / 57.20 * 100, "grow": []}
    slow = {"零页表MB": 4.0}
    lvl, hits = grade(fake, pol, slow)
    print("回放 09-16 卡死现场 →", LVL[lvl])
    for h in hits:
        print("   触发:", h)
    print("期望：🔴危险。结果:", "通过" if lvl == 3 else "**不通过，阈值需调**")
    return 0 if lvl == 3 else 1


def main():
    if REPLAY:
        return replay()
    pol = json.load(open(POLICY, encoding="utf-8"))
    logdir = _resolve(pol["日志目录"])
    os.makedirs(logdir, exist_ok=True)
    csvp = os.path.join(logdir, "guardian.csv")
    logp = os.path.join(logdir, "guardian.log")
    stopflag = _resolve(pol["急停文件"])
    fast = pol["_采样"]["快采样秒"]
    slow_every = pol["_采样"]["慢采样秒"]

    def log(msg):
        line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        print(line, flush=True)
        with open(logp, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"守护启动 模式={'执行' if ENFORCE else '观察'} 快采样={fast}s 慢采样={slow_every}s 急停文件={stopflag}")
    if not os.path.exists(csvp):
        with open(csvp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["时间", "提交GB", "上限GB", "使用率%", "未归属差额GB", "可用物理MB",
                        "零页表MB", "硬故障每s", "磁盘队列", "判定", "触发项", "急停",
                        "自身CPU%", "自身RSS_MB", "Top1", "Top2", "Top3", "处置",
                        "全系统句柄", "最大句柄进程", "最大句柄数"])

    prev, last_slow, last_scan, slow = {}, 0.0, 0.0, {}
    last_level, last_log_t, last_emergency = None, 0, False
    hist = []
    scan_every = pol["_采样"].get("进程扫描秒", slow_every)
    while True:
        try:
            t0 = time.time()
            now = time.time()
            emergency = os.path.exists(stopflag)

            if now - last_scan >= scan_every:          # ── 昂贵通道：全进程扫描（低频）
                cache = proc_scan(prev, max(1.0, now - last_scan if last_scan else scan_every))
                prev = {x["pid"]: x["priv"] for x in cache["procs"][:60]}
                last_scan = now
                if now - last_slow >= slow_every:
                    slow = counters()
                    last_slow = now
                s = sysmem()
                s.update({"t": datetime.datetime.now(),
                          "gap": s["used"] - cache["priv_sum"] / G,
                          "top": cache["procs"][:TOP_N], "grow": cache["grow"],
                          "me_cpu": cache["me_cpu"], "me_rss": cache["me_rss"],
                          "handles_total": cache["handles_total"], "hmax": cache["hmax"]})
                hist.append((now, s["gap"]))               # 差额增速：漏内存是"涨"出来的，绝对值滞后
                cutoff = now - 600
                while len(hist) > 2 and hist[0][0] < cutoff:
                    hist.pop(0)
                s["gap_rate"] = None
                if hist[-1][0] - hist[0][0] >= 600:       # 必须满 10 分钟窗口才算（差额本身有百MB级抖动）
                    s["gap_rate"] = (hist[-1][1] - hist[0][1]) / ((hist[-1][0] - hist[0][0]) / 3600)
                lvl, hits = grade(s, pol, slow)
                warn = ensure_brake(log)          # 刹车守护：不在就拉起来；连续拉不起来才报
                if warn:
                    lvl = max(lvl, 1)
                    hits.append(warn)
                acts = []
                if ENFORCE and not emergency and lvl >= 2:
                    acts = do_enforce(pol, s["top"])
                top = [f"{x['name']}:{x['priv']/G:.2f}GB" for x in s["top"][:3]]
                with open(csvp, "a", newline="", encoding="utf-8-sig") as f:
                    csv.writer(f).writerow([
                        s["t"].strftime("%Y-%m-%d %H:%M:%S"), f"{s['used']:.2f}", f"{s['limit']:.2f}",
                        f"{s['use_pct']:.1f}", f"{s['gap']:.2f}", f"{s['avail_phys_mb']:.0f}",
                        "" if slow.get("零页表MB") is None else f"{slow['零页表MB']:.0f}",
                        "" if slow.get("硬故障每s") is None else f"{slow['硬故障每s']:.0f}",
                        "" if slow.get("磁盘队列") is None else f"{slow['磁盘队列']:.2f}",
                        LVL[lvl], " ; ".join(hits)[:200], "是" if emergency else "",
                        f"{s['me_cpu']:.1f}", f"{s['me_rss']:.0f}",
                        top[0] if top else "", top[1] if len(top) > 1 else "", top[2] if len(top) > 2 else "",
                        " ; ".join(acts)[:120],
                        str(s["handles_total"]), s["hmax"][0], str(s["hmax"][2])])
                # 日志降噪：只在①等级变化 ②每 5 分钟 ③急停状态变化 ④有处置 时写一行（CSV 仍全量）
                nowt = time.time()
                if (lvl != last_level) or (nowt - last_log_t >= 300) or emergency != last_emergency or acts:
                    log(f"{LVL[lvl]} " + ("【急停生效，跳过处置】" if emergency else "") +
                        " | " + " ; ".join(hits)[:160] + (" | 处置: " + " ; ".join(acts) if acts else ""))
                    last_level, last_log_t = lvl, nowt
                last_emergency = emergency
            else:                                       # ── 廉价通道：只盯急停（2 秒一次）
                if emergency != last_emergency:
                    log("🛑 急停生效（检测到急停文件），已跳过处置" if emergency
                        else "✅ 急停解除（急停文件已删除），恢复判定")
                    last_emergency = emergency

            time.sleep(max(0.2, fast - (time.time() - t0)))
        except KeyboardInterrupt:
            log("收到中断，退出")
            return 0
        except Exception as e:
            log(f"采样异常（继续）: {e}")
            time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(main())
