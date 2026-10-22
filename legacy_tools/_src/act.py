# -*- coding: utf-8 -*-
# ═══════════════ ② 启动自检：一次报全所有缺失项（2026-10-10 派单·效率优化②）═══════════════
# 为什么这段必须放在**任何业务 import 之前**、且**不能分散在若干 try 里**：
#   真源旧版把 `import win32gui` 直接写在文件顶部 —— 用托管 Python（没有 pywin32）跑，
#   第 1 行就 `ModuleNotFoundError: No module named 'win32gui'`；等装上 pywin32 再跑，
#   第 2 遍才报 `tkinter` 缺失。**两次失败各报一项**，调用方要跑两轮才知道缺什么。
#   这与"15 分 16 秒里 8 个低级错"的现场完全同源：工具本该一次说清。
#
# 本段只做三件事，全部用标准库：
#   ① 探测宿主是否 Windows；② 探测解释器是否系统 Python（不是托管/沙箱解释器）；
#   ③ 探测 pywin32 与 tkinter 是否可导入。
# 结果汇总进 _SELFTEST，**缺失项一次全列**，然后由 _selftest_or_die() 统一拦住。
import os as _os
import sys
import sys as _sys

# 托管 Python 的路径特征（本机实测）：
#   托管：%USERPROFILE%\.workbuddy\binaries\python\versions\<ver>\python.exe
#   系统：%LOCALAPPDATA%\Programs\Python\Python312\python.exe
# 判据用「路径里有没有 .workbuddy\binaries\python」而不是「版本号」——
# 版本会变，托管根不会（它是沙箱约定的固定落点）。
_MANAGED_MARKERS = (".workbuddy" + _os.sep + "binaries" + _os.sep + "python",
                    ".workbuddy/binaries/python")


def _is_managed_python(executable=None):
    """是不是「托管 Python」（沙箱解释器）。真则缺 pywin32/tkinter 基本是必然。"""
    p = (executable or _sys.executable or "").replace("/", _os.sep).lower()
    return any(m.replace("/", _os.sep).lower() in p for m in _MANAGED_MARKERS)


def _probe_import(name, execute=True):
    """只探测「能否导入」，**不留下副作用**。

    ★ `execute=False`（M16 派单 §5 · D1，2026-10-22 主控裁决）：
      **只问「在不在」，不执行模块**（`importlib.util.find_spec`）⇒
      探 tkinter 这种**只在别的进程里才用得到**的模块时，
      **不用每次启动都真 import 一遍**（实测真 import tkinter = **28.5ms**，
      而 act.py 自己**从不**用 tkinter —— 它是授权弹窗 `auth_popup.py` 的依赖）。
      ⇒ 省 ≈28.5ms/动作。
      ⚠ **口径不变**：缺件清单照旧报出该项（`find_spec` 找不到 ⇒ 报「找不到模块」）。
      ⚠ **不假装「有」**：`find_spec` 只证明**文件在**，不证明**能 import**
        （残缺安装：Tcl/Tk 坏掉时 `import tkinter` 会炸）⇒
        通过项的措辞**只说「在」**，不承诺「可导入」；
        真要用它的那条路（授权弹窗）**自己会报真错**。
      ⚠ `execute=True`（默认）仍**真导入** —— win32gui/win32api 反正后面就要 import
        （模块已进 sys.modules ⇒ 第二次免费）⇒ 那一路**不花钱、也不改变行为**。
    """
    import importlib.util as _ilu
    try:
        if _ilu.find_spec(name) is None:
            return False, "找不到模块"
        if not execute:
            return True, ""          # ★ 只证「在」，**不执行**
        __import__(name)
        return True, ""
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def _collect_selftest():
    """把四项自检一次跑全，返回 (缺失项列表, 通过项列表)。

    缺失项每条都带**可直接执行的下一步**（这是本单 ①/② 的共同要求）。
    """
    miss, ok = [], []
    py = _sys.executable or "(未知)"

    if _sys.platform.startswith("win"):
        ok.append("宿主=Windows")
    else:
        miss.append({
            "项": "宿主操作系统",
            "实参": "sys.platform=%r" % (_sys.platform,),
            "下一步": "本工具只用 Win32 API（SendInput/PrintWindow/UIA），**没有非 Windows 实现**；"
                      "请在 Windows 上运行。",
        })

    if _is_managed_python():
        miss.append({
            "项": "解释器（用了托管 Python）",
            "实参": py,
            "下一步": "改用**系统 Python 3.12**（完整安装版，不是宿主自带的托管解释器）：\n"
                      "        用那条 python.exe 跑： <系统Python>\\python.exe -X utf8 act.py <action> --hwnd N\n"
                      "      取法：跑 `where python`，挑出「Programs\\Python\\Python3xx」那种系统安装版；\n"
                      "           再跑 `\"<那个路径>\" -c \"import sys;print(sys.executable)\"` 确认。\n"
                      "      （托管解释器 —— 宿主工具自带的那个 —— 往往没装 pywin32 与 tkinter，"
                      "换它跑必然两项都缺。）",
        })
    else:
        ok.append("解释器=非托管（%s）" % py)

    for mod in ("win32gui", "win32api"):
        good, why = _probe_import(mod)
        if good:
            ok.append("pywin32.%s 可导入" % mod)
    # 只报一次 pywin32（两个子模块任一失败即算缺）
    if not (_probe_import("win32gui")[0] and _probe_import("win32api")[0]):
        miss.append({
            "项": "pywin32（win32gui / win32api）",
            "实参": "; ".join("%s → %s" % (m, _probe_import(m)[1] or "OK")
                              for m in ("win32gui", "win32api")),
            "下一步": "给**上面那个解释器**装 pywin32：\n"
                      "        \"%s\" -m pip install pywin32\n"
                      "      装完直接重跑本命令即可（不需要重启终端）。" % py,
        })

    # ★ M16 派单 §5 · D1（2026-10-22 主控裁决）：tkinter **只问「在不在」**（find_spec），
    #   **不执行模块** ⇒ 每次启动省 ≈28.5ms（act.py 自己从不用 tkinter，它只是
    #   授权弹窗 auth_popup.py 的依赖 ⇒ 真要用它的那条路会自己报真错）。
    #   ⚠ 缺件清单语义**不变**：找不到 ⇒ 照旧报出该项（下面这条）。
    good, why = _probe_import("tkinter", execute=False)
    if good:
        # ⚠ 措辞**不承诺「可导入」**：find_spec 只证明文件在，不证明能 import
        #   （残缺安装时 `import tkinter` 会炸）⇒ 真错由授权弹窗那条路报。
        ok.append("tkinter 在（**未执行导入验证**；真正需要它的授权弹窗 auth_popup.py "
                  "那条路会自己报真错）")
    else:
        miss.append({
            "项": "tkinter（授权弹窗 auth_popup.py 依赖）",
            "实参": why,
            "下一步": "tkinter 随系统 Python 自带；缺失说明当前解释器是**精简/嵌入式版**。"
                      "请换用完整安装的系统 Python（官方安装包勾选 tcl/tk）；"
                      "可用 `where python` 找到系统安装版，再用 `-c \"import tkinter\"` 自检。",
        })
    return miss, ok


_SELFTEST_MISS, _SELFTEST_OK = _collect_selftest()


def _selftest_or_die(argv=None):
    """启动闸：缺任何一项 ⇒ **一次报全**并退出（rc=11），不进业务逻辑。

    为什么 rc=11 而不是通用 1：调用方（drill / DSH）要能一眼区分
    「环境不对」与「动作被拒」。1/2/3/4/5/6/7/8/9 已被既有语义占用。
    """
    if not _SELFTEST_MISS:
        return 0
    print("=" * 72)
    print("⛔ act.py 启动自检未通过 —— 缺 %d 项（**一次全列，不必再跑第二遍**）"
          % len(_SELFTEST_MISS))
    print("=" * 72)
    print("  解释器 : %s" % (_sys.executable or "(未知)"))
    print("  平台   : %s  %s" % (_sys.platform, _sys.version.split()[0]))
    print()
    for i, m in enumerate(_SELFTEST_MISS, 1):
        print("%d) 缺：%s" % (i, m["项"]))
        print("     实参：%s" % m["实参"])
        print("     下一步：%s" % m["下一步"])
        print()
    if _SELFTEST_OK:
        print("（已通过：%s）" % "、".join(_SELFTEST_OK))
    print("=" * 72)
    return 11


"""act.py —— 动作层（B 级：点击 / 滚动）

铁律 11 的第 2 档。**动作会改变用户的电脑状态，所以规矩比感知层严得多**：

  1. 必须过闸 `gate.B`（刹车松开 + 持 B 级通行证 + 范围完全匹配）
  2. **白名单窗口**：只允许在名单内的窗口上动作（默认只有记事本）
  3. **能 Invoke 就绝不点坐标**：UIA 支持 Invoke/SelectionItem 的控件，直接调模式，
     根本不产生鼠标事件 —— 没有坐标，就没有"点错地方"这回事
  4. 只能点坐标时：目标窗口**必须在前台**，且坐标必须落在该窗口矩形内，否则拒绝
     （**例外**：加 `--post` 走后台投递，那一次不要求前台 —— 见下）
  5. 每次动作前后各留一张目标窗口截图（PrintWindow）+ 控件记录，写 `_auth/act.log`
  6. `--dry-run` 只算不做

用法：
  python act.py targets --hwnd N                  # 列出可点的控件（只读，A 级即可）
  python act.py click --hwnd N --name "dsh.txt"    # 按控件名点（优先 UIA）
  python act.py click --hwnd N --xy 1200,800       # 按坐标点（需前台）
  python act.py click --hwnd N --xy 1200,800 --post  # 后台投递（不抢焦点，按兼容表分档）
  python act.py scroll --hwnd N --amount -3        # 滚动（优先 UIA Scroll）
"""
import argparse
import collections
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import sys
import threading
import time

# ★ M12 阶段二（2026-10-22）：**消除双实现** —— 判差与退化检测统一用 agentctl.framecache
#   （同仓同 PYTHONPATH；core 通过 _legacy_env() 把 src 传进子进程，见 core.py:_legacy_env）。
#   ★ 取不到 framecache 时**不降级回自带实现**（那正是双实现的病根）⇒ 只采样、不判变化
#     ⇒ core 侧拿不到「变化帧」⇒ **回落 wait_change**（多等，但不误判）。
try:
    from agentctl import framecache as _fc
except Exception:            # pragma: no cover
    _fc = None

HERE = os.path.dirname(os.path.abspath(__file__))
ACTLOG = os.path.join(HERE, "_auth", "act.log")

# ═════════════ ① 失败信息自带下一步（2026-10-10 派单·效率优化①）═════════════
# 现场教训（证据：任务执行记录_20261010_7步联动计时.md §三）：
#   rc=5 出现过 **3 次**（逻辑坐标 vs 物理像素 1 次、快照路径集不一致 1 次、
#   窗口不在前台 2 次），每次都要人来猜"5 到底指哪个 5"。
#   同一个错误码承载了三种完全不同的成因 ⇒ 光看码无法回答"我下一步该做什么"。
#
# 本段的做法：**拒绝之前先算好证据**，把「现象」升级成「现象 + 可执行的下一步」。
# 三条设计约束（都不是随手定的）：
#   1. 建议必须是**可直接粘进终端的命令**，不是"请检查坐标"这种废话；
#   2. 算不出来的证据要**如实说算不出来**，不许编一个看起来像样的数（见 _dpi_report）；
#   3. 只在**要拒绝时**才去取这些证据（取 DPI / 枚举快照有耗时），
#      不能让"正常路径变慢"来换"错误路径变清楚"。


def _dpi_report(hwnd=None):
    """把本轮坐标口径与 DPI 缩放因子一次说清。返回可直接打印的文本行。

    ★ 这里最容易犯的错是**想当然**：以为"设了 PerMonitorV2 就一定是 1.5×"。
      本机实测：显示器物理 2560×1600，但窗口客户区只有 1902×294 —— 说明存在
      **系统级缩放**（GetDpiForSystem()=144 ⇒ 150%）。所以缩放因子**必须实测**，
      而且**逐窗口测**（GetDpiForWindow），跨屏拖窗口时两者会不一致。
    """
    lines = []
    try:
        sysdpi = int(ctypes.windll.user32.GetDpiForSystem())
        lines.append("本进程 DPI 感知已声明 = PER_MONITOR_AWARE_V2"
                     "（act.py:SetProcessDpiAwareness(2)）")
        lines.append("系统 DPI = %d（缩放 %.0f%%）"
                     % (sysdpi, sysdpi / 96.0 * 100))
    except Exception as e:
        lines.append("系统 DPI 读不到（%s: %s）—— 下面的倍数仅供参考" % (type(e).__name__, e))
        sysdpi = None

    if hwnd is not None:
        try:
            wdpi = int(ctypes.windll.user32.GetDpiForWindow(wt.HWND(int(hwnd))))
            if wdpi:
                lines.append("目标窗口 hwnd=%d 的 DPI = %d（缩放 %.0f%%）"
                             % (int(hwnd), wdpi, wdpi / 96.0 * 100))
                if sysdpi and wdpi != sysdpi:
                    lines.append("⚠ 与系统 DPI 不一致（%d ≠ %d）：窗口被拖到了另一块不同缩放的屏幕上"
                                 % (wdpi, sysdpi))
            else:
                lines.append("目标窗口 hwnd=%d 的 GetDpiForWindow 返回 0（窗口可能已销毁）" % int(hwnd))
        except Exception as e:
            lines.append("目标窗口 DPI 读不到（%s: %s）" % (type(e).__name__, e))

    lines.append("★ 坐标口径：**act.py 全程用物理像素**；用户在截图/别的工具里量到的"
                 "多半是**逻辑坐标**。")
    if sysdpi:
        f = sysdpi / 96.0
        lines.append("★ 换算：物理 = 逻辑 × %.2f ；逻辑 = 物理 ÷ %.2f" % (f, f))
        lines.append("   例：逻辑 (100,200) → 物理 (%d,%d)"
                     % (round(100 * f), round(200 * f)))
    lines.append("★ 下一步（三条任选）：")
    lines.append("   ① 直接改用 targets 给出的「中心」值 —— 它已经是物理像素，别换算：")
    lines.append("        python -X utf8 act.py targets --hwnd %s --compact"
                 % (int(hwnd) if hwnd is not None else "<N>"))
    lines.append("   ② 若你手上只有逻辑坐标，先乘上面的倍数再传 --xy。")
    lines.append("   ③ 想确认口径，跑一次 dry-run 看它报的窗口矩形与截图里的像素是否吻合：")
    lines.append("        python -X utf8 act.py click --hwnd %s --xy x,y --dry-run"
                 % (int(hwnd) if hwnd is not None else "<N>"))
    return "\n      ".join(lines)


def _tell_next(rc, why, hwnd=None, extra=None, tip=None):
    """非零 rc 的统一出口：打印「现象」+「下一步」。返回 rc（便于 `return _tell_next(...)`）。

    刻意**不做成异常**：act.py 的既有契约是"返回错误码"（drill / DSH 都按 rc 分支），
    改成抛异常会打断所有既有调用方。所以这里是"补齐信息"，不是"改变控制流"。
    """
    lines = ["⛔ rc=%d：%s" % (rc, why)]
    if extra:
        lines.append("   " + str(extra))
    # tip 允许调用方自带建议（2026-10-16 窗K 加）：同一个 rc 在本仓可能对应两种
   # 语义（rc=7 早被「文档非空防污染护栏」占着，本单又用它表示「终端待人工确认」），
   # 光按 rc 查表会把驴唇不对马嘴的建议打出来。由调用方显式传 tip 就没这个问题。
    tip = tip if tip is not None else NEXT_STEP.get(rc)
    if tip:
        try:
            lines.append("   ↪ 下一步：\n      " + tip(hwnd))
        except Exception as e:                       # 建议生成失败不许拖垮主流程
            lines.append("   ↪ 下一步：生成建议时出错（%s: %s）；"
                         "请先跑 `act.py targets --hwnd %s --compact` 看工具看到了什么"
                         % (type(e).__name__, e, hwnd))
    else:
        lines.append("   ↪ 下一步：本错误码（rc=%d）暂无内置建议，"
                     "请把上面这行原文连同窗口 hwnd=%s 一起回传。" % (rc, hwnd))
    msg = "\n".join(lines)
    log(msg)
    return rc


def _cmd_targets(hwnd):
    return "python -X utf8 act.py targets --hwnd %s --compact" % hwnd


def _cmd_read(hwnd):
    return "python -X utf8 act.py read --hwnd %s" % hwnd


# ── ① rc=5「快照类」：快照路径集采集 ────────────────────────────────────────
# 现场（证据 §三 错 4）：调用方 `snap` 时**忘了传 --paths**，紧接着 `check` 报 rc=5
#   「前后快照盯的路径集不一致」。工具当时只给了一句现象 —— 调用方得自己回翻
#   两次 snap 的命令行才知道哪一次漏了。更糟的是：**这条路根本不经过 act.py**，
#   是 验动作.py 的 check 在判；调用方拿着 act.py 的 rc 表去查，查不到 5 的含义。
#
# 本段的做法：act.py 侧**自己采集**"此刻能找到的最近快照与它们的路径集"，
#   把"缺 --paths"和"前后两侧实际拿到什么"直接摆出来，并给出可粘贴的重拍命令。
#   采集失败一律如实说失败，**不编造路径集**（同 _dpi_report 的约束 2）。
#
# 为什么不去 import 验动作.py：那个模块在 agent-ctl 下、属新线，act.py 属老线；
#   跨线 import 会把两条线的耦合重新焊死（本单只动 act.py）。所以这里**只读文件**。
# ★ 不写死本机路径（开源的最低要求，也是 tools/port_legacy.py 的统一规则）。
#   原先这里写死了三条开发机的绝对路径：换机器/换用户名全部落空，
#   而且会把目录结构和用户名一起公开到仓库里。
#   改成：**环境变量 > 脚本自身目录**。真源就放在 tools\ 下，快照目录自然在
#   同级的 _work\，语义与原来一致，但不再依赖任何人的盘符布局。
_SNAP_CANDIDATES = tuple(
    d for d in (
        os.environ.get("AGENTCTL_SNAP_DIR"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "_work"),
        os.path.dirname(os.path.abspath(__file__)),
    ) if d
)


def _snap_dirs():
    """返回去重后的快照目录候选（只保留真实存在的）。**不递归扫盘**。"""
    seen, out = set(), []
    for d in _SNAP_CANDIDATES:
        try:
            rp = os.path.realpath(d)
            if rp in seen or not os.path.isdir(rp):
                continue
            seen.add(rp)
            out.append(rp)
        except Exception:
            continue
    return out


def _read_snap(name):
    """按 tag 名在所有候选目录里找 <tag>.json，返回 (路径, 对象) 或 (None, None)。

    按 **mtime 倒序**取最近的一份 —— 同一 tag 在多个目录都存过时，最近改的才是
    本次会话真正用的那份。
    """
    hits = []
    for d in _snap_dirs():
        p = os.path.join(d, str(name) + ".json")
        try:
            if os.path.exists(p):
                hits.append((os.path.getmtime(p), p))
        except Exception:
            continue
    for _mt, p in sorted(hits, reverse=True):
        try:
            with open(p, encoding="utf-8") as f:
                return p, json.load(f)
        except Exception as e:
            return p, {"__读失败__": "%s: %s" % (type(e).__name__, e)}
    return None, None


def _declared_paths_of(snap):
    """快照里声明的 --paths 路径集；无该字段返回 None（=不可比，不是空集）。"""
    if not isinstance(snap, dict):
        return None
    ps = snap.get("paths")
    if ps is None:
        return None
    return sorted({os.path.normcase(os.path.normpath(str(p).strip()))
                   for p in ps if str(p).strip()})


def _snapshot_path_report(tag_before, tag_after, dirs_hint):
    """① rc=5 快照类：把「缺 --paths 了吗 + 前后两侧实际拿到什么」一次说清。

    返回可直接打印的文本。**任何一项读不到就写读不到**，不猜。
    """
    lines = []
    lines.append("★ 采集：本机能找到的快照目录（只读，不扫盘）")
    ds = _snap_dirs()
    if ds:
        for d in ds:
            lines.append("     %s" % d)
    else:
        lines.append("     **一个候选目录都不存在** —— 快照可能压根没拍过，"
                     "或目录不在本工具预置的候选里（%s）" % " ；".join(_SNAP_CANDIDATES))
    lines.append("★ 两侧实际拿到的路径集：")
    if tag_before is None:
        lines.append("     before：*没告诉我叫什么* —— 请加 --before <tag>，我才能去读它")
    if tag_after is None:
        lines.append("     after ：*没告诉我叫什么* —— 请加 --tag <tag>，我才能去读它")
    for role, tag in (("before", tag_before), ("after", tag_after)):
        if tag is None:
            continue
        p, snap = _read_snap(tag)
        if p is None:
            lines.append("     %-6s tag=%-14s → **找不到** <tag>.json（上面几个目录都翻了）"
                         " —— 说明这次 snap 没落盘，或 tag 打错" % (role, tag))
            continue
        if not isinstance(snap, dict) or "__读失败__" in (snap or {}):
            lines.append("     %-6s tag=%-14s → %s **读失败**：%s"
                         % (role, tag, p, (snap or {}).get("__读失败__", "非 JSON 对象")))
            continue
        ps = _declared_paths_of(snap)
        if ps is None:
            lines.append("     %-6s tag=%-14s → ★**缺 paths 字段**（snap 时没传 --paths，"
                         "或用了很旧的版本）：%s" % (role, tag, p))
            lines.append("                「缺字段」= 不可比，**不是**「两边都是空集」—— "
                         "这正是拒绝判定的原因。")
        elif not ps:
            lines.append("     %-6s tag=%-14s → 路径集为**空**（有 paths 字段但一条都没写）：%s"
                         % (role, tag, p))
        else:
            lines.append("     %-6s tag=%-14s → 共 %d 条：%s"
                         % (role, tag, len(ps), p))
            for q in ps[:12]:
                lines.append("                - %s" % q)
            if len(ps) > 12:
                lines.append("                … 还有 %d 条" % (len(ps) - 12))
    lines.append("★ 缺失/不一致 ⇒ 判定无效（rc=5）：这是一条**硬校验**，"
                 "为的是不再产出「冤枉的 unknown」。")
    lines.append("★ 下一步（**前后都用同一组 --paths** 重拍，然后重判）：")
    t1 = tag_before or "before"
    t2 = tag_after or "after"
    lines.append("     python -X utf8 验动作.py snap --tag %s --paths \"C:\\路径1;C:\\路径2\" "
                 "--expect \"<期望谓词>\"" % t1)
    lines.append("     …做动作…")
    lines.append("     python -X utf8 验动作.py snap --tag %s --paths \"C:\\路径1;C:\\路径2\"" % t2)
    lines.append("     python -X utf8 验动作.py check --tag %s --before %s --action <名> --tier B"
                 % (t2, t1))
    lines.append("     注：`--paths` 用**分号**分隔，两侧**逐字符相同**才算一致；"
                 "只盯进程/窗口时可省略 --paths（两侧都不写即视为可比）。")
    if dirs_hint:
        lines.append("     提示：本工具默认在 %s 下找 <tag>.json；"
                     "若你的快照在别处，请 cd 过去再跑，或把目录告诉我。" % dirs_hint)
    return "\n      ".join(lines)


def _quota_line(scope=None):
    """① rc=8/9 用：把「已用/上限/剩余」直接读出来。

    ★ 这里是**读账本**，不是猜。gate 通过后动作次数已经写回 auth_pass.json，
      所以此刻读到的就是权威值。读不到就如实说读不到（不编 0）。
    """
    try:
        p = gate._load(gate.PASSFILE, {})
    except Exception as e:
        return "额度读不到（%s: %s）—— 请手动查 gate.py --status" % (type(e).__name__, e)
    if not p:
        return "账本里没有通行证（未签发或读不到）"
    n = p.get("acts", 0)
    # ★ 2026-10-19 用户定：acts_limit ≤ 0（或未写）= 不封顶（执行点改在 gate.require 里）
    _lim = p.get("acts_limit")
    limit = int(_lim) if _lim is not None else int(gate.ACT_LIMIT.get(scope, 20))
    if limit <= 0:
        return "额度：**不封顶**（用户 2026-10-19 定，靠急停兜底）；已用 %d 次" % n
    left = max(0, limit - n)
    return ("额度：已用 %d / 上限 %d / **剩余 %d**"
            "（范围「%s」，有效至 %s）"
            % (n, limit, left, p.get("scope"), p.get("expires")))


def _gate_denied(e, hwnd, scope, what, retried=False):
    """① rc=8/9 的统一出口：把「拒绝原因 + 额度三元组 + 是否失败重试」一次说清。

    现场教训（证据 §三 错 7）：「动作额度用完才发现 —— 20 次里 13 次是失败重试」。
    当时工具只回了 `rc=8 读选中文本被拒` 一行，既没报剩余额度、也没说
    "你这张证已经被失败重试吃掉了大半" —— 调用方于是接着重试，直到彻底没钱。
    """
    why = str(e)
    low = why.lower()
    is_quota = ("次数已用尽" in why) or ("额度" in why) or ("acts_limit" in low)
    extra = _quota_line(scope if isinstance(scope, str) else None)
    if is_quota:
        extra += "\n   ★ 本张证已**耗尽**，本次动作**没有执行**。"
    if retried:
        extra += "\n   ★ 本次是**失败重试**消耗的额度（上一次没成功，额度照扣）。"
    extra += "\n   注：读操作（targets/read）**不扣**动作额度；被扣的是 B/C/D 的真动作。"
    return _tell_next(9 if not is_quota else 8, f"{what}被拒：{why}", hwnd, extra=extra)


