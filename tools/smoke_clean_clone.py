# -*- coding: utf-8 -*-
r"""D1 治本：干净克隆冒烟测试 —— 把「运行期必需但没进仓」从静默缺件变成**必现报错**。

为什么存在（2026-10-12 一天付了三次学费）：
  改到生成产物、缺件、发布链路假设与实物不符……全是「装到别的机器才发现」。
  干净克隆只收 **tracked** 文件 —— 本机才有的资产在克隆里必然炸，这正是目的。

流程（严格按 README「B 离线路」走，一步不跳）：
  1. git clone <仓库> → 干净目录
  2. 克隆内：python -X utf8 tools\\pack_dist.py        → dist\\agentctl.zip
  3. 克隆内：python -X utf8 tools\\verify_dist.py      → 独立核验（不复用打包代码）
  4. 沙箱安装：install.ps1 -Zip <zip> -Dir <沙箱home>  （**不碰 %LOCALAPPDATA%**）
  5. 装后自测：app\\legacy_tools\\bench.py（11 项）+ agentctl.cmd selftest
  6. 汇总 PASS/FAIL；任一硬步骤失败 ⇒ 非零退出

★ 已知预期失败点（如实列出，不藏 —— 藏了冒烟就白跑）：
  E1  README §3 的 exe 构建链（PyInstaller `build\\AgentCtl.spec`）在干净克隆里**必炸**：
      `.gitignore:4` 的 `build/` 挡了整目录，`:6` 的 `!build/*.spec` 白名单**无效**
      —— git 不进被忽略的父目录（`git check-ignore -v` 实测命中 `build/`）。
      这是派单 R2 的第一条发现（2026-10-12）。在 ignore 修好（改成 `build/*` + 否定规则）
      或 README 把 exe 路线标明「仅本机可选」之前，本脚本检测到 build\\AgentCtl.spec
      缺失时**打印 E1 并继续**，不计入冒烟失败。
  E2  install.ps1 的 Release URL（`$RepoHost/.../releases/latest/download/agentctl.zip`）
      是 GitHub 式路径，Gitee 上疑似 404（R2「不确定」节）。冒烟走 `-Zip` 离线路，
      不依赖它；只有 README C 段（公开后一行命令）会暴露这个问题。
  E3  install.ps1 写 agentctl.cmd 用 `-Encoding OEM`：安装路径含非 ASCII 且运行时代码页
      ≠ 写时代码页时，cmd 里的路径变乱码 ⇒ 步骤 5b（agentctl selftest）FAIL。
      2026-10-12 冒烟实测抓到并在工作区修好（UTF-8 无 BOM + chcp 65001，GBK/UTF-8 双控制台
      实测通过）—— **在修复提交之前，clone@HEAD 跑本脚本 5b 会红**，那是冒烟如实反映
      已提交状态，不是脚本误报。
  E4  install.ps1 会写 **HKCU Run 自启项**（AgentCtlMcpHttp，install.ps1:402-405）——
      HKCU 是用户级全局，`-Dir` 沙箱隔离不了它。2026-10-24 冒烟把沙箱守护脚本路径写进了
      用户真实 Run 键且无人回收 ⇒ 临时目录一删，开机自启指向已删沙箱、服务静默死
      （2026-10-27 实测 8761 DOWN 抓到）。桌面快捷方式早有清理先例（见下方清理段），
      本批把 Run 键补进同一套「装前记值 → 装后恢复/删除」清理网。

用法：
    python -X utf8 tools\\smoke_clean_clone.py                    # 全自动（临时目录，跑完即删）
    python -X utf8 tools\\smoke_clean_clone.py --keep --dest D:\\smoke   # 留现场
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
import winreg

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def find_git(explicit):
    if explicit:
        return explicit
    g = shutil.which("git")
    if g:
        return g
    env_git = os.environ.get("AGENTCTL_GIT_EXE")
    if env_git and os.path.exists(env_git):   # 本机 git 不在 PATH 时用环境变量指
        return env_git
    return None


def run(cmd, cwd=None):
    print("$ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    lines = out.splitlines()
    for line in lines[:45]:
        print("  | " + line)
    if len(lines) > 45:
        print("  | …（共 %d 行，截断显示）" % len(lines))
    return r.returncode, out


def rm_readonly(func, path, exc):
    """删只读产物（gen_legacy 收尾会设只读，rmtree 需要先把写位放回来）。"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise exc


# ── E4：install.ps1 还会写 HKCU Run 自启项（HKCU 是用户级全局，-Dir 隔离不了）────
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "AgentCtlMcpHttp"


def _run_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            return winreg.QueryValueEx(k, _RUN_NAME)[0]
    except OSError:
        return None


def _set_run_value(v):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
        winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, v)


def _del_run_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _RUN_NAME)
    except FileNotFoundError:
        pass


