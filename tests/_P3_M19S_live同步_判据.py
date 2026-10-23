# -*- coding: utf-8 -*-
r"""tests\_P3_M19S_live同步_判据.py —— live 漂移自动闸的判据（全打桩、离线、零真 GUI）

★ 测的是哪一刀
  把「本机真在跑的那份 legacy（live）必须与仓内产物逐字节一致」从**人肉核 MD5**变成**自动闸**：
    · `tools\check_live_sync.py` —— 校验（逐文件规范化 md5 比对 + 退出码分档）
    · `tools\pack_dist.py`      —— 发版前挂闸（live 存在且不一致 ⇒ 中止发包）

★ 为什么值得单独立判据
  M19prime / M19P / M19P2 / M20 四轮改动的收口动作都是**人肉核三处 md5 全等**。
  人肉核漏一次的后果不是"少一次检查"，而是：**仓里完全看不出来，运行行为却已经漂了**。
  ⇒ 这条闸必须有牙（能抓住人为注入的不一致），也必须**不误伤**（live 没部署 / live 多出文件 ≠ 红）。

★ 四跑法（与本仓约定一致；每跑法期望的退出码写在这里）
    python -X utf8 tests\_P3_M19S_live同步_判据.py --case consistent   # ① 一致        ⇒ 0
    python -X utf8 tests\_P3_M19S_live同步_判据.py --case drift        # ② 不一致      ⇒ 1
    python -X utf8 tests\_P3_M19S_live同步_判据.py --case nolive       # ③ live 缺失   ⇒ 2
    python -X utf8 tests\_P3_M19S_live同步_判据.py --case extra        # ④ live 多出   ⇒ 0
    python -X utf8 tests\_P3_M19S_live同步_判据.py                    # 正跑（全部）  ⇒ 0
    python -X utf8 tests\_P3_M19S_live同步_判据.py --nosucharg        # 未知参数被忽略 ⇒ 与正跑一致
    ★ 每跑法返回的是**本判据**的退出码（0 = 该情形的断言全成立）；
      被断言的**工具**退出码写在 `CASE_EXPECT_TOOL_RC` 里。

★ 情形（全部用**临时目录**伪造，**不碰本机真 live**）
  ① 一致           ⇒ 工具退出码 0
  ② 不一致         ⇒ 工具退出码 1（且必须**点名**那个文件）
  ③ live 缺失      ⇒ 工具退出码 2（**验不了**；与「不一致」刻意分开 —— 混成一个码就会被人 --force 绕过去）
  ④ live 多出文件  ⇒ 工具退出码 0（清单外的文件不进包 ⇒ 不判红，但必须**逐条列出**并给判断）

★ 追加断言（这条闸有没有牙、有没有抄错口径、会不会误伤）
  ⑤ `--inject` 把一致改成不一致 ⇒ 必须红，且必须点名被注入的那个文件
  ⑥ 口径必须是 `gen_legacy.md5n`（去 BOM、CRLF/CR→LF）：
     CRLF+BOM 的孪生文件必须判**一致**；且本工具**不许**自己定义 `_norm`
     （两套口径 = 改个换行就能骗过其中一处，等于没有闸）
  ⑦ 闸**只读** live：跑完（正跑/--json/--inject 三种调用）后，伪 live 目录里的文件
     **一个字节都不能变**（--inject 改的是副本）
  ⑧ 本判据**不读本机真 live**：每次调用都显式指定临时目录 + 临时指针文件；
     且本机真指针文件 `build\local_legacy_home.txt` 的内容必须原封不动
  ⑨ 闸**接上了**：pack_dist.py 里必须出现 live 闸调用，且**位置在打包（写 zip）之前**
  ⑩ ★ **定位口径与运行时同口径**（本单新增，2026-10-23）：
     运行时 `src\agentctl\gate.py` 的层序是 env → 指针 → 源码态上探；
     既无指针内容、又无环境变量 ⇒ 运行时**回落**到源码态上探（gate.py:345-349）
     ⇒ 闸必须定位到**同一个目录**（仓内 legacy_tools）并明说层次 = source-tree。
     ⚠ 夹具的 gate.py 必须是「**合格**」的（含行首 `def check_targets(`）——
       否则运行时按声明层硬停（gate.py:342-343）根本不用那份 ⇒ 闸也该跟着定位不到。
  ⑪ ★ **定位冲突必须判红**（本单新增，2026-10-23）：
     `AGENTCTL_LEGACY_HOME` 与指针**同时存在且指向不同目录** ⇒ 运行时跑的那份
     和闸能比的那份**不是同一份** ⇒ 必须**退出码 1** + 显式报 `conflict` +
     两个路径都列出来。⚠ 这一条必须在**内容完全一致**（不红）的前提下仍红 ⇒
     证明红的原因是「冲突」而不是「内容漂了」。
  ⑫ 指针指向**体检不合格**的目录 ⇒ 运行时也定位不到（声明层硬停）⇒ 工具退出码 2（验不了）

退出码：0 = 绿；1 = 红（有一条不成立）。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "tools", "check_live_sync.py")
GEN = os.path.join(ROOT, "tools", "gen_legacy.py")
PACK = os.path.join(ROOT, "tools", "pack_dist.py")
REAL_POINTER = os.path.join(ROOT, "build", "local_legacy_home.txt")

CASES = ("consistent", "drift", "nolive", "extra", "inject", "norm",
         "nolocate", "readonly", "realsafe", "wired", "conflict", "unqualified")

verdicts = []


def rec(title, ok, detail=""):
    verdicts.append((title, bool(ok), detail))
    print("  [%s] %s%s" % ("绿" if ok else "红", title,
                           ("  ← %s" % detail) if detail else ""), flush=True)


def out(msg=""):
    print(msg, flush=True)


def load_filelist():
    """拿登记清单 `gen_legacy.FILES`（**只读**导入，不复制一份到本文件里）。

    ★ 判据也不许另抄一份清单：抄一份 = 清单与真源分家 = 判据在验一个假的世界。
    """
    spec = importlib.util.spec_from_file_location("_gen_legacy_for_m19s", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.FILES), mod


FILES, GEN_MOD = load_filelist()


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def snapshot(d):
    """目录里每个文件的 (名, 大小, sha256) —— 用来证明闸**只读**。"""
    snap = {}
    if not os.path.isdir(d):
        return snap
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            snap[name] = (os.path.getsize(p), sha(p))
    return snap


# ★ 夹具里的 gate.py 必须是「**合格**」的那份（对齐 gate.py 的能力体检：
#   代码里必须有**行首**的 `def check_targets(` —— 锚在行首是为了避开旧副本
#   文件头警告注释里那几次 "targets" 字样，见 gate.py:120-122）。
#   不合格 ⇒ 运行时按声明层硬停（gate.py:342-343）根本不用那份目录 ⇒
#   闸也必须跟着定位不到 ⇒ 夹具若用空壳 gate.py，情形①②④ 全会被误判成「验不了」。
GATE_STUB = (
    "# 判据夹具用的**合格** gate.py 桩（能力体检要的就是行首的 def check_targets()）\n"
    "def check_targets(*args, **kwargs):\n"
    "    return True\n"
)


def build_fixture(base, tag):
    """伪造一份「仓内产物 + live + 指针」。两份内容**逐字节相同**（= 情形①）。"""
    product = os.path.join(base, "product_%s" % tag)
    live = os.path.join(base, "live_%s" % tag)
    os.makedirs(product)
    os.makedirs(live)
    for name in FILES:
        data = (GATE_STUB if name == "gate.py"
                else ("# stub %s\n" % name)).encode("utf-8")
        with open(os.path.join(product, name), "wb") as fh:
            fh.write(data)
        with open(os.path.join(live, name), "wb") as fh:
            fh.write(data)
    ptr = os.path.join(base, "build_%s" % tag, "local_legacy_home.txt")
    os.makedirs(os.path.dirname(ptr))
    with open(ptr, "w", encoding="utf-8") as fh:
        fh.write(live)
    return product, live, ptr


def run_tool(base, args, env_home=None):
    """跑闸的 CLI。**始终**显式指定目录与指针 ⇒ 本机真 live 一次都不会被读到。

    `env_home` 非空 ⇒ 给子进程设 AGENTCTL_LEGACY_HOME（用来造「env 与指针冲突」）。
    """
    cmd = [sys.executable, "-X", "utf8", TOOL] + args
    env = dict(os.environ)
    env.pop("AGENTCTL_LEGACY_HOME", None)      # 本判据不受本机环境影响
    if env_home:
        env["AGENTCTL_LEGACY_HOME"] = env_home
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=base)
    return p.returncode, p.stdout, p.stderr


def case_consistent(base):
    """① 一致 ⇒ 0"""
    product, live, ptr = build_fixture(base, "ok")
    rc, so, se = run_tool(base, ["--product", product, "--pointer", ptr])
    rec("情形① 一致 ⇒ 退出码 0", rc == 0, "实测 rc=%d" % rc)
    rec("情形① 报告口径写明是 gen_legacy.md5n", "gen_legacy.md5n" in so, "")


def case_drift(base):
    """② 不一致 ⇒ 1（且点名）"""
    product, live, ptr = build_fixture(base, "drift")
    target = "act.py" if "act.py" in FILES else FILES[0]
    with open(os.path.join(live, target), "ab") as fh:
        fh.write(b"\n# drift\n")
    rc, so, se = run_tool(base, ["--product", product, "--pointer", ptr])
    rec("情形② 不一致 ⇒ 退出码 1", rc == 1, "实测 rc=%d" % rc)
    rec("情形② 逐条点名了 %s" % target, target in so, "")
    rc2j, so2j, _ = run_tool(base, ["--product", product, "--pointer", ptr, "--json"])
    names2 = []
    try:
        j = json.loads(so2j)
        names2 = [x["file"] for x in j.get("inconsistent", [])]
    except Exception as e:
        names2 = ["<JSON 解析失败:%s>" % e]
    rec("情形② --json 的 inconsistent 精确点名 %s" % target, names2 == [target],
        "实测 %s" % names2)


def case_nolive(base):
    """③ live 缺失 ⇒ 2"""
    product, _live, _ptr = build_fixture(base, "nolive")
    missing_ptr = os.path.join(base, "build_missing", "local_legacy_home.txt")
    os.makedirs(os.path.dirname(missing_ptr))
    with open(missing_ptr, "w", encoding="utf-8") as fh:
        fh.write(os.path.join(base, "live_does_not_exist"))
    rc, so, se = run_tool(base, ["--product", product, "--pointer", missing_ptr])
    rec("情形③ live 缺失 ⇒ 退出码 2（验不了，不是「不一致」）", rc == 2, "实测 rc=%d" % rc)
    rec("情形③ 明说「live 目录不存在」",
        "不存在" in so, so.strip().splitlines()[-1] if so.strip() else "")


def case_extra(base):
    """④ live 多出文件 ⇒ 0（且逐条列出 + 给判断）"""
    product, live, ptr = build_fixture(base, "extra")
    extras = ["_oneoff_tmp.py", "case_data.json", "act.py.bak_20260101_000000"]
    for name in extras:
        with open(os.path.join(live, name), "wb") as fh:
            fh.write(b"# extra\n")
    rc, so, se = run_tool(base, ["--product", product, "--pointer", ptr])
    rec("情形④ live 多出文件 ⇒ 退出码 0（不判红）", rc == 0, "实测 rc=%d" % rc)
    miss = [n for n in extras if n not in so]
    rec("情形④ 多出来的文件逐条列了出来", not miss, "没列出的：%s" % miss if miss else "")
    rec("情形④ 给了判断（忽略）", "忽略" in so, "")


def case_inject(base):
    """⑤ --inject：一致 ⇒ 必须红，且点名"""
    product, live, ptr = build_fixture(base, "inject")
    rc, so, se = run_tool(base, ["--product", product, "--pointer", ptr, "--inject"])
    rec("情形⑤ --inject 把一致改成不一致 ⇒ 退出码 1", rc == 1, "实测 rc=%d" % rc)
    rc5j, so5j, _ = run_tool(base, ["--product", product, "--pointer", ptr,
                                    "--inject", "--json"])
    inj_file, inj_names = None, []
    try:
        j = json.loads(so5j)
        inj_file = (j.get("injected") or {}).get("file")
        inj_names = [x["file"] for x in j.get("inconsistent", [])]
    except Exception:
        pass
    rec("情形⑤ --inject 点名了被注入的文件", bool(inj_file) and inj_file in inj_names,
        "注入=%s 不一致列表=%s" % (inj_file, inj_names))


def case_norm(base):
    """⑥ 口径：CRLF+BOM 的孪生文件必须判一致；不许自造 _norm"""
    product, live, ptr = build_fixture(base, "norm")
    # ★ 孪生文件**不许挑 gate.py**：那份孪生（BOM+CRLF）里没有行首的
    #   `def check_targets(` ⇒ 能力体检不合格 ⇒ 运行时按声明层硬停 ⇒ 闸会判
    #   「验不了」（2）而不是「一致」（0）。换一份普通文件，验的仍是**口径**。
    twin = next((n for n in FILES if n != "gate.py"), FILES[0])
    with open(os.path.join(product, twin), "wb") as fh:
        fh.write(("# stub %s\n" % twin).encode("utf-8"))
    with open(os.path.join(live, twin), "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + ("# stub %s\r\n" % twin).encode("utf-8"))
    rc, so, _ = run_tool(base, ["--product", product, "--pointer", ptr])
    rec("情形⑥ CRLF+BOM 孪生文件判**一致**（说明用的就是规范化口径）", rc == 0,
        "实测 rc=%d" % rc)
    src = open(TOOL, encoding="utf-8").read()
    rec("情形⑥ 工具**引用**了 gen_legacy 的 md5n", "md5n" in src and "gen_legacy" in src, "")
    rec("情形⑥ 工具**没有**自己另立一套 _norm", "def _norm" not in src, "")
    try:
        b = open(os.path.join(live, twin), "rb").read()
        rec("情形⑥ 与 gen_legacy.md5n 算出来一致（口径真的一致）",
            GEN_MOD.md5n(b) == GEN_MOD.md5n(open(os.path.join(product, twin), "rb").read()), "")
    except Exception as e:
        rec("情形⑥ 与 gen_legacy.md5n 算出来一致（口径真的一致）", False, str(e))


def case_nolocate(base):
    """⑩ ★ 定位口径与运行时同口径：无指针内容 + 无环境变量 ⇒ 回落**源码态上探**

    ★ 运行时（`src\\agentctl\\gate.py`）的层序是 env → 指针 → 源码态上探
      （gate.py:276-352）；指针没有内容 ⇒ 指针层不算命中 ⇒ 继续走 ④ 上探
      （gate.py:345-349）找 `<仓根>\\legacy_tools`（体检合格才用）。
    ★ 闸必须定位到**同一个目录** —— 否则「闸比的那份」≠「运行时跑的那份」，
      闸就形同虚设（本单 2026-10-23 的核心）。
    """
    _product, _live, _ptr = build_fixture(base, "nolocate")
    empty_ptr = os.path.join(base, "build_empty", "local_legacy_home.txt")
    os.makedirs(os.path.dirname(empty_ptr))
    with open(empty_ptr, "w", encoding="utf-8") as fh:
        fh.write("   \n")
    repo_product = os.path.join(ROOT, "legacy_tools")
    rc, so, se = run_tool(base, ["--product", repo_product, "--pointer", empty_ptr])
    rec("情形⑩ 无指针内容 + 无环境变量 ⇒ 定位层是「源码态上探」（source-tree）",
        "source-tree" in so,
        (so.strip().splitlines()[2] if len(so.strip().splitlines()) > 2 else ""))
    rcj, soj, _ = run_tool(base, ["--product", repo_product, "--pointer", empty_ptr, "--json"])
    j = {}
    try:
        j = json.loads(soj)
    except Exception as e:
        rec("情形⑩ --json 可解析", False, str(e))
    li = j.get("live_info") or {}
    rec("情形⑩ --json 的 layer = source-tree", li.get("layer") == "source-tree",
        "实测 %r" % li.get("layer"))
    want = os.path.normcase(os.path.join(ROOT, "legacy_tools"))
    got = os.path.normcase(li.get("runtime_live") or "")
    rec("情形⑩ 定位到的就是运行时会选的那份（仓内 legacy_tools）", got == want,
        "实测 %r / 期望 %r" % (got, want))
    rec("情形⑩ 与仓内产物一致 ⇒ 退出码 0（同一份东西比同一份东西）", rc == 0,
        "实测 rc=%d" % rc)


def case_unqualified(base):
    """⑫ 指针指向**体检不合格**的目录 ⇒ 运行时也定位不到（声明层硬停）⇒ 2"""
    bad = os.path.join(base, "unqualified")
    os.makedirs(bad)
    with open(os.path.join(bad, "gate.py"), "w", encoding="utf-8") as fh:
        fh.write("# 这份 gate.py **没有**行首的 def check_targets( ⇒ 体检不合格\n")
    ptr = os.path.join(base, "build_unq", "local_legacy_home.txt")
    os.makedirs(os.path.dirname(ptr))
    with open(ptr, "w", encoding="utf-8") as fh:
        fh.write(bad)
    rc, so, se = run_tool(base, ["--product", os.path.join(ROOT, "legacy_tools"),
                                  "--pointer", ptr])
    rec("情形⑫ 指针指向不合格目录 ⇒ 退出码 2（运行时也定位不到 ⇒ 验不了）",
        rc == 2, "实测 rc=%d" % rc)
    rec("情形⑫ 明说「不合格」且明说「不回落」（与 gate.py:342-343 同口径）",
        ("不合格" in so) and ("不回落" in so),
        (so.strip().splitlines()[-1] if so.strip() else ""))


def case_conflict(base):
    """⑪ ★ env 与指针指向不同目录 ⇒ 判红（exit 1）+ 显式报 conflict + 两条路径都列出来"""
    product_a, live_a, ptr_a = build_fixture(base, "cf_a")
    _product_b, live_b, ptr_b = build_fixture(base, "cf_b")
    # ★ `--product` 指向 env 那份 ⇒ **内容完全一致**（不会因漂移而红）
    #   ⇒ 这一条红的原因**只可能**是「冲突」⇒ 才算证明闸真的在管这件事。
    rc, so, se = run_tool(base, ["--product", product_a, "--pointer", ptr_b],
                          env_home=live_a)
    rec("情形⑪ env 与指针指向不同目录 ⇒ 退出码 1（判红）", rc == 1, "实测 rc=%d" % rc)
    rcj, soj, _ = run_tool(base, ["--product", product_a, "--pointer", ptr_b, "--json"],
                           env_home=live_a)
    j = {}
    try:
        j = json.loads(soj)
    except Exception as e:
        rec("情形⑪ --json 可解析", False, str(e))
    rec("情形⑪ --json 显式报 conflict=true", j.get("conflict") is True,
        "实测 %r" % j.get("conflict"))
    cp = j.get("conflict_paths") or {}
    # ⚠ 别拿 json.dumps 出来的串去 `in` 里面找路径：JSON 会把反斜杠写成两个 ⇒ 永远找不到。
    #   ⇒ 直接拿**解析出来的值**逐条比路径。
    vals = [str(v) for v in cp.values()]

    def _same(p, q):
        try:
            return os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(q))
        except Exception:
            return False

    rec("情形⑪ 两个路径都列了出来（env 那份 + 指针那份）",
        any(_same(v, live_a) for v in vals) and any(_same(v, live_b) for v in vals),
        "conflict_paths=%s" % json.dumps(cp, ensure_ascii=False))
    rec("情形⑪ 人读输出里点名「冲突」", "冲突" in so, "")
    rec("情形⑪ 内容其实是一致的（红的原因只有冲突，不是漂移）",
        (j.get("counts") or {}).get("red") == 0,
        "counts=%s" % json.dumps(j.get("counts") or {}, ensure_ascii=False))


def case_readonly(base):
    """⑦ 闸只读 live：跑完三种调用后，伪 live 一个字节都没变"""
    product, live, ptr = build_fixture(base, "ro")
    before = snapshot(live)
    run_tool(base, ["--product", product, "--pointer", ptr])
    run_tool(base, ["--product", product, "--pointer", ptr, "--json"])
    run_tool(base, ["--product", product, "--pointer", ptr, "--inject"])
    after = snapshot(live)
    diff = sorted(set(before) ^ set(after)) or \
        sorted(k for k in before if before.get(k) != after.get(k))
    rec("情形⑦ 闸**只读** live（三种调用跑完哈希不变）", before == after,
        "变了：%s" % diff if diff else "")


def case_realsafe(base):
    """⑧ 本机真指针文件原封不动"""
    if os.path.isfile(REAL_POINTER):
        rec("情形⑧ 本机真指针文件未被改动", True, "sha256=%s…" % sha(REAL_POINTER)[:16])
    else:
        rec("情形⑧ 本机真指针文件未被改动（本机没有该文件，跳过）", True, "（本机无指针）")


def case_wired(base):
    """⑨ 闸接上了，且接在打包之前"""
    if not os.path.isfile(PACK):
        rec("情形⑨ pack_dist.py 在？", False, PACK)
        return
    psrc = open(PACK, encoding="utf-8").read()
    rec("情形⑨ pack_dist.py 里接上了 live 闸", "check_live_sync" in psrc, "")
    i_gate = psrc.find("live 同步闸")
    # ★ 必须找**真正写包的那一处**（`zipfile.ZipFile(tmp`），
    #   不能找 "zipfile.ZipFile" —— 那会命中文件头的 `import zipfile`，
    #   位置永远在前，于是"闸在写包之前"这条断言永远不成立（第一版就踩了这个）。
    i_zip = psrc.find("zipfile.ZipFile(tmp")
    rec("情形⑨ live 闸在**写包之前**跑（发版前拦，不是事后查）",
        i_gate != -1 and i_zip != -1 and i_gate < i_zip,
        "闸@%d 写包@%d" % (i_gate, i_zip))
    rec("情形⑨ live 不存在时**不拦发包**（rc=2 走跳过分支）",
        "_lrc == 2" in psrc and "中止发包" in psrc, "")
    rec("情形⑨ live 不一致时**中止发包**（rc=1 走中止分支）",
        "_lrc == 1" in psrc, "")
    # ★ 「live 不进包」的结构性保证：闸那一段里**不许**出现任何写包动作
    #   （add_dir / writestr / z.write / shutil.copy 之类）—— 闸只许**读** live。
    blk = psrc.split("live 同步闸")[1].split("# ── T14 阶段 2")[0]
    writers = [w for w in ("add_dir(", "writestr(", "z.write(", "shutil.copy", "shutil.rmtree")
               if w in blk]
    rec("情形⑨ 闸那一段里**没有**任何写包动作（live 只被读，不被拷进包）",
        not writers, "出现了：%s" % writers if writers else "")


CASE_FUNCS = {
    "consistent": case_consistent,
    "drift": case_drift,
    "nolive": case_nolive,
    "extra": case_extra,
    "inject": case_inject,
    "norm": case_norm,
    "nolocate": case_nolocate,
    "readonly": case_readonly,
    "realsafe": case_realsafe,
    "wired": case_wired,
    "conflict": case_conflict,
    "unqualified": case_unqualified,
}

# ★ 四个「四跑法」期望的退出码（工具的退出码，不是本判据的）
CASE_EXPECT_TOOL_RC = {"consistent": 0, "drift": 1, "nolive": 2, "extra": 0,
                        "conflict": 1, "unqualified": 2}


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    ap = argparse.ArgumentParser(
        description="P3-M19S 判据 · live 漂移自动闸（全打桩 / 离线 / 零真 GUI）")
    ap.add_argument("--case", choices=("all",) + CASES, default="all",
                    help="只跑某一情形（默认 all = 全跑）")
    a, _unknown = ap.parse_known_args(argv)      # ★ 未知参数被静默忽略（与本仓约定一致）

    out("=" * 78)
    out("P3-M19S 判据 · live 漂移自动闸（全打桩 / 离线 / 零真 GUI）")
    out("=" * 78)
    out("工具：%s" % TOOL)
    out("登记清单：%d 个（取自 tools\\gen_legacy.py 的 FILES，只读导入）" % len(FILES))
    out("本次跑法：%s" % a.case)
    out()

    if not os.path.isfile(TOOL):
        out("[红] 校验工具不存在：%s —— 这条闸根本没接上。" % TOOL)
        return 1

    base = tempfile.mkdtemp(prefix="agentctl_m19s_live_sync_")
    try:
        names = list(CASE_FUNCS) if a.case == "all" else [a.case]
        for n in names:
            CASE_FUNCS[n](base)
        out()
        bad = [t for t, ok, _ in verdicts if not ok]
        if bad:
            out("【红】%d/%d 条不成立：" % (len(bad), len(verdicts)))
            for t in bad:
                out("      - %s" % t)
            return 1
        out("【绿】%d/%d 条全成立：live 漂移自动闸有牙、且不误伤。" % (len(verdicts), len(verdicts)))
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
