# -*- coding: utf-8 -*-
r"""env_probe —— 外部机器环境自检（离线只读 · 2026-10-25 · 工单 W4）

给陌生机器复现判据前的第一道门：把「这台机器能不能跑本项目」一次性说清。
**不装依赖、不联网、不碰键鼠、不起服务**；唯一的写动作是往
%LOCALAPPDATA% 写一个**自删探针文件**（可写性没法不写就验出来，写完立刻删）。

用法：
    python -X utf8 tools\env_probe.py
退出码：0 = 全满足；1 = 有硬阻塞（先修环境再继续）；2 = 只有警告（可继续，
缺的一般只影响桌面判据）。每条 ⚠️/❌ 都带「建议：」。

本进程会把 <仓>/src 与 <仓>/vendor 挂进 sys.path（存在才挂）——
与判据的运行姿势（PYTHONPATH=src）一致，探测结果才有可比性。

判据注入开关（`_INJECT`，**只给判据用**，生产路径不经过）：
    python                  版本三元组覆盖，如 (3, 11, 8)
    bits                    位数覆盖，如 32
    imports                 {模块名: bool} 覆盖，如 {"PIL.Image": False}
    dpi                     (感知级别, 系统DPI) 覆盖
    screens                 屏幕数覆盖
    interactive             交互桌面 bool 覆盖
    localappdata_writable   可写性 bool 覆盖
键缺省 = 真探测。判据用完必须清空。
"""
import ctypes
import importlib
import os
import struct
import sys
import tempfile
from collections import namedtuple
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for _p in ("src", "vendor"):
    _full = os.path.join(ROOT, _p)
    if os.path.isdir(_full) and _full not in sys.path:
        sys.path.insert(0, _full)

__all__ = ["OK", "WARN", "BLOCK", "Check", "collect_checks", "decide", "render", "main"]

OK, WARN, BLOCK = "OK", "WARN", "BLOCK"
_MARK = {OK: "✅", WARN: "⚠️", BLOCK: "❌"}

Check = namedtuple("Check", "status name detail advice")

_INJECT = {}

_SM_CMONITORS = 80
_LOGPIXELSX = 88
_DPI_AWARENESS_NAME = {0: "UNAWARE(0)", 1: "SYSTEM_AWARE(1)", 2: "PER_MONITOR_AWARE(2)"}

# ★ 显式原型：64 位下 HANDLE 是全宽值，不设 restype/argtypes 会在回传时
#   OverflowError（首跑实踩：GetDC 的 HDC 传回 GetDeviceCaps 直接炸）。
_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
try:
    _shcore = ctypes.windll.shcore
    _shcore.GetProcessDpiAwareness.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)]
    _shcore.GetProcessDpiAwareness.restype = ctypes.HRESULT
except AttributeError:
    _shcore = None  # 老于 Win8.1
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = wintypes.INT
_gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, wintypes.INT]
_gdi32.GetDeviceCaps.restype = wintypes.INT
_user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_user32.OpenInputDesktop.restype = wintypes.HDESK
_user32.CloseDesktop.argtypes = [wintypes.HDESK]
_user32.CloseDesktop.restype = wintypes.BOOL
_user32.GetSystemMetrics.argtypes = [wintypes.INT]
_user32.GetSystemMetrics.restype = wintypes.INT


def _inj(key):
    return _INJECT[key] if key in _INJECT else None


def _importable(name):
    """(可导入?, 出错摘要)。真实 import，不只 find_spec —— 装坏了也要现形。"""
    ovr = _inj("imports")
    if isinstance(ovr, dict) and name in ovr:
        return bool(ovr[name]), "判据注入：模拟不可导入"
    try:
        importlib.import_module(name)
        return True, ""
    except Exception as e:  # noqa: BLE001 —— 缺包/装坏都要如实报
        return False, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- 各项检查

