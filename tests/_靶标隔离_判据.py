# -*- coding: utf-8 -*-
r"""tests\_靶标隔离_判据.py —— 第 33 条判据：**判据不许拿用户真实状态当靶子**。

治的病（2026-10-19 16:08 真跑撞出来的）：
    为验通道保真，我签了 C 级通行证并开了个真记事本当靶标。
    组合键判据（第 29 条）在那个真窗口上真发了一次 Ctrl+V，
    **结果把用户 QQ 里的回复粘进了记事本** —— 窗口标题变成
        *.是什么意思.2先不动 - Notepad
    ⇒ **每跑一次判据，就把用户当时剪贴板里的真实内容灌进一个窗口**。

★ 为什么不用猜、只抓一个可静态判定的事实：
    Ctrl+V **只读**剪贴板、不改它 ⇒ 被污染的不是剪贴板，是**靶标窗**。
    只要判据同时满足「在真窗口上发真键」+「**没有**先接管剪贴板」，
    它就必然把用户当时剪贴板里的内容原样打进那个窗口 —— **无需任何推测**。

★ ★ v1 是**坏的**（第三次踩「判据第一版几乎一定错」那条，见下午框架 §(7)）：
    v1 用正则找 do_keys / ctl_type 等，结果**匹配到了注释、docstring 和
    配置 JSON 里的函数名**（wA 命中的是注释、P2 命中的是 action_tools 数组、
    滚动按键命中的是 docstring）⇒ 误报一片，而且抓不到真正的病灶判据 29
    （它把 act.do_keys 存进 _src 再用，正则压根看不见）。
    v2 改用 **ast**：注释/docstring/字符串在 ast 里都不是节点；且判「引用」
    而非「调用」，这样 _src = act.do_keys 这种写法也抓得到。

两条断言：
  (1) **主断言**：凡引用了真键/真点击/真打字函数（do_keys / do_type / ctl_keys /
      ctl_type / ctl_click / SendInput / keybd_event）的判据，必须同时出现
      剪贴板接管（Set-Clipboard / pyperclip.copy / win32clipboard），否则红。
  (2) 白名单 fail-closed：确需「往真窗口发字」的判据必须在 CLIPBOARD_OWNERS
      登记并写明理由；未登记的命中一律红。
  ★ **注入即红**：注入「发真键且不接管剪贴板」的样例文件，命中数必须 +1。

★ 诚实边界：
  · 本条**只管剪贴板这一条泄漏路径**。别的污染（真窗口抢前台、往用户真实
    应用里打字）**没管** —— 那要逐个人工确认，不是一行 ast 能判的。
  · 本条**纯静态**扫 tests 目录，不需要通行证，用户不在场也能跑。
  · 取「宁可误报」方向：误报代价是多写几行接管代码；漏报代价是环境继续被污染。

退出码：0=绿；1=红；2=验不了（找不到 tests 目录）。
"""
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LF = chr(10)

GREEN, RED, SKIP = [], [], []

# 会往真窗口发真键 / 真点击 / 真打字的函数名
SEND_NAMES = frozenset((
    "do_keys", "do_type", "ctl_keys", "ctl_type", "ctl_click",
    "SendInput", "keybd_event", "do_click", "do_fill",
))
# 剪贴板接管的迹象（把用户那份换成测试标记）
OWNS = re.compile(r"Set-Clipboard|pyperclip\.copy|win32clipboard|clipboard_write")

# 已登记的判据：文件名 -> 理由（fail-closed：未登记的命中一律红）
CLIPBOARD_OWNERS = {
}


def rec(name, ok, detail="", skip=False):
    print(("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"),
                              name, str(detail)[:240])), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def _refs_send(src, fname):
    r"""返回 [(行号, 名字)]：该文件里对 SEND_NAMES 的**真实代码引用**。
    用 ast 而非正则：注释 / docstring / 字符串字面量在 ast 里都不是节点，
    天然排除（v1 就是在这一条上翻车的）。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return None, "语法错误：%s" % e
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr in SEND_NAMES:
            out.append((n.lineno, n.attr))
        elif isinstance(n, ast.Name) and n.id in SEND_NAMES:
            out.append((n.lineno, n.id))
    return out, ""


def main():
    inject = "--inject" in sys.argv
    tests_dir = os.path.join(ROOT, "tests")
    if not os.path.isdir(tests_dir):
        rec("前置·找到 tests 目录", False, tests_dir, skip=True)
        return 2
    files = sorted(f for f in os.listdir(tests_dir) if f.endswith(".py"))
    probe = "_injected_probe.py"
    if inject:
        io.open(os.path.join(tests_dir, probe), "w", encoding="utf-8", newline="").write(
            LF.join(["act.do_keys(hwnd, vk=0x56, ctrl=True)", "print(1)"]))
        files = files + [probe]
        print(("（--inject：已注入「发真键且不接管剪贴板」样例 => 期望命中数 +1）"), flush=True)

    hits, unreadable = [], []
    for f in files:
        if f in CLIPBOARD_OWNERS or f == os.path.basename(__file__):
            continue
        fp = os.path.join(tests_dir, f)
        try:
            src = io.open(fp, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        refs, err = _refs_send(src, f)
        if refs is None:
            unreadable.append((f, err))
            continue
        if not refs or OWNS.search(src):
            continue
        hits.append((f, refs[0]))
    rec("(1) 真窗口发真键的判据必须先接管剪贴板", not hits,
        ("命中 %d 个：%s" % (len(hits), hits[:8])) if hits
        else "0 个；扫了 %d 个判据，没有「发真键却不接管剪贴板」的" % len(files))
    if unreadable:
        rec("附·有判据读不了（ast 解析失败）", False, unreadable[:4])

    if inject and os.path.isfile(os.path.join(tests_dir, probe)):
        try:
            os.remove(os.path.join(tests_dir, probe))
        except OSError:
            pass

    if RED:
        print((LF + "红 %d：%s" % (len(RED), "；".join(RED))), flush=True)
        return 1
    print((LF + "全绿 %d" % len(GREEN)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())