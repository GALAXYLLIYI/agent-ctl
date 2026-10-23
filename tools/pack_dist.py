# -*- coding: utf-8 -*-
r"""打分发包：把仓库裁成 dist/agentctl.zip（install.ps1 吃的那种）。

存在的唯一理由是**别把不该发的东西发到公开仓库上**：仓库里有
baseline/tools_.../_auth/（授权状态、通行证、日志），还有一堆
一次性调试脚本、备份文件、会话记录。手工 zip -r 一定会漏掉其中之一，
所以走白名单 + 敏感名闸门。

用法：
    python -X utf8 tools/pack_dist.py
    python -X utf8 tools/pack_dist.py --out D:/x.zip
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
# ★ T14 阶段 2（2026-10-09）：legacy_tools 不再是手工维护的实体目录。
#   打包时**现场从老线真源生成**（tools\gen_legacy.py：dsh-viewer-plugin\tools
#   → 复制 + 净化绝对路径 + 补齐 import + 落 md5 台账），用完即弃。
#   理由：老线唯一真源，手工维护两份必然漂移（实测漂移 10 个文件）。
#   旧目录保留在盘上作归档，但**不再参与打包**。
#
#   仍从**已净化**的产物取，不是从 baseline/：baseline/ 是另一个项目的快照，
#   里面有真实手机号和 _auth 授权状态，被 .gitignore 排除 —— 从那里打包
#   等于把隐私发到公开仓库的发行版里。
LEGACY_SRC = None            # 由 main() 现场生成后填入

# 进包的目录（白名单，不在表里的一律不发）
DIRS = ["src", "contract", "stubs", "vendor"]

# 进包的单文件（白名单）
FILES = [
    "requirements.txt",
    "install.ps1",
    "uninstall.ps1",
    "README.md",
    "LICENSE",
]

# 进包的文档（docs/ 太大，只挑对外的那几份）
DOCS = ["AGENT接口.md"]

# ★ P0-5（2026-10-23）：install.ps1 第 6 步**直接调用**的两个脚本必须随包发 ——
#   守护_mcp_http.py（登录自启 + 看门狗 + 令牌对账）与 自检_mcp_http.py（六项自检）。
#   缺件后果（体检报告 H3）：装完必然红「找不到守护脚本」⇒ MCP(HTTP) 不开机自启、
#   令牌两边不对账 ⇒ 「装完就能用」在**发布件**上不成立。
#   ★ 只放行这两个 —— install.ps1 真正取的就是这两个；
#     **不把整个 tools/ 塞进包**（tools/ 下 100+ 个脚本绝大多数是开发期一次性脚本，
#     还夹着本机工作笔记与私货；全量进包等于把开发资产发到公开仓）。
#   ★ 这两个文件名都**不以 _ 开头** ⇒ 不会被 guard() 的「_ 开头 = 一次性脚本」规则误伤，
#     所以**不需要**像 _session.py 那样再登记进 KEEP_NAMES（KEEP_NAMES 按文件名匹配）。
#   ★ 落包位置 = app\tools\…（install.ps1 第 3 步把包内容整体搬进 $AppDir）
#     ⇒ 装完落在 %LOCALAPPDATA%\AgentCtl\app\tools\。
TOOLS_FILES = [
    "tools/守护_mcp_http.py",
    "tools/自检_mcp_http.py",
]

ALWAYS_EXCLUDE_DIRS = {
    "__pycache__", ".git", "work", "dist", "build",
    ".pytest_cache", ".mypy_cache", "_auth", "_digest",
}
ALWAYS_EXCLUDE_EXT = {".pyc", ".pyo", ".pyd", ".log"}

# ★ T14 阶段 2：以 _ 开头但**必须随包发**的文件（显式白名单，不靠通配）
# ★ S3-A（2026-10-13）：加 `_session.py` —— padd2.py:50 在 conn() 里 `import _session as S`，
#   它是 gen_legacy.FILES 里的产品依赖。不显式放行的话，下面 guard() 的
#   「`_` 开头 = 一次性脚本」会把它丢掉 ⇒ 登记进 FILES 也等于没登记（两处必须成对）。
KEEP_NAMES = {"_md5台账.md", "_session.py"}

# 隐私扫描（与 tools/make_legacy_dir.py 同一套判据）——
# 打包是**最后一道闸**，不假设上游一定干净。
PAT_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
PAT_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
PAT_SECRET_ASSIGN = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"][^'\"]{6,}")

# 敏感名闸门：命中就中止，绝不「警告后照发」
SECRET_RE = re.compile(
    r"(credential|secret|token|password|passwd|\.pem$|\.pfx$|\.key$|id_rsa)",
    re.I,
)
SECRET_NAMES = {
    "auth_pass.json", "lease.json", "capture.json", "gate.lock",
    ".credentials.yaml", "config.yaml",
}

# legacy_tools 里这些是控制链路，少一个就废
LEGACY_REQUIRED = ["perceive.py", "act.py", "gate.py", "auth_popup.py"]

# ★ T7-3（2026-10-09）发行包白名单清空 —— 用户拍板。
#   开发线 legacy_tools\whitelist.json 里有 8 条白名单，其中不少是**用户的私人应用**
#   （AnythingLLM / WorkBuddy / Trae / 学习通 / Codex 桌面端…），每条都是「经用户指示」加入的。
#   白名单的语义是「系统自动放行操作的目标窗口」—— 它跟着发行包走，
#   等于把用户的授权面复制给每一个装它的人。
#   ⇒ 打包时把 whitelist 数组**清空**，只留 _关于 / _字段 两个说明字段。
#     装完之后任何窗口都不自动放行，一律走弹窗向用户现问。
#   这跟用户的长期指示一致（core.py 记着「不整名单限制，每次都问」）。
#   开发线那一份**一个字都不动**（用户明确「不同步」），只清发行包。
WHITELIST_SHIPPED_EMPTY = True


def blank_whitelist(name: str, data: bytes) -> bytes | None:
    """白名单文件在**打包时**清空。返回清空后的字节；不是白名单文件则返回 None。

    为什么放在这里而不是改源文件：源文件是用户的开发环境，
    要保留白名单才能继续干活；清空只发生在发出去的那一份上。
    """
    if not WHITELIST_SHIPPED_EMPTY or name != "whitelist.json":
        return None
    try:
        d = json.loads(data.decode("utf-8"))
    except Exception as e:
        raise SystemExit(
            "[X] whitelist.json 解析失败，拒绝发包（宁可不发也不发一个"
            "用户看不懂的白名单）：%s: %s" % (type(e).__name__, e))
    if not isinstance(d, dict):
        raise SystemExit("[X] whitelist.json 顶层不是对象，拒绝发包")
    n = len(d.get("whitelist") or [])
    d["whitelist"] = []
    d["_发行包说明"] = (
        "本文件由打包流程清空：发行包不携带任何自动放行条目。"
        "装完之后任何窗口都需要向用户现问授权。开发线的原始白名单不在此文件里。")
    print("  · 白名单清空：剔除 %d 条自动放行条目（用户拍板 T7-3）" % n)
    return json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8")


# ★ 备份/临时文件的【明确命名模式】（2026-10-12 加）。
#   原来是 `".bak" in path.name` —— 与 verify_dist.py 里**同一个错**：
#   真实备份叫 core.py_bak_W1toW7_20261005（是 _bak_，**不含 ".bak"**）⇒ 一个都拦不住。
#   实测后果：包里有 4 份过期源码备份（brake_daemon/core/gate/widget 各一份，core.py 单份 87KB）。
#   ★ 更值得记的一点：verify_dist.py 的 docstring 明写「不复用打包脚本的判据、宁可误报」，
#     却照抄了同一个错模式 ⇒ **裁判和运动员一起错，独立核验在这一点上失效**。
BACKUP_PAT = re.compile(r"(\.bak($|[._])|_bak[._]|\.old($|[._])|\.orig($|[._])|\.tmp$|~$|\.swp$)", re.I)


def guard(path: Path, rel: Path, keep_private: bool = False) -> str:
    """敏感检查：命中就直接 SystemExit，不给「照发」的选项。"""
    # ★ T14 阶段 2：md5 台账必须随包留档（工单判据 5），但它以 _ 开头，
    #   会被下面的「_ 开头 = 一次性脚本」规则挡掉 —— 显式放行，别靠通配。
    if path.name in KEEP_NAMES:
        return ""
    # ★ src/ 下**不能**按「_ 开头 = 一次性脚本」过滤。
    #   第一版一刀切，把 src/agentctl/__init__.py 和 _bundled_deps.py 一起排掉了，
    #   装完一跑就 ImportError: cannot import name '_bundled_deps' from 'agentctl'
    #   （包被降级成 namespace package，症状是 "unknown location"，不查到底会以为是环境问题）。
    #
    #   注意判断用的是**显式参数**而不是路径嗅探：rel 是相对被遍历的那个目录算的，
    #   遍历 src/ 时 parts[0] 是 "agentctl" 而不是 "src" ——
    #   第一版写成 `rel.parts[0] == "src"`，条件永远为假，缺件照旧被排掉。
    #   判据能通过「先造一个已知真阳性」验证：改完 __init__.py 就进了包，核验脚本也独立确认了。
    # 顺序有讲究：**先看目录排除**。_auth/ 整个目录本来就不发，
    # 里面的 auth_pass.json 属于「被排除」而不是「被拦截」——
    # 分清这两种，日志才不会把一次正常的打包说成事故。
    if rel.parts and set(rel.parts) & ALWAYS_EXCLUDE_DIRS:
        return "本机运行数据/缓存"
    if path.suffix.lower() in ALWAYS_EXCLUDE_EXT:
        return "编译产物/日志"
    if BACKUP_PAT.search(path.name):
        return "备份文件"
    if path.name.startswith("_") and not keep_private:
        return "一次性脚本"
    if path.name.startswith("session-"):
        return "会话记录"
    if path.name.lower() in SECRET_NAMES:
        raise SystemExit("[X] 敏感文件拦下了（不发）：%s" % path)
    if SECRET_RE.search(path.name):
        raise SystemExit("[X] 名字像密钥，拦下（不发）：%s" % path)
    return ""


def add_dir(z: zipfile.ZipFile, src: Path, arc: str, st: dict, keep_private: bool = False) -> None:
    if not src.is_dir():
        return
    for p in sorted(src.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(src)
        why = guard(p, rel, keep_private)
        if why:
            st["skip"].append((str(rel), why))
            continue
        data = p.read_bytes()
        z.writestr("%s/%s" % (arc, rel.as_posix()), data)
        st["files"] += 1
        st["bytes"] += len(data)


def add_shipped_tools(z: zipfile.ZipFile, st: dict) -> None:
    """把 install.ps1 真正要用的 tools 脚本放进包（逐个过隐私闸门）。

    ★ 为什么单独一个闸门而不是并进 FILES：FILES 里是 README/LICENSE 这类
      **人写的对外文本**，给它们加隐私扫描会与「脱敏」那条线（另一条线在改 README）
      撞在一起 ⇒ 这里只对**机器读的随包脚本**扫，命中就中止发包。
    """
    for rel in TOOLS_FILES:
        p = ROOT / rel
        if not p.is_file():
            raise SystemExit("[X] 发布件需要的文件不存在：%s（装完会缺件）" % rel)
        data = p.read_bytes()
        txt = data.decode("utf-8", "replace")
        bad = []
        if PAT_MOBILE.search(txt):
            bad.append("手机号")
        if PAT_IDCARD.search(txt):
            bad.append("身份证号")
        if PAT_SECRET_ASSIGN.search(txt):
            bad.append("密钥赋值")
        if bad:
            raise SystemExit("[X] 隐私闸门拦下（不发）：%s —— %s" % (rel, "、".join(bad)))
        z.writestr("app/" + rel, data)
        st["files"] += 1
        st["bytes"] += len(data)


def read_version() -> tuple[str | None, str]:
    """版本号**单一真源** = 产品代码里的 ``__version__``（src/agentctl/__init__.py）。

    ★ 为什么是它：它是**运行时真正对外报出去的那个版本**
      （docs\\AGENT接口.md 的 status.version 与它同源），不是文档里手写的字符串。
    ★ 为什么不在这里 import agentctl 取：import 会牵进 core 的依赖链（win32/uia…），
      打包机上不一定装得齐 ⇒ 只用正则读那一行常量。
    ★ 回退顺序（老取法不破坏）：① __version__ → ② pyproject.toml 的静态 version
      → ③ README 的「版本 x.y.z」正则（2026-10-12 起的老取法，仍留着）。
    ★ pyproject.toml 走 ``dynamic = ["version"]`` 引用同一个常量 ⇒ 仓里**只有一份数字**。
    """
    # ① 产品代码的 __version__
    try:
        txt = (ROOT / "src" / "agentctl" / "__init__.py").read_text(encoding="utf-8")
        m = re.search(r"""^__version__\s*=\s*['"]([^'"]+)['"]""", txt, re.M)
        if m:
            return m.group(1), "src/agentctl/__init__.py 的 __version__"
    except OSError:
        pass
    # ② pyproject.toml 的静态 version（若有人把它写成写死的数字）
    try:
        txt = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r"""^version\s*=\s*['"]([^'"]+)['"]""", txt, re.M)
        if m:
            return m.group(1), "pyproject.toml 的 version"
    except OSError:
        pass
    # ③ README 正则（老取法）
    try:
        txt = (ROOT / "README.md").read_text(encoding="utf-8")
        m = re.search(r"版本\s*([0-9]+\.[0-9]+\.[0-9]+)", txt)
        if m:
            return m.group(1), "README.md 的「版本 x.y.z」"
    except OSError:
        pass
    return None, "（没找到）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "agentctl.zip"))
    ap.add_argument("--legacy-src", default=None,
                    help="覆盖打包源（默认：现场从老线真源生成，不再用手工目录）")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # ── D1 第三层：打包前校验（提示性，不拦打包）──────────────────────
    #   拿仓内 legacy_tools 与双基线台账 A₀ 比，不一致 ⇒ 醒目警告。
    #   ★ 为什么不拦：打包源是下面现场从真源生成的，仓内产物状态不影响包内容；
    #     而「真源更新了、仓内产物没刷」是合法状态（G3），拦 = 误伤。
    #     警告的作用是：手改过产物的人在这里被告知"你的改动不会进包"。
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_legacy
    _n, _det = gen_legacy.verify_working_tree()
    if _n < 0:
        print("[!][D1] 打包前校验无法执行：%s" % _det[0])
    elif _n:
        print("[!][D1] 打包前校验：仓内 legacy_tools 有 %d 个文件与台账不一致"
              "（手改过，或真源更新后产物未刷）：" % _n)
        for _d in _det[:10]:
            print("      - %s" % _d)
        print("      包内容不受影响（现场从真源生成）；但若这些改动是手改产物留下的，"
              "它们**不会进包** —— 值得保留就搬回真源。")
        print()

    # ── T14 阶段 2：现场生成 legacy 打包源 ──────────────────────────────
    if args.legacy_src:
        legacy_src = Path(args.legacy_src)
        print("打包源（指定）：%s" % legacy_src)
    else:
        sys.path.insert(0, str(ROOT / "tools"))
        import gen_legacy

        legacy_src = Path(args.out).parent / "_legacy_for_pack"
        if legacy_src.is_dir():
            shutil.rmtree(legacy_src)
        print("打包源：从老线真源现场生成 → %s" % legacy_src)
        rc = gen_legacy.main_for_pack(legacy_src)
        if rc != 0:
            # rc≠0 有两种来由：净化规则未命中，或个人信息形态检测命中（gen_legacy 已打印详情）。
            # 别把后一种说成前一种 —— 会让人去查错的方向。
            raise SystemExit("[X] legacy 源生成失败（净化规则未命中，或个人信息形态检测命中；"
                             "详情见上面 gen_legacy 的输出），不发包")
        print()

    # ── pyflakes 常驻门槛（T14 阶段 3 落地，此前从 T13 起一直挂着）────
    #  只拦「运行到那行才崩」的那类（undefined / redefinition / 语法错），
    #  unused 这类存量债只提醒不拦 —— 简单全量当致命会被存量卡死，
    #  等于门槛名存实亡；全放行又等于没门槛。按后果分，才落得了地。
    sys.path.insert(0, str(ROOT / "tools"))
    import lint_gate

    print("pyflakes 常驻门槛：")
    lint_targets = [str(ROOT / "src" / "agentctl"), str(legacy_src)]
    if lint_gate.main(lint_targets) != 0:
        raise SystemExit("[X] pyflakes 致命项不为零 —— 有会让程序崩在半路的问题，不发包")
    print()

    st = {"files": 0, "bytes": 0, "skip": []}
    tmp = out.with_suffix(".zip.tmp")

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in DIRS:
            add_dir(z, ROOT / d, "app/" + d, st, keep_private=(d == "src"))
        for name in DOCS:
            p = ROOT / "docs" / name
            if p.is_file():
                z.write(p, "app/docs/" + name)
                st["files"] += 1
                st["bytes"] += p.stat().st_size
        for f in FILES:
            p = ROOT / f
            if not p.is_file():
                continue
            data = p.read_bytes()
            z.writestr("app/" + f, data)
            st["files"] += 1
            st["bytes"] += len(data)

        # ★ P0-5：install.ps1 第 6 步要用的守护 / 自检脚本（只放行 TOOLS_FILES 那两个）
        add_shipped_tools(z, st)

        # legacy_tools：控制链路的底座（窗口枚举/键鼠注入/OCR 都在里面）
        if not legacy_src.is_dir():
            raise SystemExit("[X] 找不到 legacy 脚本源目录：%s" % legacy_src)
        got = set()
        for p in sorted(legacy_src.rglob("*")):
            if p.is_dir():
                continue
            rel = p.relative_to(legacy_src)
            why = guard(p, rel)
            if why:
                st["skip"].append((str(rel), why))
                continue
            data = p.read_bytes()
            # 隐私复扫：命中就中止，不给「照发」选项。
            try:
                txt = data.decode("utf-8")
            except UnicodeDecodeError:
                txt = ""
            bad = []
            if PAT_MOBILE.search(txt):
                bad.append("手机号")
            if PAT_IDCARD.search(txt):
                bad.append("身份证号")
            if PAT_SECRET_ASSIGN.search(txt):
                bad.append("密钥赋值")
            if bad:
                raise SystemExit("[X] 隐私闸门拦下（不发）：%s —— %s" % (rel, "、".join(bad)))
            # ★ T7-3：白名单在**发出去的那一份**里清空，源文件不动。
            blanked = blank_whitelist(rel.name, data)
            if blanked is not None:
                data = blanked
            z.writestr("app/legacy_tools/" + rel.as_posix(), data)
            got.add(rel.name)
            st["files"] += 1
            st["bytes"] += len(data)

        missing = [n for n in LEGACY_REQUIRED if n not in got]
        if missing:
            raise SystemExit("[X] 控制链路缺件，不能发包：" + ", ".join(missing))

        # 指针文件：gate.py 的 _resolve_home 读它
        z.writestr("app/build/local_legacy_home.txt", "legacy_tools")

    os.replace(tmp, out)

    print("打包完成：%s" % out)
    print("  文件 %d 个，原始 %.0f KB" % (st["files"], st["bytes"] / 1024))
    print("  zip 大小 %.0f KB" % (out.stat().st_size / 1024))
    print("  MD5 %s" % hashlib.md5(out.read_bytes()).hexdigest())
    print("  跳过 %d 项（临时脚本/备份/运行数据）" % len(st["skip"]))
    for why, n in Counter(w for _, w in st["skip"]).most_common(8):
        print("    %s: %d" % (why, n))
    print()
    # ★2026-10-12 改：原来写死 "git tag v1.2.0 && gh release create …" ——
    #   仓库在 **Gitee**（gh 用不上），而且 v1.2.0 是手写的旧版本号，改一次就过期。
    #   现在版本号从 README 现读，发布口径按 Gitee 写，并把「仓库不公开=别人装不上」顶出来。
    # ★2026-10-23 改（H5）：版本号**不再从 README 读** —— README 上那个正则实测取不到值，
    #   而 README 是「另一个窗口在写」的文件 ⇒ 改为读**单一真源**（产品代码的 __version__），
    #   README 正则退成第三级回退（老取法保留，不破坏）。
    _v, _v_src = read_version()
    print()
    print("发布（仓库在 Gitee，不是 GitHub；gh 用不上）：")
    if _v:
        print("  版本号：%s（来源：%s）" % (_v, _v_src))
        print("  git tag v%s && git push origin v%s" % (_v, _v))
        print("  然后在 Gitee 仓库页 → 发行版/Release 新建，把 dist/agentctl.zip 附上")
    else:
        print("  没读到版本号（__version__ / pyproject / README 三处都没有）")
        print("  先在 src/agentctl/__init__.py 的 __version__ 标明版本号，再打 tag；"
              "然后在 Gitee 仓库页建 Release 并附上本 zip")
    print("  ★ 仓库若仍是私有，别人 `irm` 装不上（匿名请求会拿到 403）—— 公开前别对外发安装命令")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
