# -*- coding: utf-8 -*-
r"""tests\_wS_靶标进程.py —— 端到端判据的**靶窗宿主**（窗S 夹具，DSH 2026-10-17 三改）

为什么不用 python 宿主（第一、二版都栽在这里）：
    自指保护（窗K/P0）**按属主进程名硬拒 `python.exe`**（授权弹窗/急停弹窗都是 python 起的，
    见 legacy_tools\act.py 的 BLOCKED_PROCESSES_HARD）。所以任何 python 进程拥有的窗
    **永远点不到**——那不是缺陷，是设计。⇒ 靶窗必须由**非 Python** 进程拥有。

做法：现场用 .NET Framework 的 csc.exe 编一个极小 WinForms 靶（缓存在 %TEMP%），
    它是普通桌面程序（`_wS_target.exe`），不在任何黑名单里；
    WinForms 窗的 PrintWindow 抓像素是通的（昨夜 R2 实证过）。

用法（判据里起它）：
    python -X utf8 tests\_wS_靶标进程.py <状态文件>
    起来后往 stdout 打一行 JSON：{"title","pid","state"}；
    窗体上有个复选框，**点中就把 "1" 写进状态文件**（判据用文件判"靶被打勾"，不用跨进程 BM_GETCHECK）。
    ★ 不用 stdin/stdout 做握手（会在子进程沉默时永久挂住）；判据拿到一行就走，退出时 terminate 它。
"""
import json
import os
import subprocess
import sys
import tempfile

CS = r"""
using System;
using System.IO;
using System.Windows.Forms;

class T {
    [STAThread]
    static void Main(string[] a) {
        string state = a.Length > 0 ? a[0] : "state.txt";
        var f = new Form();
        f.Text = "窗S_自起测试靶";
        f.Width = 380; f.Height = 200;
        f.StartPosition = FormStartPosition.Manual;
        f.Location = new System.Drawing.Point(300, 300);
        var cb = new CheckBox();
        cb.Text = "靶：点我（窗S 自起）";
        cb.Left = 30; cb.Top = 45; cb.Width = 280; cb.Height = 30;
        cb.CheckedChanged += delegate { try { File.WriteAllText(state, cb.Checked ? "1" : "0"); } catch {} };
        f.Controls.Add(cb);
        f.Shown += delegate { try { File.WriteAllText(state, "0"); } catch {} };
        Application.Run(f);
    }
}
"""


def build_exe():
    box = os.path.join(tempfile.gettempdir(), "_wS_target_build")
    exe = os.path.join(box, "_wS_target.exe")
    if os.path.isfile(exe):
        return exe
    os.makedirs(box, exist_ok=True)
    cs = os.path.join(box, "_wS_target.cs")
    with open(cs, "w", encoding="utf-8") as f:
        f.write(CS)
    csc = None
    for p in (r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
              r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"):
        if os.path.isfile(p):
            csc = p
            break
    if not csc:
        raise RuntimeError("找不到 .NET Framework 的 csc.exe（靶窗编不出来）")
    r = subprocess.run([csc, "/nologo", "/target:winexe", "/out:" + exe, cs],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not os.path.isfile(exe):
        raise RuntimeError("csc 编译失败：%s %s" % ((r.stdout or "")[-400:], (r.stderr or "")[-400:]))
    return exe


def main():
    state = (sys.argv[1] if len(sys.argv) > 1
             else os.path.join(tempfile.gettempdir(), "_wS_state.txt"))
    exe = build_exe()
    p = subprocess.Popen([exe, state])
    # ★ 记下**靶 exe 自己的 pid**：它是我（启动器）的孙进程，terminate 我杀不掉它。
    try:
        with open(os.path.splitext(state)[0] + ".exe.pid", "w") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    print(json.dumps({"title": "窗S_自起测试靶", "pid": p.pid, "state": state}), flush=True)
    try:
        p.wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
