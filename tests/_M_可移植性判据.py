# -*- coding: utf-8 -*-
r"""tests\_M_可移植性判据.py —— **闸门不许撒谎**（窗M · 2026-10-16）

病（ZCode 干净 clone 自证，DSH 已逐行核实）：
    在干净 clone 里跑 tests\run_all.py，「8 条全绿、rc=0」——
    但 w1/w2/w3 三条把**开发机的仓根写死在源码里**，于是它们验的是
    **本机工作树**（含未提交改动），不是 clone。
    危害不只是「换台机器跑不了」，而是**闸门会撒谎**：
    放行单拿到的是一份**与入库物无关**的绿灯 —— 这比没有闸门更坏。

本判据三段，每段都能单独把闸门打红：
  A 静态·源码面   三份判据的**代码**里不许再出现盘符绝对路径；
                  必须从 __file__ 推仓根；必须有 AGENTCTL_ROOT 覆盖口。
                  （注释与文档字符串不算 —— 讲历史用的路径不参与执行）
  B 静态·被读物面 w1 读的那两份进仓文档里不许有盘符绝对路径（守住脱敏成果，不许长回去）。
  C 动态·行为面   把仓**复制一份**到 %TEMP%，从**副本**里跑 w1/w2/w3：
                    · 三条都要 rc=0（换个路径照样跑得起来）
                    · 输出里必须出现**副本**的路径
                    · 输出里**不许**出现真仓的路径  ← 写死本机仓根时，这一条必红

跑红（自证本判据是活的，一条命令）：
    python -X utf8 tests\_M_可移植性判据.py --inject-hardcoded
      ↑ **不需要任何外部材料**：把副本里那三份判据的 __file__ 改指回本机仓根，
        等价于「回到写死 F:\… 的老版本」⇒ A 段与 C 段必红。新机器上也能跑。
    python -X utf8 tests\_M_可移植性判据.py --simulate-old work\_wM_改前备份_20261016\tests
      ↑ 把「改前三份判据」塞进副本再跑 C 段 ⇒ 必红（它们的输出指向本机仓，而不是副本）

退出码：0 = 三段全绿；1 = 有红（逐条打印为什么红）；2 = 判据自身跑不起来。
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 被点名的三条 + 总入口本身（它也必须自足，否则「少一条判据」的老坑会复发）
# ★ 固定参数跟 run_all.py 的 CRITERIA 保持一致：w2 缺 --new 会跑另一条分支，
#   w3 缺段名会被 argparse 当成用法错误直接退 2（判据自身崩，不是判红）。
THREE = (("check_t1_w1.py", ()),
         ("_T1_W2_判据.py", ("--new",)),
         ("_T1_窗3_自证.py", ("all",)))
STATIC_FILES = tuple(fn for fn, _a in THREE) + ("run_all.py",)

# 复制副本时要带上的东西（dist/build/baseline/verify 一概不带 —— 与本判据无关，还死沉）
COPY_TREES = ("src", "contract", "legacy_tools", "tests")
COPY_FILES = ("docs/AGENT接口.md", "docs/详细设计_v1.md", "docs/基线摘要与开工任务书.md")

# 盘符绝对路径：https:// 这类 URL 不算（后面紧跟第二个斜杠）
DRIVE = re.compile(r"[A-Za-z]:[\\/](?![/])")

FAILS = []


def ok(msg):
    print("  [绿] " + msg, flush=True)


def red(aid, msg):
    FAILS.append((aid, msg))
    print("  [红] %s · %s" % (aid, msg), flush=True)


def head(t):
    print("\n=== %s ===" % t, flush=True)


def docstring_nodes(tree):
    """模块/类/函数 的 docstring 节点集合 —— 讲历史用的路径不算缺陷。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def code_strings(path):
    """源码 + 源码里**会被执行**的字符串字面量（注释与 docstring 已剔除）。"""
    with io.open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src, filename=path)
    docs = docstring_nodes(tree)
    lits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docs:
                lits.append((node.lineno, node.value))
    return src, lits


def check_static(tests_dir):
    head("A · 静态·源码面（%s）" % tests_dir)
    for fn in STATIC_FILES:
        p = os.path.join(tests_dir, fn)
        if not os.path.isfile(p):
            red("A-缺件", "%s 里没有 %s —— 判据文件不在，不能当全绿" % (tests_dir, fn))
            continue
        src, lits = code_strings(p)
        hits = [(ln, v) for ln, v in lits if DRIVE.search(v)]
        if hits:
            red("A-写死盘符路径", "%s 的**代码**里还有盘符绝对路径：%s"
                % (fn, ["第%d行 %r" % (ln, v[:60]) for ln, v in hits[:3]]))
        else:
            ok("%s 的代码里无盘符绝对路径" % fn)
        if "AGENTCTL_ROOT" in src:
            ok("%s 保留 AGENTCTL_ROOT 显式覆盖口" % fn)
        else:
            red("A-无覆盖口", "%s 没有 AGENTCTL_ROOT 覆盖口（指红基线仓就靠它）" % fn)
        if "__file__" in src:
            ok("%s 的仓根从 __file__ 推" % fn)
        else:
            red("A-不从自身位置推", "%s 没有从 __file__ 推仓根（换机器就找不到被测物）" % fn)


