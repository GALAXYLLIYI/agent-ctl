# -*- coding: utf-8 -*-
"""widget.py —— M1 悬浮窗（只读挂件）

用户的要求：「醒着的时候用户能看见它在干什么」。

★★ 本模块只读，绝不操作屏幕 —— 所以它不调 gate 的计数接口 ★★

为什么这条这么重要（今天想清楚的）：
  闸门里 A 级感知限 200 次动作。悬浮窗 1 fps 刷新，3 分钟就是 180 次。
  如果显示状态也走计数，**用户的额度会被一个只读挂件吃光**，
  而且它自己毫无察觉 —— 这是最坏的一类 bug（静默剥夺用户控制权）。
  -> 显示一律用只判断不计数的那条路；计数只留给真正的动作。
  -> 自检见 selftest_widget()。

「它此刻在干什么」怎么显示：**读闸门日志的尾巴**，不是读屏幕。
  这样既不用 A 级感知，也不会泄露屏幕内容。
"""
from __future__ import annotations

import json
import os
import re
import time
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 急停状态一律以 **gate 为准**，这里不再自己定义一份。
# ★ 今天踩的坑：闸门把急停文件搬到 %LOCALAPPDATA% 了，可面板这边
#   还写着 桌面\_急停.flag —— 结果「点终止」写桌面、闸门读新路径，
#   **急停形同虚设**。安全机制两边必须读同一个源，
#   所以这里直接从 gate 拿常量，不再复制一份（复制=迟早会漂）。
from agentctl.gate import (BRAKE_FLAG, brake_engaged, HOME as TOOLS,   # noqa: E402
                           AUTHDIR, PASSFILE)

# ★ 面板是**只读挂件**，所以老脚本目录定位不到时它照样得能开窗 ★
#   理由：面板显示的是「急停有没有拉下」——这是最该让用户随时看得见的东西。
#   如果因为 legacy 目录缺失就让 import 崩，那台机器上恰恰**最需要看面板的时候看不了**。
#   所以路径全部允许是 None，读不到的状态显示成「未知」，不假装、也不崩。
# ★ 2026-10-06：面板要读的是**两个**日志，不是一个。
#   gate.require()（本项目新门门）写 gate_agentctl.log；
#   老脚本 act.py 写 gate.log。两边都有意义，都保留。
#   原先面板只读老那个 → 用户点了授权，界面却停在两天前的记录上，
#   表现就是「授权完没反应」——真因是**读错了文件**，不是授权失效。
#   GATELOG 保留为旧名（指第一个），兼容外部引用。
GATELOGS = [os.path.join(AUTHDIR, n) for n in ("gate_agentctl.log", "gate.log")] if AUTHDIR else []
GATELOG = GATELOGS[0] if GATELOGS else None

REFRESH_MS = 1000          # 1 fps：够看，不吃资源（手册：两次内存事故的教训）

# 面板尺寸与字号。
# 用户 2026-10-04：「界面有点小，前面有一版清楚界面又够大的你看看」
#   说的那版物理尺寸是 **510x321** —— 那是**修 DPI 之前**的版本：
#   窗口按 340x214 画，然后被 Windows 位图放大 1.5 倍，物理上就变大了，**但字是糊的**。
#   修好 DPI 后逻辑=物理，想再要那个大小，**得把窗口和字号真的放大**，不是等它被拉大。
PANEL_W, PANEL_H = 560, 330
F_TITLE, F_HEAD, F_ROW, F_FOOT = 15, 14, 13, 11
F_LOG = 11               # 「最近动作」是流水信息，单独小一档，换更多内容
POSFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'panel_pos.json')


def _load_pos():
    """读上次的位置。读不到就用默认（右下角）。

    ⚠ 用 utf-8-sig 而不是 utf-8：PowerShell 5.1 的 `Set-Content -Encoding UTF8`
    会写 BOM，json.load 遇到 BOM 直接抛异常。而异常若被 except 静默吞掉，
    表现就是「位置记忆好像坏了」——查了很久才定位到这里（今天第三次栽在
    「异常被吞掉，症状在别处」这一类）。
    """
    try:
        with open(POSFILE, encoding='utf-8-sig') as f:
            d = json.load(f)
        return int(d['x']), int(d['y'])
    except FileNotFoundError:
        return None
    except Exception as e:
        # ★ 保留这个 try，但理由是测量出来的、不是猜的（2026-10-07）★
        #   面板要用 pythonw.exe 启（无控制台窗口，用户不要看到终端），
        #   而 pythonw / PyInstaller(console=False) 下 **sys.stdout 是 None**。实测 CPython 3.12.7：
        #       print(...)              → 静默 no-op，**不抛**
        #       traceback.print_exc()   → 静默 no-op，**不抛**
        #       sys.stdout.write(...)   → **AttributeError**
        #       sys.stdout.flush()     → **AttributeError**
        #   即现在这一行 print 本身不构成崩溃风险（一开始我认为会抛，
        #   反向对照实测才发现那个认断是错的）。
        #   但一旦以后有人把它改成 sys.stdout.write 就会真拉死面板。
        #   三行 try 换一个未来的改写，不计算多大代价。
        #   真实行为已钉在 docs\证据\验_print地雷_20261007.py（不靠注释、靠判据）。
        try:
            print('[面板] 位置文件读不了，用默认位置：%s' % e, flush=True)
        except Exception:
            pass
        return None