def step(name, rc, extra=""):
    mark = "PASS" if rc == 0 else "FAIL"
    print("\n[%s] %s%s" % (mark, name, ("  " + extra) if extra else ""))
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=ROOT, help="git 克隆源（默认本仓）")
    ap.add_argument("--dest", default=None, help="目标目录（默认临时目录）")
    ap.add_argument("--git", default=None, help="git.exe 路径（默认自动找）")
    ap.add_argument("--keep", action="store_true", help="跑完不删现场")
    a = ap.parse_args()

    git = find_git(a.git)
    if not git:
        print("[X] 找不到 git —— 用 --git <路径> 指定")
        return 2
    dest = a.dest or os.path.join(
        os.environ.get("TEMP", ROOT), "agentctl_smoke_%s" % time.strftime("%Y%m%d_%H%M%S"))
    clone = os.path.join(dest, "clone")
    home = os.path.join(dest, "home")
    os.makedirs(dest, exist_ok=True)

    fails = []
    print("=" * 72)
    print("干净克隆冒烟：repo=%s\n            dest=%s" % (a.repo, dest))
    print("=" * 72)

    # 1) clone —— 只有 tracked 文件会出现在这里
    rc, _ = run([git, "clone", "--no-hardlinks", "-q", a.repo, clone])
    fails.append(step("1 git clone（干净=只含 tracked）", rc))

    # E1 预检：build spec 在不在仓（已知预期失败点，不计失败）
    spec = os.path.join(clone, "build", "AgentCtl.spec")
    if not os.path.exists(spec):
        print("\n[E1 已知预期失败点] 克隆里没有 build\\AgentCtl.spec —— README §3 的 exe "
              "构建链在干净克隆不可用（.gitignore:4 挡 build/，!build/*.spec 因 git 不进"
              "被忽略父目录而无效；R2 发现①）。pack/verify/install 主链不受影响，继续。")

    # 2) 打包（现场从真源生成 legacy 源 —— 需要本机有 dsh-viewer-plugin\tools）
    rc, _ = run([sys.executable, "-X", "utf8", "tools\\pack_dist.py"], cwd=clone)
    fails.append(step("2 pack_dist（克隆内打包）", rc))

    # 3) 独立核验
    rc, _ = run([sys.executable, "-X", "utf8", "tools\\verify_dist.py", "dist\\agentctl.zip"],
                cwd=clone)
    fails.append(step("3 verify_dist（独立核验 zip）", rc))

    # 4) 沙箱安装（-Dir 隔离，不碰 %LOCALAPPDATA%）
    #    install.ps1 会写桌面快捷方式（install.ps1:279-280）—— 记下装前是否存在，
    #    冒烟自己创建的那个在清理时删掉，不给用户留指向已删沙箱的死快捷方式。
    #    ★ 同理还会写 HKCU Run 自启项（E4）—— 装前记值，清理时恢复/删除（见清理段）。
    run_pre = _run_value()
    desk = subprocess.run(["powershell", "-NoProfile", "-Command",
                           "[Environment]::GetFolderPath('Desktop')"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout.strip()
    lnk = os.path.join(desk, "Windows agent-ctl 组件.lnk") if desk else None
    lnk_preexisted = bool(lnk) and os.path.exists(lnk)
    zip_path = os.path.join(clone, "dist", "agentctl.zip")
    rc, _ = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", os.path.join(clone, "install.ps1"),
                 "-Zip", zip_path, "-Dir", home])
    fails.append(step("4 install.ps1 -Zip -Dir（沙箱安装）", rc))

    # 5) 装后自测：bench 11 项 + agentctl selftest
    bt = os.path.join(home, "app", "legacy_tools")
    rc, _ = run([sys.executable, "-X", "utf8", "-W", "error::SyntaxWarning", "bench.py"],
                cwd=bt if os.path.isdir(bt) else None)
    fails.append(step("5a 装后 bench（11 项行为级）", rc))

    cmd_bat = os.path.join(home, "agentctl.cmd")
    if os.path.exists(cmd_bat):
        rc, _ = run([cmd_bat, "selftest"])
        fails.append(step("5b 装后 agentctl selftest（只读挂件自检）", rc))
    else:
        fails.append(step("5b 装后 agentctl selftest", 1, "agentctl.cmd 不存在"))

    total = sum(1 for r in fails if r != 0)
    print("\n" + "=" * 72)
    print("冒烟结论：%d/%d 步 PASS%s" % (len(fails) - total, len(fails),
                                    "" if total == 0 else " —— **有 %d 步失败**" % total))
    if not a.keep:
        shutil.rmtree(dest, onerror=rm_readonly)
        print("现场已删除（--keep 可保留）")
        if lnk and not lnk_preexisted and os.path.exists(lnk):
            os.remove(lnk)
            print("本次冒烟创建的桌面快捷方式已删除（它本来不存在，是 install 建的）")
        # ★ E4 清理（沿桌面快捷方式同一先例）：只清「冒烟写的那条」——
        #   现值指向冒烟沙箱 ⇒ 恢复装前原值（原先没有就删掉）；
        #   现值不指沙箱（用户中途自己改过）⇒ 一个字节不碰。
        now = _run_value()
        if now is not None and "agentctl_smoke_" in str(now).lower():
            if run_pre is not None:
                _set_run_value(run_pre)
                print("Run 键 AgentCtlMcpHttp 已恢复装前原值（install 写成了冒烟沙箱路径）")
            else:
                _del_run_value()
                print("冒烟写入的 Run 键 AgentCtlMcpHttp 已删除（装前本不存在）")
    else:
        print("现场保留在：%s" % dest)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
