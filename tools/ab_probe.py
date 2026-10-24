# -*- coding: utf-8 -*-
r"""ab_probe.py —— A/B 采集骨架（T4）：只采集，不判红绿，不接模型。

★ 它是什么 / 不是什么
  是**数据采集器**：一个 rep 往 CSV 追加一行，列固定为
        ts, executor, task, rep, ok, steps, elapsed_ms, note
  不是判定器：`ok` / `steps` 一律照抄执行器回传的**原始值**（拿不到就写 `na`），
  本脚本不看画面、不读靶场 JSON、不给通过率。
  理由很直接：采集器一旦开始判定，「A/B 对比」就变成自己给自己发合格证（判据自证）。

★ token 为什么不在进程里测
  本仓没有 LLM，进程内测 token 只能编。⇒ note 列固定带 `token=na`，
  token 走协议侧账本；谁要 token 数，去协议那一层对账，别从这张表倒推。

★ 两个执行器
  · dryrun    —— 完全离线：**不 import pyautogui**（也不 import 任何第三方模块），
                 只回一个「没跑」的桩结果；用来冒烟链路 / CI / 证明采集列能落地。
  · pyautogui —— 仓内**参考基线**（正式 A/B 的 pyautogui 基线在仓外，见任务板 T4 约束）。
                 只有这条分支才 lazy import pyautogui；没装 / 起不来 ⇒ 明确报错退出 2。
                 基线动作是**键盘序列**（Tab 走位 + 敲字 + 空格切换 + 回车提交），
                 **不读回靶场 JSON、不判对错**；靶场见 `tools\ab_target.py`。
                 ⚠ 换靶场 / 换布局时这一段必须重新校准 —— 它只是让骨架能真跑一次的样例。

★ 用法
    python -X utf8 tools\ab_probe.py --executor dryrun --task smoke --out %TEMP%\ab.csv --reps 1
    python -X utf8 tools\ab_probe.py --executor pyautogui --task form8 --out %TEMP%\ab.csv --reps 3

★ 退出码
    0 = 行写完了
    1 = 参数 / IO / 已有 CSV 表头冲突
    2 = pyautogui 不可用（仅 pyautogui 分支）
    3 = 找不到靶场窗口（仅 pyautogui 分支）
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys
import time

# 列顺序 = CSV 表头 = 下游消费口径。★ 中途不许改（改了 A/B 两批就不可比）。
HEADER = ("ts", "executor", "task", "rep", "ok", "steps", "elapsed_ms", "note")

# 与 tools\ab_target.py 的窗口标题前缀同源；pyautogui 分支靠它找窗。
TARGET_TITLE_HINT = "AB靶场"

# ★ token 不在本进程测（本仓无 LLM）；这行会进 note 列，把口径钉在数据行上。
TOKEN_NOTE = "token=na（本仓无 LLM；token 走协议侧计，进程内不测）"


class _Parser(argparse.ArgumentParser):
    """用法错误统一走 1（参数/IO），别和 2（pyautogui 不可用）撞码。"""

    def error(self, message):
        self.print_usage(sys.stderr)
        print("%s: 参数错误：%s" % (self.prog, message), file=sys.stderr)
        raise SystemExit(1)


def _positive_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("要整数，收到 %r" % (text,))
    if value < 1:
        raise argparse.ArgumentTypeError("--reps 至少 1，收到 %d" % value)
    return value


def _resolve_path(raw, default_name):
    """把 %TEMP% / ~ / 相对路径展开成绝对路径；空值走默认目录。"""
    if raw:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or os.getcwd()
    return os.path.abspath(os.path.join(temp, default_name))


def run_dryrun(task):
    """离线桩：不碰输入设备、不 import pyautogui。

    返回的 `ok` / `steps` 是 `na` —— 意思是「没跑，所以没有原始结果」，
    不是「跑失败了」。判定不归这里管。
    """
    return {
        "ok": "na",
        "steps": "na",
        "note": "dryrun 桩：未执行任何输入（task=%s 只是标签）；ok/steps=na 表示没跑，不是判定" % task,
    }


def _find_target_window(title_hint):
    """用标准库 ctypes 找**可见**窗口（标题含 title_hint）。返回 (hwnd, title) 或 (None, None)。"""
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    hits = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):
        try:
            if u32.IsWindowVisible(hwnd):
                length = u32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    u32.GetWindowTextW(hwnd, buf, length + 1)
                    if title_hint in buf.value:
                        hits.append((int(hwnd), buf.value))
        except Exception:
            pass
        return True

    u32.EnumWindows(enum_proc(_callback), 0)
    return hits[0] if hits else (None, None)


def run_pyautogui(task):
    """参考基线：只在**这条分支**里 lazy import pyautogui。"""
    try:
        import pyautogui
    except Exception as exc:                                   # 没装 / 缺依赖 / 起不来
        print("[红] pyautogui 不可用：%r" % (exc,), file=sys.stderr)
        print("     仓内这份只是参考基线（正式基线在仓外维护）；先装好再跑，"
              "或改用 --executor dryrun 冒烟。", file=sys.stderr)
        raise SystemExit(2)

    hwnd, title = _find_target_window(TARGET_TITLE_HINT)
    if not hwnd:
        print("[红] 找不到靶场窗口（标题需含 %r）。先起靶场：\n"
              "     python -X utf8 tools\\ab_target.py" % TARGET_TITLE_HINT, file=sys.stderr)
        raise SystemExit(3)

    import ctypes

    u32 = ctypes.windll.user32
    pyautogui.FAILSAFE = True      # 甩鼠标到屏幕左上角即中止：基线也不能失控
    pyautogui.PAUSE = 0.08
    u32.ShowWindow(hwnd, 9)        # SW_RESTORE
    try:
        u32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.4)

    steps = 0

    def _press(key):
        nonlocal steps
        pyautogui.press(key)
        steps += 1

    def _write(text):
        nonlocal steps
        pyautogui.write(text)
        steps += 1

    try:
        _write("ab-probe")                       # 文本 1（靶场开窗时焦点就在文本 1）
        _press("tab")
        _write("ab-probe")                       # 文本 2
        _press("tab")
        _write("ab-probe")                       # 文本 3
        _press("tab")                            # 下拉 1
        _press("down")
        _press("return")
        _press("tab")                            # 下拉 2
        _press("down")
        _press("return")
        _press("tab")
        _press("space")                          # 复选 1
        _press("tab")
        _press("space")                          # 复选 2
        _press("tab")
        _press("return")                         # 提交
    except Exception as exc:
        print("[红] 基线动作中途失败：%r（已发出 %d 步）" % (exc, steps), file=sys.stderr)
        raise SystemExit(1)

    return {
        "ok": "na",
        "steps": steps,
        "note": ("pyautogui 参考基线：键盘序列已发出 %d 步（窗口 %r）；"
                 "未读回靶场 JSON、不判对错；task=%s 目前不区分动作" % (steps, title, task)),
    }


def _open_csv(path):
    """打开 CSV：新文件写表头，老文件只追加；表头对不上就停（别把两种口径混在一张表里）。"""
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    existing = os.path.exists(path) and os.path.getsize(path) > 0
    if existing:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            first = fh.readline().rstrip("\r\n")
        if first != ",".join(HEADER):
            print("[红] 已有 CSV 的表头不是本脚本的口径：%r" % (first,), file=sys.stderr)
            print("     换一个 --out，或先把旧表挪走（混表会让 A/B 不可比）。", file=sys.stderr)
            raise SystemExit(1)
        handle = open(path, "a", encoding="utf-8", newline="")
        writer = csv.writer(handle, lineterminator="\n")
        return handle, writer
    handle = open(path, "w", encoding="utf-8", newline="")
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(HEADER)
    handle.flush()
    return handle, writer


def main(argv=None):
    parser = _Parser(description="A/B 采集骨架：只采集耗时/步数/原始回传，不判对错、不测 token")
    parser.add_argument("--executor", choices=("dryrun", "pyautogui"), default="dryrun",
                        help="执行器：dryrun 完全离线；pyautogui 为仓内参考基线（默认 dryrun）")
    parser.add_argument("--task", default="smoke", help="任务 id（A/B 两侧必须同名才可比）")
    parser.add_argument("--out", default=None, help=r"CSV 路径；默认 %%TEMP%%\ab.csv")
    parser.add_argument("--reps", type=_positive_int, default=1,
                        help="跑几遍（每遍一行，rep 从 1 开始；默认 1）")
    args = parser.parse_args(argv)

    out_path = _resolve_path(args.out, "ab.csv")

    # 硬保证：dryrun 分支必须完全离线。若有什么东西提前把 pyautogui 拉进来了，这里当场红。
    if args.executor == "dryrun" and "pyautogui" in sys.modules:
        print("[红] dryrun 分支不该载入 pyautogui —— 有人提前 import 了它", file=sys.stderr)
        return 1

    runner = run_dryrun if args.executor == "dryrun" else run_pyautogui

    handle, writer = _open_csv(out_path)
    try:
        for rep in range(1, args.reps + 1):
            started = time.perf_counter()
            raw = runner(args.task)
            elapsed_ms = int(round((time.perf_counter() - started) * 1000))
            row = [
                # 微秒而不是毫秒：dryrun 的 rep 可能落在同一毫秒里，别让 ts 失去「先后」。
                datetime.datetime.now().astimezone().isoformat(timespec="microseconds"),
                args.executor,
                args.task,
                rep,
                raw.get("ok", "na"),
                raw.get("steps", "na"),
                elapsed_ms,
                "%s；%s" % (raw.get("note", ""), TOKEN_NOTE),
            ]
            writer.writerow(row)
            handle.flush()
            print("[OK] rep=%d ok=%r steps=%r elapsed=%dms" % (rep, row[4], row[5], elapsed_ms))
    finally:
        handle.close()

    print("[OK] 已写入 %s（%d 行；token 不在进程内测，看 note 列）" % (out_path, args.reps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