def _save_pos(x, y):
    """记住位置。**只写这一个文件**，不碰任何闸门状态文件。"""
    try:
        with open(POSFILE, 'w', encoding='utf-8') as f:
            json.dump({'x': int(x), 'y': int(y)}, f)
    except Exception:
        pass


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def collect_state():
    """把闸门状态读成一个字典。只读文件，不问闸门，不计数。"""
    brake = os.path.exists(BRAKE_FLAG)
    if not PASSFILE:
        # 老脚本目录没定位到 → 通行证**读不到**。说「读不到」，不装作「未授权」
        return {'brake': brake, 'state': '未配置', 'detail': '闸门状态目录不可用',
                'scopes': [], 'used': 0, 'limit': 0, 'expires': '—'}
    if not os.path.exists(PASSFILE):
        return {'brake': brake, 'state': '未授权', 'detail': '还没弹过授权窗',
                'scopes': [], 'used': 0, 'limit': 0, 'expires': '—'}
    p = _read_json(PASSFILE, {})
    scopes = p.get('scopes') or [p.get('scope', '')]
    base = {'brake': brake, 'scopes': scopes, 'used': p.get('acts', 0),
            'limit': p.get('acts_limit', 0), 'expires': p.get('expires', '—')}
    if p.get('consumed'):
        base.update({'state': '已结束', 'detail': '会话已被消费（一次性）'})
    elif time.time() > p.get('expires_at', 0):
        base.update({'state': '已过期', 'detail': '通行证过期了，需重新授权'})
    else:
        base.update({'state': '工作中', 'detail': '通行证有效'})
    return base


def tail_gate_log(n=1):
    r"""取闸门日志最后 n 行 —— 「它此刻在干什么」的唯一数据源（不读屏幕）。

    ★ 2026-10-06：**两个文件都读，按时间取最新那行**。
      原因见 GATELOGS 那段注释（读错文件 → 界面僵住两天）。
    日志行形式是 gate.log() 写的 [YYYY-MM-DD HH:MM:SS] msg，
      无时间戳的行计空归到最旧 —— 宁可显得旧，不自矛。

    ★ 同日第二个缺陷：原后最一行 `return lines[-n][1]` 只返回**一行**，
      而文档写的是「最后 n 行」 —— n=2 时实际返回的是**第 2 尾行**，不是最后两行。
      两种读法都能读懂，差别在于读完两个文件之后完全不可能分清。
      按文档改成真的返回最后 n 行（时间倒序）。现场只用 n=1，行为不变。
    """
    if not GATELOGS:
        return '—（闸门状态目录不可用）'
    stamp = re.compile(r'\[(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)\]')
    rows = []
    for p in GATELOGS:
        try:
            with open(p, encoding='utf-8', errors='replace') as f:
                for x in f:
                    x = x.rstrip()
                    if not x.strip():
                        continue
                    m = stamp.search(x)
                    rows.append((m.group(1) if m else '', x))
        except Exception:
            continue          # 单个文件读不到不应让整个面板挂掉
    if not rows:
        return '—（读不到闸门日志）'
    rows.sort(key=lambda t: t[0])
    n = max(1, int(n or 1))
    return '\n'.join(x[1] for x in rows[-n:][::-1])


def _one_line(s, limit=46):
    """压成一行、超长截断。挂件不是日志查看器，别让长文本把布局撑坏。"""
    s = ' '.join(str(s).split())
    return s if len(s) <= limit else s[:limit - 1] + '…'


def _strip_comments_and_docstrings(src):
    """剥掉注释和文档字符串，只留真正会执行的代码。

    用 tokenize 而不是正则：正则处理不了「字符串里含 #」和「三引号跨行」这两种情况，
    而这两种恰恰会让判据误报（今天已经踩过一次）。

    ⚠ 拼接方式是被假样例逼出来才对的三轮（每一轮都是 work/test_widget_guard.py 抓的，
       靠读代码看不出来 —— 因为「加空格」和「不加空格」都只错一半）：
         1) 用空格拼 → `require (` 搜不到「require(」 → 计数违规漏检
         2) 改成无空格 → `import pyautogui` 粘成 `importpyautogui` → import 违规漏检
         3) 最终：**照常用空格拼**，检查一律走正则（_has_call / _has_import），
            正则里的 `\\s*` 同时认得 `require(`、`require (`、`require\n(`。
       —— 教训：**「看起来等价」的两种写法，往往各漏一半**，必须用反例逐个试。
    """
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except Exception:
        # tokenize 失败就退回「只剥 # 注释」，并让调用方知道（返回标记）
        return '\n'.join(l.split('#')[0] for l in src.splitlines())
    return ' '.join(out)


def _has_call(src, name):
    """有没有 `name(` 这样的调用（允许 name 与 ( 之间有空白/换行）。"""
    import re
    return bool(re.search(r'\b' + re.escape(name) + r'\s*\(', src))