def check_docs(repo_root):
    head("B · 静态·被读物面（w1 读的那两份进仓文档）")
    for rel in ("docs/详细设计_v1.md", "docs/基线摘要与开工任务书.md"):
        p = os.path.join(repo_root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            red("B-缺件", "%s 不在 —— w1 在干净 clone 上必红（判据进了仓、被读物没进）" % rel)
            continue
        with io.open(p, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        hits = [i for i, ln in enumerate(lines, 1) if DRIVE.search(ln)]
        if hits:
            red("B-脱敏回退", "%s 里又长出盘符绝对路径，行号 %s（进仓的是脱敏版，别长回去）"
                % (rel, hits[:10]))
        else:
            ok("%s 无盘符绝对路径（脱敏成果还在）" % rel)


def make_shadow_repo(dst):
    """把仓的**被测部分**复制到 dst：src/ contract/ legacy_tools/ tests/ + w1 要读的三份文档。

    刻意不复制 dist/ build/ baseline/ verify/ —— 与可移植性无关，还死沉。
    """
    for rel in COPY_TREES:
        s = os.path.join(ROOT, rel)
        d = os.path.join(dst, rel)
        if os.path.isdir(d):
            shutil.rmtree(d)
        shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in COPY_FILES:
        s = os.path.join(ROOT, rel.replace("/", os.sep))
        d = os.path.join(dst, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(s, d)
    os.makedirs(os.path.join(dst, "work"), exist_ok=True)
    return dst


def norm_txt(txt):
    return os.path.normcase(txt.replace("/", os.sep))


def inject_hardcoded_root(tests_dir):
    """把副本里三份判据的 __file__ 改指回**真仓** ⇒ 等价于回到「仓根写死」的老版本。

    手法：在这三份文件的「仓根推导行」（含 AGENTCTL_ROOT 那一行）**之前**插一行
    `__file__ = <真仓>\tests\<文件名>` —— __file__ 是模块全局，模块内代码随后读到的
    就是这个字面量，于是 `dirname(dirname(abspath(__file__)))` 算出来的自然是真仓。
    ★ 只动 %TEMP% 里的副本；现役判据与产品代码一个字节不动。
    ★ 为什么不用「替换掉推导表达式」：那样要按每份文件的写法做正则，脆；
      改 __file__ 是语言级的、对三份文件同一套手法。
    """
    done = []
    for fn, _args in THREE:
        p = os.path.join(tests_dir, fn)
        if not os.path.isfile(p):
            red("A-缺件", "%s 里没有 %s" % (tests_dir, fn))
            continue
        with io.open(p, encoding="utf-8") as fh:
            lines = fh.read().splitlines(True)
        # ★ 必须用 AST 找那条赋值语句，不能用「第一个含 AGENTCTL_ROOT 的行」——
        #   w1 的**文档字符串**里就提到过 AGENTCTL_ROOT，按行找会插进 docstring 变成散文，
        #   看着像注入了、其实一行代码都没改 ⇒ 假绿。
        hit = None
        whole = "".join(lines)
        try:
            tree = ast.parse(whole, filename=p)
        except SyntaxError as e:
            red("A-缺件", "%s 解析不了：%s" % (fn, e))
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if not ({"ROOT", "REPO"} & set(names)):
                continue
            seg = ast.get_source_segment(whole, node) or ""
            if "AGENTCTL_ROOT" in seg or "__file__" in seg:
                hit = node.lineno - 1
                break
        if hit is None:
            red("A-缺件", "%s 里找不到「仓根推导赋值语句」（ROOT/REPO = …AGENTCTL_ROOT…），"
                "没法注入" % fn)
            continue
        fake = os.path.join(ROOT, "tests", fn)
        lines.insert(hit, "__file__ = r'%s'   # 窗M 注入：模拟「仓根写死」的老版本\n" % fake)
        with io.open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(lines))
        done.append(fn)
    return done


def norm(p):
    return os.path.normcase(os.path.normpath(p))


def run_criterion(script_path, cwd, args=(), timeout=900):
    """跑一条判据，原样收回输出。

    ★ 必须清掉 AGENTCTL_ROOT —— 否则外面环境里残留的 AGENTCTL_ROOT 会把副本里的
      判据又指回真仓，这条判据就成了自欺（它自己正是治这个病的）。
    """
    env = dict(os.environ)
    env["PYTHONWARNINGS"] = "ignore"      # 别让 SyntaxWarning 混进 stdout 干扰路径判定
    env.pop("AGENTCTL_ROOT", None)
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.monotonic()
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", script_path] + list(args),
                           cwd=cwd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "超过 %ss 没跑完" % timeout, time.monotonic() - t0, ""
    # ★ 路径判定**只看 stdout**：stderr 里可能有警告/异常行带出别的路径，
    #   拿它判「它到底验的是谁」会误判成绿。stderr 只在判红时打出来当现场。
    txt = (p.stdout or b"").decode("utf-8", errors="replace")
    err = (p.stderr or b"").decode("utf-8", errors="replace")
    return p.returncode, txt, time.monotonic() - t0, err


def check_dynamic(shadow):
    head("C · 动态·行为面（副本 %s）" % shadow)
    real = norm(ROOT)
    copy = norm(shadow)
    for fn, args in THREE:
        script = os.path.join(shadow, "tests", fn)
        rc, txt, dt, err = run_criterion(script, shadow, args)
        print("  ---- %s %s  rc=%s  %.1fs ----"
              % (fn, " ".join(args) or "（无参）", rc, dt), flush=True)
        for ln in txt.splitlines():
            s = ln.strip()
            if s.startswith(("md=", "src=", "模式", "结论", "T1 ")) or "包 " in s:
                print("       | %s" % s[:150], flush=True)
        if rc != 0:
            red("C-副本里跑不起来", "%s 在副本里 rc=%s —— 换条路径就崩" % (fn, rc))
            for ln in err.splitlines()[-6:]:
                print("       ! %s" % ln[:150], flush=True)
            continue
        ok("%s 在副本里 rc=0" % fn)
        # ★ 只看 **stdout**：stderr 里可能有警告行带出别的路径，拿它判「指向谁」会误判
        if copy in norm_txt(txt):
            ok("%s 的输出指向**副本**（%s）" % (fn, shadow))
        else:
            red("C-输出不含副本路径", "%s 的输出里找不到副本路径 %s" % (fn, shadow))
        if real in norm_txt(txt):
            red("C-输出指向真仓", "%s 的输出里出现了**真仓**路径 %s"
                "（= 它验的不是副本，闸门在撒谎）" % (fn, ROOT))
        else:
            ok("%s 的输出里没有真仓路径 %s" % (fn, ROOT))


def main(argv=None):
    ap = argparse.ArgumentParser(description="可移植性判据：闸门不许验错对象")
    ap.add_argument("--simulate-old", metavar="DIR",
                    help="把 DIR 里的改前三份判据塞进副本再跑 ⇒ 自证本判据能跑红")
    ap.add_argument("--inject-hardcoded", action="store_true",
                    help="自证跑红（不需要外部材料）：把副本里三份判据的 __file__ 改指回本机仓根")
    ap.add_argument("--keep", action="store_true", help="跑完保留副本目录（默认删）")
    args = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    print("=" * 78)
    print("窗M · 可移植性判据 · %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("本判据所在的仓 = %s" % ROOT)
    print("=" * 78)

    shadow = tempfile.mkdtemp(prefix="_M_可移植性_")
    try:
        make_shadow_repo(shadow)
        if args.inject_hardcoded:
            print("★ 自证跑红模式：把副本里三份判据的 __file__ 改指回真仓 %s" % ROOT)
            print("  （等价于回到「仓根写死」的老版本；只动副本，现役文件不变）")
            for fn in inject_hardcoded_root(os.path.join(shadow, "tests")):
                print("  已注入：%s" % fn)
        if args.simulate_old:
            old = os.path.abspath(args.simulate_old)
            print("★ 自证跑红模式：把改前判据 %s 塞进副本" % old)
            for fn, _args in THREE:
                src = os.path.join(old, fn)
                if not os.path.isfile(src):
                    red("A-缺件", "%s 里没有 %s" % (old, fn))
                    continue
                shutil.copyfile(src, os.path.join(shadow, "tests", fn))
        check_static(os.path.join(shadow, "tests"))
        check_docs(shadow)
        check_dynamic(shadow)
    finally:
        if args.keep:
            print("\n（--keep：副本留在 %s）" % shadow)
        else:
            # Windows 上句柄没释放干净就删不掉，如实报路径，别一声不响留半个目录
            # （legacy_tools*.py 在仓里是只读的，copyfile 会把只读带进副本 ⇒ 先解只读）
            for dirpath, _dirs, files in os.walk(shadow):
                for fn in files:
                    try:
                        os.chmod(os.path.join(dirpath, fn), 0o666)
                    except Exception:
                        pass
            err = None
            for _try in range(5):        # w3 起过子进程，句柄释放要等它退干净
                try:
                    shutil.rmtree(shadow)
                    err = None
                    break
                except Exception as e:
                    err = e
                    time.sleep(0.6)
            if err is not None:
                print("\n[注] 副本目录没删干净（%s: %s），可随时手删：%s"
                      % (type(err).__name__, err, shadow))

    head("汇总")
    if FAILS:
        print("结论：【红】共 %d 条不成立" % len(FAILS))
        for aid, why in FAILS:
            print("   - %s %s" % (aid, why))
        return 1
    print("结论：【绿】w1/w2/w3 验的都是「它们自己所在的那份仓」，"
          "换机器 / 干净 clone 也成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