# rc → 建议生成函数。**每个非零 rc 都必须在这里有落点**（没有的走 _tell_next 的兜底）。
NEXT_STEP = {
    2: lambda h: ("命令行参数不合法。先看用法：\n"
                  "        python -X utf8 act.py -h\n"
                  "      最常用的三句：targets / click --name / read"),
    3: lambda h: ("踩到了使用者的授权弹窗（标题 DSH电脑控制授权），任何动作一律禁止 —— 这是防自授权闸门，别绕。\n"
                  "      先确认打的是哪个窗口：python -X utf8 win_list.py <关键词>\n"
                  "      ★ 2026-10-12 起应用白名单已废止，不在名单的窗口不会再被这里拒。工具目录：%s" % HERE),
    4: lambda h: ("控件/文档控件没找到。先让工具把**它看到的**列出来，再照着名字点：\n"
                  "        %s\n"
                  "      名字要用 targets 里「名称」那一列的**完整前缀**（支持子串匹配）。" % _cmd_targets(h)),
    5: lambda h: ("坐标 / 快照 / 前台三类共用 rc=5，**先分清你撞的是哪一类**（看上面那条 extra）：\n"
                  "      · 坐标类 → extra 里已给 DPI 缩放因子与换算；最省事的做法是**别自己换算**，\n"
                  "          直接用 targets 给出的物理像素中心值：%s\n"
                  "      · 快照类 → 这是 `验动作.py check` 的硬校验：前后两侧**必须传同一组 --paths**。\n"
                  "          extra 里已列出「两侧实际拿到的路径集」与缺字段的那一侧；对齐后重拍：\n"
                  "            python -X utf8 验动作.py snap --tag before --paths \"C:\\a;C:\\b\" --expect \"...\"\n"
                  "            …做动作…\n"
                  "            python -X utf8 验动作.py snap --tag after  --paths \"C:\\a;C:\\b\"\n"
                  "            python -X utf8 验动作.py check --tag after --before before --action <名> --tier B\n"
                  "          （--paths 用**分号**分隔；只盯进程/窗口时两侧都不写即视为可比。）\n"
                  "      · 前台类 → act.py 已自动置前（派单 ③）；若仍失败，多半是模态框压着，先关它。\n"
                  "      跑一条只读命令确认工具看到了什么：%s" % (_cmd_targets(h), _cmd_targets(h))),
    6: lambda h: ("被模态对话框挡住 / 切前台没成功。**先关掉那个对话框**（它吞掉一切点击），再重试；\n"
                  "      或者手动把窗口点起来，然后：%s" % _cmd_targets(h)),
    7: lambda h: ("文档非空，防污染护栏拒绝打字。**两条合规通路，任选一条**：\n"
                  "      ① 清空后重打（推荐，可逆、可核验）：\n"
                  "           %s          # 读回当前字符数\n"
                  "           python -X utf8 act.py keys --hwnd %s --vk 0x41 --ctrl      # Ctrl+A 全选\n"
                  "           python -X utf8 act.py keys --hwnd %s --vk 0x08 --times 3    # 退格清空\n"
                  "      ② 换成一张空文档再打（例如记事本 Ctrl+N 新建标签页）。" % (_cmd_read(h), h, h)),
    8: lambda h: ("动作发出去了但没生效（页面无变化）/ 额度相关问题。\n"
                  "      先看账本确认是不是**额度耗尽**：python -X utf8 gate.py --status\n"
                  "      若是额度：请用户重新签发（%s\\auth_popup.py）。\n"
                  "      若是页面无变化：重跑一次带截图的 dry-run 定位：%s" % (HERE, _cmd_targets(h))),
    9: lambda h: ("没拿到对应级别的通行证。**不要绕**。先看现状：\n"
                  "        python -X utf8 gate.py --status\n"
                  "      再请用户签发（%s\\auth_popup.py）；注意范围串用**英文逗号**分隔，\n"
                  "      顿号「、」会被当成一个不存在的范围（2026-10-10 实测踩过）。" % HERE),
    11: lambda h: ("环境自检没过。**上面已经列全了缺什么**（本次只跑了这一轮，不必再跑第二遍）——\n"
                   "      照「下一步」逐条装上，然后**原命令直接重跑**即可。"),
}

# ── 启动自检闸（2026-10-10 派单 ②）：**一次报全所有缺失项** ────────────────
# 自检本体定义在文件最上方（必须在 `import win32gui` 之前跑，否则第 1 项缺失
# 就会以 ModuleNotFoundError 的形式把进程打死，后面的项根本没机会报）。
# 这里只负责：**自检没过 ⇒ 立刻拦停**，并且把依赖 import 降到"能 import 就 import"，
# 保证自检信息能完整打出来（否则第一行就崩，等于没做自检）。
sys.path.insert(0, HERE)
if _SELFTEST_MISS:
    # 缺件：立刻报全并退出，**不进任何业务分支**（连 argparse 都不进）
    raise SystemExit(_selftest_or_die())

import gate                                                # noqa: E402
import inject_magic                                       # noqa: E402
import win32gui                                            # noqa: E402
import win32process                                        # noqa: E402

# ── DPI 感知：**必须在读任何窗口坐标之前声明**（2026-09-19 18:45 补）
#    框架 §7 明文：「perceive.py / act.py 一律 SetProcessDpiAwareness(2)，坐标统一在物理像素口径」。
#    act.py 此前**漏了这句** → 它在 150% 缩放下读到的是**虚拟化坐标**：
#      同一窗口 perceive 报 [1228,25,2610,1364]，act 报 [819,17,1740,910] —— **正好差 1.5 倍**，
#      导致"坐标不在窗口矩形内"被误拒（把窗口没动当成窗口动了），
#      更危险的是：坐标归一化会按错误的分辨率算，**真点下去会偏**。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── 白名单：只允许在这些窗口上动作。想加窗口 = 改这里 + 用户点头。
#    2026-09-18 经用户授权加入 VirtualBoxVM.exe：客户机是**沙箱**，且那是用户自己的作业环境，
#    训练/排障都在里面做；动它的风险远低于动宿主桌面。
#    2026-09-19 18:43 经用户**明确授权**（原话「改，准」）加入 WeChatAppEx.exe：
#    用户要在此窗口上做「识别内容→按内容做下一步」的电脑控制测试；用户已知悉
#    「B 级从此也能点在微信上」这一风险扩大，并同意承担。
#    2026-09-23 18:36 修 bug：白名单改配置化时，_load_whitelist() 返回 tuple
#    而 check_target() 仍按 dict 取键（w["进程"]/w["类名"]）→ TypeError，
#    act.py 自 2026-09-20 起实际不可用（act.log 最后一条也在那天以前）。
#    本次统一为 dict，两处口径一致。
# ── 白名单：可配置（2026-09-20 改）─────────────────────────
# 目标：让"授权新窗口"变成改配置而不是改代码（本地模型要能读配置）。
# 配置：本目录 whitelist.json；读不到就用下面的内置默认值，保证不会因为缺文件而拒一切。
_DEFAULT_WHITELIST = [
    {'进程': 'notepad.exe', '类名': 'Notepad'},
    {'进程': 'VirtualBoxVM.exe', '类名': 'Qt680QWindowIcon'},
    {'进程': 'WeChatAppEx.exe', '类名': 'Chrome_WidgetWin_0'},
]


def _load_whitelist():
    import json as _json
    import os as _os
    p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "whitelist.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = _json.load(f)
        if not isinstance(d, dict):
            return _DEFAULT_WHITELIST
    except Exception:
        return _DEFAULT_WHITELIST
    wl = d.get("whitelist") or []
    # ★ 2026-10-10 class 空值语义（Codex 提出两案 / 用户批准一起解决 / 取保守的拒载案）：
    #   匹配是 process 与 class **精确相等**，class 为空的条目**永远匹配不上** ——
    #   不报错、不告警，只是让人误以为放行了（ChatGPT.exe 条目 2026-10-09 摘除前
    #   一直就是这样静默失效的）。一条永远匹配不上的白名单条目是纯负债 ⇒
    #   加载时就拒，说清是哪条、怎么修。（SystemExit 是 BaseException，
    #   不会被上面的 except Exception 吞掉；发行包 whitelist 数组为空不受影响。）
    _bad = [w.get("process") for w in wl
            if w.get("process") and not (w.get("class") or "").strip()]
    if _bad:
        raise SystemExit(
            "[X] whitelist.json 有 %d 条 class 为空的条目（%s）—— class 留空不会变成"
            "『只按进程名』，只会永远匹配不上。要放行就填真实窗口类名（用 perceive 查），"
            "要么删掉该条。拒绝带病加载。" % (len(_bad), ", ".join(_bad)))
    out = [{"进程": w.get("process", ""), "类名": w.get("class", "")}
           for w in wl if w.get("process")]
    if out:
        return out
    return _DEFAULT_WHITELIST


def whitelist_source():
    """给日志用：告诉人这次白名单是从哪来的。"""
    import os as _os
    p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "whitelist.json")
    return "whitelist.json" if _os.path.exists(p) else "内置默认"


WHITELIST = _load_whitelist()

# ⚠️ 2026-10-12：同一天应用白名单被废止，**唯独这一条不能跟着删**。
# 它让控制系统无法操作使用者自己的授权弹窗，是唯一的**防自授权**闸门：
# 删掉它，agent 就能点掉自己的授权框给自己放行，闸门系统形同虚设。
#
# ⚠️ 2026-10-16（窗I）加第二条「AgentCtl 急停」—— 与上面**同源**，所以不能只写一句：
#   急停拉下时会弹一张**桌面置顶**的告警窗（新线 src\agentctl\brake_popup.py），
#   上面写着「是谁在动 / 它的 MCP 链接 / 怎么恢复」。
#   那张窗是**安全提示**，不是提示性装饰 —— 一旦 agent 能点掉它、或用自己的窗口
#   把它压在下面，用户就永远看不见「车已经被停住了」，而急停的**唯一价值恰恰
#   就在于「一定会被看见」**。
#   更要紧的一条：那张窗是 agent 唯一能拿到「自己是谁」的出口（actor + MCP 段名）；
#   盖住它 = 让出事前自证的路。所以它和授权弹窗属于同一类：**不许被本系统触碰**。
#   判据：tests\_急停弹窗判据.py 第 6 条按字面查这一行，改了就红。
#
#   按**标题子串**拦（下面 check_target 判的是 `b in i["title"]`），所以登记的是
#   稳定短键而不是整条标题 —— 整条标题里那个 ⛔ 是 emoji，改文案不该连带改闸门。
#   窗口**类名**是 tkinter 的 TkTopLevel（与本进程所有 Tk 顶层窗共用，改它会波及
#   同进程别的窗），所以这里只能按标题拦 —— 这一点写清楚，免得后人以为漏了。
#
# ⚠️⚠️ 2026-10-16 同步提醒（本仓库这一份**不是生效的那份**，见本目录 README）：
#   生效的老线真源是 <工作区>\dsh-viewer-plugin\tools\act.py，
#   打包也从那里现场生成（tools\gen_legacy.py 的 SRC 常量）。
#   **那一份必须同步加上同样的 "AgentCtl 急停"**，否则这条硬约束在运行时是空的。
BLOCKED_TITLES = (
    "DSH电脑控制授权",
    "AgentCtl 急停",
)

# ⚠️ 2026-10-16（窗I）新增：**按类名**也拦一条。
#   为什么要类名这一层：BLOCKED_TITLES 判的是**标题子串**，标题是可以被改的 ——
#   改一次标题就绕过去了。而这张急停窗的类名是它自己注册的（AgentCtlBrakePopup），
#   **不随标题变**，所以多这一层不多花什么，却堵掉了「改标题绕过」这条路。
#   判据：tests\_急停弹窗判据.py 第 6b 条按字面查这一行，改了就红。
#
# ⚠️⚠️ 同上：本仓库这一份**不是生效的那份**（见本目录 README）。
#   生效的老线真源是 <工作区>\dsh-viewer-plugin\tools\act.py，
#   打包也从那里现场生成。两处都要有，否则这一层是空的。
BLOCKED_CLASSES = (
    "AgentCtlBrakePopup",
)

# ═════════ 2026-10-16（窗K · P0）自指保护判据：标题 → **身份优先 + 三重匹配** ═════════
# 为什么要升级（派单原话）：上面 BLOCKED_TITLES 自己在注释里写着「唯一的防自授权闸门」，
# 而它只认**一个标题字符串**。标题是最容易被改的一层 —— 改一个字、换一种语言、
# 换个弹窗形态，或者干脆**躲进终端里手打命令**，这道闸门就漏。
# 「agent 点掉自己的授权框给自己放行」正是这里要防的头号情形，所以判据必须落在
# **改不掉的东西**上：**属主进程身份**。
#
# 三层判据（check_target 里依次判，任一命中即拒）：
#   ① **属主进程名**（GetWindowThreadProcessId → pid → 进程名）—— 身份优先，本层为主；
#   ② **窗口类名**（GetClassNameW）—— 进程名读不到、或进程名变了时的第二道；
#   ③ **窗口标题** —— BLOCKED_TITLES 原样保留作**兜底**，一个字没删、没挪。
#
# ⚠️ 下面这些进程名与类名是**实跑测出来的**，不是照抄文档（原始输出见回执
#    work\_2h_wK\回执.md 与 work\_2h_wK\probe_*.py）：
#   · cmd.exe 开的新控制台：类名 ConsoleWindowClass。属主**大多数时候**是 cmd.exe，
#     但只要控制台里前台跑起别的程序，那张窗的属主就**变成那个程序** ——
#     实测：cmd.exe /k 里跑 ping，属主读出来是 PING.EXE。
#     ⇒ 进程层与类名层**必须都有**，少任何一层都会漏。这是本判据存在的第一理由。
#   · python.exe 自己建的窗，属主进程名读出来就是 python.exe
#     ⇒ 授权弹窗那一栏用进程名判（派单要求「别猜，去跑一遍确认」，跑出来的就是这个）。
#   · conhost.exe 名下实测到两张窗：UAC Input Indicator / UAC_InputIndicatorOverlayWnd
#     （UAC 提示的输入指示器）。**它们不是 ConsoleWindowClass** ⇒ 只认类名会漏，
#     这也是进程层必须保留 conhost.exe 的原因。
#   · Windows Terminal 本机**没装**（实测 System32\wt.exe 不存在）
#     ⇒ 那两行是预防性登记，本轮**没有实测数据**，不拿「实测」二字糊弄人。
#   · ⚠️ 授权弹窗的**类名**（tkinter 的 TkTopLevel）在本轮**没能实测** ——
#     这台机器上 Python 的 Tcl 组件装坏了（_tkinter.TclError: 找不到 init.tcl），
#     auth_popup 那个 tk 弹窗根本起不来。TkTopLevel 这条是**照本文件上方那段旧记述**
#     登记的（见「AgentCtl 急停」注释里关于窗口类名的那几行），不是本轮实测。

# ① 属主进程名 · **硬拒**（永远不给临时放行，理由见下面的 _deny_owner）
BLOCKED_PROCESSES_HARD = (
    # 实测：本机 auth_popup.py 由 core.py 用 python 子进程拉起，tk.Tk 建的窗属主就是这个
    # 解释器。把它列进来 = 「凡是 Python 进程自己拥有的窗口，一律不许被本系统触碰」。
    # ★ 这条网织得**故意粗**：用户自己写的 Python GUI 也会被一起拦下。这是选安全侧的取舍 ——
    #   粗网漏不掉自授权，窄网漏。收窄成「python.exe + TkTopLevel 类名」需要用户拍板。
    "python.exe",
    # ★ 冻结打包态的同一个解释器：core.py 的 _py_argv 走 [sys.executable, ...]，
    #   打包后 sys.executable 就是 AgentCtl.exe —— 授权弹窗与急停弹窗的属主
    #   在发行态会读成这个名字。本轮实测跑的是源码态（读出 python.exe），
    #   这一行是**照 core.py:450 推出来的**，不是实测，别当成实测数据。
    #   就算它错了也不致命：TkTopLevel / AgentCtlBrakePopup 两层类名仍在后面兜着。
    "agentctl.exe",
    "consent.exe",         # UAC 提权确认框：点它 = 直接把权限给了
    "SystemSettings.exe",  # 系统安全设置页：改的就是权限本身
)

# ① 属主进程名 · **终端**（双刃剑）：默认拒，但允许「人确认过的 30 分钟临时放行」
BLOCKED_PROCESSES_TERMINAL = (
    "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
    "wt.exe", "WindowsTerminal.exe", "OpenConsole.exe",
)

# ② 窗口类名 · 硬拒（在 BLOCKED_CLASSES 之外补的；TkTopLevel 即 tkinter 顶层窗）
BLOCKED_CLASSES_HARD_EXTRA = (
    "TkTopLevel",
)

# ② 窗口类名 · 终端（ConsoleWindowClass 与两张 UAC 指示器为实测；CASCADIA 为预防）
BLOCKED_CLASSES_TERMINAL = (
    "ConsoleWindowClass",             # 实测：cmd / powershell 的控制台窗
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal（本机未装，预防性登记）
    "UAC Input Indicator",            # 实测：conhost 名下
    "UAC_InputIndicatorOverlayWnd",   # 实测：conhost 名下
)

# 类名硬拒的合集（既有 BLOCKED_CLASSES + 本轮新增）。单独起名是为了
# **一个字都不动 BLOCKED_CLASSES 那个字面量** —— wI 判据第 ⑥ 条是按字面读它的。
BLOCKED_CLASSES_HARD_ALL = BLOCKED_CLASSES + BLOCKED_CLASSES_HARD_EXTRA

# 属主进程名统一小写后比较：Windows 上进程名**大小写随映像头而定**（实测同一台机器上
#   读出过 cmd.exe 与 PING.EXE 两种写法），按 == 精确比大小写等于给自己埋一个漏。
BLOCKED_PROCESSES_HARD_LC = tuple(p.lower() for p in BLOCKED_PROCESSES_HARD)
BLOCKED_PROCESSES_TERMINAL_LC = tuple(p.lower() for p in BLOCKED_PROCESSES_TERMINAL)

# ── 终端的 30 分钟临时放行（复用 code 7 那套「问一次、管 30 分钟」的机制）────────
# ★ 为什么终端不能一刀切永久封：终端是**双刃剑**。拦住它能防「躲进终端手打命令绕闸门」，
#   但用户有时**就是要**让助手在终端里跑一句。所以做成
#   **默认拒 + 需人当场确认的 30 分钟临时放行**，而且：
#     · 按 **(进程, 类名)** 记，不是永久白名单（进程重开即失效，类名不同也不认）；
#     · 时长**硬上限 30 分钟**（TERMINAL_PASS_MAX_MIN），给再大的参数也压回来；
#     · 签发与拒签都**写事件**（_auth/terminal_pass.jsonl，一行一条 JSON），事后查得到；
#     · 授权弹窗 / UAC / 系统设置那一族**根本没有这条通路**（approve 明确拒签）——
#       否则「agent 自己给自己开一张通行证」就等于把这道闸门整个拆了。
AUTHDIR = os.path.join(HERE, "_auth")
TERMINAL_PASS_MAX_MIN = 30
# 这两个路径给判据留了覆盖口（与 wI 判据用 AGENTCTL_TIMELINE 同一路子）：
# 判据把状态写到自己的临时目录，绝不碰真机的放行记录。
TERMINAL_PASS_FILE = os.environ.get("AGENTCTL_TERMINAL_PASS_FILE") or os.path.join(
    AUTHDIR, "terminal_pass.json")
TERMINAL_PASS_LOG = os.environ.get("AGENTCTL_TERMINAL_PASS_LOG") or (
    TERMINAL_PASS_FILE + ".jsonl")


def _pass_now(now=None):
    return float(time.time() if now is None else now)


def _hhmmss(t):
    return datetime.datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d %H:%M:%S")


def _pass_alive(p, t):
    try:
        return float(p.get("expires_at") or 0) > t
    except (TypeError, ValueError):
        return False