def _has_import(src, name):
    """有没有 import 这个模块（允许 from x import y 与各种空白）。"""
    import re
    return bool(re.search(r'\bimport\s+' + re.escape(name) + r'\b', src)
                or re.search(r'\bfrom\s+[\w.]*\s*import\s+[\w., ]*\b'
                             + re.escape(name) + r'\b', src))


# ─────────────────── 按钮：调现成工具，自己不碰闸门 ───────────────────

def tool_cmd(script, *args):
    """怎么调 <另一项目>\tools 里的老脚本。

  源码态：直接用当前解释器跑
  打包后：走 AgentCtl.exe legacy <脚本>（用 exe 自带的解释器，不要求机器上装 Python）
    """
    import sys
    if not TOOLS:
        raise RuntimeError('老脚本目录不可用，按钮动作已拒绝（不是授权问题）')
    if getattr(sys, 'frozen', False):
        return [sys.executable, 'legacy', os.path.join(TOOLS, script)] + list(args)
    return [sys.executable, os.path.join(TOOLS, script)] + list(args)


def brake_status():
    """刹车状态。**只读文件**，不去跑 brake.py（1 fps 跑子进程太重）。"""
    return brake_engaged()


def _enable_dpi_awareness():
    """让本进程知道系统有缩放，界面才清晰。必须在建窗口之前调用。

    依次尝试：per-monitor v2 → per-monitor → system DPI aware。
    都失败也不致命（只是字会糊），所以每一步都吞异常。
    """
    import ctypes
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2（Win10 1703+，最理想）
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return 'per-monitor-v2'
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return 'per-monitor'
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return 'system'
    except Exception:
        return 'none'


def do_brake(action):
    """拉/松手刹。

    ★ 今天改成**直接调 gate**，不再 spawn 老 brake.py 子进程 ★
    原因是我把闸门的急停路径搬走了，可这里还在往老脚本里跑 ——
    老脚本把急停写死成**桌面上**那个文件，闸门读的是 %LOCALAPPDATA% 下的新路径，
    两边对不上，**点「终止」等于没点**。
    一个安全机制的两端读不同的文件，这件事不能靠「下次记得同步」解决。

    仍然满足「挂件不自己实现闸门逻辑」：这里没有任何 if 判断，
    只是把「调现成工具」的对象从老脚本换成了本项目的 gate。
    """
    from agentctl import gate as _g
    if action == 'stop':
        return _g.set_brake("悬浮窗终止按钮")
    return _g.clear_brake()


def do_authorize(scope, minutes, acts):
    r"""请求授权 —— 弹的是 auth_popup，**用户在那个窗里输密钥**，我们看不到。

    ★ 2026-10-06：改走 `core.dispatch('ctl_authorize')`，**不再直接 spawn 老脚本**。
      原因：直接 spawn 时 scope 是**原样透传**的 —— 用户输 'abcd'，
      就会原样存成一个**永远匹配不上的非法范围**，面板却照样显示「档位 abcd」，
      看着像授权成功了，实际上任何动作都会被拒（实测：拒绝原因就是「范围不符」）。
      真相源（parse_scopes → SCOPES → 半角逗号拼接）只在 core.t_authorize 里有一份，
      面板绕开它就等于**自己造了第二个真相源**（与急停标志那次同类）。

    ★ 不违反只读挂件纪律：t_authorize 只拉弹窗、不过闸门，**不消耗动作次数**，
      也不 import act/pyautogui/pynput/uiautomation —— selftest_widget 的两条禁令不受影响。
    """
    from agentctl import core
    r = core.dispatch('ctl_authorize',
                      {'scopes': scope, 'minutes': minutes, 'acts': acts,
                       'actor': 'panel', 'timeout_sec': 300},
                      actor='panel')
    if r.get('ok'):
        d = r.get('data') or {}
        return True, '已放行：%s（%d 分钟 / %d 次）' % (
            '、'.join(d.get('scopes') or []), d.get('minutes') or minutes,
            d.get('acts_limit') or acts)
    return False, r.get('warning') or '授权未通过'


def _run_tool(cmd, timeout):
    """跑老脚本并把输出读回来。

    ⚠ 老脚本用中文 Windows 控制台（GBK/936）输出，直接按 utf-8 解会满屏「��ͣ」乱码
       （实测：终止按钮第一次跑就这毛病）。所以**逐个编码试**，谁解得出来用谁，
       解不出来才退回 replace。
    """
    import subprocess
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception as e:
        return False, '调 %s 失败：%s' % (os.path.basename(cmd[-1]), e)
    best = ''
    for enc in ('utf-8', 'gbk', 'mbcs'):
        try:
            s = (p.stdout or b'').decode(enc)
        except Exception:
            continue
        if '�' not in s:
            best = s
            break
        best = best or s
    for enc in ('utf-8', 'gbk'):
        try:
            e = (p.stderr or b'').decode(enc)
        except Exception:
            continue
        if e.strip():
            return False, e.strip()
    return (p.returncode == 0), best.strip() or '（无输出）'


