# -*- coding: utf-8 -*-
"""AB 靶场（WinForms）· python 版 —— 属主进程不是终端，绕开「终端默认禁」。

★ 为什么不用 tools\\ab_target_winforms.ps1：
  那个脚本用 pwsh 跑 ⇒ **属主进程名读出来是 pwsh.exe**
  ⇒ act.py 的 BLOCKED_PROCESSES_TERMINAL 命中 ⇒ 靶场被判「敏感窗口」
  ⇒ ctl_click 返回 code=3、ctl_focus 返回 code=7（等 20 秒要人工确认）。
  ⇒ **测真动作耗时根本测不出来**（测到的全是「被敏感窗拦」）。
  ⇒ 这里用 python（tkinter 之外的 WinForms 走 ctypes 太重 ⇒ 用 tkinter 的**原生
    控件名**，但 tkinter 对 UIA 只暴露无名 Pane……）

  ⇒ 所以：**用 python 起一个 WinForms 窗**（用 .NET 反射调 System.Windows.Forms，
    属主进程 = python.exe ⇒ 不算终端），控件用 WinForms 原生控件（UIA 有名字）。

用法：
  python -X utf8 tools\\ab_target_py.py            # 开着
  python -X utf8 tools\\ab_target_py.py --out f.json
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# WinForms 要 STA
# ★ 不要在这里调 SetThreadDpiAwareness —— 本机 shcore 导出的是
#   SetThreadDpiAwarenessContext（不是 SetThreadDpiAwareness）⇒ 直接 AttributeError。
#   pythonnet 拉起 WinForms 时会自己按 DPI 缩放，不需要这里干预。
try:
    ctypes.windll.shcore.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

import clr  # pythonnet

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
from System.Windows.Forms import Form, Application, TextBox, CheckBox, ComboBox, Button, Label  # noqa: E402
from System.Windows.Forms import FormStartPosition  # noqa: E402
from System.Drawing import Size, Point  # noqa: E402

TITLE = "AB靶场 WinForms(PY)｜8 控件"
TEXT_KEYS = ("text_1", "text_2", "text_3")
COMBO_KEYS = ("combo_1", "combo_2")
CHECK_KEYS = ("check_1", "check_2")


def build(out_path: str):
    form = Form()
    form.Text = TITLE
    form.Size = Size(520, 460)
    form.StartPosition = FormStartPosition.CenterScreen
    y = 14
    payload = {}

    def add_row(label, ctrl):
        nonlocal y
        lbl = Label()
        lbl.Text = label
        lbl.Location = Point(14, y + 4)
        lbl.Size = Size(130, 22)
        form.Controls.Add(lbl)
        ctrl.Location = Point(150, y)
        ctrl.Size = Size(330, 24)
        form.Controls.Add(ctrl)
        y += 36

    texts = {}
    for k in TEXT_KEYS:
        tb = TextBox()
        tb.Name = k
        tb.AccessibleName = k            # ★ UIA 要有名字才找得到
        texts[k] = tb
        add_row("文本 %s" % k[-1], tb)

    combos = {}
    for k in COMBO_KEYS:
        cb = ComboBox()
        cb.Name = k
        cb.AccessibleName = k
        cb.Items.AddRange([str(i) for i in range(1, 6)])
        cb.SelectedIndex = 0
        combos[k] = cb
        add_row("下拉 %s" % k[-1], cb)

    checks = {}
    for k in CHECK_KEYS:
        ck = CheckBox()
        ck.Name = k
        ck.AccessibleName = k
        ck.Text = "复选 %s" % k[-1]
        checks[k] = ck
        add_row("", ck)

    btn = Button()
    btn.Name = "submit"
    btn.AccessibleName = "提交"
    btn.Text = "提交"
    btn.Location = Point(150, y)
    btn.Size = Size(330, 32)
    form.Controls.Add(btn)

    def on_click(sender, args):
        for k in TEXT_KEYS:
            payload[k] = texts[k].Text
        for k in COMBO_KEYS:
            payload[k] = combos[k].SelectedItem
        for k in CHECK_KEYS:
            payload[k] = checks[k].Checked
        payload["submit"] = "提交"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
        form.Close()

    btn.Click += on_click
    Application.Run(form)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."), "ab_target_py.json"))
    a = ap.parse_args()
    build(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