def chk_python():
    v = _inj("python") or tuple(sys.version_info[:3])
    vtxt = "%d.%d.%d" % tuple(v[:3])
    if (v[0], v[1]) == (3, 12):
        return Check(OK, "Python 版本（3.12 硬要求）", vtxt, "")
    return Check(BLOCK, "Python 版本（3.12 硬要求）", vtxt,
                 "本项目按 Python 3.12 验收，别的版本没测过、不背书；请装 3.12 后重跑本自检")


def chk_bits():
    bits = _inj("bits") or struct.calcsize("P") * 8
    if bits == 64:
        return Check(OK, "Python 位数", "64 位", "")
    return Check(WARN, "Python 位数", "%d 位" % bits,
                 "建议 64 位：32 位未测过，桌面判据可能因坐标/驱动差异误判")


def chk_dep(label, modules, install_hint):
    missing = []
    for m in modules:
        ok, err = _importable(m)
        if not ok:
            missing.append("%s（%s）" % (m, err))
    if not missing:
        return Check(OK, "%s 可导入" % label, "、".join(modules), "")
    return Check(WARN, "%s 可导入" % label, "缺/装坏：%s" % "; ".join(missing),
                 "%s。缺它只影响桌面判据，无桌面判据照跑" % install_hint)


def chk_dpi_awareness():
    ovr = _inj("dpi")
    try:
        if ovr is not None:
            aware = int(ovr[0])
        else:
            if _shcore is None:
                raise RuntimeError("shcore 不可用")
            val = ctypes.c_int(0)
            rc = _shcore.GetProcessDpiAwareness(None, ctypes.byref(val))
            aware = val.value if rc == 0 else -1
    except Exception as e:  # noqa: BLE001
        return Check(WARN, "DPI 感知（本进程，仅供参考）", "探测不了（%s）" % e,
                     "shcore 缺失说明系统老于 Win8.1：本项目面向 Win10/11")
    return Check(OK, "DPI 感知（本进程，仅供参考）", _DPI_AWARENESS_NAME.get(aware, "%d" % aware)
                 + "；判据/服务进程各自设置感知，本值仅参考", "")


def _probe_system_dpi():
    """主屏 DPI：GetDC(None)+GetDeviceCaps(LOGPIXELSX)，用完释放。读屏 DC，不碰输入。"""
    ovr = _inj("dpi")
    if ovr is not None:
        return int(ovr[1])
    hdc = _user32.GetDC(None)
    if not hdc:
        return 0
    try:
        return _gdi32.GetDeviceCaps(hdc, _LOGPIXELSX) or 0
    finally:
        _user32.ReleaseDC(None, hdc)


def chk_system_dpi():
    dpi = _probe_system_dpi()
    if dpi <= 0:
        return Check(WARN, "系统主屏 DPI 与缩放", "读不到（无显示会话？）",
                     "无显示会话时抓屏判据会退化，见「交互桌面」项")
    scale = dpi / 96.0
    extra = "；缩放 >200% 多混合屏属未测区" if scale > 2.0 else ""
    return Check(OK, "系统主屏 DPI 与缩放", "%d DPI（%.0f%%）%s" % (dpi, scale * 100, extra), "")


def chk_screens():
    n = _inj("screens")
    if n is None:
        n = ctypes.windll.user32.GetSystemMetrics(_SM_CMONITORS)
    n = int(n)
    if n >= 1:
        return Check(OK, "屏幕数", "%d 块" % n, "")
    return Check(WARN, "屏幕数", "0 块（无显示会话）",
                 "无头会话：抓屏与窗口判据不可信，请只跑无桌面判据")


def chk_interactive_desktop():
    ovr = _inj("interactive")
    if ovr is not None:
        ok = bool(ovr)
    else:
        try:
            hdesk = _user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_GENERIC_READ
            ok = bool(hdesk)
            if hdesk:
                _user32.CloseDesktop(hdesk)
        except Exception as e:  # noqa: BLE001
            return Check(WARN, "交互桌面（OpenInputDesktop）", "探测不了（%s）" % e,
                         "极旧系统或权限策略；桌面判据前先人工确认能看见桌面")
    if ok:
        return Check(OK, "交互桌面（OpenInputDesktop）", "可打开输入桌面：可跑桌面判据", "")
    return Check(WARN, "交互桌面（OpenInputDesktop）", "打不开输入桌面（服务会话 / SSH / 计划任务常见）",
                 "无头会话：桌面判据会失败或误判，请只跑无桌面判据（docs\\复现_外部机器.md §二④）")