def selftest_widget():
    """M1 自检：确保这个只读挂件不会变成绕过闸门的后门。

  1) 本文件里不许出现计数接口 —— 显示状态一律只判断
  2) 不许 import 任何能操作屏幕的模块
  3) 状态能读出来（哪怕是「未授权」，也必须是可读的结构，不是异常）
    """
    # ★ 冻结态（PyInstaller）下 .py 原文不在包内 ★
    # 实测踩过：装完的 exe 跑 selftest 直接崩
    #   FileNotFoundError: ...\_internal\agentctl\widget.py
    # 因为这个自检要读**自己的源码原文**做审计（查有没有 require(、
    # 有没有 import 能操作屏幕的模块），而 PyInstaller 只带编译产物。
    #
    # 为什么不在冻结态下把这段跳过：跳过了，装出来的包就**没法自证
    # 没被开后门**——那才是这个自检存在的唯一理由。
    # 所以让源码跟着包走（spec 里单独带一份到 _selftest_src/），
    # 两态都能审计。注意是平铺目录、**不叫 agentctl**，
    # 免得跟真正的包重名把 import 路径搅乱。
    _p = os.path.abspath(__file__)
    if not os.path.exists(_p):
        _meipass = getattr(sys, '_MEIPASS', '')
        if _meipass:
            _p = os.path.join(_meipass, '_selftest_src', 'widget.py')
    src = open(_p, encoding='utf-8').read()
    # ① 先剥注释（否则自己写的说明文字会误伤）
    # ② 再剥文档字符串（__doc__ 里就写着这两个词，不剥必然误报）
    # ⚠ 顺序不能反：早先我把 replace('require(') 放在检查之前，等于把违规先消掉再问「还有没有」，
    #    判据永远返回「通过」—— 是自检对四个假样例的测试把它抓出来的。
    code = _strip_comments_and_docstrings(src)
    bad = []
    if _has_call(code, 'require'):
        bad.append('出现了计数接口 —— 只读挂件不得消耗额度')
    for mod in ('act', 'pyautogui', 'pynput', 'uiautomation'):
        if _has_import(code, mod):
            bad.append('import 了 ' + mod + ' —— 只读挂件不得操作屏幕')
    # ★ 高危 Win32 输入 API（2026-10-05，WorkBuddy 004 的「洞1」最低成本补法）★
    #   它建议把 ctypes 整个加进上面的黑名单。**我没照做，因为那会让自检永远失败**：
    #   本文件第 199 行 _enable_dpi_awareness 就合法地 import ctypes 设 DPI。
    #   → 改成抓**具体的高危调用**，同一个模块里「设 DPI」放行、「发按键」拦下，零误报。
    #   （仍然挡不住「import 一个内部就用 ctypes 的模块」——那是枚举式判据的固有边界，
    #     写进 README 已知局限，不假装解决了。）
    for api in ('SendInput', 'keybd_event', 'mouse_event', 'BlockInput',
                'SetCursorPos', 'SetWindowsHookEx', 'RegisterRawInputDevices'):
        if api in code:
            bad.append('出现了 %s —— 只读挂件不得合成输入或挂钩子' % api)
    st = collect_state()
    if not isinstance(st.get('state'), str) or not st['state']:
        bad.append('状态读不出来')
    return bad, st


def _open_install_dir():
    """在资源管理器里打开本程序所在目录。
    面板装完就缩成右下角一小块，用户想翻配置、看日志、找卸载入口时，
    常常根本不知道它被放在哪儿 —— 右键菜单里给一条路。
    """
    try:
        if getattr(sys, 'frozen', False):
            d = os.path.dirname(sys.executable)
        else:
            d = os.path.dirname(os.path.abspath(__file__))
        os.startfile(d)          # noqa: S606  (Windows 专有 API)
    except Exception as exc:
        try:
            from tkinter import messagebox
            messagebox.showerror('打不开目录', str(exc))
        except Exception:
            pass


# ─────────────────────────── 悬浮窗 ───────────────────────────

BG = '#1e1f26'
FG = '#e6e6e6'
DIM = '#8b8d97'
OK = '#5ac37a'
WARN = '#e0a33e'
BAD = '#e05c5c'
ACCENT = '#5b9bd5'

# ── 任务行（2026-10-06，框架 2026-10-06_面板任务进度.md）──
#
# ★ 防混淆是硬要求，不是配色偏好 ★（框架第 10 节，用户提问后加）
#   1) 三态**颜色必须错开**：「待确认」用琥珀不用绿 ——
#      它本质上不是成功，用绿色会让用户把「等你确认」误读成「已经好了」。
#   2) **不能只靠颜色**：附一个符号（●/◆/✓），色觉障碍时仍可读。
#   3) **声音只用来叫人**：只有 need_me（已交付待用户确认）才响。
#      交付时不响、完成时不响、失败时也不响 ——
#      否则用户会学成「听到声音就是成了」，真出事时反而麻木。
_TASK_COLORS = {"dim": DIM, "accent": ACCENT, "warn": WARN,
                "good": OK, "bad": BAD}
_task_sound_done = {"key": None, "at": 0.0}      # V8：防重复发声