def _read_passes():
    """读放行记录。**读不出来一律当作「没有记录」**（fail-closed：状态不明即不认）。"""
    try:
        with open(TERMINAL_PASS_FILE, encoding="utf-8") as f:
            obj = json.load(f)
        ps = obj.get("passes") if isinstance(obj, dict) else None
        return ps if isinstance(ps, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        log("⚠️ 终端放行记录读不出来（%s: %s）—— 按**没有放行**处理（fail-closed）"
            % (type(e).__name__, e))
        return []


def _write_passes(passes):
    """原子写。与 auth_popup / gate 共用同一把跨进程锁与同一个 save_json
    （gate_lock 是本仓这三侧的**唯一定义处**，不许在这里复制一份锁逻辑过去）。"""
    import gate_lock
    with gate_lock.exclusive(os.path.dirname(TERMINAL_PASS_FILE) or AUTHDIR):
        gate_lock.save_json(TERMINAL_PASS_FILE, {"v": 1, "passes": passes})


def terminal_pass_ok(process, class_, now=None):
    """这个 (进程, 类名) 现在有没有**没过期的**临时放行。返回 (bool, 说明)。"""
    t = _pass_now(now)
    proc = (process or "").strip().lower()
    cls = (class_ or "").strip()
    if not proc:
        return False, "属主进程名读不到，没有可核对的放行身份"
    for p in _read_passes():
        if (p.get("process") or "").strip().lower() != proc:
            continue
        if (p.get("class") or "").strip() != cls:
            continue
        try:
            exp = float(p.get("expires_at") or 0)
        except (TypeError, ValueError):
            continue
        if exp > t:
            left = int(round((exp - t) / 60.0))
            return True, ("命中用户确认过的临时放行：%s/%s，还剩约 %d 分钟（%s 到期）"
                          % (proc, cls or "(空)", left, p.get("expires_at_h") or "?"))
    return False, ""


def _pass_event(kind, process, class_, **extra):
    """写一条事件。**放行本身必须留痕** —— 没有事件，
    「谁在什么时候让助手碰了终端」就成了一件查不出来的事。"""
    try:
        import getpass
        user = getpass.getuser()
    except Exception:
        user = "?"
    rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
           "event": kind, "process": process, "class": class_,
           "user": user, "pid": os.getpid()}
    rec.update(extra)
    try:
        d = os.path.dirname(TERMINAL_PASS_LOG)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(TERMINAL_PASS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log("⚠️ 事件写不下去（%s: %s）—— 本次放行**照常生效**，但事后查不到，"
            "这一条要人工补记" % (type(e).__name__, e))
    return rec


def grant_terminal_pass(process, class_, minutes=TERMINAL_PASS_MAX_MIN, now=None):
    """给 (进程, 类名) 记一条临时放行。返回 (成功?, 说明)。

    ★ 只对**终端族**开放。授权弹窗 / UAC / 系统设置那一族一律拒签 ——
      理由写在上面那段注释里：给它们开这张证，等于让 agent 自己给自己发通行证，
      这道闸门就没有意义了。
    """
    proc = (process or "").strip()
    cls = (class_ or "").strip()
    pl = proc.lower()
    is_term = pl in BLOCKED_PROCESSES_TERMINAL_LC or cls in BLOCKED_CLASSES_TERMINAL
    if not is_term:
        why = ("%s/%s 不属于终端族 —— 临时放行**只对终端开放**。"
               "授权弹窗、UAC、系统设置这些窗永远不许被本系统碰，"
               "这里是最后一道不签字的地方。" % (proc, cls))
        _pass_event("terminal_pass_refused", proc, cls, reason=why)
        return False, why
    mins = max(1, min(int(minutes or TERMINAL_PASS_MAX_MIN), TERMINAL_PASS_MAX_MIN))
    t = _pass_now(now)
    exp = t + mins * 60
    rec = {"process": pl, "class": cls,
           "granted_at": _hhmmss(t), "expires_at_h": _hhmmss(exp),
           "expires_at": exp, "minutes": mins}
    passes = [p for p in _read_passes() if _pass_alive(p, t)]
    passes = [p for p in passes
              if not (p.get("process") == pl and (p.get("class") or "") == cls)]
    passes.append(rec)
    _write_passes(passes)
    _pass_event("terminal_pass_granted", pl, cls, minutes=mins,
                expires_at_h=rec["expires_at_h"],
                reason="用户当场确认（code 7 弹窗）")
    log("🔓 终端临时放行 %d 分钟：%s / %s —— 只在这一对身份上、到点自动失效，不是白名单"
        % (mins, pl, cls))
    return True, "已记 %d 分钟临时放行：%s / %s（到 %s 自动失效）" % (
        mins, pl, cls, rec["expires_at_h"])


# ── 拒绝的两类出口：rc 决定「硬拒」还是「要问用户」────────────────────────
# 硬拒（授权弹窗 / 急停窗 / UAC / 系统设置）→ rc=3，与改动前一致。
# 终端默认拒 → **rc=7**：复用 code 7 那条「这个窗口用户还没点头，去问一声」的通路
#   （core.py 的 _map_rc 把 rc=7 映成 CODE_NEEDS_ASK，随后弹窗问用户本人；
#    用户同意 → act.py approve → 记 30 分钟放行 → 原样重试一次）。
#   为什么不给它 3：3 在契约里的意思是「白名单/敏感窗口拒绝」，是**死路一条**；
#   终端不是死路 —— 用户点个头就能过。给 3 等于把一条正常的人机协作通路堵死。
#
# ⚠️ 一处**已知的老撞车**（本轮没资格改，如实记下）：act.py 的 rc=7 早被
#   「文档非空防污染护栏」（do_type，act.py 里那处 _tell_next(7, ...)）占着，
#   而 core.py 把 rc=7 一律映成 CODE_NEEDS_ASK ⇒ 打字的防污染护栏会**误触发**
#   一次授权弹窗。这是改动前就有的老账，不在本单范围内（不许改闸门判定逻辑）。
#   本单因此给「问用户」这件事加了标记前缀（_TERMINAL_ASK_MARK）而不是只看 rc，
#   至少本单这一路的**提示语**是对的、不会打出防污染护栏那段驴唇不对马嘴的建议。
TERMINAL_ASK_RC = 7
_TERMINAL_ASK_MARK = "〔终端·待人工确认〕"


def _is_terminal_ask(why):
    return bool(why) and str(why).startswith(_TERMINAL_ASK_MARK)


def _tell_deny(why, hwnd=None, extra=None):
    """check_target 的拒绝统一出口：终端类走 rc=7，其余走 rc=3。"""
    if _is_terminal_ask(why):
        return _tell_next(TERMINAL_ASK_RC, why, hwnd, extra=extra, tip=lambda h: (
            "目标是**终端窗口**，默认禁 —— 终端能绕开一切闸门，所以不许碰。\n"
            "  ★ 但它不是死路：平台会弹窗问用户本人，要不要让助手在这个终端里跑一句；\n"
            "    用户当场同意 → 按 (进程, 类名) 记 **30 分钟**临时放行"
            "（写事件、到点自动失效）→ 这次任务里就能正常操作。\n"
            "  ⛔ 别绕道：换别的手段去动那个窗口，等于把闸门当没设。\n"
            "  先确认自己打的是哪个窗口：python -X utf8 win_list.py <关键词>"))
    return _tell_next(3, why, hwnd, extra=extra)


def _owner_lc(info):
    return (info.get("process") or "").strip().lower()


def _deny_owner(info):
    """① 属主进程身份这一层。返回拒绝原因（None = 本层没拦）。"""
    p = _owner_lc(info)
    if not p or p == "?":
        # 读不到属主（提权窗口 / psutil 读不到）**不许当成命中** —— 那会把所有提权
        # 窗口一起打死，代价远大于收益。但也**不许当成安全**：这一层判空这件事
        # 得让人看得见，所以由调用方把它打进日志，而不是假装判过了。
        return None
    if p in BLOCKED_PROCESSES_HARD_LC:
        if (info.get("class") or "") == "AgentCtlBrakePopup":
            return ("急停置顶告警窗（谁在动 / 怎么恢复），任何动作一律禁止"
                    "〔属主进程 %s〕" % info.get("process"))
        return ("要操作窗口属主进程是 **%s** —— 本机的授权弹窗、UAC 提权框、"
                "系统安全设置页都长这样（实测 auth_popup 的属主就是 python.exe）。"
                "这套命令存在的意义就是**不许助手碰这一族窗口**：碰了它，"
                "agent 就能自己点掉授权框、自己给自己放行。⛔ 不要绕道。"
                % info.get("process"))
    if p in BLOCKED_PROCESSES_TERMINAL_LC:
        return (_TERMINAL_ASK_MARK +
                "要操作窗口是**终端**（属主进程 %s / 窗口类名 %s）—— 终端默认禁："
                "它是双刃剑，躲进去就能手打命令绕开一切闸门。"
                "但用户有时就是要让助手在终端里跑一句，所以这里**不写死**："
                "交回上层弹窗问用户本人，当场同意后按 (进程, 类名) 记一条 30 分钟"
                "临时放行（写事件、到点自动失效），之后这一路才放行。"
                "⛔ 不要绕道用别的手段去动那个窗口。"
                % (info.get("process"), info.get("class")))
    return None


def _deny_class(info):
    """② 窗口类名这一层（进程层之外的第二道）。返回拒绝原因或 None。"""
    cls = (info.get("class") or "").strip()
    if not cls:
        return None
    if cls in BLOCKED_CLASSES_HARD_ALL:
        if cls == "AgentCtlBrakePopup":
            return "这是急停置顶告警窗（谁在动 / 怎么恢复），任何动作一律禁止"
        return ("要操作窗口的类名是 **%s**（tkinter 顶层窗）—— 授权弹窗就属于这一族，"
                "任何动作一律禁止。⛔ 不要绕道。" % cls)
    if cls in BLOCKED_CLASSES_TERMINAL:
        return (_TERMINAL_ASK_MARK +
                "要操作窗口的类名是 **%s**（终端窗）—— 终端默认禁：躲进去就能手打命令"
                "绕开一切闸门。属主进程这次读出来是 %s，但**类名这一层不依赖进程名**："
                "实测 cmd.exe 里前台跑起别的程序后，那张控制台窗的属主就会变成那个"
                "程序（实测读到 PING.EXE），只认进程名会漏。用户当场确认后可按"
                "(进程, 类名) 记 30 分钟临时放行。⛔ 不要绕道。"
                % (cls, info.get("process")))
    return None

user32 = ctypes.windll.user32

# ── PostMessage 后台投递（2026-10-08 新增，工单 T4）──────────────────────
# 目的：给 B 档的**坐标点击**多一条"不抢前台焦点"的通路 —— 把一次左键的
#       WM_MOUSEMOVE / WM_LBUTTONDOWN / WM_LBUTTONUP 用 PostMessage 直接投给
#       目标窗口，既不写系统输入队列，也不调 SetForegroundWindow。
#
# ★ 为什么默认关闭、为什么必须先查兼容表（这决定了整条设计的形状）：
#   WinUI3 / XAML / UWP 打包应用的输入走 CoreInput dispatcher，**只消费系统输入
#   队列**；raw PostMessage(WM_LBUTTONDOWN/UP) 会被直接丢弃。若不查表就投，
#   会出现"动作报成功、界面纹丝不动"的**假成功** —— 那比拒绝更危险。
#   所以：兼容表未登记 或 登记为"不消费" → 一律**回退**原有 SendInput 前台路线，
#   并输出一行「↩ 已回退：<原因>」（工单 T4 要求 3：回退不许静默）。
#
#   兼容表 = 本目录 post_compat.json（按 process + class 登记，实测得出）。
#   读不到表 = 谁都不认识 = 全部回退（fail-closed，落在安全侧）。
#
#   ⚠ UIA 支持 Invoke / SelectionItem 的控件**仍然优先走 UIA** —— 那条路连鼠标
#     事件都不产生，比投递更干净。--post 只改变"本来就得点坐标"的那一次。
POST_COMPAT_PATH = os.path.join(HERE, "post_compat.json")

WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0200, 0x0201, 0x0202, 0x0001

# WindowFromPoint / ScreenToClient / PostMessageW 收发的都是**指针宽度**的量，
# ctypes 默认按 c_int 处理会截断 —— 必须显式声明原型（32 位句柄侥幸能跑，
# 64 位句柄会静默投给错误窗口）。
user32.WindowFromPoint.restype = wt.HWND
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]


def load_post_compat():
    """读兼容表 → {(process_lower, class): 条目}。读不到/解析失败 → {}（= 全部回退）。"""
    import json as _json
    try:
        with open(POST_COMPAT_PATH, encoding="utf-8") as f:
            d = _json.load(f)
    except Exception:
        return {}
    out = {}
    for e in (d.get("compat") or []):
        if not isinstance(e, dict):
            continue
        p = str(e.get("process") or "").strip().lower()
        if not p:
            continue
        out[(p, str(e.get("class") or "").strip())] = e
    return out


POST_COMPAT = load_post_compat()


def post_compat_of(info):
    """查兼容表 → (状态, 条目)，状态 ∈ {'consumes','refuses','unverified','unknown'}。

    四态是刻意的：**"实测不消费" 与 "还没测过" 是两件事**，不能都用 false 表示 ——
    否则一张尚未预扫的表会被读成"全都实测过且都不消费"，那就是假证据。
    post=true→consumes；post=false→refuses；post=null/缺失→unverified；无此条目→unknown。

    两级匹配：先精确 (进程, 类名)；未命中再看 (进程, '') —— class 留空的登记条目
    表示"该进程只按进程名匹配"，与 whitelist.json 是同一套口径。
    """
    pl = (info.get("process") or "").lower()
    e = POST_COMPAT.get((pl, info.get("class") or ""))
    if e is None:
        e = POST_COMPAT.get((pl, ""))
    if e is None:
        return "unknown", None
    st = e.get("post")
    if st is True:
        return "consumes", e
    if st is False:
        return "refuses", e
    return "unverified", e


def resolve_post(info, post_force=False):
    """决定这次坐标点击走"后台投递"还是"回退前台"。返回 (用投递吗, 说明)。"""
    if post_force:
        return True, "强制后台投递（预扫模式，绕过兼容表）"
    st, e = post_compat_of(info)
    why = (e or {}).get("why") or "无说明"
    who = "%s/%s" % (info.get("process"), info.get("class"))
    if st == "consumes":
        return True, f"兼容表：{who} 会消费 PostMessage（{why}）"
    if st == "refuses":
        return False, f"兼容表判定 {who} **不消费** PostMessage（{why}）"
    if st == "unverified":
        return False, f"{who} 在表里但**尚未实测**（{why}）"
    return False, f"兼容表未登记 {who}"


# ── 阶段耗时插桩（2026-10-19 · 默认关闭，AGENTCTL_PROFILE=1 才打）──────────────
_PF_ON = os.environ.get("AGENTCTL_PROFILE") == "1"
_PF_LAST = [0.0]


def _pf(tag):
    """打一条阶段耗时（毫秒），并重置计时点。默认零开销、零输出。"""
    if not _PF_ON:
        return
    now = time.perf_counter()
    prev = _PF_LAST[0] or now
    _PF_LAST[0] = now
    _line = "[prof] %-14s %6.0f ms" % (tag, (now - prev) * 1000)
    _path = (os.environ.get("AGENTCTL_PROFILE_FILE")
             or os.path.join(tempfile.gettempdir(), "agentctl_prof.log"))
    try:
        with open(_path, "a", encoding="utf-8") as _f:
            _f.write(_line + "\n")
    except Exception:
        print(_line, file=sys.stderr, flush=True)

def _settle(default_ms):
    """动作后的稳定等待（秒）。★ 2026-10-19：由固定值改成可调，默认不变。

    为什么：实测一步打字 1150ms，其中固定 sleep 占 0.4~0.5s；
    把等待做成旋钮后，低延迟场景（连点/批量）可以调到几十毫秒，
    而**默认值保持原样**——不改变任何人已有的行为。
    """
    try:
        v = os.environ.get("AGENTCTL_SETTLE_MS")
        if v is not None and str(v).strip() != "":
            return max(0.0, float(v) / 1000.0)
    except Exception:
        pass
    return default_ms / 1000.0


# ══════════════ M7 · 就绪轮询（替代固定 sleep）══════════════
# ★ 派单 M7：把「固定 sleep 链」换成「**轮询指纹，变了就停 / 稳了就走**」。
#   ⛔ **不许把等待砍成 0**（TOCTOU 文献 arXiv:2604.18860 实测「观测→动作」间隙
#     平均 6.51 秒，那正是「点了但界面已变」的成因）。这里是**换机制**：
#     固定等 450 ms → 轮询到「界面给出信号」为止，最长等 budget_ms。
#
# ★ 判据口径与 agentctl 侧**同一组常量**（全仓一份定义，两单不许打架）：
#     agentctl/framecache.py:  DIFF_STEP=2 / DIFF_CARET_PX=8 / DIFF_MIN_AREA=24
#                              DIFF_CARET_ASPECT=2.0 / HYSTERESIS=2
#   （那边是**全分辨率**判据；本文件在**1/4 降采样**上做等价判定 ⇒ 阈值按比例缩放，
#     见 `POLL_SCALE` 与 `_img_diff` 的注释。判据 `tests/_P3_就绪轮询_判据.py`
#     会**核对两边常量一致**，防止漂移。）
POLL_STEP = 2               # = framecache.DIFF_STEP
POLL_CARET_PX = 8           # = framecache.DIFF_CARET_PX（**降采样后**除以 POLL_SCALE）
POLL_MIN_AREA = 24          # = framecache.DIFF_MIN_AREA
POLL_CARET_ASPECT = 2.0     # = framecache.DIFF_CARET_ASPECT
POLL_HYSTERESIS = 2         # = framecache.HYSTERESIS（连续 N 帧同一结论才认）
POLL_SCALE = 4              # 轮询用的降采样倍数（越大越快、越钝）
POLL_INTERVAL_MS = 50       # 采样间隔（派单 §4.3 建议 16~30；实测 50 更省 CPU，
                            #  同一预算里少采几帧 ⇒ 少几百 ms 的 PrintWindow 开销）
POLL_BUDGET_MS = 800        # 点击的总预算（超时 ⇒ 没测出来）
POLL_BUDGET_MS_FILL = 1500  # 填字段的总预算


#: 轮询采样口径（"" = 整窗 PrintWindow；"client" = 客户区 PrintWindow flag=3）。
#: ★ M12 阶段三（B）：**基线由 core 下发**（`AGENTCTL_BASELINE` + `AGENTCTL_BASELINE_META`），
#:   core 的基线是**客户区**帧 ⇒ 采样口径必须跟着它走，否则两帧不可比。
_POLL_SCOPE = ""