def chk_msvcrt():
    try:
        import msvcrt
        if not (hasattr(msvcrt, "getch") and hasattr(msvcrt, "kbhit")):
            raise RuntimeError("getch/kbhit 缺失")
    except Exception as e:  # noqa: BLE001
        return Check(BLOCK, "msvcrt 可用", "不可用（%s）" % e,
                     "Windows 专属标准库都缺 ⇒ 大概率不是官方 Windows 版 Python，请换 python.org 安装包")
    return Check(OK, "msvcrt 可用", "getch / kbhit 在（只 import 验证，不真读键）", "")


def chk_localappdata():
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        return Check(BLOCK, "%LOCALAPPDATA% 可写", "环境变量缺失",
                     "AgentCtl 的状态/租约/急停旗子都落这里；请恢复 LOCALAPPDATA 环境变量")
    ovr = _inj("localappdata_writable")
    if ovr is False:
        return Check(BLOCK, "%LOCALAPPDATA% 可写", "%s（判据注入：模拟不可写）" % la,
                     "检查目录 ACL / 杀软隔离 / 重定向")
    if ovr is not True:
        try:
            fd, tmp = tempfile.mkstemp(prefix="agentctl_envprobe_", dir=la)
            try:
                os.write(fd, b"probe")
            finally:
                os.close(fd)
            os.remove(tmp)
        except Exception as e:  # noqa: BLE001
            return Check(BLOCK, "%LOCALAPPDATA% 可写", "%s（%s）" % (la, e),
                         "检查目录 ACL / 杀软隔离 / 重定向；状态写不进去服务跑不起来")
    return Check(OK, "%LOCALAPPDATA% 可写", la + "（探针文件已写即删）", "")


def collect_checks():
    """跑全部检查，按固定顺序返回 Check 列表（除 LOCALAPPDATA 探针外全只读）。"""
    return [
        chk_python(),
        chk_bits(),
        chk_dep("pywin32", ("win32api", "win32gui", "win32con"),
                "python -X utf8 -m pip install -r requirements.txt（含 pywin32）"),
        chk_dep("uiautomation", ("uiautomation",),
                "python -X utf8 -m pip install -r requirements.txt（含 uiautomation）"),
        chk_dep("PIL", ("PIL.Image",),
                "python -X utf8 -m pip install -r requirements.txt（含 pillow）"),
        chk_dpi_awareness(),
        chk_system_dpi(),
        chk_screens(),
        chk_interactive_desktop(),
        chk_msvcrt(),
        chk_localappdata(),
    ]


def decide(checks):
    """退出码语义：0 全满足 / 1 有硬阻塞（优先于警告）/ 2 只有警告。"""
    statuses = {c.status for c in checks}
    if BLOCK in statuses:
        return 1
    if WARN in statuses:
        return 2
    return 0


def render(checks):
    """人话报告：✅/⚠️/❌ + 建议；结尾小结带退出码。"""
    lines = ["环境自检（agentctl · 离线只读）", ""]
    for c in checks:
        lines.append("%s %s —— %s" % (_MARK[c.status], c.name, c.detail or ""))
        if c.status != OK and c.advice:
            lines.append("    建议：%s" % c.advice)
    n_block = sum(1 for c in checks if c.status == BLOCK)
    n_warn = sum(1 for c in checks if c.status == WARN)
    lines.append("")
    lines.append("小结：✅ %d ⚠️ %d ❌ %d ⇒ 退出码 %d"
                 % (len(checks) - n_block - n_warn, n_warn, n_block, decide(checks)))
    return "\n".join(lines)


def main(argv=None):
    checks = collect_checks()
    print("（已把 <仓>/src、<仓>/vendor 挂入 sys.path —— 与判据运行姿势一致）")
    print(render(checks))
    return decide(checks)


if __name__ == "__main__":
    raise SystemExit(main())