def _task_tick(label):
    """刷新「任务」那一行。**只读**，任何异常都归成「读不到」而不是抛。

    读的是 timeline.db 的 events，**不做二次统计** ——
    面板一旦自己算进度，就会和事实源各说各话（那正是今天面板热键告警的病根）。
    """
    import time as _t
    from agentctl import tasks as _tasks
    from agentctl.core import TIMELINE_DIR
    try:
        import os as _os
        r = _tasks.current(_os.path.join(TIMELINE_DIR, 'timeline.db'))
    except Exception:
        try:
            label.config(text='？ 读不到', fg=DIM)
        except Exception:
            pass
        return
    icon = r.get('icon', '—')
    lab_ = r.get('label', '—')
    name = r.get('name') or ''
    steps, cur = r.get('steps') or 0, r.get('current') or 0
    txt = '%s %s' % (icon, lab_)
    if name:
        txt += '  ' + _one_line(name, 22)
    if steps:
        # 用「4/5」而不是进度条：面板宽度有限，数字比条更容易一眼读准
        txt += '  %d/%d' % (min(cur, steps), steps)
    if r.get('elapsed_sec'):
        m, s = divmod(int(r['elapsed_sec']), 60)
        txt += '  %dm%02ds' % (m, s) if m else '  %ds' % s
    if r.get('why'):
        txt = '%s  %s' % (icon, _one_line(r['why'], 30))
    try:
        label.config(text=txt, fg=_TASK_COLORS.get(r.get('color'), FG))
    except Exception:
        pass

    # V6/V8：只有「待用户确认」才出声，且同一任务只响一次
    key = r.get('task_id') or ''
    if r.get('need_me') and key:
        now = _t.time()
        if _task_sound_done['key'] != key:
            _task_sound_done['key'] = key
            _task_sound_done['at'] = now
            _task_beep()


def _task_beep():
    """轻提示音。**不是完成音** —— 它在问「要你确认一下吗」。

    刻意用低音量短促音：惊吓式的提示音会让人紧张，
    而这里要表达的是「有一件事等你」，不是「出事了」。
    出事的声音是急停的红色横幅，它**不发声**（框架第 10 节分工）。
    """
    try:
        import winsound
        winsound.Beep(880, 90)
    except Exception:
        pass


