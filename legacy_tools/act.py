# -*- coding: utf-8 -*-
"""act.py —— 动作层（B 级：点击 / 滚动）

铁律 11 的第 2 档。**动作会改变用户的电脑状态，所以规矩比感知层严得多**：

  1. 必须过闸 `gate.B`（刹车松开 + 持 B 级通行证 + 范围完全匹配）
  2. **白名单窗口**：只允许在名单内的窗口上动作（默认只有记事本）
  3. **能 Invoke 就绝不点坐标**：UIA 支持 Invoke/SelectionItem 的控件，直接调模式，
     根本不产生鼠标事件 —— 没有坐标，就没有"点错地方"这回事
  4. 只能点坐标时：目标窗口**必须在前台**，且坐标必须落在该窗口矩形内，否则拒绝
  5. 每次动作前后各留一张目标窗口截图（PrintWindow）+ 控件记录，写 `_auth/act.log`
  6. `--dry-run` 只算不做

用法：
  python act.py targets --hwnd N                  # 列出可点的控件（只读，A 级即可）
  python act.py click --hwnd N --name "dsh.txt"    # 按控件名点（优先 UIA）
  python act.py click --hwnd N --xy 1200,800       # 按坐标点（需前台）
  python act.py scroll --hwnd N --amount -3        # 滚动（优先 UIA Scroll）
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate                                                # noqa: E402
import win32gui                                            # noqa: E402
import win32process                                        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ACTLOG = os.path.join(HERE, "_auth", "act.log")

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
        wl = d.get("whitelist") or []
        out = [{"进程": w.get("process", ""), "类名": w.get("class", "")}
               for w in wl if w.get("process")]
        if out:
            return out
    except Exception:
        pass
    return _DEFAULT_WHITELIST


def whitelist_source():
    """给日志用：告诉人这次白名单是从哪来的。"""
    import os as _os
    p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "whitelist.json")
    return "whitelist.json" if _os.path.exists(p) else "内置默认"


WHITELIST = _load_whitelist()

BLOCKED_TITLES = ("DSH电脑控制授权",)

user32 = ctypes.windll.user32


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

    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _l):
        try:
            if not user32.IsWindowVisible(h):
                return True
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


def check_target(hwnd):
    """白名单 + 敏感窗口检查。返回 (info, 拒绝原因)。"""
    i = win_info(hwnd)
    if not i["title"] and not i["class"]:
        return i, f"句柄 {hwnd} 不是有效窗口"
    if any(b in i["title"] for b in BLOCKED_TITLES):
        return i, "这是用户的授权弹窗（正在输密钥），任何动作一律禁止"
    for w in WHITELIST:
        if i["process"].lower() == w["进程"].lower() and i["class"] == w["类名"]:
            return i, None
    return i, (f"窗口「{i['title']}」({i['process']}/{i['class']}) 不在白名单里。"
               f"当前白名单只有：{', '.join(w['进程'] for w in WHITELIST)}")


# ─────────────────── UIA ───────────────────
def _uia(hwnd):
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(2)
    try:
        auto.InitializeUIAutomationInCurrentThread()
    except Exception:
        pass
    return auto, auto.ControlFromHandle(hwnd)


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


def targets(hwnd, limit=40):
    """列出可交互的控件（只读，但同样要过闸）。"""
    if not _need_perceive("列可点控件"):
        return []
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
    return out


def _has(ctrl, name):
    try:
        return getattr(ctrl, "Get" + name + "Pattern")() is not None
    except Exception:
        return False


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
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTU(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTU)]


MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_WHEEL, WHEEL_DELTA = 0x0800, 120
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002


def send_mouse(flags, x=0, y=0, data=0):
    inp = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(x, y, data, flags, 0, None))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_unicode(ch):
    """**中文输入必须走 KEYEVENTF_UNICODE**：按虚拟键码只能打出 ASCII，
    中文要靠"直接把码点写进键盘事件"，绕开输入法与键盘布局。"""
    code = ord(ch)
    for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
        inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, code, flags, 0, None))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def click_at(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    send_mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.05)
    send_mouse(MOUSEEVENTF_LEFTUP)


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
    inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, 0, flags, 0, None))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def do_keys(hwnd, vk=0x41, ctrl=False, times=1, dry=False, wait=0.06):
    """C 级：发送虚拟键组合。vk 默认 0x41=Ctrl+A。

    ctrl=True 时按"按住 Ctrl → 敲键 → 松开 Ctrl"的顺序；times>1 时连敲（如退格清空）。
    """
    info, why = check_target(hwnd)
    if why:
        log(f"⛔ 拒绝按键：{why}")
        return 3
    try:
        gate.require(gate.C, f"按键 vk=0x{vk:X} ctrl={ctrl} ×{times}")
    except gate.GateDenied as e:
        log(f"⛔ 未获授权，不按键：{e}")
        return 9
    if not info["foreground"] and not dry:
        log("⛔ 按键前窗口必须在前台（按键会发给当前焦点）—— 拒绝")
        return 5
    if dry:
        log(f"（演练）⌨ 按键 vk=0x{vk:X} ctrl={ctrl} ×{times}")
        return 0
    before = evidence(hwnd, "before")
    if ctrl:
        send_vk(0x11, True)                 # VK_CONTROL down
        time.sleep(0.03)
    for _ in range(max(1, times)):
        send_vk(vk, True)
        time.sleep(0.02)
        send_vk(vk, False)
        time.sleep(wait)
    if ctrl:
        time.sleep(0.03)
        send_vk(0x11, False)                # VK_CONTROL up
    time.sleep(0.4)
    after = evidence(hwnd, "after")
    fp0, fp1 = _img_fp(before.get("文件") or ""), _img_fp(after.get("文件") or "")
    changed = None if (fp0 is None or fp1 is None) else (fp1 != fp0)
    ev = "" if changed is None else ("　✔ 页面已变化" if changed else "　⚠ 页面无变化")
    log(f"⌨ 按键 [vk=0x{vk:X}{' +Ctrl' if ctrl else ''} ×{times}] 窗口「{info['title']}」"
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
        log(f"⛔ 拒绝填字段：{why}")
        return 3
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
        log(f"⛔ 未获授权，不填字段：{e}")
        return 9
    x, y = xy
    r = info["rect"]
    if not (r[0] <= x <= r[2] and r[1] <= y <= r[3]):
        log(f"⛔ 坐标 ({x},{y}) 不在窗口矩形 {r} 内 —— 拒绝")
        return 5
    if not info["foreground"] and not dry:
        log("⛔ 填字段前窗口必须在前台（按键会发给当前焦点）—— 拒绝")
        return 5
    if dry:
        log(f"（演练）⌨ 填字段 坐标({x},{y}) 「{text}」{'（先清空）' if clear else ''}")
        return 0

    b0 = evidence(hwnd, "fill0")
    click_at(x, y)
    time.sleep(0.35)
    if clear:
        # Ctrl+A 全选 → 随后打字即整体覆盖（比退格可靠：不依赖旧值长度）
        send_vk(0x11, True)
        time.sleep(0.03)
        send_vk(0x41, True)
        time.sleep(0.02)
        send_vk(0x41, False)
        time.sleep(0.06)
        send_vk(0x11, False)
        time.sleep(0.2)
    for ch in text:
        send_unicode(ch)
        time.sleep(0.012)
    time.sleep(0.5)
    b1 = evidence(hwnd, "fill1")
    f0, f1 = _img_fp(b0.get("文件") or ""), _img_fp(b1.get("文件") or "")
    changed = None if (f0 is None or f1 is None) else (f1 != f0)
    ev = "" if changed is None else ("　✔ 页面已变化" if changed else "　⚠⚠ 页面无变化——去核验")
    log(f"⌨ 填字段 [{len(text)}字{'，含中文' if any(ord(c) > 127 for c in text) else ''}"
        f"{'，已先清空' if clear else ''}] 坐标({x},{y}) 窗口「{info['title']}」"
        f"　前图={b0.get('文件') or '无'} 后图={b1.get('文件') or '无'}" + ev)
    return 0 if changed is not False else 8


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


def do_click(hwnd, name=None, xy=None, dry=False, force_coord=False, retry=1):
    """force_coord=True：**强制走坐标点击**（考核用，专门练 SendInput 那条退路）。"""
    info, why = check_target(hwnd)
    if why:
        log(f"⛔ 拒绝点击：{why}")
        return 3
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
        log(f"⛔ 未获授权，不点击：{e}")
        return 9

    before = {} if dry else evidence(hwnd, "before")
    method, detail = None, {}

    if name:
        c = find_control(hwnd, name)
        if c is None:
            log(f"⛔ 窗口里找不到名为「{name}」的控件，不做任何动作")
            return 4
        x, y, r, r_ok = _center(c)
        detail = {"控件": c.Name, "类型": c.ControlTypeName, "矩形": r, "中心": [x, y]}
        if (not force_coord) and _has(c, "Invoke"):
            method = "UIA.Invoke（无鼠标事件）"
            if not dry:
                getattr(c, "GetInvokePattern")().Invoke()
        elif (not force_coord) and _has(c, "SelectionItem"):
            method = "UIA.SelectionItem.Select（无鼠标事件）"
            if not dry:
                getattr(c, "GetSelectionItemPattern")().Select()
        else:
            # 只能点坐标（或考核强制走坐标）—— 这时矩形必须真实，否则"中心"会算成 (0,0) 点到屏幕左上角
            if not r_ok:
                log(f"⛔ 控件「{c.Name}」矩形为空（未渲染），只能点坐标时会点到 (0,0) —— 拒绝")
                return 5
            wrect = win_info(hwnd)["rect"]
            if not (wrect[0] <= x <= wrect[2] and wrect[1] <= y <= wrect[3]):
                log(f"⛔ 控件中心 ({x},{y}) 不在窗口矩形 {wrect} 内 —— 拒绝")
                return 5
            if not info["foreground"] and not dry:
                log("⛔ 只能点坐标，但目标窗口不在前台 —— 拒绝（会点到别的窗口上）")
                return 5
            # 反竞态：标签栏会滚动/有动画，矩形可能在"读到"与"点到"之间变掉 →
            # 要求隔 0.25 秒两次读数**完全一致**才动手，否则宁可不点。
            r1 = r
            if not dry:
                time.sleep(0.25)
                c2 = find_control(hwnd, c.Name) or c
                x, y, r2, ok2 = _center(c2)
                if (not ok2) or r2 != r1:
                    log(f"⛔ 控件矩形不稳定 {r1} → {r2}（界面正在滚动/动画）—— 拒绝点击，避免点错地方")
                    return 5
            method = ("SendInput 坐标点击（考核强制）" if force_coord
                      else "SendInput 坐标点击（该控件不支持 Invoke）")
            if not dry:
                click_at(x, y)
        if not r_ok:
            detail["备注"] = "控件未渲染（矩形为空）；本次走 UIA 模式，不涉及坐标"
    else:
        x, y = xy
        r = info["rect"]
        if not (r[0] <= x <= r[2] and r[1] <= y <= r[3]):
            log(f"⛔ 坐标 ({x},{y}) 不在目标窗口矩形 {r} 内 —— 拒绝")
            return 5
        if not info["foreground"] and not dry:
            log("⛔ 坐标点击要求目标窗口在前台，当前不在 —— 拒绝")
            return 5
        method = "SendInput 坐标点击"
        detail = {"坐标": [x, y]}

    changed = None
    attempts = 0
    if not dry and method.startswith("SendInput"):
        fp0 = _img_fp(before.get("文件") or "")
        while True:
            attempts += 1
            click_at(x, y)
            time.sleep(0.45)
            after = evidence(hwnd, "after")
            fp1 = _img_fp(after.get("文件") or "")
            if fp0 is None or fp1 is None:
                changed = None
                break
            changed = (fp1 != fp0)
            if changed or attempts > retry:
                break
            log(f"   ⚠ 第 {attempts} 次点击后页面〔无变化〕→ 自动重试")
            time.sleep(0.35)
    else:
        time.sleep(0.45)
        after = {} if dry else evidence(hwnd, "after")
    ev = "" if dry else ("　前图=%s 后图=%s" % (before.get("文件") or "无", after.get("文件") or "无"))
    if not dry and ("拒绝" in before or "拒绝" in after):
        ev += "　⚠ 本次证据不完整"
    if changed is True:
        ev += f"　✔ 页面已变化（共点 {attempts} 次）"
    elif changed is False:
        ev += f"　⚠⚠ 页面无变化（点了 {attempts} 次都没反应）—— **别当成功，去核验**"
    if attempts > 1:
        detail["重试次数"] = attempts - 1
    log(f"{'（演练）' if dry else ''}🖱 点击 [{method}] 窗口「{info['title']}」 {json.dumps(detail, ensure_ascii=False)}" + ev)
    return 0 if (dry or changed is not False) else 8


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


def do_scroll(hwnd, amount, dry=False, force_wheel=False):
    info, why = check_target(hwnd)
    if why:
        log(f"⛔ 拒绝滚动：{why}")
        return 3
    # 模态对话框前置检查（2026-09-18 新增）：被挡就拒绝，别做出"看着成功其实无效"的动作
    _mb = require_no_modal(hwnd, "滚动")
    if _mb:
        return _mb
    try:
        gate.require(gate.B, f"滚动 {amount}")
    except gate.GateDenied as e:
        log(f"⛔ 未获授权，不滚动：{e}")
        return 9
    auto, root = _uia(hwnd)
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
    if not info["foreground"]:
        log("⛔ 滚轮事件发给光标下的窗口，目标不在前台时不可控 —— 拒绝")
        return 5
    x, y, rr, ok_rect = _center(root)
    if not ok_rect:                       # 根控件矩形为空时，退回窗口矩形中心
        r = info["rect"]
        x, y = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
    r = info["rect"]
    if not (r[0] <= x <= r[2] and r[1] <= y <= r[3]):
        log(f"⛔ 滚轮落点 ({x},{y}) 不在窗口矩形 {r} 内 —— 拒绝")
        return 5
    user32.SetCursorPos(x, y)
    time.sleep(0.12)
    if not dry:
        # 注意符号：MOUSEEVENTF_WHEEL 的 data **正值 = 向上滚**（远离用户）。
        # 所以"下滚 amount 格"（amount 为负）要发负值 —— 写成 wheel(-120*amount) 会方向反掉，
        # 表现是"数值一点不动"，因为已经在顶了。这个错被判据抓出来过一次。
        wheel(120 * amount)
    log(f"{'（演练）' if dry else ''}🖱 滚动 [SendInput 滚轮 {amount} 格，光标置于 ({x},{y})] 窗口「{info['title']}」")
    return 0


def doc_control(hwnd):
    _auto, root = _uia(hwnd)
    found = []

    def walk(c, d=0):
        if d > 8 or found:
            return
        try:
            if c.ControlTypeName == "DocumentControl":
                found.append(c)
                return
        except Exception:
            pass
        try:
            for ch in c.GetChildren():
                walk(ch, d + 1)
        except Exception:
            pass

    walk(root)
    return found[0] if found else None


def read_doc(hwnd):
    """读回文档真实内容（核验用，只读）。返回 (控件, 文本 或 None, 说明)。"""
    if not _need_perceive("读文档内容"):
        return None, None, "未获授权"
    c = doc_control(hwnd)
    if c is None:
        return None, None, "找不到文本编辑区"
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
        log(f"⛔ 拒绝切前台：{why}")
        return 3
    try:
        gate.require(gate.B, "切前台")
    except gate.GateDenied as e:
        log(f"⛔ 未获授权，不切前台：{e}")
        return 9
    if not dry:
        try:
            user32.ShowWindow(hwnd, 9)            # SW_RESTORE
        except Exception:
            pass
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.5)
    now_fg = win_info(hwnd)["foreground"]
    log(f"{'（演练）' if dry else ''}🎯 切前台 [{info['title']}] → 现在前台={now_fg}")
    return 0 if (dry or now_fg) else 6


def do_type(hwnd, text, dry=False, allow_if_starts_with=None, force_send=False):
    """C 级：打字（含中文走 Unicode）。

    两种模式：
      ① **文档模式**（默认，安全阀拉满）：必须有 UIA 文本区，且**只允许往空文档（或训练靶）打**，
         打完整字符回读核验 —— 防的是"把字打进用户的真实文件"。
      ② **网页表单模式**（`--force-send`，2026-09-19 新增）：目标没有 UIA 文本区时（微信内置浏览器
         之类），直接发键。**核验从"UIA 回读"降级为"外部截图"**，并且**没有"空文档"这道保险**
         —— 所以仅在**用户明确授权的表单填写**场景使用，命令行必须显式写 `--force-send`。
    """
    info, why = check_target(hwnd)
    if why:
        log(f"⛔ 拒绝打字：{why}")
        return 3
    # 模态对话框前置检查（2026-09-18 新增）：被挡就拒绝，别做出"看着成功其实无效"的动作
    _mb = require_no_modal(hwnd, "打字")
    if _mb:
        return _mb
    try:
        gate.require(gate.C, f"打字 {len(text)} 字")
    except gate.GateDenied as e:
        log(f"⛔ 未获授权，不打字：{e}")
        return 9
    if not info["foreground"] and not dry:
        log("⛔ 打字前窗口必须在前台（按键会发给当前焦点）—— 拒绝")
        return 5
    c, before, how = read_doc(hwnd)
    if before is None and not force_send:
        log(f"⛔ 打不了字：{how}")
        return 4
    if before is None and force_send:
        log("⚠ 目标无 UIA 文本区 → 走【网页表单模式】：直接发键，"
            "**不做 UIA 回读核验**，改用前后截图比对（2026-09-19 补，此前是盲打无核验）")
        b0 = {} if dry else evidence(hwnd, "typesend0")
        before = ""
        for i, ch in enumerate(text):
            send_unicode(ch)
            time.sleep(0.012)
        time.sleep(0.45)
        b1 = {} if dry else evidence(hwnd, "typesend1")
        f0, f1 = _img_fp(b0.get("文件") or ""), _img_fp(b1.get("文件") or "")
        changed = None if (f0 is None or f1 is None) else (f1 != f0)
        ev = "" if changed is None else ("　✔ 页面已变化" if changed else "　⚠⚠ 页面无变化——别当成功，去核验")
        log(f"{'（演练）' if dry else ''}⌨ 打字 [网页表单模式 {len(text)} 字"
            f"{'，含中文' if any(ord(x) > 127 for x in text) else ''}] 窗口「{info['title']}」"
            f"　前图={b0.get('文件') or '无'} 后图={b1.get('文件') or '无'}" + ev)
        return 0 if (dry or changed is not False) else 8
    # ★ 最关键的一道保险：只往空文档（或带训练标记的靶文档）里打字，绝不可能污染用户的真实文件
    if before.strip() != "" and not (allow_if_starts_with and before.startswith(allow_if_starts_with)):
        log(f"⛔ 目标文档里已有 {len(before)} 个字符，且不是训练靶 —— 拒绝打字"
            f"（防止把字打进用户的真实文件里）")
        return 7
    if not dry:
        for ch in text:
            send_unicode(ch)
            time.sleep(0.012)
    time.sleep(0.45)
    _, after, _ = read_doc(hwnd)
    ok = (after == before + text)
    log(f"{'（演练）' if dry else ''}⌨ 打字 [{len(text)} 字{'（含中文）' if any(ord(x) > 127 for x in text) else ''}] "
        f"窗口「{info['title']}」 {'✅ 读回逐字符一致' if ok else '❌ 读回不一致'} "
        f"（{len(before)} → {len(after) if after else 'None'} 字）")
    return 0 if (dry or ok) else 8


def do_drag(hwnd, frm, to, dry=False):
    """D 级：在文本编辑区里拖拽选字。结果用"读回选中的文本"来核验。"""
    info, why = check_target(hwnd)
    if why:
        log(f"⛔ 拒绝拖拽：{why}")
        return 3
    try:
        gate.require(gate.D, f"拖拽 {frm}→{to}")
    except gate.GateDenied as e:
        log(f"⛔ 未获授权，不拖拽：{e}")
        return 9
    r = info["rect"]
    for p in (frm, to):
        if not (r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]):
            log(f"⛔ 拖拽端点 {p} 不在窗口矩形 {r} 内 —— 拒绝")
            return 5
    if not info["foreground"] and not dry:
        log("⛔ 拖拽要求窗口在前台 —— 拒绝")
        return 5
    if not dry:
        drag(frm, to)
    time.sleep(0.4)
    sel = read_selection(hwnd) if not dry else None
    log(f"{'（演练）' if dry else ''}✋ 拖拽 [{frm} → {to}] 窗口「{info['title']}」"
        + ("" if dry else f"　选中了 {len(sel) if sel is not None else '?'} 字："
                          f"「{(sel or '')[:30]}」"))
    return 0 if (dry or (sel is not None and len(sel) > 0)) else 8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action",
                    choices=["targets", "click", "scroll", "focus", "type", "drag", "read", "keys", "fill"])
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--name")
    ap.add_argument("--xy")
    ap.add_argument("--amount", type=int, default=-3)
    ap.add_argument("--text")
    ap.add_argument("--to")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--coord", action="store_true", help="点击：强制走坐标路径（练 SendInput 退路）")
    ap.add_argument("--retry", type=int, default=1,
                    help="点击：页面无变化时自动重试几次（默认 1；**幂等动作才用**，切换类按钮设 0）")
    ap.add_argument("--vk", type=lambda s: int(s, 0), default=0x41,
                    help="keys：虚拟键码（默认 0x41 = A，配 --ctrl 即 Ctrl+A 全选；0x08 = 退格）")
    ap.add_argument("--ctrl", action="store_true", help="keys：与 Ctrl 组合")
    ap.add_argument("--times", type=int, default=1, help="keys：连按几次（清空字段时配 0x08 用）")
    ap.add_argument("--no-clear", action="store_true",
                    help="fill：不要先清空（默认会 Ctrl+A 覆盖旧值；仅在目标本来就是空字段时才加）")
    ap.add_argument("--force-send", action="store_true",
                    help="打字：**网页表单模式** —— 目标没有 UIA 文本区时，直接发键，核验改由外部截图负责")
    ap.add_argument("--compact", action="store_true", help="targets：紧凑文本，一行一个控件")
    a = ap.parse_args()

    if a.action == "fill":
        if not a.xy:
            print("fill 需要 --xy x,y")
            return 2
        xy = tuple(int(v) for v in a.xy.split(","))
        return do_fill(a.hwnd, xy, a.text or "", a.dry_run, clear=not a.no_clear)

    if a.action == "keys":
        return do_keys(a.hwnd, a.vk, a.ctrl, a.times, a.dry_run)

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
            print("⛔ " + why)
            return 3
        ts = targets(a.hwnd)
        if a.compact:
            print(f"窗口：{info['title']}　前台={info['foreground']}　可交互控件 {len(ts)} 个")
            for t in ts:
                print("  {:<17} | {:<44} | 中心 {:>5},{:<5} | {}{}".format(
                    t["类型"][:17], t["名称"][:44], t["中心"][0], t["中心"][1],
                    ",".join(t["模式"]),
                    "  ★当前选中" if t.get("选中") else ""))
            return 0
        print(json.dumps({"窗口": info, "白名单": "OK", "可交互控件": ts},
                         ensure_ascii=False, indent=1))
        return 0
    if a.action == "click":
        xy = tuple(int(v) for v in a.xy.split(",")) if a.xy else None
        if not a.name and not xy:
            print("要 --name 或 --xy")
            return 1
        return do_click(a.hwnd, a.name, xy, a.dry_run, force_coord=a.coord, retry=max(0, a.retry))
    if a.action == "focus":
        return do_focus(a.hwnd, a.dry_run)
    if a.action == "type":
        if a.text is None:
            print("要 --text")
            return 1
        return do_type(a.hwnd, a.text, a.dry_run, force_send=a.force_send)
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
