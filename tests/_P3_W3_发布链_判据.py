# -*- coding: utf-8 -*-
r"""W3 判据 · 发布链硬伤：cp312 版本门（install.ps1）+ msvcp140 C-0（不分发）

★ 本判据管两件（都是「装出来是坏的」这一类硬伤）：

  【A · cp312 硬伤（卡所有用户）】
    事实：`vendor/` 里 7 个 winrt wheel 全是 `cp312-cp312-win_amd64`（**非 abi3**）
    ⇒ 3.13+ 加载 winrt 必失败。本机实测：
        Python 3.12 ⇒ LOAD-OK
        Python 3.14 ⇒ ModuleNotFoundError: No module named 'winrt._winrt'
                        （去掉 cp312 的 ABI tag 让它找到文件 ⇒
                         ImportError: DLL load failed while importing _winrt）
    ⇒ `install.ps1` 原来只写「下限 3.10」、候选里还有 `py -3`（本机解析到 3.14）
      ⇒ **装出来可能是坏的**。
    ⇒ 修法（主控拍板 ①）：把解释器**窗口**钉死在 **[3.12, 3.13)** ——
      下限 3.12，**3.13+ 明确拒绝**（明确报错 + 给可操作提示，不许静默降级）。

  【B · msvcp140 C-0（不分发）】
    `msvcp140.dll` 是微软专有件，条款写明再分发 "limited to licensed Visual Studio users"
    ⇒ 本仓**不再分发**它（文件已从 `vendor\winrt\` 移除），程序走**目标机 System32 自带那份**；
    目标机缺 VC++ 运行库时，`install.ps1` **只指路**微软官网的官方 vcredist。

★ **版本门是「可执行」的**：本判据把 `install.ps1` 里 `# >>> PY-GATE-BEGIN` … `# <<< PY-GATE-END`
  那段**逐字抽出来**，用 pwsh 实跑（喂版本号进去 ⇒ 落在窗口内退出 0，窗口外退出非 0）。
  ⇒ 判的不是"脚本里写了什么字"，而是"**真跑会怎样**"。

★ **注入必红**：四处注入各对应一组断言（改坏 ⇒ 必须报警）：
    INJ-1 去掉版本门的**上限**（3.13/3.14 会被放行）   ⇒ A-5/A-6 必须红
    INJ-2 把**下限**改回 3.10（3.10 会被放行）        ⇒ A-2 必须红
    INJ-3 把 `msvcp140.dll` 塞回 vendor（又会被分发） ⇒ B-1 必须红
    INJ-4 删掉 install.ps1 的 VC++ 运行库检测与指引   ⇒ B-3 必须红

★ 本判据**离线**：只读文件 + 跑一段纯 PowerShell 版本门（不装任何东西、不碰窗口、不碰真 GUI）。

用法：
    python -X utf8 tests\_P3_W3_发布链_判据.py            # 正跑（期望 exit=0）
    python -X utf8 tests\_P3_W3_发布链_判据.py --inject   # 注入自证（期望 exit=1）

退出码：0=全绿 ｜ 1=有红（缺陷仍在 / 注入被逮到）｜ 2=注入没让行为退化（判据没牙）
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

INSTALL_PS1 = os.path.join(ROOT, "install.ps1")
PACK_DIST = os.path.join(ROOT, "tools", "pack_dist.py")
VENDOR_DLL = os.path.join(ROOT, "vendor", "winrt", "msvcp140.dll")
ZIP = os.path.join(ROOT, "dist", "agentctl.zip")

GATE_BEGIN = "# >>> PY-GATE-BEGIN"
GATE_END = "# <<< PY-GATE-END"

# 窗口：落在 [3.12, 3.13) 才算可用
WINDOW_OK = "3.12"
WINDOW_TOO_OLD = ("3.10", "3.11")
WINDOW_TOO_NEW = ("3.13", "3.14")


# ══════════════════════════════════════════════════════════════════════════
# 一 · 版本门：逐字抽出 install.ps1 的 PY-GATE 段，用 pwsh 实跑
# ══════════════════════════════════════════════════════════════════════════
def extract_gate(install_ps1):
    """抽出 install.ps1 里 `# >>> PY-GATE-BEGIN` … `# <<< PY-GATE-END` 那段（逐字）。"""
    with io.open(install_ps1, encoding="utf-8-sig") as f:
        text = f.read()
    i = text.find(GATE_BEGIN)
    j = text.find(GATE_END)
    if i < 0 or j < 0 or j < i:
        return None
    return text[i:j + len(GATE_END)]


def pick_shell():
    """优先 pwsh（PowerShell 7+），退到 Windows PowerShell 5.1。"""
    for exe in ("pwsh", "powershell"):
        p = shutil.which(exe)
        if p:
            return p
    return None


def run_gate(install_ps1, version):
    """跑抽出的版本门，喂一个版本号 ⇒ 返回退出码（None = 跑不起来）。"""
    block = extract_gate(install_ps1)
    if not block:
        return None
    shell = pick_shell()
    if not shell:
        return None
    tmp = tempfile.mkdtemp(prefix="_W3_门_")
    try:
        script = os.path.join(tmp, "gate.ps1")
        with io.open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write(block)
            f.write("\n$rc = Test-PyVersion ([Version]'%s')\nexit $rc\n" % version)
        p = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", script],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        return p.returncode
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════
# 二 · 检查组
# ══════════════════════════════════════════════════════════════════════════
def checks(install_ps1=INSTALL_PS1, vendor_dll=VENDOR_DLL,
           pack_dist_py=PACK_DIST, zip_path=ZIP):
    """跑全部检查。返回 [(id, 说明, 期望, 实得, 通过?)]。"""
    out = []

    def add(aid, desc, want, got):
        out.append((aid, desc, want, got, got == want))

    # ── A 组 · cp312 版本门 ────────────────────────────────────────────
    block = extract_gate(install_ps1)
    add("A-1", "install.ps1 里有可抽取的 PY-GATE 块（判据要能实跑它）",
        True, bool(block))
    for v in WINDOW_TOO_OLD:
        rc = run_gate(install_ps1, v)
        add("A-2", "Python %s ⇒ 退出码非 0（太老，不许放行）" % v,
            "非0", "非0" if (rc is not None and rc != 0) else ("0" if rc == 0 else "跑不起来"))
    rc = run_gate(install_ps1, WINDOW_OK)
    add("A-3", "Python %s ⇒ 退出码 0（窗口内，必须放行）" % WINDOW_OK,
        "0", "0" if rc == 0 else ("非0:%s" % rc if rc is not None else "跑不起来"))
    for v in WINDOW_TOO_NEW:
        rc = run_gate(install_ps1, v)
        add("A-4", "Python %s ⇒ 退出码非 0（★太新：cp312 硬绑定，装出来是坏的）" % v,
            "非0", "非0" if (rc is not None and rc != 0) else ("0" if rc == 0 else "跑不起来"))
    with io.open(install_ps1, encoding="utf-8-sig") as f:
        ps = f.read()
    add("A-5", "Find-Python 走版本门（出现 Test-PyVersion 的调用）",
        True, "Test-PyVersion" in ps)
    add("A-6", "★不许再有「只比下限」的旧判据（`$v -ge $MinPy`）",
        False, "$v -ge $MinPy" in ps)
    add("A-7", "拒绝时的话要说清「不在受支持窗口」",
        True, "不在受支持窗口" in ps)
    add("A-8", "拒绝时的话要说清原因（cp312 / 非 abi3）",
        True, ("cp312" in ps and "abi3" in ps))
    add("A-9", "拒绝时给可操作提示（winget / python.org）",
        True, ("winget install Python.Python.3.12" in ps
               and "python.org/downloads" in ps))

    # ── B 组 · msvcp140 C-0 ───────────────────────────────────────────
    add("B-1", "★仓内没有 vendor\\winrt\\msvcp140.dll（我们不分发它）",
        False, os.path.isfile(vendor_dll))
    try:
        p = subprocess.run(["git", "-C", ROOT, "ls-files", "vendor/winrt/msvcp140.dll"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        tracked = (p.stdout or "").strip()
    except Exception:
        tracked = "<git 跑不起来>"
    add("B-2", "★git 也不再跟踪它（公开仓不带微软专有二进制）",
        "", tracked)
    add("B-3", "install.ps1 真的检测目标机 System32\\msvcp140.dll（看代码，不看注释）",
        True, ("$vcrt = Join-Path" in ps and "Test-Path $vcrt" in ps))
    add("B-4", "install.ps1 缺时**只指路**微软官网的官方 vcredist（看 URL，不看注释）",
        True, ("vc_redist.x64.exe" in ps and "vc_redist.x86.exe" in ps))
    add("B-5", "★install.ps1 明说「我们不分发」它",
        True, "不分发" in ps)
    with io.open(pack_dist_py, encoding="utf-8") as f:
        pd = f.read()
    add("B-6", "★pack_dist 不调 PyInstaller ⇒ 不会从 System32 收任何 dll",
        False, "pyinstaller" in pd.lower())
    if os.path.isfile(zip_path):
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
        hit = [n for n in names if n.lower().endswith("msvcp140.dll")]
        add("B-7", "★出包里没有 msvcp140.dll（实包核）",
            0, len(hit))
    else:
        add("B-7", "★出包里没有 msvcp140.dll（实包核）—— 包不存在 ⇒ 验不了",
            "验不了", "验不了")

    return out


# ══════════════════════════════════════════════════════════════════════════
# 三 · 注入矩阵（每处注入都必须让对应断言转红）
# ══════════════════════════════════════════════════════════════════════════
def _mutate(src, old, new):
    """把 src 复制一份并替换一处文本（要求唯一命中）。"""
    with io.open(src, encoding="utf-8-sig") as f:
        text = f.read()
    n = text.count(old)
    if n != 1:
        raise AssertionError("锚点出现 %d 次（要求唯一）⇒ 注入失效" % n)
    base = tempfile.mkdtemp(prefix="_W3_注入_")
    dst = os.path.join(base, "install.ps1")
    with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace(old, new, 1))
    return dst, base


def do_inject():
    print("注入只发生在 %TEMP% 的副本上，现役一个字节不动。\n", flush=True)
    problems, all_caught = [], True

    def report(iid, desc, rows, targets):
        nonlocal all_caught
        by_id = {r[0]: r for r in rows}
        missed = [t for t in targets if t not in by_id or by_id[t][4]]
        if missed:
            all_caught = False
            problems.append("%s 目标断言没红：%s" % (iid, missed))
        extra = [r[0] for r in rows if not r[4] and r[0] not in targets]
        print("【%s】%s" % (iid, desc), flush=True)
        print("     目标断言 %s ⇒ %s" % (list(targets),
                                      "全红 ✔（改坏被逮到）" if not missed
                                      else "★ 没红：%s" % missed), flush=True)
        if extra:
            print("     （附带转红：%s —— 连带影响，正常）" % extra, flush=True)

    # INJ-1：去掉版本门的**上限**
    base = None
    try:
        dst, base = _mutate(INSTALL_PS1,
                            "    if ($V -ge $MaxPy) { return 2 }      # 太新：cp312 硬绑定，3.13+ 加载不了 winrt",
                            "    if ($false) { return 2 }")
        report("INJ-1", "去掉版本门的上限（3.13/3.14 会被放行）",
               checks(install_ps1=dst), ("A-4",))
    except AssertionError as e:
        all_caught = False
        problems.append("INJ-1 注入失败：%s" % e)
        print("【INJ-1】★ 注入失败：%s" % e, flush=True)
    finally:
        if base:
            shutil.rmtree(base, ignore_errors=True)

    # INJ-2：把下限改回 3.10
    base = None
    try:
        dst, base = _mutate(INSTALL_PS1,
                            "$MinPy = [Version]'3.12'",
                            "$MinPy = [Version]'3.10'")
        report("INJ-2", "把下限改回 3.10（3.10 会被放行）",
               checks(install_ps1=dst), ("A-2",))
    except AssertionError as e:
        all_caught = False
        problems.append("INJ-2 注入失败：%s" % e)
        print("【INJ-2】★ 注入失败：%s" % e, flush=True)
    finally:
        if base:
            shutil.rmtree(base, ignore_errors=True)

    # INJ-3：把 msvcp140.dll 塞回 vendor（又会被分发）
    base = tempfile.mkdtemp(prefix="_W3_注入_")
    try:
        fake = os.path.join(base, "vendor", "winrt")
        os.makedirs(fake)
        with io.open(os.path.join(fake, "msvcp140.dll"), "wb") as f:
            f.write(b"stub")
        report("INJ-3", "把 msvcp140.dll 塞回 vendor（又会被分发）",
               checks(vendor_dll=os.path.join(fake, "msvcp140.dll")), ("B-1",))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # INJ-4：删掉 install.ps1 的 VC++ 运行库检测与指引
    base = None
    try:
        with io.open(INSTALL_PS1, encoding="utf-8-sig") as f:
            ps = f.read()
        # ★ 连注释一起删（注释里也写着 System32/msvcp140/vcredist ⇒ 只删代码段会留下"证据"）
        i = ps.find("# ★ M19P3-W3 · C-0 落地")
        j = ps.find("if (-not $Dir)")
        if i < 0 or j < 0 or j < i:
            raise AssertionError("找不到 VC++ 检测段（install.ps1 结构变了？）")
        stripped = ps[:i] + ps[j:]
        base = tempfile.mkdtemp(prefix="_W3_注入_")
        dst = os.path.join(base, "install.ps1")
        with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(stripped)
        report("INJ-4", "删掉 install.ps1 的 VC++ 运行库检测与指引",
               checks(install_ps1=dst), ("B-3", "B-4"))
    except AssertionError as e:
        all_caught = False
        problems.append("INJ-4 注入失败：%s" % e)
        print("【INJ-4】★ 注入失败：%s" % e, flush=True)
    finally:
        if base:
            shutil.rmtree(base, ignore_errors=True)

    print()
    print("=" * 78, flush=True)
    if all_caught:
        print("【红】4 处注入**全被逮到** ⇒ 判据有证明力 ⇒ exit=1", flush=True)
        return 1
    print("【✗崩溃】有注入**没**让行为退化（%s）⇒ 判据没牙 ⇒ exit=2"
          % "；".join(problems), flush=True)
    return 2


def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for aid, desc, want, got, ok in rows:
        print("  %-5s %-58s 期望=%-8s 实得=%-14s %s"
              % (aid, desc, want, got, "PASS" if ok else "FAIL"), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红，exit=1）")
    args, _unknown = ap.parse_known_args()      # 未知参数静默忽略

    print("== W3 判据 · 发布链硬伤：cp312 版本门 + msvcp140 C-0 ==")
    print("[口径] 解释器窗口 = [3.12, 3.13)：3.12 放行，3.10/3.11 太老，3.13/3.14 太新（cp312 硬绑定）")
    print("[口径] C-0：不分发 msvcp140.dll；程序走目标机 System32 自带那份，缺则只指路")
    print("[模式] %s" % ("注入自证（--inject）" if args.inject else "正跑"))
    if args.inject:
        return do_inject()
    rows = checks()
    show_rows("W3 · A 组（cp312 版本门）+ B 组（C-0）", rows)
    fails = [r for r in rows if not r[4]]
    print()
    print("=" * 78, flush=True)
    unver = [r[0] for r in rows if r[2] == "验不了"]
    if unver:
        print("【验不了】%s（包还没出 ⇒ 出包后补跑本条）" % unver, flush=True)
    if fails:
        print("【红】失败 %d 项 -> %s" % (len(fails),
                                        ["%s %s" % (r[0], r[1]) for r in fails]), flush=True)
        return 1
    print("【绿】全过", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