def _baseline_from_env():
    """取 core 下发的**基线**（M12 阶段三 B）⇒ `(图, 口径)`。

    ★ 为什么基线要由 core 下发：以前是 act 在**注入之后**现抓一帧当基线
      （`_poll_base(hwnd)` 是实参 ⇒ 求值在注入之后）⇒ 目标重绘快于一次
      PrintWindow 时，**基线里已经含了变化 ⇒ 首变被吞** ⇒ 只能等满预算。
    ★ 口径（w/h/origin/dpi_scale/来源）随帧一起给 ⇒ core 侧能核对
      （不一致就**回落 wait_change**，不"现取 origin"补救）。
    ★ 取不到就返回 `(None, {})` ⇒ 调用方回落「现抓一帧」（老路，**并在回执里标明**）。
    """
    meta = {}
    try:
        meta = json.loads(os.environ.get("AGENTCTL_BASELINE_META") or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    p = os.environ.get("AGENTCTL_BASELINE") or ""
    if not p or not os.path.isfile(p):
        return None, meta
    try:
        from PIL import Image
        with Image.open(p) as im:
            im.load()
            return im.copy(), meta
    except Exception:
        return None, meta


def _too_small(img):
    """图够不够大 —— `_poll_gray` 的**唯一**用途。

    ★ M12 阶段三（D）：`_poll_gray` 每轮跑一次（实测 **6.3ms/轮**）只为判这个，
      而 `img.size` 就够 ⇒ 降采样那一步（convert+resize）纯属纯税。
    """
    try:
        w, h = img.size
        return bool(w < POLL_SCALE * 4 or h < POLL_SCALE * 4)
    except Exception:
        return True


def _frame_scope(img):
    """这一帧的**尺寸口径**（`w`/`h`）—— 随帧回传，core 侧据此核对可比性。

    ★ M12 阶段三（B）：口径必须**随帧回传** ⇒ core 侧发现对不上就
      **回落 `wait_change`（fail-closed）**，不许"现取 origin"补救（窗口会动）。
    """
    try:
        w, h = img.size
        return {"w": int(w), "h": int(h)}
    except Exception:
        return {}


def _is_degraded(img):
    """退化打标（**只打标**，裁决权在 core）。

    ★ M12 阶段三（D）：改前每轮都跑 `framecache.is_blank`（实测 **5.7ms/轮**），
      但退化只是打标 ⇒ **每轮打一次和最后一帧打一次结论完全一样**
      ⇒ 只在**要返回的那一帧**上判一次。
    """
    if _fc is None or img is None:
        return False
    try:
        return bool(_fc.is_blank(img))
    except Exception:
        return False


def _poll_base(hwnd):
    """取「动作前」那一帧（内存内）—— 轮询的基线。

    ★ 不落盘：轮询基线只是一张小图，不需要变成证据文件。
    ★ M12 阶段三（B）：**优先用 core 下发的基线**（在注入之前就拍好了），
      没有才现抓（老路，回执里标明来源）。
    """
    img, meta = _baseline_from_env()
    if img is not None:
        return img
    return _poll_sample(hwnd)


def _poll_sample(hwnd):
    """内存内取一帧客户/整窗画面（**不落盘**）。

    ★ 为什么不用 `evidence()`：它每次都**写一张带时间戳的 PNG**，
      轮询每 30 ms 走一次 ⇒ 每 30 ms 落一张全窗 PNG ⇒ 磁盘 IO 比省下的 450 ms 更亏。
    ★ 用的还是 perceive._print_window（PrintWindow + PW_RENDERFULLCONTENT=2）——
      和证据图**同一个来源** ⇒ 指纹与前后证据图同口径。
    ★ 拿不到就返回 None（调用方按「没测出来」处理，**不假装没变**）。
    """
    try:
        if _POLL_SCOPE == "client":
            # ★ M12 阶段三（B）：基线是 core 下发的**客户区**帧 ⇒ 采样也必须是客户区，
            #   否则两帧尺寸不同 ⇒ 判差必然 None（"没测出来"）。
            #   用 `digest._capture_client_rgb`（framecache 的**同一个来源**），
            #   且**不**用 `framecache.capture_client`（它把退化帧丢成 None，
            #   那样就分不出「退化」与「抓不到」了）。
            try:
                from agentctl import digest as _dg
                img = _dg._capture_client_rgb(int(hwnd))
            except Exception:
                return None
            return img if img is not None else None
        import perceive
        img, ok = perceive._print_window(int(hwnd))
        if img is None or not ok:
            return None
        return img
    except Exception:
        return None


def _poll_gray(img):
    """降采样成灰度小图（1/POLL_SCALE），轮询用。

    ★ 为什么要降采样：全分辨率逐行分块是**纯 Python 循环**（2560 行 ⇒ 几十 ms），
      轮询扛不住。降 4 倍 ⇒ 640 行 ⇒ 4 倍快。
    ★ 代价：细节变钝 ⇒ 阈值按比例缩放（见 `_img_diff`）。
    """
    try:
        from PIL import Image
        s = POLL_SCALE
        w, h = img.size
        if w < s * 4 or h < s * 4:
            return None
        g = img.convert("L").resize((max(1, w // s), max(1, h // s)), Image.BOX)
        return g
    except Exception:
        return None


def _img_diff(a, b):
    """两幅图求差 ⇒ {changed: True/False/None, ratio}。

    ★ **M12 阶段二（2026-10-22）：本函数已不再自带实现** —— 口径统一到
      `agentctl.framecache.diff_images`（阈值 2/255、caret 窄边 8px/长宽比 2.0、最小面积 24px²）。
      改前这里是一份**缩放过的拷贝**（阈值按 1/POLL_SCALE 折算）⇒ 两份拷贝会漂移
      （改了 framecache 忘了改这里就是 bug）⇒ 本单把双实现拆掉。
    ⚠ 传入的必须是**同口径**的图（调用点已改成传全尺寸帧）。
    ⚠ **None = 没测出来**（图拿不到 / 尺寸对不上 / framecache 不可用）—— 永不当 False。
    """
    # ★ M12 阶段二：判差**转发**给 framecache（唯一真源）。
    if _fc is None or a is None or b is None:
        return {"changed": None, "ratio": 0.0}
    try:
        # ★ 参数名**照抄 framecache 的真签名**：diff_images(before, after, caret_px, min_area_px, step)
        #   （caret 的长宽比在 framecache.is_caret_like 内部，**不是** diff_images 的参数）
        d = _fc.diff_images(a, b, caret_px=_fc.DIFF_CARET_PX,
                            min_area_px=_fc.DIFF_MIN_AREA)
        return {"changed": d.get("changed"), "ratio": d.get("ratio")}
    except Exception:
        return {"changed": None, "ratio": 0.0}
    try:
        import numpy as np
    except Exception:
        return {"changed": None, "ratio": 0.0}
    if a is None or b is None:
        return {"changed": None, "ratio": 0.0}
    try:
        sa, sb = a.size, b.size
    except Exception:
        return {"changed": None, "ratio": 0.0}
    if sa != sb:
        return {"changed": None, "ratio": 0.0}
    try:
        A = np.asarray(a, dtype=np.int16)
        B = np.asarray(b, dtype=np.int16)
    except Exception:
        return {"changed": None, "ratio": 0.0}
    if A.shape != B.shape:
        return {"changed": None, "ratio": 0.0}
    try:
        mask = np.abs(A - B) >= POLL_STEP
        H, W = mask.shape
        runs = []
        for y in range(H):
            xs = np.flatnonzero(mask[y])
            if len(xs) == 0:
                continue
            start = prev = int(xs[0])
            for x in xs[1:]:
                x = int(x)
                if x != prev + 1:
                    runs.append([y, start, prev])
                    start = x
                prev = x
            runs.append([y, start, prev])
        if not runs:
            return {"changed": False, "ratio": 0.0}
        parent = list(range(len(runs)))

        def _find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def _union(i, j):
            ri, rj = _find(i), _find(j)
            if ri != rj:
                parent[rj] = ri

        idx = 0
        n = len(runs)
        while idx < n:
            y = runs[idx][0]
            j = idx
            while j < n and runs[j][0] == y:
                j += 1
            k = j
            while k < n and runs[k][0] == y + 1:
                k += 1
            for ai in range(idx, j):
                for bi in range(j, k):
                    if runs[ai][1] <= runs[bi][2] and runs[bi][1] <= runs[ai][2]:
                        _union(ai, bi)
            idx = j
        agg = {}
        for i, (y, x0, x1) in enumerate(runs):
            r = _find(i)
            e = agg.get(r)
            if e is None:
                agg[r] = {"area": x1 - x0 + 1, "x0": x0, "x1": x1, "y0": y, "y1": y}
            else:
                e["area"] += x1 - x0 + 1
                e["x0"] = min(e["x0"], x0)
                e["x1"] = max(e["x1"], x1)
                e["y1"] = max(e["y1"], y)
        narrow_max = max(1, POLL_CARET_PX // POLL_SCALE)
        min_area = max(1, int(round(POLL_MIN_AREA / float(POLL_SCALE * POLL_SCALE))))
        live = 0
        for v in agg.values():
            w = v["x1"] - v["x0"] + 1
            h = v["y1"] - v["y0"] + 1
            narrow = min(w, h)
            if narrow <= narrow_max and (max(w, h) / float(narrow)) >= POLL_CARET_ASPECT:
                continue                      # 光标类 → 丢
            live += v["area"]
        if live <= 0:
            return {"changed": False, "ratio": 0.0}
        if live < min_area:
            return {"changed": False, "ratio": 0.0}
        return {"changed": True, "ratio": float(live) / float(mask.size)}
    except Exception:
        return {"changed": None, "ratio": 0.0}
def _wait_ready(hwnd, base_img, budget_ms=POLL_BUDGET_MS, need=POLL_HYSTERESIS,
                capture=None):
    """**就绪轮询**：连续 `need` 帧都判「没变」⇒ 界面稳了，可以动手。

    ★ 这取代了原来的 `time.sleep(_settle(250))`（反竞态前的那一刀）：
      固定等 250 ms 是「不管稳没稳都等」；这里是「**稳了就走**」。
    ★ 返回 {就绪: True/False/None, 轮次, 耗时ms}
      None = 没测出来（抓不到画面 / 预算内没稳）—— **不是「没变」**。
    """
    import time as _t
    t0 = _t.perf_counter()
    grab = capture or _poll_sample
    # ★ M12 阶段三（D）：`_poll_gray` 每轮 6.3ms 只为判「图够不够大」⇒ 改用 `img.size`。
    if base_img is None or _too_small(base_img):
        return {"就绪": None, "轮次": 0, "耗时ms": 0.0}
    streak = 0
    rounds = 0
    deadline = t0 + max(0.0, float(budget_ms) / 1000.0)
    while True:
        rounds += 1
        # ★ 预算检查在**采样之前**：采样本身要 20 ms+，放在检查之后会让总耗时**超出**预算
        #   （实测：轮询比老的固定 sleep 还多花 ~100 ms）。先查预算 ⇒ **永远不慢于改前**。
        if _t.perf_counter() >= deadline:
            return {"就绪": False, "轮次": rounds,
                    "耗时ms": (_t.perf_counter() - t0) * 1000.0}
        cur = grab(hwnd)
        # ★ M12 阶段二：判差用**全尺寸帧 + framecache 的尺子**（口径与 core 完全一致）。
        #   `_poll_gray` 只当「图够不够大」的守卫，不再参与判差（它那份缩放阈值已删）。
        # ★ M12 阶段三（D）：那个守卫也换成 `img.size`（省 6.3ms/轮）。
        if cur is None or _too_small(cur):
            return {"就绪": None, "轮次": rounds,
                    "耗时ms": (_t.perf_counter() - t0) * 1000.0}
        d = _img_diff(base_img, cur)
        if d.get("changed") is False:
            streak += 1
        else:
            streak = 0
        if streak >= max(1, int(need)):
            return {"就绪": True, "轮次": rounds,
                    "耗时ms": (_t.perf_counter() - t0) * 1000.0}
        if _t.perf_counter() >= deadline:
            return {"就绪": False, "轮次": rounds,
                    "耗时ms": (_t.perf_counter() - t0) * 1000.0}
        _t.sleep(POLL_INTERVAL_MS / 1000.0)


def _wait_effect(hwnd, base_img, budget_ms=POLL_BUDGET_MS, need=POLL_HYSTERESIS,
                 capture=None):
    """**变化即停**：连续 `need` 帧都判「变了」⇒ 动作生效了，可以收工。

    ★ 这取代了原来的 `time.sleep(_settle(450))`（动作后等界面反应）。
    ★ 返回 {生效: True/False/None, 轮次, 耗时ms, 图}
      None = 没测出来；False = 预算内**没看到**变化（= 老的「页面无变化」口径）。
    ★ **不落盘**：轮询期只在内存里采样；最后那张由调用方决定要不要落盘。
    """
    import time as _t
    t0 = _t.perf_counter()
    grab = capture or _poll_sample
    # ★ M12 阶段三（D）：`_poll_gray` 只为判「图够不够大」⇒ 改用 `img.size`（省 6.3ms/轮）。
    if base_img is None or _too_small(base_img):
        return {"生效": None, "轮次": 0, "耗时ms": 0.0, "图": None,
                "退化": False, "首变图": None, "口径": _frame_scope(base_img)}
    streak = 0
    rounds = 0
    last = None
    first_img = None       # ★ M12：第一次「变化」那一帧（回传给 core 当证据）
    deadline = t0 + max(0.0, float(budget_ms) / 1000.0)
    while True:
        rounds += 1
        # ★ 同上：预算检查在采样之前 ⇒ 总耗时 ≤ 预算（**永远不慢于改前的固定 sleep**）
        if _t.perf_counter() >= deadline:
            return {"生效": False, "轮次": rounds,
                    "耗时ms": (_t.perf_counter() - t0) * 1000.0, "图": last,
                    "退化": _is_degraded(last), "首变图": first_img,
                    "口径": _frame_scope(last or base_img)}
        cur = grab(hwnd)
        if cur is None or _too_small(cur):
            return {"生效": None, "轮次": rounds,
                    "耗时ms": (_t.perf_counter() - t0) * 1000.0, "图": last,
                    "退化": _is_degraded(last), "首变图": first_img,
                    "口径": _frame_scope(last or base_img)}
        last = cur
        d = _img_diff(base_img, cur)
        if d.get("changed") is True:
            streak += 1
            if first_img is None:
                first_img = cur
        else:
            streak = 0
        if streak >= max(1, int(need)):
            return {"生效": True, "轮次": rounds,
                    "耗时ms": (_t.perf_counter() - t0) * 1000.0, "图": last,
                    "退化": _is_degraded(cur), "首变图": first_img,
                    "口径": _frame_scope(cur)}
        if _t.perf_counter() >= deadline:
            return {"生效": False, "轮次": rounds,
                    "耗时ms": (_t.perf_counter() - t0) * 1000.0, "图": last,
                    "退化": _is_degraded(last), "首变图": first_img,
                    "口径": _frame_scope(last or base_img)}
        _t.sleep(POLL_INTERVAL_MS / 1000.0)


def _emit_effect(eff, hwnd):
    """★ M12 阶段二：把「证据」回传给 core —— **只传帧与原始量，不传结论**。

    · 帧：第一次「变化」那一帧，存到 `AGENTCTL_SNAP_DIR/first.png`
      （目录由 core 在调本进程**之前**建好 ⇒ 帧与本次动作**一一对应**，
        杜绝「上一轮的帧被当本轮」）。
    · 退化：每轮 `framecache.is_blank()` 的结果（**只打标**；裁决在 core）。
    · 通道：stdout 多打一行 JSON（沿用本文件既有 `print(json.dumps(...))` 约定）⇒ **不新造通道**。
    ⚠ 解析失败/没有帧 ⇒ core 一律**回落** wait_change（绝不当作「没变」）。
    """
    try:
        _base_img, _base_meta = _baseline_from_env()
        payload = {"effect": {"rounds": int((eff or {}).get("轮次") or 0),
                              "first_change_ms": round(float((eff or {}).get("耗时ms") or 0.0), 1),
                              "degraded": bool((eff or {}).get("退化")),
                              "frame_ref": None,
                              # ★ M12 阶段三（B）：**口径随帧回传** ——
                              #   core 侧拿它跟自己的基线核对；对不上就**回落 wait_change**
                              #   （fail-closed），**不许**"现取 origin"补救（窗口会动）。
                              "口径": dict((eff or {}).get("口径") or {}),
                              "origin": list(_base_meta.get("origin") or []) or None,
                              "dpi_scale": _base_meta.get("dpi_scale"),
                              "来源": str(_base_meta.get("来源") or ""),
                              "基线": str(_base_meta.get("基线来源")
                                          or ("core下发" if _base_img is not None else "现抓(旧路)")),
                              "判据版本": str(_base_meta.get("判据版本") or "")}}
        # ★ M12 阶段二：**总有���帧可回传** ——
        #   优先「第一次变化的那一帧」；没有（画面没变）就回传**最后采到的那一帧**。
        #   ⇒ core 拿到帧后用**自己的尺子**判：变了⇒成功；没变⇒code=8
        #     ⇒ 「画面没变」这一路也**不用再等第二遍**（那 300ms 才是真浪费）。
        img = (eff or {}).get("首变图") or (eff or {}).get("图")
        d = os.environ.get("AGENTCTL_SNAP_DIR") or ""
        if img is not None and d:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "first.bmp")
            try:
                # ★ M12 阶段三（C④）：**内部交接**改 BMP（PNG save 29.9ms/张 → BMP 5.3ms/张）。
                #   这张是**内部**帧（给 core 判差用）；**证据图另存 PNG**（给人看）。
                img.save(p, format="BMP")
                payload["effect"]["frame_ref"] = p
            except Exception:
                payload["effect"]["frame_ref"] = None
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception as e:
        try:
            print(json.dumps({"effect": {"frame_ref": None, "degraded": None,
                                        "错误": "%s: %s" % (type(e).__name__, e)}},
                             ensure_ascii=False), flush=True)
        except Exception:
            pass


def log(msg):
    os.makedirs(os.path.dirname(ACTLOG), exist_ok=True)
    with open(ACTLOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    print(msg, flush=True)


def win_info(hwnd):
    title = win32gui.GetWindowText(hwnd) or ""
    cls = win32gui.GetClassName(hwnd) or ""
    try:
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        import psutil
        proc = psutil.Process(pid).name()
    except Exception:
        proc = "?"
    rect = win32gui.GetWindowRect(hwnd)
    return {"hwnd": hwnd, "title": title, "class": cls, "process": proc,
            "rect": list(rect), "foreground": win32gui.GetForegroundWindow() == hwnd}


# ─────────────── 模态对话框前置检查（2026-09-18 接管后新增） ───────────────
# 为什么必须加：模态对话框会**吞掉窗体外部的点击**。目标窗口看着正常、UIA 也读得到控件，
# 但坐标点击会落到对话框上或干脆无效 —— 表现为"动作做对了却不生效"，考核就此卡死（前任踩过）。
# 判据（任一成立即视为被挡）：
#   ① 目标进程有可见的对话框类窗口（#32770 / TaskDialog / 常见 shell 对话框类）
#   ② 该对话框的属主链指回目标窗口
#   ③ 目标窗口自身 IsWindowEnabled=False（被模态禁用的典型状态）
DIALOG_CLASSES = (
    "#32770",                 # Win32 标准对话框（MessageBox / 文件对话框）
    "TaskDialog",             # 任务对话框
    "OperationStatusWindow",  # 常见：操作进度/确认
    "DirectUIHWND",           # 部分 shell 对话框（仅在与目标同进程时才算）
)


def _owner_chain(hwnd, target, depth=4):
    """沿属主链向上，判断该窗口是否最终属于 target。HWND 不透明：只做相等/层级比较，不打印。"""
    cur = hwnd
    for _ in range(depth):
        try:
            cur = user32.GetWindow(cur, 4)     # GW_OWNER = 4
        except Exception:
            return False
        if not cur:
            return False
        if cur == target:
            return True
    return False


def _ancestor_chain(hwnd, depth=64):
    """从 hwnd 沿【父/属主链】向上，返回沿途窗口（含自身）。

    ★2026-10-12 新增。原 modal_blocker 只按「同进程有可见对话框」判，于是：
      ① 目标是对话框【本身】时，枚举会找到它自己 ⇒ 自己把自己挡死（rc=6）
      ② 目标是对话框【里面的控件】时，那个对话框是它的祖先 ⇒ 同样被挡
      两种都是假阳性 —— 那个对话框不是挡路的，它就是你要操作的东西。
    """
    out, cur = [], hwnd
    for _ in range(depth):
        if not cur:
            break
        out.append(cur)
        try:
            nxt = user32.GetParent(cur)      # 子窗口返回父；顶层弹出窗返回属主
        except Exception:
            break
        if not nxt or nxt == cur:
            break
        cur = nxt
    return out


def modal_blocker(hwnd):
    """返回 (是否被挡, 说明)。纯只读探测：只枚举与查询，不做任何动作。"""
    try:
        if not user32.IsWindow(hwnd):
            return False, ""
        if not user32.IsWindowEnabled(hwnd):
            return True, "目标窗口当前处于禁用状态（典型：其上有模态对话框）"
    except Exception:
        return False, ""

    try:
        _tid, tpid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return False, ""

    # ★2026-10-12：先算出「目标自己 + 它的全部祖先」。枚举到的对话框若落在这条链上，
    #   说明它就是目标本人或目标的容器 —— **不是挡路的那个**，必须跳过。
    #   （修前：目标 = 对话框本身 ⇒ 枚举找到自己 ⇒ 自己把自己挡死，返回 rc=6）
    _chain = _ancestor_chain(hwnd)

    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _l):
        try:
            if not user32.IsWindowVisible(h):
                return True
            if h in _chain:
                return True                  # 目标自己 / 目标的祖先 ⇒ 不是挡路的
            cls = win32gui.GetClassName(h) or ""
            if cls not in DIALOG_CLASSES:
                return True
            try:
                _t, pid2 = win32process.GetWindowThreadProcessId(h)
            except Exception:
                return True
            if cls == "DirectUIHWND" and pid2 != tpid:
                return True          # 满街都是，只认同进程的
            title = win32gui.GetWindowText(h) or ""
            own = user32.GetWindow(h, 4)
            if pid2 == tpid or own == hwnd or _owner_chain(h, hwnd):
                found.append((h, cls, title[:40]))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(cb, 0)
    except Exception as e:
        return False, f"(枚举失败，未阻断：{e})"

    if found:
        h, cls, title = found[0]
        return True, f"目标进程上有可见对话框：类名={cls} 标题=「{title}」hwnd={h}（共 {len(found)} 个）"
    return False, ""


def require_no_modal(hwnd, what):
    """动作前调用：被模态挡住就拒绝（返回 6，与其它拒绝码区分）。未被挡返回 0。"""
    blocked, why = modal_blocker(hwnd)
    if blocked:
        log(f"⛔ 拒绝{what}：{why}（模态对话框会吞掉动作，先关掉它）")
        return 6
    return 0


# ═════════ ③ 动作前自动置前（2026-10-10 派单·效率优化③）═════════════
# 现场教训（证据 §三 错 8）："窗口丢前台就开拖 ⇒ 两次 rc=5"。
#   旧实现的动作是「不在前台 ⇒ 拒绝」，把"把窗口弄到前台"这件**机械的事**
#   推给了调用方。调用方（drill / 模型）于是要先 focus 一次、再重试动作 ——
#   两次调用、两次过闸、两次额度，其中一次注定失败。
#
# 改成：**先自己试一次置前，成功就继续做动作**；只有真置不上才拒绝。
# 三条边界（都不是随手定的）：
#   1. 置前是**动作**（会改用户桌面状态）⇒ 仍然要有该级别的证；
#      走 require_note 传同一张证，**不额外扣一次动作额度**（同一次动作，不是两次）。
#   2. `--post`（后台投递）的语义就是**不抢前台** ⇒ 那条路**绝不置前**，
#      否则把整个 T4 设计的立足点拆了。
#   3. `--dry-run` 不置前（演练不许改状态）；但要如实报"真跑会置前"。
#
# 为什么不用 `_force_foreground` 那种强抢：阶段一已裁决为「废」——
#   AttachThreadInput + BringWindowToTop 会抢走用户正在打字的焦点，
#   且抢不到时会留下输入队列脏状态。这里只用最温和的 SW_RESTORE + SetForegroundWindow，
#   失败就如实拒绝（fail-closed），不升级手段。
FOREGROUND_SWITCH_TIMEOUT = 0.9      # SetForegroundWindow 之后给窗口的生效时间


def _try_foreground(hwnd, what, dry=False):
    """把目标窗口弄到前台。返回 (现在是否在前台, 做了什么说明)。

    不做任何强抢（不 AttachThreadInput、不发 ALT 假键）——
    抢不来就承认抢不来，交给调用方决定，而不是用副作用更大的手段硬上。
    """
    h = int(hwnd)
    notes = []
    if win32gui.GetForegroundWindow() == h:
        return True, "本来就在前台"
    if dry:
        return False, "演练：**未置前**（真跑时会先 SW_RESTORE + SetForegroundWindow）"
    try:
        if user32.IsIconic(wt.HWND(h)):
            notes.append("窗口处于最小化 → SW_RESTORE")
        user32.ShowWindow(wt.HWND(h), 9)          # SW_RESTORE
    except Exception as e:
        notes.append("ShowWindow 失败(%s)" % type(e).__name__)
    try:
        user32.SetForegroundWindow(wt.HWND(h))
        notes.append("SetForegroundWindow")
    except Exception as e:
        notes.append("SetForegroundWindow 失败(%s)" % type(e).__name__)
    time.sleep(FOREGROUND_SWITCH_TIMEOUT)
    fg = win32gui.GetForegroundWindow() == h
    if not fg:
        try:
            cur = win32gui.GetForegroundWindow()
            cur_t = win32gui.GetWindowText(cur) or "(无标题)"
            notes.append("现前台仍是 hwnd=%d「%s」" % (cur, cur_t[:30]))
        except Exception:
            pass
    return fg, "；".join(notes) or "未做任何操作"


def ensure_foreground(hwnd, what, note="", dry=False):
    """③ 统一入口：动作前保证目标在前台。

    返回 0 = 已在前台（可继续动作）；非零 = 拒绝码（5=置前失败，9=无证）。
    **注意**：本函数在真正置前时**不额外过闸计数** —— 它是同一次动作的一部分。
    但"置前"本身是动作，所以照旧要求调用方已经拿到对应级别的证（note 仅用于日志）。
    """
    fg = win32gui.GetForegroundWindow() == hwnd
    if fg:
        return 0
    ok, why = _try_foreground(hwnd, what, dry=dry)
    if ok:
        log(f"   ↑ 动作前自动置前成功（{why}）—— 不再因「不在前台」拒绝")
        return 0
    if dry:
        log(f"   （演练）{what}：目标不在前台。{why}")
        return 0                                   # 演练不因前台拒绝（与旧行为一致）
    return _tell_next(
        5, f"{what}：目标窗口不在前台，且**自动置前也没成功**（{why}）",
        hwnd, extra="目标窗口可能被别的窗口（含「总在最前」）压着，或置前被系统拒。")


def check_target(hwnd):
    """目标窗口检查。返回 (info, 拒绝原因)；拒绝原因为 None 即放行。

    ★ 2026-10-12（用户裁决）：**应用白名单不再作为放行条件**。
      原先这里要求 process+class 精确命中白名单才放行，代价是换个应用就得改配置、
      还得找使用者点头。现在任何窗口都放行，改由上层通行证系统逐次把关：
      A/B/C/D 分级 + 单写者租约 + 三条独立急停 + 动作额度。
      废止前的 14 条连同 why 全文留档在同目录 whitelist.已撤销.json ——
      那些 why 记的是**使用者当时亲手划的操作边界**，属于决定记录而不是配置，删了就没了。

    ⚠️ 下面那三层自指保护**不在这条废止范围内**，也**绝不能**跟着白名单一起删：
      它们禁止控制系统去操作使用者自己的授权弹窗、UAC 框、系统设置页。
      没有它们，agent 就能点掉自己的授权框给自己放行 —— 那是唯一的防自授权闸门。

    ★ 2026-10-16（窗K · P0）三层判据，顺序即优先级：
         ① 属主进程名  —— 身份优先（_deny_owner）。**主判据**。
         ② 窗口类名    —— _deny_class。进程名会变（实测控制台属主会变成前台那条命令），
                          类名不会，所以它是第二道，也是唯一能盖住「进程名变了」那路的。
         ③ 窗口标题    —— 下面两行 BLOCKED_TITLES / BLOCKED_CLASSES，**兜底，一个字没删**。
      顺序为什么这么排：身份改不动，类名次之，标题最容易改 —— 所以先问身份，
      别让「标题碰巧对上了」冒充「身份核对过了」。
      终端那一族额外有一条**人确认过的 30 分钟临时放行**（terminal_pass_ok）：
      默认拦，但用户当场点头后按 (进程, 类名) 放行 30 分钟、写事件、到点自动失效。
      授权弹窗那一族**没有**这条通路（grant_terminal_pass 拒签）。
    """
    i = win_info(hwnd)
    if not i["title"] and not i["class"]:
        return i, f"句柄 {hwnd} 不是有效窗口"
    # ① 属主进程身份（读不到属主时这一层判空，如实记账，见 _deny_owner 注释）
    _own = _deny_owner(i)
    if _own and not _is_terminal_ask(_own):
        return i, _own
    # ② 窗口类名
    _cls = _deny_class(i)
    if _cls and not _is_terminal_ask(_cls):
        return i, _cls
    # ── 到这里说明 ①/② 有一层命中了**终端族** ──
    #   终端是双刃剑，所以**默认拦、但不写死**：先看有没有「用户当场确认过的
    #   30 分钟临时放行」，有就只放这一对 (进程, 类名)，到点自动失效；没有就拒。
    #   ⚠️ 授权弹窗 / UAC / 系统设置**走不到这里** —— 上面两层的硬拒已经先返回了。
    if _own or _cls:
        ok, why_ok = terminal_pass_ok(i.get("process"), i.get("class"))
        if not ok:
            return i, _own or _cls
        log("🔓 终端临时放行生效，放行这一路：%s / %s（%s）"
            % (i.get("process"), i.get("class"), why_ok))
    # 进程层判空的记账：属主进程名读不到（提权窗口常见）时，**如实说这一层没判成**，
   # 不许让它悄悄变成「查过了，安全」。类名层与标题层照常兜底。
    if not _owner_lc(i) or _owner_lc(i) == "?":
        log("⚠️ 目标 hwnd=%s 的属主进程名读不到 —— 身份层**没判成**（类名/标题层照常兜底）"
            % hwnd)
    # ③ 窗口标题（兜底，两行原样保留）
    if any(b in i["title"] for b in BLOCKED_TITLES):
        return i, "这是用户的授权弹窗（正在输密钥），任何动作一律禁止"
    if any(b == i["class"] for b in BLOCKED_CLASSES):
        # 精确相等，不做子串 —— 类名是我们自己注册的短名，子串匹配只会误伤。
        return i, "这是急停置顶告警窗（谁在动 / 怎么恢复），任何动作一律禁止"
    return i, None


# ─────────────────── UIA ───────────────────
def _uia(hwnd):
    # ★ D17 阶段二（慢路径兜底）：`ControlFromHandle` 对**不响应**的窗口会**无限等待**
    #   （实测 >40s 不返回，只能 kill）。`SetGlobalSearchTimeout(2)` 只管**搜索**，
    #   管不住这一步 —— 所以这里必须自己加超时。
    #
    #   为什么 `hung_guard` 的前置检查还不够（实测踩到的）：`IsHungAppWindow` **只在窗口
    #   已经被问过、且 5 秒没回答时才返回 1**。一个刚创建、还没人发过消息的窗口返回 0
    #   ⇒ 前置检查放行 ⇒ `ControlFromHandle` 一发消息就卡。**两个都得有**。
    #
    #   取舍：超时后那个 UIA 线程**留在后台卡着**（daemon 线程，进程退出时被直接丢弃）。
    #   这是刻意的 —— 宁可漏一个卡死线程，也不能让整个 act 挂住。
    box = {}

    def _grab():
        # ★ COM 是**每线程**初始化的。新起的线程里必须自己初始化一次，
        #   否则 ControlFromHandle 直接抛「尚未调用 CoInitialize」（实测踩到了）。
        try:
            auto.InitializeUIAutomationInCurrentThread()
        except Exception:
            pass
        try:
            box["ctrl"] = auto.ControlFromHandle(hwnd)
        except Exception as e:                      # 记下来，主线程决定怎么办
            box["err"] = e

    import uiautomation as auto
    auto.SetGlobalSearchTimeout(2)
    th = threading.Thread(target=_grab, daemon=True)
    th.start()
    th.join(UIA_HANG_TIMEOUT)
    if th.is_alive():
        _HUNG_SEEN.add(int(hwnd))
        raise _UiATimeout(
            "UIA 取控件超时 %.1fs（目标窗口多半不响应）—— fail-closed 拒绝，"
            "不无限等" % UIA_HANG_TIMEOUT)
    if "err" in box:
        raise box["err"]
    return auto, box["ctrl"]


def _center(ctrl):
    """控件中心。注意：**矩形可能是空的**（虚拟化的列表项、未渲染的标签页 = [0,0,0,0]），
    此时"中心"是 (0,0) —— 屏幕左上角。所以这里同时返回矩形是否有效。"""
    r = ctrl.BoundingRectangle
    rect = [r.left, r.top, r.right, r.bottom]
    ok = (rect[2] > rect[0]) and (rect[3] > rect[1])
    return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2, rect, ok


def _need_perceive(note):
    """读取也要过闸 —— 不能只在命令行入口把守。

    踩过的洞：drill.py 直接调 targets()/read_doc()，而闸门只在 act.main() 里检查，
    于是"绕过闸门的脚本"就真绕过去了。**闸门必须长在被调用的函数里，不是长在入口上。**
    """
    try:
        # count=False：**读操作不扣动作额度**。扣了会导致"看得多就没额度动手"这种荒唐结果。
        gate.require_any(gate.PERCEIVE_SCOPES, note, count=False)
        return True
    except gate.GateDenied as e:
        log(f"⛔ 拒绝读取（{note}）：{e}")
        return False


def targets(hwnd, limit=40, with_geo=False):
    """列出可交互的控件（只读，但同样要过闸）。

    ★ 返回值形状（2026-10-10 加 ④ 时为兼容而保留）：
      `with_geo=False`（**默认**）→ 返回 `list`，与改前逐字节一致。
        drill.py 等既有调用方 `act.targets(hwnd, limit=80)` 照旧拿到列表。
      `with_geo=True` → 返回 `(可点控件列表, 全树几何列表)`，供 ④ 推断带状区。
    **默认不改形状**是刻意的：为了加一个输出字段而改掉函数返回类型，
    等于把所有既有调用方一起打破（drill.py 就有两处）。
    """
    if not _need_perceive("列可点控件"):
        return ([], []) if with_geo else []
    auto, root = _uia(hwnd)
    out = []

    def walk(c, d=0):
        if len(out) >= limit or d > 8:
            return
        try:
            pats = [n for n in ("Invoke", "SelectionItem", "Toggle", "ExpandCollapse", "Scroll")
                    if getattr(c, "Get" + n + "Pattern", None) and _has(c, n)]
            if pats and c.Name:
                x, y, r, r_ok = _center(c)
                sel = None
                if "SelectionItem" in pats:
                    try:
                        sel = bool(c.GetSelectionItemPattern().IsSelected)
                    except Exception:
                        sel = None
                out.append({"类型": c.ControlTypeName, "名称": c.Name[:50],
                            "矩形": r, "中心": [x, y], "模式": pats,
                            "已渲染": r_ok, "选中": sel})
        except Exception:
            pass
        try:
            for ch in c.GetChildren():
                walk(ch, d + 1)
        except Exception:
            pass

    walk(root)
    if not with_geo:
        return out
    # ★ ④：把"哪条带能拖、哪条带是内容"一并交出去。
    #   上面 walk 只收「有交互模式且有名字」的控件 —— 而**标签页行/菜单栏这种
    #   占位带往往没有名字**（实测 TabControl/AppBarControl 都是 Name=''），
    #   所以这里**另做一遍全树几何扫描**，专收"压在最上面的那条带"。
    #   两遍口径不同，刻意不合并：合并会让"可点控件"清单被无名控件污染。
    geo = []
    try:
        _scan_all_geometry(root, geo, 0, 8)
    except Exception:
        geo = []
    return out, geo


def _scan_all_geometry(c, acc, d=0, maxd=8):
    """第二遍扫描：**不筛模式、不筛名字**，只收几何 —— 专供 ④ 的带状区推断。"""
    if d > maxd:
        return
    try:
        r = c.BoundingRectangle
        rect = [r.left, r.top, r.right, r.bottom]
        acc.append({"类型": c.ControlTypeName, "名称": (c.Name or "")[:50], "矩形": rect})
    except Exception:
        pass
    try:
        for ch in c.GetChildren():
            _scan_all_geometry(ch, acc, d + 1, maxd)
    except Exception:
        pass


def _has(ctrl, name):
    try:
        return getattr(ctrl, "Get" + name + "Pattern")() is not None
    except Exception:
        return False


# ═════════ ④ targets 标注「可操作区」（2026-10-10 派单·效率优化④）═════════════
# 现场教训（证据 §三 错 6）：调用方为了拖窗口，**连试 7 次都点在标签页上**。
#   根因：Win11 记事本的标题栏**大半被标签页行占了** ——
#     窗口 rect      = (87,321,2007,624)
#     标签页行 (Tab) = (156,335,1733,383)   ← 高度 48px，几乎铺满标题栏的可拖带
#     菜单栏   (Menu)= (96, 383, 1998,433)
#     正文     (Doc) = (96, 433, 1998,567)
#   传统直觉"标题栏 y 在窗口顶部 30px 内 ⇒ 可拖"在这里**正好落在标签页上**，
#   拖拽于是被标签页吃掉（拖成了"移动标签"或"无反应"）。
#
# 这件事**只有工具知道**（它读了整棵 UIA 树），调用方靠试是试不出来的 ——
# 所以由工具把"哪条带能拖、哪条带是内容"直接写进 targets 输出。
#
# 判据形状：不猜"标题栏在哪"，而是**从实际控件几何反推**：
#   顶部第一行可拖带 = 窗口客户区顶边 → 第一个"占位控件"（Tab/菜单/地址栏）的上边
#   左右可拖带       = 客户区左右边到内容区，宽度 = 窗口边框厚度
#   是否是"可拖窗口"  = 窗口样式里有 WS_CAPTION | WS_THICKFRAME（无边框窗口不能拖）
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000


def _bands(hwnd, controls):
    """从窗口几何 + 控件矩形反推三级带状区。返回 dict（可直接进 JSON 输出）。

    全部用**物理像素**（与 targets 里「中心」同一口径）。
    """
    out = {
        "坐标系": "物理像素（与上面各控件的「矩形」「中心」同口径）",
        "可拖拽判定": "窗口样式含 WS_CAPTION|WS_THICKFRAME 才可拖；无边框窗口拖不动",
    }
    try:
        rect = list(win32gui.GetWindowRect(hwnd))
    except Exception as e:
        out["错误"] = "拿不到窗口矩形：%s" % e
        return out
    out["窗口矩形"] = rect
    try:
        cli = win32gui.GetClientRect(hwnd)
        cxo, cyo = win32gui.ClientToScreen(hwnd, (0, 0))
    except Exception:
        cli, cxo, cyo = None, rect[0], rect[1]
    out["客户区矩形"] = [cli[0], cli[1], cli[2], cli[3]] if cli else None
    out["客户区屏幕原点"] = [cxo, cyo]
    try:
        st = win32gui.GetWindowLong(hwnd, -16)          # GWL_STYLE
        ex = win32gui.GetWindowLong(hwnd, -20)          # GWL_EXSTYLE
        out["窗口样式"] = "STYLE=0x%08X EXSTYLE=0x%08X" % (st, ex)
        out["可拖拽窗口"] = bool(st & WS_CAPTION and st & WS_THICKFRAME)
        out["有标题栏"] = bool(st & WS_CAPTION)
        out["有系统菜单"] = bool(st & WS_SYSMENU)
        out["有缩放边框"] = bool(st & WS_THICKFRAME)
    except Exception as e:
        out["窗口样式"] = "读不到：%s" % e
        out["可拖拽窗口"] = None

    # ── 顶部占位控件：找出"压在客户区顶部"的那条**带** ──
    # 判据用 **几何**，不认控件类型名 —— 类型名各应用不同
    # （TabControl / AppBarControl / EditControl…），几何才是公共语言。
    #
    # ★ 两次实测踩到的坑，都记在这里（否则下一个人还会踩）：
    #   坑一：只按"顶边贴客户区顶边 + 够宽"筛，会把**根 WindowControl** 收进来
    #        （矩形 = 整个窗口）⇒ 可拖带被算成 303px，把标签页行、菜单栏、正文全包进去。
    #   坑二：改用"顶边严格贴顶（容差 8px）"来躲坑一 ⇒ **又漏掉了真正的标题栏**：
    #        记事本窗口顶 y=321、客户区顶 y=321，而标签页行是 y=335——
    #        真正的可拖带只有 321→335 这 **14px**，它比 8px 容差还宽一点，
    #        但它对应的控件（Tab Pane）顶边在 335，根本不满足"贴顶"。
    #
    # 正确判据（**只按几何，不按类型**）：在客户区里找**最靠上的那条"横向铺开、
    # 且不贯穿到底"的控件**，把它当作顶部分隔线。它上方的空白带 = 标题栏可拖区。
    #   · "横向铺开"  = 宽 ≥ 客户区宽 30%
    #   · "不贯穿到底" = 底边 < 客户区底边 - 8（贯穿的是容器，不是条带）
    #   · "最靠上"    = 在所有满足前两条的控件里取 top 最小的那个
    cli_top = cyo + cli[1] if cli else rect[1]
    cli_bottom = cyo + cli[3] if cli else rect[3]
    cli_left = cxo + cli[0] if cli else rect[0]
    cli_right = cxo + cli[2] if cli else rect[2]
    strip_top = None
    top_owner = None
    doc_rect = None
    content_rect = None
    for c in controls:
        r = c.get("矩形") or [0, 0, 0, 0]
        if (r[2] <= r[0]) or (r[3] <= r[1]):
            continue                                     # 未渲染（[0,0,0,0]）
        if c.get("类型") == "DocumentControl":
            if doc_rect is None or (r[1] < doc_rect[1]):
                doc_rect = r
            continue
        if not cli:
            continue
        if ((r[2] - r[0]) >= (cli[2] - cli[0]) * 0.3      # 横向铺得够宽
                and r[3] < cli_bottom - 8):               # 不贯穿到底（贯穿者为容器）
            if strip_top is None or r[1] < strip_top:
                strip_top = r[1]
                top_owner = {"类型": c.get("类型"), "名称": c.get("名称"), "矩形": r}

    out["顶部占位控件"] = top_owner
    top_band_bottom = strip_top

    # ── ① 可拖带（上边框）：窗口顶边 → 顶部占位控件的下边 ──
    # 注意这里用的是**窗口顶边 rect[1]**（不是客户区顶边）——
    # 真实可拖区从窗口最上沿就算起，客户区顶边以下的第一个像素才是占位控件。
    # 本机实测记事本：窗口顶 321 / 客户区顶 321（无可见上边框）/ 标签页行 335–383，
    # 所以可拖带 = [96,321,1998,335]，高 14px —— 与截图里那条细边吻合。
    if cli and top_band_bottom is not None and top_band_bottom > rect[1]:
        h = top_band_bottom - rect[1]
        out["可拖带_上边框"] = {
            "矩形": [cli_left, rect[1], cli_right, top_band_bottom],
            "高": h,
            "说明": "窗口顶边到第一个占位控件（%s「%s」）之间这条带**只属于标题栏**，拖它 = 移动窗口"
                    % (top_owner["类型"], (top_owner["名称"] or "(无题)")[:24]),
        }
    elif cli:
        out["可拖带_上边框"] = {
            "矩形": None, "高": 0,
            "说明": "**没有可用顶部可拖带** —— 占位控件（%s「%s」）顶边已贴到窗口顶边，"
                    "拖这条带会落在该控件上，不会移动窗口。改用下面的边框带。"
                    % (top_owner["类型"] if top_owner else "?",
                       (top_owner.get("名称") or "(无题)")[:24] if top_owner else "?"),
        }
    else:
        out["可拖带_上边框"] = {"矩形": None, "高": 0, "说明": "客户区几何读不到，无法推断"}

    # ── ② 左右/下边框带：厚度 = 客户区与外框的差 ──
    if cli:
        left_w = cxo + cli[0] - rect[0]
        right_w = rect[2] - (cxo + cli[2])
        bottom_h = rect[3] - (cyo + cli[3])
        out["边框厚度"] = {"左": left_w, "右": right_w, "下": bottom_h}
        if left_w > 0:
            out["可拖带_左边框"] = {"矩形": [rect[0], rect[1], cxo + cli[0], rect[3]], "宽": left_w}
        if right_w > 0:
            out["可拖带_右边框"] = {"矩形": [cxo + cli[2], rect[1], rect[2], rect[3]], "宽": right_w}
        if bottom_h > 0:
            out["可拖带_下边框"] = {"矩形": [rect[0], cyo + cli[3], rect[2], rect[3]], "高": bottom_h}

    # ── ③ 内容区：最浅的 DocumentControl；没有就用顶部带以下的客户区 ──
    if doc_rect is not None:
        content_rect = doc_rect
        out["内容区"] = {
            "矩形": doc_rect,
            "来源": "UIA DocumentControl（可直接 read / type / scroll）",
        }
    elif cli and top_band_bottom is not None:
        content_rect = [cxo + cli[0], top_band_bottom, cxo + cli[2], cyo + cli[3]]
        out["内容区"] = {
            "矩形": content_rect,
            "来源": "无 DocumentControl ⇒ 取顶部占位控件以下的客户区（**内容区未经验证**，仅供拖拽落点参考）",
        }
    else:
        out["内容区"] = {"矩形": None, "来源": "推断不出来（客户区几何缺失）"}

    # ── ④ 拖拽推荐点：上边框正中的一个实际可拖点 + 边框带 ──
    rec = {}
    if out.get("可拖带_上边框", {}).get("矩形"):
        r = out["可拖带_上边框"]["矩形"]
        rec["拖标题栏"] = [(r[0] + r[2]) // 2, (r[1] + r[3]) // 2]
    if out.get("可拖带_左边框"):
        r = out["可拖带_左边框"]["矩形"]
        rec["拖左边框"] = [(r[0] + r[2]) // 2, (r[1] + r[3]) // 2]
    if doc_rect:
        rec["内容区中心"] = [(doc_rect[0] + doc_rect[2]) // 2, (doc_rect[1] + doc_rect[3]) // 2]
    rec["**别拖这里**"] = (
        "顶部占位控件（%s「%s」矩形 %s）**不是标题栏** —— 它是窗口最上面那条"
        "「铺开且不贯穿到底」的控件（Win11 记事本这里是标签页行，资源管理器这里是"
        "工具栏/标签行）。拖它会落在该控件上，**不会移动窗口**；能拖的只有上面的"
        "「可拖带」和左右的「边框带」。"
        % (top_owner["类型"] if top_owner else "?",
           (top_owner.get("名称") or "(无题)")[:24] if top_owner else "?",
           top_owner.get("矩形") if top_owner else None))
    out["拖拽推荐点"] = rec
    return out


def find_control(hwnd, name, max_depth=10):
    """按名字找控件，**并优先返回"能用 UIA 操作"的那个**。

    两个坑都要躲：
      1. 别用 `root.Control(Name=...)`：它返回泛型包装，取不到 GetSelectionItemPattern
         之类只定义在具体类型上的方法 → 会被误判成"不支持 UIA"，白白退到点坐标。
      2. 名字对上 ≠ 能用 UIA 点：标签页 `game_boost.ps1. 未修改。` 里还裹着一个
         同名 `TextControl`，它**什么模式都没有**，只会把动作逼回坐标路径。
    所以：收集所有名字命中者，按 **可控性（Invoke > SelectionItem > Toggle > 无）** 排序，
    同级再取更靠近根节点的（父节点才是真正可操作的那个）。
    """
    _auto, root = _uia(hwnd)
    cands = []

    def walk(c, d=0):
        if d > max_depth:
            return
        try:
            nm = c.Name or ""
            if nm and (nm == name or (name and name in nm)):
                score = 0
                for pat, sc in (("Invoke", 3), ("SelectionItem", 2), ("Toggle", 1)):
                    if _has(c, pat):
                        score = sc
                        break
                cands.append((score + (0.5 if nm == name else 0), -d, c))
        except Exception:
            pass
        try:
            for ch in c.GetChildren():
                walk(ch, d + 1)
        except Exception:
            pass

    walk(root)
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return cands[0][2]


# ─────────────────── 输入（SendInput，仅在没有 UIA 模式时才用） ───────────────────
# ★ M4（2026-10-22）：`dwExtraInfo` 从「指针」改成 **ULONG_PTR 宽度**（ctypes.c_size_t）。
#   改的理由只有一个：**要能把魔数填进去**。
#   · Win32 的定义是 `ULONG_PTR dwExtraInfo`（指针宽度的无符号整数），
#     老代码把它声明成 `POINTER(c_ulong)` —— 宽度**恰好相同**（x64 8 字节 / x86 4 字节），
#     所以**结构体布局一个字节都不变**，但语义是「指针」⇒ 填不进值。
#   · 改成 `c_size_t` 后宽度不变、语义正确 ⇒ 老代码的 `None`（= NULL = 0）仍然合法。
# ★ 魔数定义在**单独一个模块** inject_magic.py（单一真源，注入方与判定方共用）：
#   `inject_magic.INJECT_MAGIC` —— 判据与将来的归因层 import 的就是这一个定义。
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTU(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTU)]


MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_WHEEL, WHEEL_DELTA = 0x0800, 120
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
KEYEVENTF_SCANCODE = 0x0008               # ★ 2026-10-10 新增：带真实扫描码注入
MAPVK_VK_TO_VSC = 0


# ───────────────────── 扫描码注入（2026-10-10 为 VirtualBox 靶新增）─────────
# 为什么需要这一套（实测出来的，不是推的）：
#   本文件原有的两条按键路径**都不带硬件扫描码**——
#     · send_unicode：wVk=0 + KEYEVENTF_UNICODE（wScan 填的是码点，不是 scancode）
#     · send_vk     ：wScan=0
#   而 VirtualBox 模拟的是 PS/2 键盘（VBoxManage showvminfo 写明
#   Keyboard Device: PS/2 Keyboard），PS/2 模型只认扫描码，收到没有扫描码的
#   事件直接丢弃。表现极具迷惑性：坐标点击能正常穿透 guest，一发按键就全灭，
#   连 host key（Right Ctrl）都按不动，看起来像「键盘模式没切」。
#
# 所以新增这条路径：用 VkKeyScan 查「这个字符要按哪个键、要不要 Shift」，
# 再用 MapVirtualKey 换真实 scancode，以 KEYEVENTF_SCANCODE 注入。
#
# 边界（明写在代码里，别当成万能的）：
#   · VkKeyScan 换不出的字符（中文、全角符号等）自动回退 UNICODE 路径并记账，
#     不静默吞掉；
#   · 这条路径只在显式开启时使用（do_keys/do_type 的 scan_code=True 或命令行
#     --scan-code），默认行为与改前逐字节一致；
#   · 走扫描码意味着经过键盘布局与输入法，与 UNICODE 直写码点的语义不同。
_VkKeyScanW = ctypes.WINFUNCTYPE(
    ctypes.c_short, ctypes.c_wchar)(("VkKeyScanW", user32))
_MapVirtualKeyW = ctypes.WINFUNCTYPE(
    ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)(("MapVirtualKeyW", user32))


def send_scan(scancode, down=True):
    """按真实扫描码注入一次按键（KEYEVENTF_SCANCODE 路径）。

    按 MS 约定：置了 KEYEVENTF_SCANCODE 后 wVk 必须置 0，wScan 才被采用。
    """
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if not down else 0)
    inp = INPUT(type=INPUT_KEYBOARD,
                ki=KEYBDINPUT(0, scancode, flags, 0, inject_magic.INJECT_MAGIC))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def vk_to_scancode(vk):
    """虚拟键码 → 扫描码；换不出返回 0。"""
    return _MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)


def send_vk_scan(vk, down=True):
    """send_vk 的扫描码版（快捷键用：Ctrl/Backspace/方向键等）。

    换不出扫描码时回退到 send_vk，并返回 False 告诉调用方「这次没走扫描码」，
    由上层记账——不静默降级。
    """
    sc = vk_to_scancode(vk)
    if not sc:
        send_vk(vk, down)
        return False
    send_scan(sc, down)
    return True


def send_text_scan(text, gap=0.012):
    """把一段文本按扫描码逐字符打出去。返回 (走扫描码的字符数, 回退字符列表)。

    用 VkKeyScanW 拿「vk + Shift 状态」：大写字母/符号会要求按住 Shift，
    这条路径会如实处理，不是只按 A-Z 数字。
    """
    shift_sc = vk_to_scancode(0x10)          # VK_SHIFT
    n = 0
    fallback = []
    for ch in text:
        vks = _VkKeyScanW(ch)
        if vks == -1:
            send_unicode(ch)                  # 换不出（如中文）→ 回退原路径
            fallback.append(ch)
            continue
        vk = vks & 0xFF
        need_shift = bool((vks >> 8) & 1)
        sc = vk_to_scancode(vk)
        if not sc:
            send_unicode(ch)
            fallback.append(ch)
            continue
        if need_shift:
            send_scan(shift_sc, True)
        send_scan(sc, True)
        send_scan(sc, False)
        if need_shift:
            send_scan(shift_sc, False)
        n += 1
        time.sleep(gap)
    return n, fallback


def send_mouse(flags, x=0, y=0, data=0):
    inp = INPUT(type=INPUT_MOUSE,
                mi=MOUSEINPUT(x, y, data, flags, 0, inject_magic.INJECT_MAGIC))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_unicode(ch):
    """**中文输入必须走 KEYEVENTF_UNICODE**：按虚拟键码只能打出 ASCII，
    中文要靠"直接把码点写进键盘事件"，绕开输入法与键盘布局。"""
    code = ord(ch)
    # ★ 2026-10-18 P0-B 补：非 BMP 字符（emoji 等 U+1F000 以上）**必须拆成
    #   UTF-16 代理对分别发**。改前直接把 ord(ch) 塞进 wScan ——
    #   wScan 是 16 位，0x1F60A 被截成 0xF60A，收进去是**半个代理项**
    #   （实测：A+emoji+B 回读成 'A\uf60aB'，屏幕上不是那个 emoji）。
    #   BMP 段（中文/字母/数字）走的还是原来那条，逐字节一致。
    if code > 0xFFFF:
        _c = code - 0x10000
        units = (0xD800 | (_c >> 10), 0xDC00 | (_c & 0x3FF))
    else:
        units = (code,)
    for _u in units:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD,
                        ki=KEYBDINPUT(0, _u, flags, 0, inject_magic.INJECT_MAGIC))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_text(text, gap=0.012, nl="enter"):
    """按文本逐字发键，但**换行/制表符必须走虚拟键码**。

    2026-10-10 晚实测坑一（记事本，229 → 224 字）：
    `send_unicode("\\n")` 发出去的是 KEYEVENTF_UNICODE 的码点 0x0A，
    而 Notepad 这类标准编辑控件只认 WM_KEYDOWN 的 VK_RETURN(0x0D) ——
    换行被**静默丢弃**，一字不报错，回读才发现少了 5 个换行。
    （扫描码路径 `send_text_scan` 本身没问题：VkKeyScanW 能把 "\\n" 映射成 0x0D。）

    2026-10-10 晚实测坑二（WorkBuddy 聊天框，1127 字被拆成多条消息自动发出）：
    换行**一律翻成 VK_RETURN 在聊天框里等于「发送」** —— 一段多行任务文本
    被每个换行切成一段、每段立刻提交，凭空造出两个垃圾任务在跑。
    所以换行发什么**必须由调用语境决定**，不能写死：

      nl="enter"       发 VK_RETURN —— 文档/编辑器语境（`do_type` 默认）
      nl="shift_enter" 发 Shift+VK_RETURN —— 聊天框/表单语境（`do_fill` 默认）
      nl="space"       换行降级成空格 —— 单行输入框，Enter 会提交表单

    默认值是 "enter" 是为了不改变 `do_type` 的既有行为；
    **凡是往「输入框」而不是「文档」里打字的，必须显式选 shift_enter 或 space。**
    """
    if nl not in ("enter", "shift_enter", "space"):
        raise ValueError("nl 只能是 enter / shift_enter / space，收到 %r" % (nl,))
    for ch in text:
        if ch in ("\n", "\r"):
            if nl == "enter":
                send_vk(0x0D)
            elif nl == "shift_enter":
                send_vk(0x10, True)      # 按住 Shift
                send_vk(0x0D)
                send_vk(0x10, False)
            else:
                send_unicode(" ")
        elif ch == "\t":
            send_vk(0x09)
        else:
            send_unicode(ch)
        time.sleep(gap)


def click_at(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    send_mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.05)
    send_mouse(MOUSEEVENTF_LEFTUP)


def post_click(hwnd, x, y):
    """把一次左键点击**后台投递**给**目标窗口 hwnd** 的客户区坐标 (x,y)。

    返回 (投递目标句柄, 客户区坐标, 消息条数, 屏幕点下最深的那层窗口)；异常交给调用方。

    四个要点（每一个踩过都会变成"看着成功其实没点到"）：
      1. **不抢焦点**：全程不调 SetForegroundWindow、不碰 SetCursorPos，也不产生
         系统输入事件 —— 这正是这条通路存在的唯一理由（工单 T4 要求 4）。
      2. **投给目标窗口本身**（工单 T4 原话：「PostMessage … 到**目标窗口**客户区坐标」）。
         2026-10-08 预扫实测纠偏：先前版本投给 `WindowFromPoint` 找到的最深子窗口，
         结果在 VirtualBoxVM（Qt6）上**完全无效** —— 实测该窗口整片客户区被一个
         铺满的子窗口盖住（该子窗口客户区原点就在屏幕 y=0），鼠标消息投进去
         无人处理，前/后证据图**逐像素相同（0/1172171）**、窗口清单也无新弹窗。
         投给顶层窗口后即被消费（见 _预扫记录）。所以"最深子窗口"只作**诊断**保留，
         不再当投递目标 —— 框架型窗口（Qt/WinUI/XAML）的承点是顶层，子窗口是噪音。
      3. lParam 要用**客户区坐标**（ScreenToClient 换算），不是屏幕坐标 —— 把屏幕
         坐标塞进 lParam 是这类代码最常见的错，表现是整片点偏。
      4. 最深子窗口记进返回值第 4 项，写进输出日志的「深点窗口」字段，便于事后复盘
         "到底点到了谁"；它**不参与**投递，也就不可能因此点偏。
    """
    deep = user32.WindowFromPoint(wt.POINT(int(x), int(y))) or hwnd
    target = hwnd
    c = wt.POINT(int(x), int(y))
    if not user32.ScreenToClient(target, ctypes.byref(c)):
        raise ctypes.WinError(ctypes.get_last_error())
    lp = ((c.y & 0xFFFF) << 16) | (c.x & 0xFFFF)
    for msg, wp in ((WM_MOUSEMOVE, 0), (WM_LBUTTONDOWN, MK_LBUTTON), (WM_LBUTTONUP, 0)):
        user32.PostMessageW(target, msg, wp, lp)
    return target, (c.x, c.y), 3, deep


def drag(from_xy, to_xy, steps=12):
    """按住 → 分步移动 → 松开。分步是必须的：一步跳过去很多控件收不到中间移动事件。"""
    user32.SetCursorPos(int(from_xy[0]), int(from_xy[1]))
    time.sleep(0.12)
    send_mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.08)
    x0, y0 = from_xy
    x1, y1 = to_xy
    for i in range(1, steps + 1):
        send_mouse(MOUSEEVENTF_MOVE, int(x0 + (x1 - x0) * i / steps), int(y0 + (y1 - y0) * i / steps))
        time.sleep(0.02)
    time.sleep(0.08)
    send_mouse(MOUSEEVENTF_LEFTUP)


def wheel(delta):
    send_mouse(MOUSEEVENTF_WHEEL, data=delta)


def send_vk(vk, down=True):
    """按一个**虚拟键**（用于 Ctrl+A 全选、Backspace 退格这类快捷键）。

    为什么必须补（2026-09-19 实测闯祸）：
      工具此前**只能敲字符**，**不能按键** —— 于是"把输入框里已有的值换成新值"这件事**做不到**。
      我在实操里想改「工号」，只能用三连击碰运气选全；**没选中就变成追加**，
      结果字段从 `0002` 变成 `00000032`，还把 `0003` 误打进了旁边的手机号里。
      **"写入"和"改写"是两件事，工具必须两种都会。**
    属 C 级（快捷键）。
    """
    flags = 0 if down else KEYEVENTF_KEYUP
    inp = INPUT(type=INPUT_KEYBOARD,
                ki=KEYBDINPUT(vk, 0, flags, 0, inject_magic.INJECT_MAGIC))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


# ══ 键名 → 虚拟键码（2026-10-18 · P0-B · Codex-2）═════════════════════════════
# ★ 为什么加这张表：今天 QQ 现场的第一道断点就是「ctl_keys 不认字母」——
#   传 keys="v" 直接被上游白名单拒掉，于是 Ctrl+V 走不通、中文只能退到
#   KEYEVENTF_UNICODE，而那条路当时还被 UIA 元素入口挡着。
# ★ **只补「字母 / 数字 / 常见编辑键」**，不放行整个 16 位 vk 空间：
#   那个空间里绝大多数是切窗口 / 截屏 / 锁屏 / 重启这类组合键，
#   全量放行等于把它们直接递出去（core.py KEY_WHITELIST 上方那段理由同样成立）。
#   ★ 字母统一映射到**大写** VK（Windows 的字母 vk 本来就是 0x41~0x5A）；
#     要打小写/符号得配 Shift，那是 send_vk(0x10) 的事，不在键名表里编。
KEY_NAME_TO_VK = {}
for _i in range(26):                                   # a~z / A~Z 同码
    KEY_NAME_TO_VK[chr(ord("a") + _i)] = 0x41 + _i
    KEY_NAME_TO_VK[chr(ord("A") + _i)] = 0x41 + _i
for _d in range(10):                                   # 0~9
    KEY_NAME_TO_VK[str(_d)] = 0x30 + _d
KEY_NAME_TO_VK.update({
    "space": 0x20, "Space": 0x20,
    "enter": 0x0D, "Enter": 0x0D, "return": 0x0D,
    "tab": 0x09, "Tab": 0x09,
    "esc": 0x1B, "Esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "Backspace": 0x08,
    "delete": 0x2E, "Delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "Insert": 0x2D,
    "home": 0x24, "Home": 0x24,
    "end": 0x23, "End": 0x23,
    "pageup": 0x21, "PageUp": 0x21,
    "pagedown": 0x22, "PageDown": 0x22,
    "up": 0x26, "arrowup": 0x26, "ArrowUp": 0x26,
    "down": 0x28, "arrowdown": 0x28, "ArrowDown": 0x28,
    "left": 0x25, "arrowleft": 0x25, "ArrowLeft": 0x25,
    "right": 0x27, "arrowright": 0x27, "ArrowRight": 0x27,
})
for _f in range(1, 13):                                # F1~F12
    KEY_NAME_TO_VK["f%d" % _f] = 0x6F + _f
    KEY_NAME_TO_VK["F%d" % _f] = 0x6F + _f
KEY_NAMES_HELP = "、".join(
    ["a~z / A~Z", "0~9", "F1~F12", "Space", "Enter", "Tab", "Esc", "Backspace",
     "Delete", "Insert", "Home", "End", "PageUp", "PageDown",
     "Up/Down/Left/Right"])


def resolve_vk(text):
    """把 --vk 的入参解成虚拟键码：**键名或数值都收**。

    返回 (vk, 说明)；解不出抛 ValueError（由 main 转成一条人话 + 非 0 码）。
    ★ 收数值是为了**向后兼容**：改前 --vk 就是 int(s, 0)，
    0x41 / 65 / a 现在走同一条路 —— 老脚本一个字都不用改。
    """
    s = str(text or "").strip()
    if not s:
        raise ValueError("--vk 不能为空（键名如 v / 0 / Enter，或数值 0x56 / 86）")
    if s in KEY_NAME_TO_VK:
        return KEY_NAME_TO_VK[s], "键名 %s" % s
    try:
        return int(s, 0), "数值 %s" % s
    except ValueError:
        raise ValueError("--vk 认不出 %r。可用键名：%s；或十进制/十六进制数值"
                         "（如 0x56 / 86）。" % (s, KEY_NAMES_HELP))


def do_keys(hwnd, vk=0x41, ctrl=False, times=1, dry=False, wait=0.06, scan_code=False,
            key_name=""):
    """C 级：发送虚拟键组合。vk 默认 0x41=Ctrl+A。

    ctrl=True 时按"按住 Ctrl → 敲键 → 松开 Ctrl"的顺序；times>1 时连敲（如退格清空）。

    scan_code=True 走**扫描码注入**（VirtualBox 等 PS/2 键盘模拟的目标必需，
    见 send_text_scan 的注释）。默认 False —— 行为与改前逐字节一致。
    """
    info, why = check_target(hwnd)
    if why:
        return _tell_deny(why, hwnd, extra="（按键动作 vk=0x%X）" % vk)
    # 模态对话框前置检查（2026-10-12 补，C6）：与 click/fill/scroll/type 同一道闸。
    #   位置与它们一致：**在 gate.require 与任何输入合成之前** —— 被挡就返回 6，
    #   不消耗证/额度，也绝不做出"看着成功其实无效"的按键。
    _mb = require_no_modal(hwnd, "按键")
    if _mb:
        return _mb
    try:
        gate.require(gate.C, f"按键 vk=0x{vk:X} ctrl={ctrl} ×{times}")
    except gate.GateDenied as e:
        return _gate_denied(e, hwnd, gate.C, "按键")
    # ★ 路线 P（2026-10-17 F2）曾在这里「先不碰前台，PostMessage 按键试一次」。
    #   ★★ M16 派单 §E 已把那条**整条废掉**（原因见下面那段）——
    #   这里保留它的**背景**存档，因为它解释的是「**为什么组合键不能走后台**」：
    #     _post_keys 把「Ctrl down」和「V down」分成两条 WM_KEYDOWN 投过去，
    #     目标若按 wParam 直接合成字符（**Qt / Electron / 自绘控件都是这一类**），
    #     Ctrl 那个修饰位压根没参与 ⇒ 结果是打出一个**字面量 v**。
    #     而「打出一个字面量 v」**同样会让画面变化** ⇒ 失败与成功在像素上不可区分。
    #     真跑记录（2026-10-19 微信 Qt 窗口）：输入框里出现 `vvaaav`，
    #     日志逐行写着「✔ 后台按键生效（像素证据有变化）」—— 字面量 v 本身
    #     就是它引以为傲的那份「证据」。
    #     按项目自己的口径（§㊾ ZCode：「输入/事件通道层是独立失败源」）：
    #     **点没点中属输入通道，不属感知通道**；同理，「组合键有没有带上修饰位」
    #     也归输入通道，而输入通道的失败**不能交给像素去裁**。
    # ★★★ M16 派单 §E（主控 2026-10-22 12:36 实测三次复现的**「判词不实」级缺陷**）：
    #   改前的顺序是「**先**后台 PostMessage（= 真投递）→ **再**置前 precheck →
    #   precheck 拒了就回 rc=5」⇒ 契约文案明写「本次**一个键都没发**」，
    #   **但键照送**（主控实测：ctl_keys Delete 把文件删了，回执却说「没发」）。
    #   ⇒ 改法：**precheck 提到投递之前** ⇒ 它拒 ⇒ **真的一个键都没发**（判词与事实一致）。
    #
    #   ⇒ 顺带废掉的那条「先投一次、400ms 后看像素、再无条件落回前台」的探路，
    #     它本来就有四个毛病：
    #       ① 在 precheck **之前**投递 ⇒ 判词不实（本缺陷的病根）；
    #       ② 必然**双投递**（探路投一次 + 前台兜底再投一次）；
    #       ③ 白等 `_settle(400)`；
    #       ④ 白拍两张证据图（post_keys_before/after）。
    #     ⇒ 代价：**按键会置前**（改前非 ctrl 键走后台不抢焦点）——
    #       取舍是「**判词真实 > 不抢焦点**」（判词不实比慢更危险）。
    #
    #   ★ 原「路线 P」的背景保留在案（它解释的是**为什么组合键不走后台**）：
    #     PostMessage 把「Ctrl down」和「V down」分成两条 WM_KEYDOWN 投过去，
    #     目标若按 wParam 直接合成字符（Qt / Electron / 自绘控件），
    #     Ctrl 那个修饰位压根没参与 ⇒ 结果是打出一个**字面量 v**，
    #     而「打出一个字面量 v」**同样会让画面变化** ⇒ 失败与成功在像素上不可区分。
    #     （这条理由对**组合键**依然成立；改后组合键与普通键都走前台真按键。）
    #
    # ★ ③ 动作前自动置前（不再因"不在前台"直接拒绝）——**必须在任何投递之前**
    _fg = ensure_foreground(hwnd, "按键", dry=dry)
    if _fg:
        return _fg
    if dry:
        log(f"（演练）⌨ 按键 vk=0x{vk:X} ctrl={ctrl} ×{times}")
        return 0
    before = evidence(hwnd, "before")
    _send = send_vk_scan if scan_code else send_vk
    if ctrl:
        log("   ⓘ 组合键**不走后台 PostMessage**（2026-10-19 起）："
            "自绘控件会把 Ctrl 丢掉并按 wParam 直接打字母，"
            "而那种乱字符**和成功在像素上长得一样**，判不出来 ⇒ 直接走前台真按键。")
    if ctrl:
        _send(0x11, True)                   # VK_CONTROL down
        time.sleep(0.03)
    for _ in range(max(1, times)):
        _send(vk, True)
        time.sleep(0.02)
        _send(vk, False)
        time.sleep(wait)
    if ctrl:
        time.sleep(0.03)
        _send(0x11, False)                  # VK_CONTROL up
    time.sleep(_settle(400))
    after = evidence(hwnd, "after")
    fp0, fp1 = _img_fp(before.get("文件") or ""), _img_fp(after.get("文件") or "")
    changed = None if (fp0 is None or fp1 is None) else (fp1 != fp0)
    ev = "" if changed is None else ("　✔ 页面已变化" if changed else "　⚠ 页面无变化")
    log(f"⌨ 按键 [vk=0x{vk:X}{' +Ctrl' if ctrl else ''} ×{times}"
        f"{' 扫描码' if scan_code else ''}] 窗口「{info['title']}」"
        f"　前图={before.get('文件') or '无'} 后图={after.get('文件') or '无'}" + ev)
    return 0


def do_fill(hwnd, xy, text, dry=False, clear=True):
    """C 级（内含一次 B 级点击）：**原子填字段** —— 点进输入框 → 清空旧值 → 写入新值 → 截图核验。

    为什么要做成一个原子动作（2026-09-19 实测，我把数据搞脏了两次）：
      这一套"点→Ctrl+A→打字→截图看"我当晚**手写了 30+ 次**，每次手写都可能漏一步：
        · 忘了 Ctrl+A → 变成追加，`0002` 被我敲成 `00000032`
        · 点击落空而我没看日志 → 焦点还在原处，号码被打进**隔壁的手机号框**
      **把"必须一起做、漏一步就出错"的几件事压成一个动作，就不可能再漏。**

    与 `type` 的区别：`type` 只管敲字（不点、不清空）；`fill` 管"把一个字段设成指定值"。
    """
    info, why = check_target(hwnd)
    if why:
        return _tell_deny(why, hwnd, extra="（填字段动作）")
    _mb = require_no_modal(hwnd, "填字段")
    if _mb:
        return _mb
    try:
        # 演练只读不写：任何级别的证都能演练（感知是各级底座）；真动作才必须 C 级
        if dry:
            gate.require_any(gate.PERCEIVE_SCOPES, f"演练填字段 {len(text)} 字")
        else:
            gate.require(gate.C, f"填字段 {len(text)} 字")
    except gate.GateDenied as e:
        return _gate_denied(e, hwnd, gate.C, "填字段")
    x, y = xy
    r = info["rect"]
    if not (r[0] <= x <= r[2] and r[1] <= y <= r[3]):
        return _tell_next(
            5, f"**坐标类**：填字段坐标 ({x},{y}) 不在窗口矩形 {r} 内", hwnd,
            extra=_dpi_report(hwnd))
    # ★ ③ 动作前自动置前（不再因"不在前台"直接拒绝）
    _fg = ensure_foreground(hwnd, "填字段", dry=dry)
    if _fg:
        return _fg
    if dry:
        log(f"（演练）⌨ 填字段 坐标({x},{y}) 「{text}」{'（先清空）' if clear else ''}")
        return 0

    b0 = evidence(hwnd, "fill0")
    click_at(x, y)
    # ★ M7：固定 350 ms → 变化即停（轮询到界面变了就收工）
    _eff = _wait_effect(hwnd, _poll_base(hwnd),
                         budget_ms=int(_settle(350) * 1000))
    if clear:
        # Ctrl+A 全选 → 随后打字即整体覆盖（比退格可靠：不依赖旧值长度）
        send_vk(0x11, True)
        time.sleep(0.03)
        send_vk(0x41, True)
        time.sleep(0.02)
        send_vk(0x41, False)
        time.sleep(0.06)
        send_vk(0x11, False)
        # ★ M7：清空后等「选区真的变了」，不等满 200 ms
        _eff = _wait_effect(hwnd, _poll_base(hwnd),
                             budget_ms=int(_settle(200) * 1000))
    # 表单/聊天框语境：换行发 Shift+Enter。**绝不能发裸 Enter** ——
    # 2026-10-10 实测：往 WorkBuddy 聊天框发多行文本，裸 Enter 把 1127 字
    # 拆成多条消息自动提交，凭空造出两个垃圾任务（见 send_text 的坑二）。
    send_text(text, nl="shift_enter")
    # ★ M7：打完字等「内容真的变了」，不等满 500 ms
    _eff = _wait_effect(hwnd, _poll_base(hwnd),
                         budget_ms=int(_settle(500) * 1000))
    b1 = evidence(hwnd, "fill1")
    f0, f1 = _img_fp(b0.get("文件") or ""), _img_fp(b1.get("文件") or "")
    changed = None if (f0 is None or f1 is None) else (f1 != f0)
    ev = "" if changed is None else ("　✔ 页面已变化" if changed else "　⚠⚠ 页面无变化——去核验")
    log(f"⌨ 填字段 [{len(text)}字{'，含中文' if any(ord(c) > 127 for c in text) else ''}"
        f"{'，已先清空' if clear else ''}] 坐标({x},{y}) 窗口「{info['title']}」"
        f"　前图={b0.get('文件') or '无'} 后图={b1.get('文件') or '无'}" + ev)
    return 0 if changed is True else 8


# ─────────────────── 证据 ───────────────────
def evidence(hwnd, tag):
    import perceive
    r = perceive.shot(hwnd=hwnd, path=os.path.join(
        perceive.SHOTDIR, f"act_{datetime.datetime.now():%Y%m%d_%H%M%S}_{tag}.png"))
    if "拒绝" in r:
        # 拿不到有效证据，**必须显式记下来**，不能悄悄留个空
        log(f"   ⚠ 取证失败（{tag}）：{r['拒绝']}")
    return r


# ─────────────────── 动作 ───────────────────
def _img_fp(path):
    """证据图的指纹，用来判"这次点击到底让页面变了没有"。

    为什么要有（2026-09-19 实测教训）：
      本函数之前**拍了前后图却从不比对**，于是出现"act.py 返回 0，页面一动不动"的
      **静默失效** —— 我在实操里因此白丢了好几次机会（点菜单没切模块、点按钮没弹窗）。
      "返回 0" 只代表**键鼠事件发出去了**，不代表**界面响应了**。两件事必须分开核。
    """
    if not path:
        return None
    try:
        import hashlib
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def do_click(hwnd, name=None, xy=None, dry=False, force_coord=False, retry=0,
             post=False, post_force=False):
    # ★ P0-3（2026-10-21）：retry 默认 **1 → 0**。
    #   为什么：坐标点击「没变化」时**默认会再点一次** —— 对输入框是多余的一下
    #   （可能顺手选中一段字），对 toggle 类是「开了又关」。
    #   「点了没反应」这件事**该由调用方判断**（换定位招 / 换目标），不该由脚本替他多点一次。
    #   需要重试就显式传 --retry N（契约里 ctl_click 的 retry 参数照旧可传）。

    """force_coord=True：**强制走坐标点击**（考核用，专门练 SendInput 那条退路）。

    post=True：**后台投递模式** —— 本来要走坐标的那一次点击改用 PostMessage 投给
    目标窗口，不抢前台焦点；按 post_compat.json 分档，判定不消费/未登记则自动
    **回退** SendInput 前台路线并在输出留一行原因。
    post_force=True：后台投递且**跳过兼容表**（仅用于兼容性预扫）。
    ⚠ 两者都默认关；关掉时本函数行为与改前完全一致（逐字节）。
    """
    info, why = check_target(hwnd)
    if why:
        # ⚠️ 2026-10-16（窗K）顺手修一个**改动前就有的崩**：原来这行是
        #    extra="（点击动作 %s）" % (name or xy)，而 --xy 进来是个**元组**
        #    （(10, 10)），于是只要「按 xy 点击 + 被 check_target 拒」就抛
        #    TypeError: not all arguments converted —— 拒绝原因直接丢掉、退出码变 1。
        #    平时碰不到是因为白名单废止后 check_target 几乎不拒；本单把终端接了进来，
        #    这条**每次拒终端点击都会走**，不修就等于闸门在关键路径上崩给你看。
        _what = name or ("%s,%s" % xy if xy else "?")
        return _tell_deny(why, hwnd, extra="（点击动作 %s）" % _what)
    # 模态对话框前置检查（2026-09-18 新增）：被挡就拒绝，别做出"看着成功其实无效"的动作
    _mb = require_no_modal(hwnd, "点击")
    if _mb:
        return _mb
    try:
        # 演练只读不写：任何级别的证都能演练（感知是各级底座）；真动作才必须 B 级
        if dry:
            gate.require_any(gate.PERCEIVE_SCOPES, f"演练点击 {name or xy}")
        else:
            gate.require(gate.B, f"点击 {name or xy}")
    except gate.GateDenied as e:
        return _gate_denied(e, hwnd, gate.B, "点击")

    method, detail = None, {}
    _post_mode = bool(post or post_force)
    use_post = False
    # ★ M12 阶段三（C①）：**before 只拍一次** ——
    #   改前这里无条件拍一张，随后正常路径在「还原后」**又拍一张**覆盖它
    #   ⇒ 一次整窗抓帧 + 一次 PNG 编码 + 一张多余 PNG（小窗 ~43ms / 大窗 ~78ms）纯浪费。
    #   现在：只有**后台投递路**（不会走到下面的「还原后重拍」）在这里拍；
    #   正常路用下面那张（还原后那张，不是最小化哨兵垃圾）。
    before = {} if dry else (evidence(hwnd, "before") if _post_mode else {})

    # ★ ③ 动作前自动置前 —— **必须放在任何几何/坐标校验之前**。
    #   2026-10-10 实测踩到的坑：窗口**最小化**时 GetWindowRect 返回哨兵矩形
    #   [-32000,-32000,-31763,-31961]，于是"坐标不在窗口矩形内"这条校验先炸了，
    #   rc=5 的坐标类文案盖住了真正的原因（窗口根本没还原）—— 判据③要的是
    #   "丢到后台再执行必须一次成功"，所以必须**先还原再量**。
    #   后台投递模式（--post）**绝不置前**：置前会把它"不抢焦点"的立足点拆掉；
    #   但它也不量坐标（走 PostMessage 客户区坐标），所以不冲突。
    _restored = False          # 是否刚经历过"还原"（决定要不要重解控件几何）
    if not use_post and not _post_mode and not dry:
        try:
            _restored = bool(ctypes.windll.user32.IsIconic(wt.HWND(int(hwnd))))
        except Exception:
            _restored = False
        _fg = ensure_foreground(hwnd, "点击", dry=dry)
        if _fg:
            return _fg
        before = evidence(hwnd, "before")     # 还原后重新取证（还原前的图是哨兵垃圾像素）

    # ★ M12 阶段三（B）：**基线由 core 下发**（core 在注入之前就拍好了）。
    #   改前是 `_wait_effect(hwnd, _poll_base(hwnd), …)` —— `_poll_base(hwnd)` 是**实参**
    #   ⇒ 在**注入之后**才求值 ⇒ 目标重绘快于一次 PrintWindow 时，
    #   **基线里已经含了变化 ⇒ 首变被吞** ⇒ 只能等满预算。
    #   口径（w/h/origin/dpi_scale/来源）随基线一起给 ⇒ core 侧能核对。
    global _POLL_SCOPE
    _base_img, _base_meta = _baseline_from_env()
    if _base_img is None:
        _base_img = _poll_base(hwnd)        # 旧路兜底（回执里标明来源）
        _base_src = "现抓(旧路)"
    else:
        _base_src = "core下发"
    # 采样口径跟着基线走：基线是**客户区**帧 ⇒ 采样也必须是客户区，否则两帧不可比。
    _POLL_SCOPE = "client" if str(_base_meta.get("来源") or "") == "客户区" else ""
    detail["基线"] = _base_src

    def _try_post():
        ok, why = resolve_post(info, post_force)
        if ok:
            detail["投递依据"] = why
            return True
        log(f"   ↩ 已回退到 SendInput 前台路线：{why}")
        detail["已回退"] = why     # 也写进 detail，让它出现在最终那条日志的 JSON 里
        return False

    if name:
        c = find_control(hwnd, name)
        if c is None:
            return _tell_next(
                4, f"窗口里找不到名为「{name}」的控件，不做任何动作", hwnd,
                extra="名字匹配是**子串**匹配（name in 控件名）。先看工具实际看到的控件：\n"
                      "      " + _cmd_targets(hwnd))
        # ★ ③ 若函数开头刚做过 SW_RESTORE，控件的 UIA 矩形可能是**还原前**读到的
        #   （最小化时控件树常报空矩形或哨兵值）—— 重新解析一次控件，拿还原后的几何。
        #   只在"确实刚还原过"时才重解，正常路径零额外开销。
        if not dry and _restored:
            try:
                c2 = find_control(hwnd, c.Name)
                if c2 is not None:
                    c = c2
            except Exception:
                pass
        x, y, r, r_ok = _center(c)
        detail = {"控件": c.Name, "类型": c.ControlTypeName, "矩形": r, "中心": [x, y]}
        if (not force_coord) and _has(c, "Invoke"):
            method = "UIA.Invoke（无鼠标事件）"
            if _post_mode:
                log("   ℹ --post 未启用：该控件支持 UIA.Invoke，本来就不产生鼠标事件（比投递更干净）")
            if not dry:
                getattr(c, "GetInvokePattern")().Invoke()
        elif (not force_coord) and _has(c, "SelectionItem"):
            method = "UIA.SelectionItem.Select（无鼠标事件）"
            if _post_mode:
                log("   ℹ --post 未启用：该控件支持 UIA.SelectionItem，本来就不产生鼠标事件")
            if not dry:
                getattr(c, "GetSelectionItemPattern")().Select()
        else:
            # 只能点坐标（或考核强制走坐标）—— 这时矩形必须真实，否则"中心"会算成 (0,0) 点到屏幕左上角
            if not r_ok:
                return _tell_next(
                    5, f"**控件未渲染**：控件「{c.Name}」矩形为空([0,0,0,0])，"
                       f"走坐标路径会点到屏幕左上角 (0,0)", hwnd,
                    extra="这类控件（虚拟化列表项、未滚动到的标签页）必须先滚动进视野；\n"
                          "      或改用支持 UIA 的同类控件（targets 里「已渲染」字段为 true 的那些）。\n"
                          "      下一步：" + _cmd_targets(hwnd))
            wrect = win_info(hwnd)["rect"]
            if not (wrect[0] <= x <= wrect[2] and wrect[1] <= y <= wrect[3]):
                return _tell_next(
                    5, f"**坐标类**：控件「{c.Name}」中心 ({x},{y}) 不在窗口矩形 {wrect} 内", hwnd,
                    extra=_dpi_report(hwnd))
            if _post_mode:
                use_post = _try_post()
            # ★ ③ 置前已在函数开头做过（在坐标校验之前，见那里的注释）——
            #   这里**不再重复一次**：重复调用会在日志里打两遍"自动置前成功"，
            #   而且窗口若在此刻被抢走，这里补一次也来不及（下面的反竞态会拦）。
            # 反竞态：标签栏会滚动/有动画，矩形可能在"读到"与"点到"之间变掉 →
            # 要求隔 0.25 秒两次读数**完全一致**才动手，否则宁可不点。
            r1 = r
            if not dry:
                # ★ M7：反竞态的「等 250 ms」换成「**就绪轮询**」——
                #   连续 %d 帧都判「没变」才动手（稳了就走，不必等满）。
                #   ⛔ 不是把等待砍成 0：轮询有预算上限，没稳就照旧按「矩形不稳定」拒。
                _rdy = _wait_ready(hwnd, _base_img,
                                   budget_ms=int(_settle(250) * 1000))
                c2 = find_control(hwnd, c.Name) or c
                x, y, r2, ok2 = _center(c2)
                if (not ok2) or r2 != r1:
                    return _tell_next(
                        5, f"**反竞态拒绝**：控件「{c.Name}」矩形不稳定 {r1} → {r2}"
                           f"（界面正在滚动/动画）", hwnd,
                        extra="这是刻意设计的护栏：动的手指比读到的坐标慢，宁可不点也不点错。\n"
                              "      下一步：等界面停下（或先 scroll 到位）再重试；"
                              "滚动类控件可改用 UIA Scroll（本工具默认就会优先走它）。")
            if use_post:
                method = "PostMessage 后台投递（不抢前台焦点）"
                if not dry:
                    _tg, _cx, _n, _dp = post_click(hwnd, x, y)
                    detail["投递目标"] = _tg
                    detail["客户区坐标"] = [_cx[0], _cx[1]]
                    detail["深点窗口"] = _dp
            else:
                method = ("SendInput 坐标点击（考核强制）" if force_coord
                          else "SendInput 坐标点击（该控件不支持 Invoke）")
                if not dry:
                    click_at(x, y)
        if not r_ok:
            detail["备注"] = "控件未渲染（矩形为空）；本次走 UIA 模式，不涉及坐标"
    else:
        x, y = xy
        # ★ ③ 这里必须**重新取**矩形：函数开头为置前做过 SW_RESTORE，还原前后
        #   GetWindowRect 差很远（最小化时是 [-32000,…] 哨兵值）。沿用开头那份
        #   info["rect"] 会把"已还原的窗口"按"最小化的哨兵矩形"去校验，照样报 rc=5。
        r = win_info(hwnd)["rect"] if not dry else info["rect"]
        if not (r[0] <= x <= r[2] and r[1] <= y <= r[3]):
            return _tell_next(
                5, f"**坐标类**：点击坐标 ({x},{y}) 不在目标窗口矩形 {r} 内", hwnd,
                extra=_dpi_report(hwnd))
        if _post_mode:
            use_post = _try_post()
        if use_post:
            method = "PostMessage 后台投递（不抢前台焦点）"
            detail.update({"坐标": [x, y]})
            if not dry:
                _tg, _cx, _n, _dp = post_click(hwnd, x, y)
                detail["投递目标"] = _tg
                detail["客户区坐标"] = [_cx[0], _cx[1]]
                detail["深点窗口"] = _dp
        else:
            # ★ ③ 置前已在函数开头做过（见那里的注释），这里不再重复。
            method = "SendInput 坐标点击"
            detail.update({"坐标": [x, y]})

    changed = None
    attempts = 0
    if not dry and use_post:
        # 后台投递：只比对**一次**，绝不重投 —— 重投在"按钮/菜单项"上等于双击（开了又关），
        # 会把观察结果搅乱。所以这条路径不走下面的重试循环。
        # ⚠ 这个信号只是**提示**：WinUI3 这类界面刚还原时有渲染残留，前后图会差几个像素。
        #   判定"吃不吃投递"要以**功能性判据**（菜单有没有开、标签有没有切）为准。
        attempts = 1
        _eff = _wait_effect(hwnd, _base_img,
                             budget_ms=int(_settle(450) * 1000))
        _emit_effect(_eff, hwnd)          # ★ M12：回传证据帧（只传帧，不传结论）
        after = evidence(hwnd, "after")    # ★ 审计图留一张（不再拿它算 md5）
        # ★ M12 阶段三（A）：「变了没」**不再由 md5 判**（那是第二把尺子），
        #   改用**对基线的差分**（与 core 同源）⇒ 只供**重试**用，不进退出码。
        changed = _eff.get("生效")
    elif not dry and method.startswith("SendInput"):
        while True:
            attempts += 1
            click_at(x, y)
            # ★ M7：动作后不再盲等 450 ms —— 轮询到「界面变了」就收工（连续 %d 帧）。
            #   预算用 _settle(450) 换算（旋钮语义不变：AGENTCTL_SETTLE_MS 仍是上限）。
            _eff = _wait_effect(hwnd, _base_img,
                                 budget_ms=int(_settle(450) * 1000))
            _emit_effect(_eff, hwnd)      # ★ M12：回传证据帧（只传帧，不传结论）
            after = evidence(hwnd, "after")    # ★ 审计图留一张（不再算 md5）
            # ★ M12 阶段三（A）：重试判定**仍然拿得到**「变了没」（`_eff["生效"]`），
            #   但它**不进退出码** —— 画面结论由 core 判。
            changed = _eff.get("生效")
            if changed is True or attempts > retry:
                break
            log(f"   ⚠ 第 {attempts} 次点击后页面〔无变化〕→ 自动重试")
            time.sleep(_settle(350))
    else:
        _eff = _wait_effect(hwnd, _base_img,
                             budget_ms=int(_settle(450) * 1000))
        _emit_effect(_eff, hwnd)          # ★ M12：回传证据帧（只传帧，不传结论）
        after = {} if dry else evidence(hwnd, "after")
        # ★ M12 阶段三（A）：UIA.Invoke / SelectionItem 这条路**同样**不再用 md5 判
        #   （改前它自己算 md5，而 before 常常拿不到 ⇒ changed 恒 None ⇒ 这条路**恒返回 rc=8**）。
        changed = _eff.get("生效")
    ev = "" if dry else ("　前图=%s 后图=%s" % (before.get("文件") or "无", after.get("文件") or "无"))
    if not dry and ("拒绝" in before or "拒绝" in after):
        ev += "　⚠ 本次证据不完整"
    if changed is True:
        ev += f"　✔ 页面已变化（共点 {attempts} 次）"
    elif changed is False:
        ev += f"　⚠⚠ 页面无变化（点了 {attempts} 次都没反应）—— **别当成功，去核验**"
        if use_post:
            ev += "　⚠ 后台投递无反应：该应用很可能不消费 PostMessage（预扫结论候选＝不兼容）"
    elif changed is None and not dry:
        # ★ P0-3：None = **判不了**（前后图取不到），不是「没变」。
        #   判据 34（unknown 不当成功）与判据 36（文案与数据一致）都盯着这条：
        #   数据说「判不出」，文案就不许说「画面没变」。
        ev += "　⚠ 判不了（前后图取不到，指纹比不出来）—— **不许当成功**，去核验"
    if attempts > 1:
        detail["重试次数"] = attempts - 1
    log(f"{'（演练）' if dry else ''}🖱 点击 [{method}] 窗口「{info['title']}」 {json.dumps(detail, ensure_ascii=False)}" + ev)
    # ★ M12 阶段三（A）：**act 的 rc 只表示「输入送没送到」** ——
    #   「画面变了没」**不再承载在退出码里** ⇒ 由 core 用**自己的尺子**判：
    #     变了 ⇒ code=0；**确认没变** ⇒ code=8；**没测出来/退化** ⇒ code=5。
    #   ⚠ 重试判定仍然拿得到「变了没」（`_eff["生效"]`，同一把尺子），
    #     但它**只管要不要再点一次**，不体现在 rc 上 ——
    #     这正是「判定权留 core」与「重试仍可用」唯一能同时成立的位置。
    #   ⚠ 改前这里是 `return 0 if (dry or changed is True) else 8`
    #     ⇒ 普通 click 的 code 由 act 的**文件 md5** 裁决（第二把尺子），
    #        且 `degraded` 被丢掉（不会落 code=5）—— M12 阶段三要补的正是这两条。
    return 0


def scroll_pct(hwnd):
    """读回文档的垂直滚动百分比（核验滚动是否真发生）。None = 该控件不支持。"""
    if not _need_perceive("读滚动位置"):
        return None
    c = doc_control(hwnd)
    if c is None:
        return None
    try:
        return c.GetScrollPattern().VerticalScrollPercent
    except Exception:
        return None


# ═════════ 2026-10-17（F2 · 路线 P）：不抢前台的 PostMessage 输入 ═════════
# 背景：真机上 ensure_foreground 被 Windows 前台锁拒（rc=5），而 AttachThreadInput
#   那套在本文件 1173-1175 早被判「废」（抢焦点 + 输入队列脏状态）⇒ 只做本路线。
#   投递成功 ≠ 生效：一律用 evidence 前后指纹判，判不出来就报"未验"，判出没变就
#   返回可执行提示（要么让用户点一下窗口、要么接受没有后台滚动的窗口类型）。
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101


def _post_wheel(hwnd, info, amount):
    """PostMessage 滚轮。lParam 用**屏幕坐标**（WM_MOUSEWHEEL 的规定），wParam 高字=delta（正=向上）。"""
    r = info.get("rect") or (0, 0, 0, 0)
    x, y = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
    delta = 120 * int(amount)
    wparam = ((delta & 0xFFFF) << 16) & 0xFFFFFFFF
    lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    # ★ 2026-10-17（DSH 补）：只投顶层窗**常常无效** —— WinForms/经典窗口把滚轮交给
    #   **光标下的那个子控件**。所以再向 WindowFromPoint 拿到的子窗投一次（多投无害）。
    targets = [int(hwnd)]
    try:
        pt = wt.POINT(x, y)
        ch = user32.WindowFromPoint(pt)
        if ch and int(ch) != int(hwnd):
            targets.append(int(ch))
    except Exception:
        pass
    ok = False
    for h in targets:
        try:
            ok = bool(user32.PostMessageW(h, WM_MOUSEWHEEL, wparam, lparam)) or ok
        except Exception as e:
            log(f"   PostMessage(WM_MOUSEWHEEL) 失败 hwnd={h}：{str(e)[:60]}")
    log(f"🖱 滚动 [PostMessage 滚轮 delta={delta} 屏幕({x},{y})，**不碰前台**] "
        f"窗口「{info.get('title', '')}」 投给={targets} 投递={ok}")
    return ok


def _post_keys(hwnd, vk, ctrl, times, info=None):
    """PostMessage 按键（WM_KEYDOWN/WM_KEYUP）。不碰前台；Chromium 通常不认这条路。

    ★ 2026-10-17（DSH 补）：顶层窗常忽略 WM_KEY*，真正处理按键的是**带焦点的子控件**
      ⇒ 除了顶层 hwnd，再向"窗口中心处 WindowFromPoint 拿到的子窗"投一次（与滚轮同一招）。
    """
    targets = [int(hwnd)]
    try:
        r = (info or {}).get("rect") or (0, 0, 0, 0)
        pt = wt.POINT((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)
        ch = user32.WindowFromPoint(pt)
        if ch and int(ch) != int(hwnd):
            targets.append(int(ch))
    except Exception:
        pass
    ok = False
    for h in targets:
        try:
            one = True
            if ctrl:
                one = bool(user32.PostMessageW(h, WM_KEYDOWN, 0x11, 0)) and one
            for _ in range(max(1, int(times))):
                one = bool(user32.PostMessageW(h, WM_KEYDOWN, int(vk), 0)) and one
                one = bool(user32.PostMessageW(h, WM_KEYUP, int(vk), 0)) and one
            if ctrl:
                one = bool(user32.PostMessageW(h, WM_KEYUP, 0x11, 0)) and one
            ok = ok or one
        except Exception as e:
            log(f"   PostMessage(WM_KEY*) 失败 hwnd={h}：{str(e)[:60]}")
    log(f"⌨ 按键 [PostMessage vk=0x{int(vk):X}{' +Ctrl' if ctrl else ''} ×{times}，"
        f"**不碰前台**] 投给={targets} 投递={ok}")
    return ok


def _changed_by_evidence(hwnd, tag):
    """拍前后两张证据图，返回 True/False/None(判不了)。用于"投递成功≠生效"。"""
    try:
        b = evidence(hwnd, "post_" + tag + "_before")
        time.sleep(_settle(350))
        a = evidence(hwnd, "post_" + tag + "_after")
        f0 = _img_fp(b.get("文件") or "")
        f1 = _img_fp(a.get("文件") or "")
        if f0 is None or f1 is None:
            return None
        return f1 != f0
    except Exception:
        return None


def _post_hint(kind, hwnd):
    """后台输入没生效时的可执行提示（不抢前台是我们自己定的规矩）。"""
    return _tell_next(
        5,
        f"后台{kind}已投递但**窗口没反应**（该类窗口忽略 WM_* 消息，常见于 Chromium/Electron）。"
        "可选两条路：① 请用户把该窗口点到前台后重试（本工具不会自己抢前台）；"
        "② 该窗口若有 UIA ScrollPattern，改用 UIA 路线"
        + ("（滚动：本工具会先试 UIA，失败才走消息）" if kind == "滚动" else ""),
        hwnd)


def do_scroll(hwnd, amount, dry=False, force_wheel=False):
    info, why = check_target(hwnd)
    if why:
        return _tell_deny(why, hwnd, extra="（滚动动作）")
    # 模态对话框前置检查（2026-09-18 新增）：被挡就拒绝，别做出"看着成功其实无效"的动作
    _mb = require_no_modal(hwnd, "滚动")
    if _mb:
        return _mb
    try:
        gate.require(gate.B, f"滚动 {amount}")
    except gate.GateDenied as e:
        return _gate_denied(e, hwnd, gate.B, "滚动")
    try:
        auto, root = _uia(hwnd)
    except _UiATimeout as e:
        # ★ 2026-10-17：UIA 超时**不许炸**（旧版会 traceback ⇒ rc=1 ⇒ 被读成未知返回码）。
        log(f"   UIA 建构超时（{str(e)[:60]}）⇒ 直接走路线 P（PostMessage 滚轮）")
        root = None
    except Exception as e:
        log(f"   UIA 建构失败（{str(e)[:60]}）⇒ 直接走路线 P")
        root = None
    # 优先找支持 Scroll 的控件
    target = None

    def walk(c, d=0):
        nonlocal target
        if target is not None or d > 8:
            return
        try:
            if _has(c, "Scroll") and c.ControlTypeName in ("DocumentControl", "PaneControl", "ListControl"):
                target = c
                return
        except Exception:
            pass
        try:
            for ch in c.GetChildren():
                walk(ch, d + 1)
        except Exception:
            pass

    if root is not None:
        walk(root)
    if target is not None and not force_wheel:
        try:
            pat = target.GetScrollPattern()
            amt = (auto.ScrollAmount.LargeIncrement if amount < 0
                   else auto.ScrollAmount.LargeDecrement)
            if not dry:
                pat.Scroll(auto.ScrollAmount.NoAmount, amt)
            log(f"{'（演练）' if dry else ''}🖱 滚动 [{target.ControlTypeName} 走 UIA Scroll，无鼠标事件] "
                f"窗口「{info['title']}」")
            return 0
        except Exception as e:
            log(f"   UIA Scroll 不可用（{str(e)[:60]}），改用鼠标滚轮")
    # ★ 路线 P（2026-10-17 F2）：**先不碰前台**，用 PostMessage 滚轮试一次
    if not dry:
        _b = evidence(hwnd, "post_wheel_before")
        if _post_wheel(hwnd, info, amount):
            time.sleep(_settle(400))
            _a = evidence(hwnd, "post_wheel_after")
            _f0, _f1 = _img_fp(_b.get("文件") or ""), _img_fp(_a.get("文件") or "")
            _ch = None if (_f0 is None or _f1 is None) else (_f1 != _f0)
            if _ch:
                # ★ 2026-10-19（判据 32①）：滚轮同病 —— 「页面动了」不能
                #   证明「滚轮生效了」（页面可能自己在动）。
                log("   ⚠ 后台滚轮投递了但画面变了 不证明**滚轮生效**"
                    "（页面可能自己在动）")
            else:
                log("   ⚠ 后台滚轮投递了但画面%s ⇒ 落回前台兜底"
                    % ("判不了（unknown）" if _ch is None else "没变"))
    # ★ ③ 动作前自动置前：滚轮事件发给**光标下的窗口**，不在前台时不可控
    _fg = ensure_foreground(hwnd, "滚动(滚轮)", dry=dry)
    if _fg:
        return _fg
    x, y, rr, ok_rect = _center(root)
    if not ok_rect:                       # 根控件矩形为空时，退回窗口矩形中心
        r = info["rect"]
        x, y = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
    r = info["rect"]
    if not (r[0] <= x <= r[2] and r[1] <= y <= r[3]):
        return _tell_next(
            5, f"**坐标类**：滚轮落点 ({x},{y}) 不在窗口矩形 {r} 内", hwnd,
            extra=_dpi_report(hwnd))
    user32.SetCursorPos(x, y)
    time.sleep(0.12)
    if not dry:
        # 注意符号：MOUSEEVENTF_WHEEL 的 data **正值 = 向上滚**（远离用户）。
        # 所以"下滚 amount 格"（amount 为负）要发负值 —— 写成 wheel(-120*amount) 会方向反掉，
        # 表现是"数值一点不动"，因为已经在顶了。这个错被判据抓出来过一次。
        wheel(120 * amount)
    log(f"{'（演练）' if dry else ''}🖱 滚动 [SendInput 滚轮 {amount} 格，光标置于 ({x},{y})] 窗口「{info['title']}」")
    return 0


# ─────────────────── D17 阶段二（2026-10-10）：无响应窗口前置 fail-closed ──────
# 根因（事件 #1043，DSH 逐调用计时实测钉死，不是推测）：
#   `doc_control` → `_uia` → `auto.ControlFromHandle` 对**不响应**（没在抽消息循环）
#   的窗口会**无限等待** —— 实测 >40s 不返回，只能 kill。
#   而同一窗口上 `GetWindowText`/`GetClassName` 0.003s、`win_info` 0.085s、
#   `modal_blocker` 0.026s、`check_target` 0.043s、`IsHungAppWindow` 0.000s **全部秒回**
#   （Windows 给的是缓存标题）。⇒ 卡点唯一且明确，就落在 UIA 那一步。
#
# 危害：闸门**已经放行**了，动作却永远不返回 ⇒ 真遇到无响应的应用，整个 act 会挂住。
#
# 修法：**进 UIA 之前**先问一句 `IsHungAppWindow`，命中就 fail-closed 拒绝并留痕。
#   「不进 UIA」是这件事的关键 —— 进去就出不来了，守卫必须落在 `_uia` 前面。
_HUNG_SEEN = set()
UIA_HANG_TIMEOUT = 6.0     # ControlFromHandle 的超时（秒）。6s 够快，
                           # 又不至于把「慢但正常的大窗口」误判成挂死。


class _UiATimeout(Exception):
    pass


def is_hung(hwnd):
    # 该窗口是否「不响应」（没在抽消息）。实测 0.000s，随便问都不卡。
    try:
        return bool(user32.IsHungAppWindow(wt.HWND(int(hwnd))))
    except Exception:
        return False


def hung_guard(hwnd, what="读文档"):
    """无响应窗口守卫。命中返回 True（**调用方必须据此拒绝**，不要继续往下走）。"""
    try:
        h = int(hwnd)
    except Exception:
        return False
    if not is_hung(h):
        return False
    _HUNG_SEEN.add(h)
    log(f"⛔ 目标窗口不响应（IsHungAppWindow=1），**fail-closed 拒绝「{what}」，不进 UIA**"
        f"（实测 ControlFromHandle 对这种窗口会无限等待 >40s，进去就挂死）")
    return True


DOC_MAX_DEPTH = 12          # 原为 8。**够不到浏览器页面文档**（实测正文在 9–11 层）。
DOC_MAX_NODES = 5000        # 安全阀：UIA 树超大时别把时间/内存耗死。


def doc_control(hwnd):
    # ★ D17 阶段二守卫：必须在 `_uia` **之前**。见上面 hung_guard 的说明。
    if hung_guard(hwnd, "取文档控件"):
        return None
    _auto, root = _uia(hwnd)

    # ★ A2-F3：改成 **BFS，取「最浅」的 DocumentControl**。
    #
    # 为什么不能只把 8 改成 12：原实现是 **DFS + 「找到就全局停」**，拿到的是
    # 「先序遍历里遇到的第一个」，**不保证是最浅的**。UIA 树里越浅的越可能是
    # 「这个窗口的正文」（框架性控件），越深的多半是嵌套内容（比如 iframe 内的文档）。
    # 一旦取错，`read_doc` / `scroll_pct` / `read_selection` **三个核验同时废**
    # —— 这就是 A2-F3 「一处取错三个全废」的由来。
    # BFS 天然保证「先找到的就是最浅的」，**加深也不会因此取错**。
    #
    # 深度上限的来由：Edge 页面正文实测在 9–11 层，8 不够；12 留一层余量。
    # 调深容易、调浅不会更安全（只会更早放弃），所以宁深勿浅 —— 但**前提是 BFS**，
    # 否则深了反而会取错。**这两件事必须一起改，只改数字是危险的。**
    q = collections.deque([(root, 0)])
    seen = 0
    while q:
        c, d = q.popleft()
        seen += 1
        if seen > DOC_MAX_NODES:
            log(f"⛔ UIA 树已遍历 {seen} 个节点仍未找到文档控件，放弃"
                f"（上限 {DOC_MAX_NODES}，防在超大树上耗死）")
            return None
        try:
            if c.ControlTypeName == "DocumentControl":
                return c
        except Exception:
            pass
        if d >= DOC_MAX_DEPTH:
            continue
        try:
            for ch in c.GetChildren():
                q.append((ch, d + 1))
        except Exception:
            pass
    return None


def read_doc(hwnd):
    """读回文档真实内容（核验用，只读）。返回 (控件, 文本 或 None, 说明)。"""
    if not _need_perceive("读文档内容"):
        return None, None, "未获授权"
    c = doc_control(hwnd)
    if c is None:
        # ★ D17：区分「窗口不响应」与「真找不到控件」—— 两者都返回 None，
        #   但对调用者是完全不同的结论，不能都报成「找不到文本编辑区」。
        try:
            hung = int(hwnd) in _HUNG_SEEN
        except Exception:
            hung = False
        return None, None, ("目标窗口不响应（IsHungAppWindow=1），已拒绝读文档"
                            if hung else "找不到文本编辑区")
    try:
        return c, c.GetValuePattern().Value, "ValuePattern"
    except Exception:
        pass
    try:
        return c, c.GetTextPattern().DocumentRange.GetText(-1), "TextPattern"
    except Exception as e:
        return c, None, f"读不到文本：{str(e)[:60]}"


def read_selection(hwnd):
    """读回当前**选中的文本** —— 拖拽选字之后，这就是可核验的结果。"""
    if not _need_perceive("读选中文本"):
        return None
    c = doc_control(hwnd)
    if c is None:
        return None
    try:
        return "".join(r.GetText(-1) for r in c.GetTextPattern().GetSelection())
    except Exception:
        return None


# ─────────────────── 动作 ───────────────────
def do_focus(hwnd, dry=False):
    """把白名单窗口切到前台（打字/拖拽的前置条件）。属于动作，记 B 级。"""
    info, why = check_target(hwnd)
    if why:
        return _tell_deny(why, hwnd, extra="（切前台动作）")
    try:
        gate.require(gate.B, "切前台")
    except gate.GateDenied as e:
        return _gate_denied(e, hwnd, gate.B, "切前台")
    if not dry:
        try:
            user32.ShowWindow(hwnd, 9)            # SW_RESTORE
        except Exception:
            pass
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(_settle(500))
    now_fg = win_info(hwnd)["foreground"]
    log(f"{'（演练）' if dry else ''}🎯 切前台 [{info['title']}] → 现在前台={now_fg}")
    # ★ 2026-10-12 补（C6）：此处**不许返回 6** —— rc=6 是模态闸的**专用码**，
    #   只由 require_no_modal 产生（core.py 把它映射成 blocked_by=modal_dialog）。
    #   原先这里对"没置到前台"返回 6 ⇒ core.py 误报成「被模态对话框挡住…先关掉那个
    #   对话框再重试」，agent 便对着一个并不存在的对话框绕圈（C6 §5 查出来的撞车）。
    #   置前台失败的语义是「前台/窗口不稳定」，与 CODE_UNSTABLE 的既有含义正对 ⇒ 返回 5。
    return 0 if (dry or now_fg) else 5


def _norm_nl(s):
    """核验前统一换行表示。

    2026-10-10 晚实测：记事本 UIA 的 ValuePattern 把换行回读成**孤立的 \\r**
    （既不是 \\n 也不是 \\r\\n），所以多行文本跟原始输入做**原样**比较必然不相等，
    do_type 会一路报「❌ 读回不一致」，把人往「打错了」的方向带 ——
    实测字是全对的（229/229，只有 5 个换行符表示不同）。

    这里**只**放行换行表示这一种差异，其余任何字符不一致照旧判 FAIL。
    """
    return s.replace("\r\n", "\n").replace("\r", "\n")


def do_type(hwnd, text, dry=False, allow_if_starts_with=None, force_send=False,
             scan_code=False, focus=False, nl=None):
    """C 级：打字（含中文走 Unicode）。

    两种模式：
      ① **文档模式**（默认，安全阀拉满）：必须有 UIA 文本区，且**只允许往空文档（或训练靶）打**，
         打完整字符回读核验 —— 防的是"把字打进用户的真实文件"。
    ② **网页表单模式**（`--force-send`，2026-09-19 新增）：目标没有 UIA 文本区时（微信内置浏览器
       之类），直接发键。**核验从"UIA 回读"降级为"外部截图"**，并且**没有"空文档"这道保险**
       —— 所以仅在**用户明确授权的表单填写**场景使用，命令行必须显式写 `--force-send`。

    scan_code=True（命令行 `--scan-code`）走**扫描码注入**，给 VirtualBox 这类
    PS/2 键盘模拟的目标用（见 send_text_scan 的注释）。默认 False。

    ③ **无元素直发**（`--focus`，2026-10-18 P0-B 新增）：**压根不找 UIA 元素**，
       对前台焦点窗逐字符 `SendInput(KEYEVENTF_UNICODE)`（send_text 的默认路径）。
       为什么要有第三种：①② 都以「UIA 文本区」为入口，而 QQ 主窗这类
       **一个可定位元素都没有**（今天现场 ctl_type 直接 code=7、data.raw 无元素），
       ①② 全都走不通 ⇒ 输入通道整条断掉。
       ★ 与 ② 的差别不是「更宽松」而是**换入口**：② 先试 read_doc、成功就仍走
         文档模式的安全阀；③ 连 read_doc 都不调，不存在"文档非空"的判断。
       ★ 中文 / emoji / 密码框通用：只发字符、**不回读**（密码框回读也没意义）。
       ★ 仍要求目标窗在前台：ensure_foreground 失败就是现有 rc=5 口径
         （"没前台，可置前后解决"），**不静默改成盲打**。
       ★ 失败要能分开：「没前台」⇒ rc=5；「发了但画面没变」⇒ rc=8，绝不当成功。
       ★ 换行默认 `shift_enter`：这一路的场景（QQ/聊天框/单行输入框）里
         Enter = **提交**，按 enter 会把一段任务文本切成好几条各自发出去
         （send_text 的注释里记着这个坑，WorkBuddy 那次 1127 字被拆成多条）。
         要真文档语义显式传 `--nl enter`。
    """
    info, why = check_target(hwnd)
    if why:
        return _tell_deny(why, hwnd, extra="（打字动作）")
    # 模态对话框前置检查（2026-09-18 新增）：被挡就拒绝，别做出"看着成功其实无效"的动作
    _mb = require_no_modal(hwnd, "打字")
    if _mb:
        return _mb
    try:
        gate.require(gate.C, f"打字 {len(text)} 字")
    except gate.GateDenied as e:
        return _gate_denied(e, hwnd, gate.C, "打字")
    # ★ ③ 动作前自动置前（不再因"不在前台"直接拒绝）
    _fg = ensure_foreground(hwnd, "打字", dry=dry)
    if _fg:
        return _fg
    # ── ③ 无元素直发（--focus）：**在 read_doc 之前**分叉，一个 UIA 元素都不找。
    if focus:
        _PF_LAST[0] = time.perf_counter()
        nl_mode = nl or "shift_enter"
        if nl_mode not in ("enter", "shift_enter", "space"):
            return _tell_next(2, "换行口径只能是 enter / shift_enter / space，收到 %r"
                              % nl_mode, hwnd)
        if dry:
            log("（演练）⌨ 打字 [无元素直发 %d 字，换行=%s] 窗口「%s」"
                % (len(text), nl_mode, info["title"]))
            return 0
        _pf("gate+fg")
        b0 = evidence(hwnd, "focussend0")
        _pf("evidence.before")
        if scan_code:
            n_scan, fb_chars = send_text_scan(text)
        else:
            n_scan, fb_chars = 0, []
            send_text(text, nl=nl_mode)
        _pf("send_text")
        time.sleep(_settle(450))
        _pf("settle")
        b1 = evidence(hwnd, "focussend1")
        _pf("evidence.after")
        f0, f1 = _img_fp(b0.get("文件") or ""), _img_fp(b1.get("文件") or "")
        changed = None if (f0 is None or f1 is None) else (f1 != f0)
        ev = ("" if changed is None
              else ("　✔ 画面已变化" if changed else "　⚠⚠ 画面无变化——别当成功"))
        log("⌨ 打字 [无元素直发 %d 字（含中文=%s）换行=%s%s] 窗口「%s」"
            "　前图=%s 后图=%s%s"
            % (len(text), any(ord(x) > 127 for x in text), nl_mode,
               " 扫描码" if scan_code else "", info["title"],
               b0.get("文件") or "无", b1.get("文件") or "无", ev))
        if scan_code:
            log("   扫描码注入：%d/%d 字符走了扫描码；回退 UNICODE 的字符：%s"
                % (n_scan, len(text), "".join(fb_chars) or "无"))
        # ★ 核验降级说明必须留痕：无元素直发**没有 UIA 回读**可比，
        #   「画面没变」判 8 而不是默默当成功。
        log("   核验：前后截图比对（无 UIA 回读可比）；判不了=unknown 不算失败")
        return 0 if (changed is True) else 8
    c, before, how = read_doc(hwnd)
    if before is None and not force_send:
        return _tell_next(4, f"打不了字：{how}", hwnd,
                          extra="打字需要 UIA 文本区来做「只往空文档打」的安全阀。\n"
                                "      若目标确实没有 UIA 文本区（微信内置浏览器之类），"
                                "才用网页表单模式（**用户须明确授权**）：加 --force-send")
    if before is None and force_send:
        log("⚠ 目标无 UIA 文本区 → 走【网页表单模式】：直接发键，"
            "**不做 UIA 回读核验**，改用前后截图比对（2026-09-19 补，此前是盲打无核验）")
        b0 = {} if dry else evidence(hwnd, "typesend0")
        before = ""
        n_scan, fb_chars = 0, []
        if scan_code:
            n_scan, fb_chars = send_text_scan(text)
        else:
            send_text(text)
        time.sleep(_settle(450))
        b1 = {} if dry else evidence(hwnd, "typesend1")
        f0, f1 = _img_fp(b0.get("文件") or ""), _img_fp(b1.get("文件") or "")
        changed = None if (f0 is None or f1 is None) else (f1 != f0)
        ev = "" if changed is None else ("　✔ 页面已变化" if changed else "　⚠⚠ 页面无变化——别当成功，去核验")
        log(f"{'（演练）' if dry else ''}⌨ 打字 [网页表单模式 {len(text)} 字"
            f"{' 扫描码' if scan_code else ''}"
            f"{'，含中文' if any(ord(x) > 127 for x in text) else ''}] 窗口「{info['title']}」"
            f"　前图={b0.get('文件') or '无'} 后图={b1.get('文件') or '无'}" + ev)
        if scan_code:
            log(f"   扫描码注入：{n_scan}/{len(text)} 字符走了扫描码；"
                f"回退 UNICODE 的字符：{''.join(fb_chars) or '无'}")
        return 0 if (dry or changed is True) else 8
    # ★ 最关键的一道保险：只往空文档（或带训练标记的靶文档）里打字，绝不可能污染用户的真实文件
    if before.strip() != "" and not (allow_if_starts_with and before.startswith(allow_if_starts_with)):
        # ① rc=7 必须**自带下一步**：报出当前字符数 + 清空命令原文。
        #   现场（证据 §三 错 4→"改完接着打"）当时只回了"文档非空"，调用方得自己
        #   摸索出"Ctrl+A + Delete"这条合规通路 —— 那是工具本该给的。
        first = (before[:40].replace("\n", "␊").replace("\r", "␍"))
        return _tell_next(
            7,
            f"打不了字：目标文档里已有 **{len(before)} 个字符**，且不是训练靶",
            hwnd,
            extra=("  文档开头 40 字：「%s」\n"
                   "  这道护栏防的是「把字打进用户的真实文件里」，**不要绕**。\n"
                   "  合规清空命令原文（两条都可用，推荐先 read 再清）：\n"
                   "      %s\n"
                   "      python -X utf8 act.py keys --hwnd %s --vk 0x41 --ctrl"
                   "     # Ctrl+A 全选\n"
                   "      python -X utf8 act.py keys --hwnd %s --vk 0x08 --times 3"
                   "   # 退格清空（times 给足即可）\n"
                   "  清完用 %s 验字符数为 0，再重跑本次打字。"
                   % (first, _cmd_read(hwnd), hwnd, hwnd, _cmd_read(hwnd))))
    if not dry:
        send_text(text)
    time.sleep(_settle(450))
    _, after, _ = read_doc(hwnd)
    want = before + text
    ok = (after == want) or (_norm_nl(after) == _norm_nl(want))
    nl_only = (after != want)
    mark = ("✅ 读回逐字符一致（仅换行表示不同）" if nl_only
            else "✅ 读回逐字符一致") if ok else "❌ 读回不一致"
    log(f"{'（演练）' if dry else ''}⌨ 打字 [{len(text)} 字"
        f"{'（含中文）' if any(ord(x) > 127 for x in text) else ''}] "
        f"窗口「{info['title']}」 {mark} "
        f"（{len(before)} → {len(after) if after else 'None'} 字）")
    return 0 if (dry or ok) else 8


def do_drag(hwnd, frm, to, dry=False):
    """D 级：在文本编辑区里拖拽选字。结果用"读回选中的文本"来核验。"""
    info, why = check_target(hwnd)
    if why:
        return _tell_deny(why, hwnd, extra="（拖拽动作）")
    # 模态对话框前置检查（2026-10-12 补，C6）：与 click/fill/scroll/type 同一道闸。
    #   位置与它们一致：**在 gate.require / ensure_foreground / drag() 之前** ——
    #   被挡就返回 6，不消耗证/额度，也不置前、不合成任何鼠标事件。
    _mb = require_no_modal(hwnd, "拖拽")
    if _mb:
        return _mb
    try:
        gate.require(gate.D, f"拖拽 {frm}→{to}")
    except gate.GateDenied as e:
        return _gate_denied(e, hwnd, gate.D, "拖拽")
    # ★ ③ 动作前自动置前 —— **必须在取窗口矩形之前**：
    #   2026-10-10 实测：窗口最小化时 GetWindowRect 返回哨兵矩形
    #   [-32000,-32000,…]，"端点不在窗口矩形内"会先炸，把真正的原因
    #   （窗口没还原）盖成一条坐标类 rc=5。判据③要的是"丢后台也一次成功"，
    #   所以先还原、再量 rect、再校端点。
    #   现场原貌（证据 §三 错 8）就是"窗口丢前台就开拖 ⇒ 两次 rc=5"。
    _fg = ensure_foreground(hwnd, "拖拽", dry=dry)
    if _fg:
        return _fg
    if not dry:
        info, _why2 = check_target(hwnd)
        info = info or win_info(hwnd)      # 还原后重取矩形（还原前是哨兵值）
    r = info["rect"]
    for p in (frm, to):
        if not (r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]):
            # ① rc=5 坐标类：报 DPI 缩放因子 + 坐标口径 + 换算
            #   现场（证据 §三 错 5）：「拖拽用逻辑坐标 ⇒ rc=5 端点不在窗口矩形内」，
            #   当时根本不知道 act.py 用物理像素、也不知道本机缩放是 1.5×。
            return _tell_next(
                5, f"**坐标类**：拖拽端点 {p} 不在窗口矩形 {r} 内", hwnd,
                extra=_dpi_report(hwnd) +
                "\n  另注：拖拽两端**都**必须在窗口矩形内；"
                "拖窗口边框请参考 targets 输出的「可拖带」。")
    if not dry:
        drag(frm, to)
    time.sleep(_settle(400))
    sel = read_selection(hwnd) if not dry else None
    log(f"{'（演练）' if dry else ''}✋ 拖拽 [{frm} → {to}] 窗口「{info['title']}」"
        + ("" if dry else f"　选中了 {len(sel) if sel is not None else '?'} 字："
                          f"「{(sel or '')[:30]}」"))
    return 0 if (dry or (sel is not None and len(sel) > 0)) else 8


def main():
    # ── 启动自检闸门（派单 ②）─────────────────────────────────────────────
    # ★ 真正的拦截已挪到**模块顶部**（`raise SystemExit(_selftest_or_die())`）：
    #   放这里太晚了 —— 用托管 Python 跑时，第 1 行 `import win32gui` 就把进程打死，
    #   本函数根本执行不到。（2026-10-10 派单要求的正是「第一行就报全」。）
    #   这里只留一道**兜底**：万一将来有人把顶部那道闸拆了，至少不会静默带病运行。
    if _SELFTEST_MISS:
        return _selftest_or_die()
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?",
                    choices=["targets", "click", "scroll", "focus", "type", "drag", "read",
                             "keys", "fill", "approve"],
                    help="动作名；纯只读诊断（--snap-check）可不给")
    # ★ --hwnd 从 required 改 nargs="?"：① 的 --snap-check 是**纯文件只读诊断**
    #   （只看快照 json，不碰任何窗口），逼它编一个 --hwnd 是没道理的。
    #   给所有动作保留"不给就报 2 + 提示"的原有语义（见下面的入口守卫）。
    ap.add_argument("--hwnd", type=int, default=None)
    ap.add_argument("--name")
    ap.add_argument("--xy")
    ap.add_argument("--amount", type=int, default=-3)
    ap.add_argument("--text")
    ap.add_argument("--to")
    ap.add_argument("--dry-run", action="store_true")
    # ── 2026-10-16（窗K · P0）approve：给**终端**记一条 30 分钟临时放行 ──
    #   ★ 这不是白名单口子，是 code 7 那条「问一次、管 30 分钟」的下半段：
    #     core.py 的 _ask_then_retry 在用户**于弹窗里当场点了同意**之后，调的就是
    #     "act.py approve --process X --class Y"（见 core.py:_legacy_approve_target）。
    #     参数名与那一处**逐字对齐**，改了就接不上。
    #   ★ 它**只能**给终端族签字：授权弹窗 / UAC / 系统设置一律拒签并留事件。
    ap.add_argument("--process", help="approve：要放行的属主进程名")
    ap.add_argument("--class", dest="cls",
                    help="approve：要放行的窗口类名（与进程名一起构成这一对身份）")
    ap.add_argument("--minutes", type=int, default=TERMINAL_PASS_MAX_MIN,
                    help="approve：放行几分钟（硬上限 %d 分钟，给再大也压回来）"
                         % TERMINAL_PASS_MAX_MIN)
    ap.add_argument("--coord", action="store_true", help="点击：强制走坐标路径（练 SendInput 退路）")
    ap.add_argument("--retry", type=int, default=0,
                    help="点击：页面无变化时自动重试几次（默认 1；**幂等动作才用**，切换类按钮设 0）")
    ap.add_argument("--vk", default="0x41",
                    help="keys：虚拟键码**或键名**（2026-10-18 P0-B 补了 a~z / A~Z / 0~9 / F1~F12 "
                         "/ Space·Enter·Tab·Esc·Backspace·Delete·Insert·Home·End·PageUp·PageDown"
                         "/ 方向键）。数值写法一字未改（0x41 / 65 都还收），默认 0x41 = A，"
                         "配 --ctrl 即 Ctrl+A 全选；Ctrl+V 就写 --vk v --ctrl。")
    # ★ 无元素直发：压根不找 UIA 元素，对前台焦点窗逐字符 SendInput(KEYEVENTF_UNICODE)。
    #   今天 QQ 现场的病根就是这一条：ctl_type 在没有可定位元素的窗口上直接 code=7。
    ap.add_argument("--focus", action="store_true",
                    help="type：**无元素直发** —— 不找 UIA 元素，对**前台焦点窗**逐字符 "
                         "SendInput(KEYEVENTF_UNICODE)（中文/emoji/密码框通用，不回读）。"
                         "仍要求目标窗在前台：置不上去就是现有 rc=5。"
                         "⚠ 没有「文档非空」那道保险，回执里如实写明。")
    ap.add_argument("--nl", choices=["enter", "shift_enter", "space"], default=None,
                    help="type --focus：换行怎么发。默认 shift_enter —— "
                         "无元素直发的场景（聊天框/单行输入框）里 Enter = **提交**，"
                         "按 enter 会把一段任务文本切成多条各自发出去。"
                         "真文档语义显式写 --nl enter。")
    ap.add_argument("--ctrl", action="store_true", help="keys：与 Ctrl 组合")
    ap.add_argument("--times", type=int, default=1, help="keys：连按几次（清空字段时配 0x08 用）")
    ap.add_argument("--no-clear", action="store_true",
                    help="fill：不要先清空（默认会 Ctrl+A 覆盖旧值；仅在目标本来就是空字段时才加）")
    ap.add_argument("--force-send", action="store_true",
                    help="打字：**网页表单模式** —— 目标没有 UIA 文本区时，直接发键，核验改由外部截图负责")
    ap.add_argument("--scan-code", action="store_true",
                    help="打字/按键：**扫描码注入** —— 用 MapVirtualKey 换真实 scancode 再发。"
                         "VirtualBox 等 PS/2 键盘模拟的目标必需（没扫描码的事件它直接丢弃）。"
                         "不影响鼠标；中文等换不出扫描码的字符会自动回退原路径并记账")
    ap.add_argument("--compact", action="store_true", help="targets：紧凑文本，一行一个控件")
    # ── ① rc=5 快照类：只读诊断（派单 ① 明列）─────────────────────────────
    ap.add_argument("--snap-check", action="store_true",
                    help="① 只读：诊断「前后快照路径集」是否可比（rc=5 快照类）。"
                         "列出快照目录、两侧各自拿到的路径集、缺 --paths 的那一侧，"
                         "并给出可粘贴的重拍命令。**不碰窗口、不过闸、不扣额度**。")
    ap.add_argument("--snap-before", default="before", metavar="TAG",
                    help="--snap-check：基准快照 tag（默认 before，即 验动作.py 的 --before）")
    ap.add_argument("--snap-after", default="after", metavar="TAG",
                    help="--snap-check：对比快照 tag（默认 after，即 验动作.py 的 --tag）")
    ap.add_argument("--post", action="store_true",
                    help="click：**后台投递模式** —— 本来要走坐标的那次点击改用 PostMessage 投给目标窗口，"
                         "**不抢前台焦点**；按 post_compat.json 分档，不消费/未登记则自动回退 SendInput 并留一行原因")
    ap.add_argument("--post-force", action="store_true",
                    help="click：后台投递且**跳过兼容表**（仅用于兼容性预扫；仍过闸 + 白名单 + 模态检查）")
    a = ap.parse_args()

    # ── 入口守卫（--hwnd 由 required 改为可选之后的兜底）────────────────────
    # ★ --snap-check 是**纯文件只读诊断**：它只看快照 json，跟窗口无关，
    #   所以放行「无 hwnd」；其余动作仍然必须有 hwnd —— 少了就走 ① 的 rc=2 提示，
    #   而不是 argparse 的英文 usage 崩（调用方要的是"下一步"，不是 "the following
    #   arguments are required"）。
    if a.snap_check:
        pass
    elif a.action == "approve":
        # approve 不碰任何窗口（只往 _auth/terminal_pass.json 记一条 + 写事件），
        # 所以不逼它编一个 --hwnd —— 与 --snap-check 同理。
        pass
    elif not a.action:
        return _tell_next(2, "缺动作名（且没给 --snap-check）", a.hwnd)
    elif a.hwnd is None:
        return _tell_next(2, "缺 --hwnd（除 --snap-check 外所有动作都必须给）", a.hwnd)

    if (a.post or a.post_force) and a.action != "click":
        print(f"⛔ --post / --post-force 只在 click 上生效：{a.action} 没有「投递一次左键」这回事。")
        return 2

    # ── approve（窗K · P0）：签发终端的 30 分钟临时放行 ──
    #   放在**任何窗口动作之前**、不碰 gate：它本身不是一次动作，
    #   而是「用户已经在弹窗里点了同意」之后，把那个同意**落成一条有时限的记录」。
    if a.action == "approve":
        ok, why = grant_terminal_pass(a.process or "", a.cls or "", a.minutes)
        print(("✅ " if ok else "⛔ ") + why)
        print("   记录文件：%s" % TERMINAL_PASS_FILE)
        print("   事件文件：%s（签发/拒签都写一行，到点自动失效，无需手工清理）"
              % TERMINAL_PASS_LOG)
        return 0 if ok else 3

    if a.action == "fill":
        if not a.xy:
            print("fill 需要 --xy x,y")
            return 2
        xy = tuple(int(v) for v in a.xy.split(","))
        return do_fill(a.hwnd, xy, a.text or "", a.dry_run, clear=not a.no_clear)

    if a.action == "keys":
        # ★ 2026-10-18 P0-B：--vk 现在**键名或数值都收**（a~z / 0~9 / F1~F12 / Enter…）。
        #   Ctrl+V ⇒ --vk v --ctrl；老写法 --vk 0x41 一个字没改，照旧。
        try:
            _vk, _how = resolve_vk(a.vk)
        except ValueError as e:
            print(str(e))
            return 2
        return do_keys(a.hwnd, _vk, a.ctrl, a.times, a.dry_run,
                        scan_code=a.scan_code, key_name=_how)

    # ── ① rc=5「快照类」出口（派单 ① 明列的第三类 rc=5）────────────────────
    # 为什么 act.py 要有这条路：派单 ① 把 rc=5 拆成**坐标类 / 快照类 / 前台类**，
    #   但快照那一条的真身在 `验动作.py check`（新线工具），act.py 里原本没有落点 ——
    #   调用方在 act.py 侧撞上「前后快照路径集不一致」时，没有任何入口能问
    #   "缺的是哪一侧的 --paths、两侧当时各拿到什么"。
    # 本岔口**纯只读**：不碰窗口、不碰闸门、不扣额度，任何时刻都能跑。
    if a.snap_check:
        try:
            gate.require_any(gate.PERCEIVE_SCOPES, "查快照路径集")
        except gate.GateDenied as e:
            return _gate_denied(e, a.hwnd, None, "查快照路径集")
        extra = _snapshot_path_report(a.snap_before, a.snap_after,
                                      " ；".join(_snap_dirs()) or "(无)")
        # 统一走 rc=5 的外观：现象 + 采集到的证据 + 下一步。
        # 这是**诊断**命令，目的是让调用方一眼看清"我这组快照能不能判"。
        why = ("快照路径集诊断（派单 ① rc=5 快照类）："
               "before=%s / after=%s" % (a.snap_before or "<未给>", a.snap_after or "<未给>"))
        rc = _tell_next(5, why, a.hwnd, extra=extra)
        print()
        print("  ℹ 本条是**只读诊断**：rc=5 在这里表示「我按 rc=5 的形状把快照可比性摆给你看」，")
        print("    不是「本次动作失败」。真正的判定仍由 `验动作.py check` 出（它才认 rc=5）。")
        return rc

    if a.action == "read":
        try:
            gate.require_any(gate.PERCEIVE_SCOPES, "读文档")
        except gate.GateDenied as e:
            print("⛔ 未获授权，不读取：" + str(e))
            return 9
        _c, txt, how = read_doc(a.hwnd)
        sel = read_selection(a.hwnd)
        print(json.dumps({"读法": how, "字符数": len(txt) if txt is not None else None,
                          "前80字": (txt or "")[:80], "选中": (sel or "")[:60],
                          "选中字数": len(sel) if sel is not None else None},
                         ensure_ascii=False, indent=1))
        return 0

    if a.action == "targets":
        try:
            gate.require_any(gate.PERCEIVE_SCOPES, "列可点控件")
        except gate.GateDenied as e:
            print("⛔ 未获授权，不读取：" + str(e))
            return 9
        info, why = check_target(a.hwnd)
        if why:
            return _tell_deny(why, a.hwnd)
        ts, geo = targets(a.hwnd, with_geo=True)
        bands = _bands(a.hwnd, geo)
        if a.compact:
            print(f"窗口：{info['title']}　前台={info['foreground']}　可交互控件 {len(ts)} 个")
            for t in ts:
                print("  {:<17} | {:<44} | 中心 {:>5},{:<5} | {}{}".format(
                    t["类型"][:17], t["名称"][:44], t["中心"][0], t["中心"][1],
                    ",".join(t["模式"]),
                    "  ★当前选中" if t.get("选中") else ""))
            # ★ ④ 紧凑模式也**必须**带出可操作区 —— 调用方最常用的就是这条命令，
            #   把它藏在 --json 里等于没做。
            print("  ── 可操作区（④）──")
            print("     窗口矩形       : %s" % bands.get("窗口矩形"))
            print("     可拖拽窗口     : %s（%s）" % (bands.get("可拖拽窗口"), bands.get("窗口样式")))
            ab = bands.get("可拖带_上边框", {})
            if ab.get("矩形"):
                print("     可拖带·上边框  : %s（高 %spx）" % (ab["矩形"], ab.get("高")))
                print("                      %s" % ab.get("说明"))
            else:
                print("     可拖带·上边框  : **无** —— %s" % ab.get("说明"))
            lb = bands.get("可拖带_左边框", {}).get("矩形")
            rb = bands.get("可拖带_右边框", {}).get("矩形")
            bb = bands.get("可拖带_下边框", {}).get("矩形")
            print("     可拖带·边框    : 左=%s 右=%s 下=%s（厚度 %s）"
                  % (lb, rb, bb, bands.get("边框厚度")))
            cb = bands.get("内容区", {})
            print("     内容区         : %s（%s）" % (cb.get("矩形"), cb.get("来源")))
            print("     拖拽推荐点     : %s" % json.dumps(bands.get("拖拽推荐点", {}),
                                                        ensure_ascii=False))
            print("     坐标系         : %s" % bands.get("坐标系"))
            return 0
        print(json.dumps({"窗口": info, "白名单": "OK", "可交互控件": ts,
                          "可操作区(④)": bands},
                         ensure_ascii=False, indent=1))
        return 0
    if a.action == "click":
        xy = tuple(int(v) for v in a.xy.split(",")) if a.xy else None
        if not a.name and not xy:
            print("要 --name 或 --xy")
            return 1
        return do_click(a.hwnd, a.name, xy, a.dry_run, force_coord=a.coord, retry=max(0, a.retry),
                        post=a.post, post_force=a.post_force)
    if a.action == "focus":
        return do_focus(a.hwnd, a.dry_run)
    if a.action == "type":
        if a.text is None:
            print("要 --text")
            return 1
        return do_type(a.hwnd, a.text, a.dry_run, force_send=a.force_send,
                       scan_code=a.scan_code, focus=a.focus, nl=a.nl)
    if a.action == "drag":
        if not (a.xy and a.to):
            print("要 --xy 起点 --to 终点")
            return 1
        frm = tuple(int(v) for v in a.xy.split(","))
        to = tuple(int(v) for v in a.to.split(","))
        return do_drag(a.hwnd, frm, to, a.dry_run)
    return do_scroll(a.hwnd, a.amount, a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
