# -*- coding: utf-8 -*-
r"""ab_target.py —— A/B 靶场：8 个控件的纯 tkinter 表单（T4 配套）。

★ 控件（顺序 = Tab 顺序 = 创建顺序）
    文本 1 / 文本 2 / 文本 3  →  下拉 1 / 下拉 2  →  复选 1 / 复选 2  →  [提交]
  ⇒ 基线（pyautogui）开窗后从「文本 1」一路按 Tab 就能不靠坐标把表单填满；
    这一条是**假设**，真机上可先用 `tk_focusNext` 复核一遍（见任务板 T4）。

★ 提交写什么：**固定 8 个键，没别的**
    {"text_1": ..., "text_2": ..., "text_3": ...,
     "combo_1": ..., "combo_2": ...,
     "check_1": true/false, "check_2": true/false,
     "submit_button": "提交"}
  ⚠ 第 8 个控件是按钮，**按钮没有值**；这里记它的可见文字，表示「是提交把它写下来的」——
    不记成 True，免得下游把按钮值当成第 8 个输入值。
  ⚠ 只有按下提交才写文件；`--selftest` 也走同一个 `submit()`，差别只在值从哪来。

★ 三种跑法
  1) 交互（人点）：
        python -X utf8 tools\ab_target.py
  2) CI / 无鼠标（**不建窗**，直接调提交逻辑写一次，rc=0）：
        python -X utf8 tools\ab_target.py --selftest --out %TEMP%\ab_target.json
  3) 采集用（建窗后自动关，默认 5 秒）：
        python -X utf8 tools\ab_target.py --auto-close
        python -X utf8 tools\ab_target.py --auto-close 1.5

★ 不依赖 pyautogui；只用标准库（tkinter / json / argparse）。
★ 窗口标题以「AB靶场」开头 —— `tools\ab_probe.py` 的 pyautogui 分支靠这个找窗。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tkinter as tk
from tkinter import ttk

TITLE_HINT = "AB靶场"
APP_TITLE = "AB靶场｜8 控件｜T4 采集骨架"

TEXT_KEYS = ("text_1", "text_2", "text_3")
COMBO_KEYS = ("combo_1", "combo_2")
CHECK_KEYS = ("check_1", "check_2")
FIELDS = TEXT_KEYS + COMBO_KEYS + CHECK_KEYS
SUBMIT_KEY = "submit_button"
SUBMIT_LABEL = "提交"

COMBO_OPTIONS = {
    "combo_1": ("选项A", "选项B", "选项C"),
    "combo_2": ("档一", "档二"),
}
CHECK_LABELS = {"check_1": "复选 1", "check_2": "复选 2"}
FIELD_LABELS = {
    "text_1": "文本 1（text_1）",
    "text_2": "文本 2（text_2）",
    "text_3": "文本 3（text_3）",
    "combo_1": "下拉 1（combo_1）",
    "combo_2": "下拉 2（combo_2）",
}

# --selftest 用：取**非默认**值（下拉取第二项、复选 1 勾上）⇒ 一眼能看出没走默认。
SAMPLE = {
    "text_1": "selftest-1",
    "text_2": "selftest-2",
    "text_3": "selftest-3",
    "combo_1": COMBO_OPTIONS["combo_1"][1],
    "combo_2": COMBO_OPTIONS["combo_2"][1],
    "check_1": True,
    "check_2": False,
    SUBMIT_KEY: SUBMIT_LABEL,
}


def _resolve_path(raw):
    """把 %TEMP% / ~ / 相对路径展开成绝对路径；空值走 %TEMP%\\ab_target.json。"""
    if raw:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or os.getcwd()
    return os.path.abspath(os.path.join(temp, "ab_target.json"))


def _enable_dpi_awareness():
    """建窗之前声明 DPI 感知：否则 150% 缩放下窗口被系统拉伸，键鼠/截图口径都会偏。

    仓内同款做法见 `src\\agentctl\\widget.py` 的 `_enable_dpi_awareness()`（建窗前调）。
    """
    try:
        import ctypes
    except Exception:
        return False
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def submit(values, out_path):
    """把 8 个值写成 JSON。GUI 的「提交」按钮与 `--selftest` 都走这里（同一条写盘路径）。

    返回 (写到的绝对路径, payload)。
    """
    missing = [key for key in FIELDS if key not in values]
    if missing:
        raise ValueError("缺控件值：%s" % missing)
    payload = {key: values[key] for key in FIELDS}
    for key in CHECK_KEYS:                       # 类型钉住：JSON 里必须是 true/false
        payload[key] = bool(payload[key])
    payload[SUBMIT_KEY] = str(values.get(SUBMIT_KEY, SUBMIT_LABEL))
    if len(payload) != 8:
        raise ValueError("靶场只写 8 个值，实际 %d 个" % len(payload))

    path = _resolve_path(out_path)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path, payload


class TargetApp:
    """8 控件表单。控件创建顺序 = Tab 顺序（见模块头）。"""

    def __init__(self, root, out_path):
        self.root = root
        self.out_path = out_path
        self.text_vars = {key: tk.StringVar(master=root, value="") for key in TEXT_KEYS}
        self.combo_vars = {key: tk.StringVar(master=root, value=COMBO_OPTIONS[key][0])
                           for key in COMBO_KEYS}
        self.check_vars = {key: tk.BooleanVar(master=root, value=False) for key in CHECK_KEYS}
        self.entries = []
        self.order = []          # ★ 键盘 Tab 走它：Entry/Combobox/Checkbutton/Button 全在列（真机实测 2026-10-25）
        self.status = None

    def values(self):
        merged = {}
        merged.update({key: var.get() for key, var in self.text_vars.items()})
        merged.update({key: var.get() for key, var in self.combo_vars.items()})
        merged.update({key: var.get() for key, var in self.check_vars.items()})
        merged[SUBMIT_KEY] = SUBMIT_LABEL
        return merged

    def _on_submit(self):
        try:
            path, payload = submit(self.values(), self.out_path)
        except (OSError, ValueError) as exc:
            self.status.config(text="[红] 写文件失败：%r" % (exc,), foreground="#8B0000")
            print("[红] 写文件失败：%r" % (exc,), file=sys.stderr)
            return
        self.status.config(text="[OK] 已提交 → %s（8 值）" % path, foreground="#0B6623")
        print("[OK] 已提交 → %s" % path)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _focus_first(self):
        if self.entries:
            self.entries[0].focus_force()

    def build(self):
        root = self.root
        root.title(APP_TITLE)
        root.geometry("520x420")
        root.minsize(480, 380)

        form = ttk.Frame(root, padding=14)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        pad = {"padx": 6, "pady": 6}
        row = 0

        for key in TEXT_KEYS:
            ttk.Label(form, text=FIELD_LABELS[key]).grid(row=row, column=0, sticky="w", **pad)
            entry = ttk.Entry(form, textvariable=self.text_vars[key], width=36)
            entry.grid(row=row, column=1, sticky="we", **pad)
            self.entries.append(entry)
            self.order.append(entry)
            row += 1

        for key in COMBO_KEYS:
            ttk.Label(form, text=FIELD_LABELS[key]).grid(row=row, column=0, sticky="w", **pad)
            combo = ttk.Combobox(form, textvariable=self.combo_vars[key],
                                 values=list(COMBO_OPTIONS[key]), state="readonly", width=34)
            combo.grid(row=row, column=1, sticky="we", **pad)
            self.order.append(combo)
            row += 1

        for key in CHECK_KEYS:
            cb = ttk.Checkbutton(form, text=CHECK_LABELS[key], variable=self.check_vars[key],
                                 takefocus=True)
            cb.grid(row=row, column=0, columnspan=2, sticky="w", **pad)
            self.order.append(cb)
            row += 1

        button = ttk.Button(form, text=SUBMIT_LABEL, command=self._on_submit, takefocus=True)
        button.grid(row=row, column=0, columnspan=2, sticky="we", pady=(10, 4))
        self.order.append(button)
        row += 1

        self.status = ttk.Label(form, text="尚未提交（%s）" % os.path.basename(self.out_path),
                                foreground="#555555")
        self.status.grid(row=row, column=0, columnspan=2, sticky="w", padx=6)

        # ★ 2026-10-25 真机实测：ttk 默认的 Tab 遍历在这台机器上**不换焦点**
        #   （pyautogui 连打三行全进文本1）⇒ 显式绑定，Tab/Shift-Tab 由我们按 order 走。
        for w in self.order:
            w.bind("<Tab>", lambda e, w=w: self._hop(w, 1))
            w.bind("<Shift-Tab>", lambda e, w=w: self._hop(w, -1))

        root.after(80, self._focus_first)        # 焦点落在文本 1：键盘基线的起点
        root.lift()

    def _hop(self, w, step):
        try:
            i = self.order.index(w)
            self.order[(i + step) % len(self.order)].focus_set()
        except ValueError:
            pass
        return "break"


def build_app(root, out_path):
    """建好表单并返回 TargetApp —— 单独抽出来，方便在**隐藏窗口**里做构造自检。"""
    app = TargetApp(root, out_path)
    app.build()
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="A/B 靶场：8 控件 tkinter 表单，提交后把 8 个值写成 JSON")
    parser.add_argument("--out", default=None, help=r"JSON 写到哪；默认 %%TEMP%%\ab_target.json")
    parser.add_argument("--selftest", action="store_true",
                        help="不建窗、不碰鼠标，直接调提交逻辑写一次文件并退出 0")
    parser.add_argument("--auto-close", nargs="?", type=float, const=5.0, default=None,
                        metavar="SEC", help="建窗后 N 秒自动关闭（给非交互采集用；默认 5）")
    parser.add_argument("--layout-out", default=None,
                        help="把 8 个控件的屏幕坐标写成 JSON（给点击式基线用；默认不写）")
    args = parser.parse_args(argv)

    out_path = _resolve_path(args.out)

    if args.selftest:
        try:
            path, payload = submit(SAMPLE, out_path)
        except (OSError, ValueError) as exc:
            print("[红] selftest 写不出来：%r" % (exc,), file=sys.stderr)
            return 1
        print("[OK] selftest：不建窗、不碰鼠标；已写 %s" % path)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    _enable_dpi_awareness()
    root = tk.Tk()
    app = build_app(root, out_path)
    if args.layout_out:
        def _dump_layout():
            keys = list(TEXT_KEYS) + list(COMBO_KEYS) + list(CHECK_KEYS) + [SUBMIT_KEY]
            lay = {}
            for key, w in zip(keys, app.order):
                try:
                    lay[key] = [w.winfo_rootx(), w.winfo_rooty(),
                                w.winfo_width(), w.winfo_height()]
                except Exception:
                    pass
            with open(_resolve_path(args.layout_out), "w", encoding="utf-8") as f:
                json.dump(lay, f, ensure_ascii=False, indent=1)
            print("[OK] layout → %s（%d 个）" % (_resolve_path(args.layout_out), len(lay)), flush=True)
        root.after(600, _dump_layout)
    if args.auto_close is not None:
        delay_ms = max(0, int(args.auto_close * 1000))
        root.after(delay_ms, root.destroy)
        print("[OK] --auto-close：%d ms 后自动关闭（目标文件 %s）" % (delay_ms, out_path), flush=True)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