def run():
    # ★★ 必须在这一行之前设 DPI 感知，否则界面会被 Windows 位图放大 1.5 倍 → 字发糊。
    #    实测（本机）：屏幕 2560 物理像素，Tk 却以为 1707 逻辑像素（=150% 缩放），
    #    而进程 DPI 感知级别 = 0 → Tk 按 96dpi 画 340x214 的窗口，
    #    Windows 再把整张位图拉成 510x321，**字和图标一起被放大，所以糊**。
    #    用户怀疑「换 Java 会不会好点」——Java(Swing) 天生 DPI 友好，但那是治标；
    #    真正的病根是这个进程标志位，设对了 Tk 一样清晰。
    _enable_dpi_awareness()
    import tkinter as tk

    root = tk.Tk()
    root.title('电脑控制平台')
    root.configure(bg=BG)
    root.attributes('-topmost', True)
    root.resizable(False, False)
    # 去掉系统标题栏：省 30px 高度，且标题栏会盖住内容区（第一版就是这么挤没的）
    try:
        root.overrideredirect(True)
    except Exception:
        pass
    try:
        # 默认放右下角：用户的屏幕左边通常开着工作窗口，挂件别挡事
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        pos = _load_pos()
        if pos is None:
            pos = (sw - PANEL_W - 20, sh - PANEL_H - 20)   # 第一次开：右下角
        root.geometry('%dx%d+%d+%d' % (PANEL_W, PANEL_H, pos[0], pos[1]))
    except Exception:
        pass

    def lab(parent, text, fg=FG, size=10, bold=False, **kw):
        kw.setdefault('anchor', 'w')
        # ★ Tk 里字号：正数=磅（随 DPI 缩放），负数=像素（不缩放）。
        #   修好 DPI 感知后窗口是 340 物理像素，磅制字号会按 150% 渲染 → 溢出窗口。
        #   改像素字号布局才稳（DPI 感知 + 像素字号必须一起上，只上一个都不对）。
        return tk.Label(parent, text=text, bg=BG, fg=fg,
                        font=('Microsoft YaHei UI', -abs(size),
                              'bold' if bold else 'normal'),
                        **kw)

    # ★ 用户 2026-10-04 13:30 拍板 J3「无密钥不启动」时的落地方式。
    #   为什么不直接把面板锁死（输密钥才让窗口出现）：
    #     面板上就有「⏹ 终止」键 —— **刹车必须永远够得着**。
    #     把刹车锁进「先输密钥才能看到」的界面，等于出事时逼用户去开终端找刹车。
    #   所以这里的「不启动」= **不进入工作态**：
    #     面板照常起来（可随时开、可随时急停），但整块内容区换成醒目的未授权横幅，
    #     明说「本机处于未启用状态，agent 无法控制这台电脑」。
    #   真正拦得住的是闸门本身 —— 没有有效通行证，任何控制动作都过不了 gate。
    hdr = tk.Frame(root, bg='#2a2c36')
    hdr.pack(fill='x')
    lab(hdr, '电脑控制平台 · AgentCtl', FG, F_TITLE, True).pack(side='left', padx=14, pady=9)

    # ★ 关闭按钮：用户 2026-10-04「控制面板我不知道怎么关」★
    #   病根：为了省 30px 高度做了 overrideredirect(True)，把系统标题栏整个去掉了，
    #   于是连 × 都没了；而底部那个「⏹ 终止」是**急停刹车**，不是关面板，
    #   用户分不清就等于没有出口。这不是用户不会用，是设计上漏了一个。
    #   所以补一个真·关闭：点它退的是面板进程，跟急停是两回事。
    #
    # ★ 为什么不用 root.protocol('WM_DELETE_WINDOW') ★
    #   那是系统标题栏的关闭协议，overrideredirect 之后系统标题栏没了，
    #   协议再正确也没有任何触发入口，等于白写。
    close_btn = tk.Label(hdr, text='✕', bg='#2a2c36', fg=DIM,
                         font=('Microsoft YaHei UI', -abs(F_HEAD), 'bold'),
                         cursor='hand2', padx=6)
    close_btn.pack(side='right', padx=(2, 10))
    state_lbl = lab(hdr, '读取中…', DIM, F_HEAD, True)
    state_lbl.pack(side='right', padx=(0, 10))

    def on_close():
        """关面板。**关面板不等于解除急停** —— 急停是安全状态，
        不该被「关个窗口」这种随手动作带走。
        反过来，agent 正在干活时默默关掉面板 = 用户在毫不知情的情况下
        失去了唯一的可视化入口，所以这时候必须问一句。"""
        from tkinter import messagebox
        try:
            st = collect_state()
        except Exception:
            st = {'brake': True, 'state': '未知'}
        busy = (not st.get('brake')) and st.get('state') == '工作中'
        if busy:
            ans = messagebox.askyesnocancel(
                '关闭电脑控制平台',
                'agent 现在正在控制这台电脑。\n\n'
                '「是」= 先拉下急停再关闭面板（推荐）\n'
                '「否」= 只关面板，agent 继续工作\n'
                '「取消」= 不关',
                icon='warning')
            if ans is None:
                return
            if ans:
                try:
                    do_brake('stop')
                except Exception:
                    pass
        root.destroy()

    def _close_click(e):
        # 返回 'break' 是**必须的**：不返回的话事件继续冒泡到 hdr 的
        # on_press，按下 × 的瞬间整窗就跟着鼠标跑，点「关」变成了拖窗口。
        on_close()
        return 'break'

    close_btn.bind('<Button-1>', _close_click)

    # ── 急停通道自检：热键被占就说出来 ──────────────────────
    # 用户 2026-10-04「组合键 + 弹窗够了」之后，Ctrl+Alt+Home 成了
    # 主要急停方式。**但热键在独立进程里注册，面板自己不知道它成没成。**
    # 不说的话就是骗人：面板好好地显示着，用户以为按热键能停，
    # 真出事按了没反应。宁可开机就告诉他一声。
    # （只查不注册，见 brake_daemon.check_hotkey_free 的注释）
    # ★ 2026-10-06：改成**每帧重查**，不再只查开机那一次。
    #   实测原因：今天 daemon 是面板**开完之后**才起的，而面板自己开机时
    #   查的是「空闲」，把「热键未启用」写死在标题栏上，后来 daemon 起了它一直不改。
    #   而这正是上面那段注释自己警告过的「骗人」，**更危害的是反向**：
    #   daemon 之后挂了，面板仍然显示「热键✓」，用户急事按下去只有被取消的命。
    #   安全标志必须是**实时的**，不能是开机快照。
    #
    #   ★ 三态（W2，2026-10-05）★
    #     ours = 被本项目 brake-daemon 正常占着 —— **这是常态，不是告警**
    #     旧两态判据下，这里每天开机必弹一个红色「⚠ 热键被占」+ 一声响铃。
    #     常态误报会训练用户对这块红字脱敏，等热键真被抢时反而看不见。
    #     所以只有 foreign 才响铃。
    hot_lbl = lab(hdr, '—', FG, F_FOOT)
    hot_lbl.pack(side='right', padx=(0, 4))
    hot_seen = {'state': None}

    def refresh_hotkey():
        """每帧重查热键三态，并只在状态**迁移**时响铃。

        为什么不能每帧都响：tick 是 1 fps，每帧都响 = 不断闹铃，而且用户面前有东西遮着。
        只在 free ↔ ours 之间跳一次才值得一声——那才是真正的「事态变了」。

        ★ 不能用裸 except 吞掉异常（守则里的老经验）：
          今天实测到原版本写的是 `GOOD`，而 widget.py 里的绿色常量名字是 **`OK`**，
          `GOOD` 根本不存在 → NameError → 被外层 `except: pass` 吞掉。
          后果是：**daemon 跑着的时候（就是健康时）标签整个消失**，而 daemon 死了反而显示「未启用」。
          一个裸 except 把真 bug 变成了假告警——这是第二次同样的挡。
          所以这里的异常要同时做两件事：面板不能因为一个显示 bug 整体死掉（急停按钮必须永远够着），
          且必须把错写进日志、把屏幕上的状态改成「待核实」——**不能猜、不能继续冒充正常**。
        """
        try:
            from agentctl.brake_daemon import check_hotkey_free
            st_, _why = check_hotkey_free()
            txt, col = {'ours': ('热键✓', OK), 'free': ('热键未启用', WARN)}.get(
                st_, ('⚠ 热键被占', BAD))
        except Exception as e:
            # 查不到就说查不到，不继续把上一帧的旧状态挂着。
            if hot_seen.get('err') != type(e).__name__:
                hot_seen['err'] = type(e).__name__
                try:
                    import traceback
                    with open(os.path.join(HERE, '..', 'panel_hotkey.err'), 'a',
                              encoding='utf-8') as f:
                        f.write(traceback.format_exc())
                except Exception:
                    pass
            st_ = 'unknown'
            txt, col = '热键待核实', BAD
        hot_lbl.config(text=txt, fg=col)
        if hot_seen['state'] == st_:
            return
        hot_seen['state'] = st_
        if st_ == 'foreign':      # 被删掉了也要弄明白：急停渠道正在流失
            hdr.bell()

    refresh_hotkey()

    # 自由移动：用户 2026-10-04「可以自由移动」。
    # 因为去掉了系统标题栏（overrideredirect），没有拖动的地方了，自己实现一个：
    # 按住顶部这条拖 → 整窗跟着走。松手后记住位置，下次开还在那儿。
    drag = {'on': False, 'dx': 0, 'dy': 0}

    def on_press(e):
        drag['on'] = True
        drag['dx'] = e.x_root - root.winfo_x()
        drag['dy'] = e.y_root - root.winfo_y()

    def on_move(e):
        if drag['on']:
            root.geometry('+%d+%d' % (e.x_root - drag['dx'], e.y_root - drag['dy']))

    def on_release(_e):
        drag['on'] = False
        _save_pos(root.winfo_x(), root.winfo_y())

    for w in (root, hdr):
        w.bind('<ButtonPress-1>', on_press)
        w.bind('<B1-Motion>', on_move)
        w.bind('<ButtonRelease-1>', on_release)
    for child in hdr.winfo_children():
        # 关闭键不参与拖动：绑了 on_press 的话，按下 × 的瞬间整窗就开始挪，
        # 用户在很小的按钮上想点「关」，实际拖了一下窗——点不中，还以为是坏的。
        if child is close_btn:
            continue
        child.bind('<ButtonPress-1>', on_press)
        child.bind('<B1-Motion>', on_move)
        child.bind('<ButtonRelease-1>', on_release)
    try:
        hdr.configure(cursor='fleur')
    except Exception:
        pass

    # 内容区单独装一个容器：横幅只盖它，**下面的按钮条永远露在外面**。
    # 第一版横幅铺在整个窗口上，把「⏹ 终止」也盖住了 —— 那正是这个设计要避免的：
    # 刹车必须永远够得着，出了事用户不能找不到。
    content = tk.Frame(root, bg=BG)
    content.pack(fill='both', expand=True)

    body = tk.Frame(content, bg=BG)
    body.pack(fill='both', expand=True, padx=14, pady=(12, 4))

    rows = {}
    for i, (key, cap) in enumerate((('brake', '急停'), ('scope', '档位'),
                                    ('quota', '额度'), ('expires', '到期'),
                                    ('task', '任务'))):
        lab(body, cap, DIM, F_ROW).grid(row=i, column=0, sticky='w', pady=3)
        v = lab(body, '—', FG, F_ROW)
        v.grid(row=i, column=1, sticky='w', padx=(16, 0), pady=3)
        rows[key] = v

    tk.Frame(body, bg='#33353f', height=1).grid(row=5, column=0, columnspan=2, sticky='ew', pady=11)
    lab(body, '最近动作', DIM, F_LOG).grid(row=6, column=0, sticky='nw')
    # 固定一行：第一版让它自动换行，长日志把下面的按钮顶出窗口了（按钮看不见 = 等于没有）
    last_val = lab(body, '—', FG, F_LOG, justify='left')
    last_val.grid(row=6, column=1, sticky='w', padx=(16, 0))

    # ── 按钮区（2026-10-04 用户：「能点击的吧，加上个终止按键」）──
    # ⚠ 三个按钮都只是**调用现成工具**（brake.py / auth_popup.py），
    #    挂件自己一行闸门逻辑都没有 —— 这样防退化自检才守得住。
    bar = tk.Frame(root, bg=BG)
    bar.pack(fill='x', padx=14, pady=(10, 0))

    def mkbtn(parent, text, fg, cmd):
        return tk.Button(parent, text=text, bg='#2a2c36', fg=fg, activebackground='#3a3d49',
                         activeforeground=fg, relief='flat', bd=0, cursor='hand2',
                         font=('Microsoft YaHei UI', -F_ROW), padx=12, pady=6, command=cmd)

    def toast(msg, bad=False):
        last_val.config(text=msg, fg=BAD if bad else FG)

    def on_stop():
        ok, msg = do_brake('stop')
        toast(('✅ ' if ok else '⛔ ') + msg, bad=not ok)
        root.after(300, tick)

    def on_release():
        from tkinter import messagebox
        if not messagebox.askokcancel(
                '解除急停',
                '解除后 agent 可以重新控制这台电脑。\n\n确定要解除吗？',
                icon='warning'):
            toast('已取消解除')
            return
        ok, msg = do_brake('release')
        toast(('✅ ' if ok else '⛔ ') + msg, bad=not ok)
        root.after(300, tick)

    def on_auth():
        from tkinter import simpledialog
        scope = simpledialog.askstring(
            '请求授权', '要哪一档？\n\n'
            '  A级 只读感知\n  B级 点击滚动\n  C级 打字快捷键\n  D级 拖拽\n\n'
            '写法随你：\n'
            '  B            单档\n'
            '  ABCD         连写＝四档全要\n'
            '  A,B,C        逗号分隔\n'
            '  B级 点击滚动   照抄也行\n\n'
            '（写错会明确报错，不会默默放行）',
            initialvalue='B级 点击滚动', parent=root)
        if not scope:
            toast('已取消授权')
            return
        ok, msg = do_authorize(scope.strip(), 20, 30)
        toast(('✅ ' if ok else '⛔ ') + msg, bad=not ok)
        root.after(300, tick)

    mkbtn(bar, '🔑 授权', OK, on_auth).pack(side='left')
    mkbtn(bar, '⏹ 终止', BAD, on_stop).pack(side='left', padx=6)
    mkbtn(bar, '▶ 解除急停', WARN, on_release).pack(side='left')

    lab(root, '点击只调现成工具 · 不操作屏幕 · 1 fps', DIM, F_FOOT).pack(anchor='w', padx=14, pady=(8, 10))

    # 未授权 / 急停 横幅：盖在内容区正中央，让「没启用」这件事无法被忽略
    banner = tk.Frame(content, bg='#1e1f26')

    def show_banner(title, detail, color):
        try:
            body.pack_forget()
        except Exception:
            pass
        for w in banner.winfo_children():
            w.destroy()
        banner.place(relx=0, rely=0, relwidth=1, relheight=1)
        tk.Label(banner, text=title, bg='#1e1f26', fg=color,
                 font=('Microsoft YaHei UI', -24, 'bold')).pack(pady=(70, 10))
        tk.Label(banner, text=detail, bg='#1e1f26', fg=FG,
                 font=('Microsoft YaHei UI', -F_ROW), wraplength=PANEL_W - 60,
                 justify='center').pack(padx=16)
        b = tk.Button(banner, text='🔑 点这里请求授权', bg='#2a2c36', fg=OK,
                      activebackground='#3a3d49', relief='flat', bd=0,
                      cursor='hand2', font=('Microsoft YaHei UI', -F_ROW),
                      padx=18, pady=9, command=on_auth)
        b.pack(pady=22)

    def hide_banner():
        banner.place_forget()
        body.pack(fill='both', expand=True, padx=10, pady=(8, 2))

    def tick():
        st = collect_state()
        if st['brake']:
            col, txt = BAD, '急停已拉下'
        elif st['state'] == '工作中':
            col, txt = OK, '工作中'
        elif st['state'] in ('已过期', '已结束'):
            col, txt = WARN, st['state']
        else:
            col, txt = DIM, st['state']
        state_lbl.config(text=txt, fg=col)
        # 未启用 = 不进入工作态，横幅盖住内容区（刹车键仍在横幅下方，永远够得着）
        if st['brake']:
            show_banner('已停止', '急停已拉下，一切自动控制停止。\n确认安全后再解除。', BAD)
        elif st['state'] != '工作中':
            show_banner('未启用',
                        '没有有效通行证，agent 无法控制这台电脑。\n%s' % st['detail'], WARN)
        else:
            hide_banner()
        rows['brake'].config(text='已拉下（一切自动控制停止）' if st['brake'] else '松开',
                             fg=BAD if st['brake'] else OK)
        rows['scope'].config(text='、'.join(s for s in st['scopes'] if s) or '—')
        left = max(0, st['limit'] - st['used'])
        rows['quota'].config(text='%d / %d（剩 %d）' % (st['used'], st['limit'], left)
                             if st['limit'] else '—')
        rows['expires'].config(text=st['expires'])
        _task_tick(rows['task'])
        last_val.config(text=_one_line(tail_gate_log(1), 52))
        refresh_hotkey()
        root.after(REFRESH_MS, tick)

    # ── 右键菜单：第二个出口 ──
    # 为什么要有：✕ 只有二十来个像素，用户说过「界面有点小」，
    # 万一还是点不中，得有条兜住的路。右键在哪儿都能唤出来。
    menu = tk.Menu(root, tearoff=0, bg='#2a2c36', fg=FG,
                   activebackground='#3a3d49', activeforeground=FG,
                   font=('Microsoft YaHei UI', -abs(F_ROW)),
                   borderwidth=0, relief='flat')
    menu.add_command(label='立即刷新状态', command=tick)
    menu.add_command(label='在资源管理器中打开', command=_open_install_dir)
    menu.add_separator()
    menu.add_command(label='退出平台面板', command=on_close)

    def show_menu(e):
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    for _w in (root, hdr, close_btn):
        _w.bind('<Button-3>', show_menu)

    tick()
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
