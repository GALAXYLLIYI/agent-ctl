# -*- coding: utf-8 -*-
r"""tests\_组合键通道_判据.py —— 第 29 条判据：**组合键不许走后台 PostMessage**。

治的病（2026-10-19 窗口D 实跑「把分镜脚本发到微信群」时真踩出）：
  `act.do_keys` 的路线 P 会先 PostMessage 一次按键，再用**前后截图是否有变化**判成败。
  对 `ctrl=True`（Ctrl+V / Ctrl+A / …）这条路是**错的**，而且是**判不出来的错**：
    · PostMessage 把「Ctrl down」与「V down」分成两条 WM_KEYDOWN 投过去；
    · 目标按 wParam 直接合成字符时（**Qt / Electron / 自绘控件都是这一类**），
      Ctrl 修饰位压根没参与 ⇒ 打出一个**字面量 v**；
    · 而「多了一个字面量 v」**同样会让画面变化** ⇒ 像素判据读成「后台按键生效」
      ⇒ `return 0` ⇒ **前台兜底那条真正正确的路压根走不到**。
  真跑留下的证据（微信 Qt 窗口，输入框里出现 `vvaaav`，日志逐行写着
  「✔ 后台按键生效（像素证据有变化）」）—— 字面量 v 本身**就是**它引以为傲的那份证据。

  ★ 这条病属于**输入通道**，不属于感知通道：按项目自己的口径（§㊾ 窗口A
    「输入/事件通道层是独立失败源」），组合键有没有带上修饰位**不能交给像素去裁**。

三条断言（真记事本窗口，被测函数是**真函数**；只包一层计数器，不改产品行为）：
  ① 组合键（ctrl=True）⇒ `_post_keys` **一次都没被调**
  ② 普通键（ctrl=False）⇒ `_post_keys` **被调了**（证明只砍组合键，没误伤路线 P）
  ③ 反证（`--inject`）：把「组合键也走后台」塞回去 ⇒ ① 必须红 ⇒ 本判据红

诚实边界（必须写在这里，不许含糊）：
  本条**证明的是路由决策**，不是 Qt 上的端到端复现。
  Qt 端到端复现需要环境里有一个 Qt 应用；本机那个是**用户的微信**，
  拿用户正在用的聊天窗口当靶子会往群里打脏字，因此**不进自动化判据**。
  端到端的证据是 2026-10-19 的真跑记录（见上），人可复核。

退出码：0=绿；1=红；2=验不了（无通行证 / 起不出窗 / 额度用尽 —— **2 不许当绿读**）。
"""
import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _find_legacy_tools():
    r"""定位真源 act.py 所在目录。**不许把盘符写死**（tests\_M_可移植性判据.py 的红线）。

    顺序：环境变量 → 仓内 legacy_tools/ → 兄弟目录 dsh-viewer-plugin/tools/。
    """
    cands = []
    env = os.environ.get("AGENTCTL_LEGACY_HOME")
    if env:
        cands.append(env)
    cands.append(os.path.join(ROOT, "legacy_tools"))
    parent = os.path.dirname(ROOT)
    cands.append(os.path.join(parent, "dsh-viewer-plugin", "tools"))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "act.py")):
            return c
    return ""


GREEN, RED, SKIP = [], [], []


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, str(detail)[:200]), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def notepad():
    u = ctypes.windll.user32
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        c = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, c, 64)
        if c.value == "Notepad" and u.IsWindowVisible(h):
            hits.append(int(h))
        return True
    u.EnumWindows(cb, 0)
    return hits[0] if hits else 0


def main():
    inject = "--inject" in sys.argv
    home = _find_legacy_tools()
    if not home:
        rec("前置·找到真源 act.py", False, "三处候选都没找到 act.py", skip=True)
        return 2
    # ★ 2026-10-19 修「AUTHDIR 两本账」：本判据**直连** legacy act.py（不经 core），
    #   拿不到 core 注入的 AGENTCTL_AUTHDIR ⇒ 会去读 legacy_tools\_auth（空的）报"没有通行证"。
    #   这里显式补上：优先用 core 认定的那本账。
    try:
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from agentctl import gate as _coregate
        if getattr(_coregate, "PASSFILE", None):
            os.environ.setdefault("AGENTCTL_AUTHDIR", os.path.dirname(_coregate.PASSFILE))
    except Exception:
        pass
    sys.path.insert(0, home)
    import act

    # ⚠ act.gate 是随包那层 gate.py，它的 check() 返回 **2** 元组；
    #   src\agentctl\gate.py 的 check() 返回 3（多一个 code）。别照抄 src 的解包。
    _ck = act.gate.check(act.gate.C)
    ok_pass, why = _ck[0], _ck[1]
    if not ok_pass:
        rec("前置·C 级通行证", False, why, skip=True)
        return 2
    hwnd = notepad()
    if not hwnd:
        rec("前置·真记事本窗口", False, "起不来记事本", skip=True)
        return 2

    calls = []
    _real_post = act._post_keys

    def _counting_post(*a, **kw):
        calls.append(a[1] if len(a) > 1 else None)
        return True

    act._post_keys = _counting_post
    _src = act.do_keys

    def _do_keys_prefix_combo(hwnd, vk=0x41, ctrl=False, times=1, dry=False, **kw):
        if ctrl:
            _counting_post(hwnd, vk, ctrl, times)
        return _src(hwnd, vk=vk, ctrl=ctrl, times=times, dry=dry, **kw)

    fn = _do_keys_prefix_combo if inject else _src
    try:
        calls.clear()
        fn(hwnd, vk=0x56, ctrl=True, times=1)
        rec("① 组合键 ⇒ 后台 PostKeys 一次都没被调", len(calls) == 0,
            "被调 %d 次" % len(calls))

        calls.clear()
        fn(hwnd, vk=0x56, ctrl=False, times=1)
        rec("② 普通键 ⇒ 后台 PostKeys 仍被调（没误伤路线 P）", len(calls) >= 1,
            "被调 %d 次" % len(calls))
    finally:
        act._post_keys = _real_post

    if RED:
        print("\n红 %d：%s" % (len(RED), "；".join(RED)), flush=True)
        return 1
    print("\n全绿 %d" % len(GREEN), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
