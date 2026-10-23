# -*- coding: utf-8 -*-
"""M19P4 判据 · act.py 的两条 pyflakes 致命项（重复 import sys / 用了 tempfile 却没 import）

★ 为什么要有这条判据（2026-10-23 首次跑完整 pack_dist 时撞出来的）：
    · act.py:185  redefinition of unused 'sys' from line 14
        —— 顶层 import 块里**第二个**裸 `import sys`（模块顶部 :14 已经 import 过）。
    · act.py:1072 undefined name 'tempfile'
        —— `_prof()` 里 `tempfile.gettempdir()`，**却从没 import tempfile**
        ⇒ 只要开 profile（AGENTCTL_PROFILE）就 **NameError 崩在半路**（真会崩的那类）。
  这两条被 pyflakes 常驻门槛判为**致命** ⇒ 发包被拦（`pack_dist` 退出码 1，不发包）。

★ 修法（真源 `legacy_tools\\_src\\act.py`，2026-10-23 主控）：
    · 删掉顶层 import 块里重复的 `import sys`（模块顶部 :14 那个照样可用）
    · 补 `import tempfile`

★ 本判据的口径（两条腿走路，互不依赖）：
    ① **致命项腿**：判定用的 FATAL 词表**从仓内 `tools\\lint_gate.py` 的文本里解析出来**
       （不 import 它 ⇒ 零副作用；同源 ⇒ 口径不可能漂移 ⇒ 「判据绿但发包红」不会发生），
       pyflakes 用 `python -m pyflakes` 跑（与 pack_dist / lint_gate 同一把尺子）。
    ② **运行期腿**：用 `ast` 只抽取「顶层 import（os / sys / tempfile）+ `_prof` 函数」
       ⇒ exec 到干净命名空间 ⇒ 手动置 `_PF_ON = True` ⇒ **真调 `_prof()`**
       ⇒ tempfile 没绑定就当场 NameError（与 pyflakes 独立 ⇒ 判据不是只会念 pyflakes 的话）。
    ③ **注入对照**：把副本里的 `import tempfile` 删掉 ⇒ 致命项腿与运行期腿**必须同时转红**
       ⇒ 证明判据有牙；而「使用点还在」那条断言**不许**跟着红
       （不许靠删使用点来骗判据）。

★ 跑法（约定四跑法）：
    正跑            python -X utf8 tests\\_P3_M19P4_致命项_判据.py            ⇒ exit 0
    注入必红        … --inject                                                 ⇒ exit 1
    产物一路        … --scope product                                         ⇒ exit 0
    未知参数        … --未知参数                                              ⇒ exit 0（静默忽略）

★ 本判据**离线**：不碰真 GUI、不碰通行证、不碰用户窗口；只在 %TEMP% 造副本（跑完删）。
"""
import argparse
import ast
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC_ACT = os.path.join(REPO, "legacy_tools", "_src", "act.py")
PROD_ACT = os.path.join(REPO, "legacy_tools", "act.py")
LINT_GATE = os.path.join(REPO, "tools", "lint_gate.py")
_TMP = tempfile.mkdtemp(prefix="_m19p4_")

# ── 致命项词表：从 tools\lint_gate.py 的文本里解析（不 import ⇒ 零副作用，同源 ⇒ 不漂移）──
def _fatal_words():
    try:
        txt = open(LINT_GATE, encoding="utf-8").read()
    except OSError:
        return None
    tree = ast.parse(txt)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "FATAL":
                    try:
                        val = ast.literal_eval(node.value)
                    except Exception:
                        return None
                    return tuple(val) if isinstance(val, (list, tuple)) else None
    return None


FATAL = _fatal_words()
if not FATAL:
    print("!! 读不到 tools\\lint_gate.py 的 FATAL 定义 ⇒ 判据不成立（不许当通过）")
    sys.exit(2)


def md5_of(path):
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest().upper()


def pyflakes_fatal(path):
    """返回该文件的致命项列表（口径 = lint_gate 的 FATAL 词表 + 同一把 pyflakes）。"""
    cmd = [sys.executable, "-X", "utf8", "-m", "pyflakes", path]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = (r.stdout or "") + (r.stderr or "")
    if "No module named pyflakes" in txt:
        return None            # ★ 工具没跑起来 ≠ 没有致命项（照 lint_gate 的探针口径）
    return [l for l in txt.splitlines() if l.strip() and any(k in l for k in FATAL)]


def top_level_plain_imports(text, name):
    """模块级裸 `import <name>` 的次数（`import X as Y` 不算）。"""
    tree = ast.parse(text)
    n = 0
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == name and a.asname is None:
                    n += 1
    return n


def has_plain_import(text, name):
    return top_level_plain_imports(text, name) >= 1


def runtime_prof_call(text):
    """运行期独立断言：抽「顶层 import(os/sys/tempfile) + _prof」⇒ exec ⇒ 真调 _prof()。

    返回 None = 通过；返回异常类名 = 失败（NameError 等）。
    ★ 只 exec 三个 import + 一个函数 ⇒ 零副作用（不 import 老线的 gate/win32 模块）。
    ★ `_PF_ON` 由手动静置（不依赖它在本文件里怎么定义）⇒ 只测 tempfile 绑没绑定。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return "SyntaxError(%s)" % e
    keep = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(a.name in ("os", "sys", "time", "tempfile") for a in node.names):
                keep.append(node)
    pf = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_pf":
            pf = node
            break
    if pf is None:
        return "NoPfFunc"
    keep.append(pf)
    ns = {"__name__": "_m19p4_runtime"}
    try:
        exec(compile(ast.Module(body=keep, type_ignores=[]), "<m19p4>", "exec"), ns)
    except Exception as e:
        return "%s(%s)" % (type(e).__name__, e)
    ns["_PF_ON"] = True
    ns["_PF_LAST"] = [0.0]
    ns["open"] = lambda *a, **k: io.StringIO()      # ★ 假 open ⇒ 不落盘
    try:
        ns["_pf"]("m19p4")
    except Exception as e:
        return "%s(%s)" % (type(e).__name__, e)
    return None


def battery(path, role):
    """对一份 act.py 跑全部断言 ⇒ 返回 [(断言号, 说明, 期望, 实得, 是否通过)]。"""
    rows = []
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        rows.append(("P4-x", "文件读不出来", "读得到", str(e), False))
        return rows

    fat = pyflakes_fatal(path)
    if fat is None:
        rows.append(("P4-fatal-tool", "pyflakes 探针（工具没跑起来 ⇒ 不许当通过）",
                     "pyflakes 可用", "缺 pyflakes", False))
    else:
        rows.append(("P4-fatal", "%s：pyflakes 致命项必须为零" % role,
                     "0 条", "%d 条 %s" % (len(fat), fat[:3]), len(fat) == 0))

    n_sys = top_level_plain_imports(text, "sys")
    rows.append(("P4-sys1", "%s：顶层裸 `import sys` 恰好 1 处（不许重复定义）" % role,
                 1, n_sys, n_sys == 1))
    ok_t = has_plain_import(text, "tempfile")
    rows.append(("P4-temp", "%s：顶层必须有 `import tempfile`" % role,
                 True, ok_t, ok_t))
    used = "tempfile.gettempdir(" in text
    rows.append(("P4-used", "%s：`tempfile.gettempdir()` 使用点仍在（不许靠删使用点骗判据）" % role,
                 True, used, used))
    err = runtime_prof_call(text)
    rows.append(("P4-run", "%s：运行期真调 `_pf()` 不许 NameError" % role,
                 None, err if err else "正常", err is None))
    return rows


def show_rows(title, rows):
    print("---- %s ----" % title, flush=True)
    for aid, desc, want, got, ok in rows:
        print("  %-14s %-58s 期望=%-6s 实得=%-28s %s"
              % (aid, desc[:58], str(want)[:6], str(got)[:28], "PASS" if ok else "FAIL"),
              flush=True)


# ── 注入对照：把副本里的 `import tempfile` 删掉 ⇒ 致命项腿 + 运行期腿必须同时红 ──
INJECTIONS = [
    ("INJ-1", "删掉顶层 `import tempfile`（复现 undefined name 'tempfile' 那个致命项）",
     "import tempfile\n", "", ("P4-fatal", "P4-temp", "P4-run")),
    ("INJ-2", "在顶层 import 块里再加一个 `import sys`（复现 redefinition of unused 'sys'）",
     "import tempfile\n", "import tempfile\nimport sys\n", ("P4-fatal", "P4-sys1")),
]


def make_injected(src_path, old_text, new_text):
    """造一份注入了的副本（%TEMP%），返回 (副本路径, 临时根)。"""
    base = tempfile.mkdtemp(dir=_TMP, prefix="inj_")
    dst = os.path.join(base, "act_injected.py")
    txt = open(src_path, encoding="utf-8").read()
    if old_text not in txt:
        raise AssertionError("注入锚点不在文件里：%r" % old_text[:60])
    txt = txt.replace(old_text, new_text, 1)
    open(dst, "w", encoding="utf-8", newline="\n").write(txt)
    return dst, base


def do_inject():
    print("注入只发生在 %TEMP% 的副本上，现役一个字节不动。\n" % _TMP, flush=True)
    all_caught, problems = True, []
    for iid, desc, old_text, new_text, targets in INJECTIONS:
        try:
            dst, base = make_injected(SRC_ACT, old_text, new_text)
        except AssertionError as e:
            all_caught = False
            problems.append("%s 注入失败：%s" % (iid, e))
            print("【%s】%s\n     ★ 注入失败：%s" % (iid, desc, e), flush=True)
            continue
        try:
            rows = battery(dst, "注入副本")
            show_rows("%s · %s" % (iid, desc), rows)
            by_id = {r[0]: r for r in rows}
            missed = [t for t in targets if t not in by_id or by_id[t][4]]
            extra = [r[0] for r in rows if not r[4] and r[0] not in targets]
            if missed:
                all_caught = False
                problems.append("%s 目标断言没红：%s" % (iid, missed))
            print("【%s】%s" % (iid, desc), flush=True)
            print("     目标断言 %s ⇒ %s" % (list(targets),
                                        "全红 ✔（改坏被逮到）" if not missed
                                        else "★ 没红：%s" % missed), flush=True)
            if extra:
                print("     （附带转红：%s —— 同一处缺陷的连带影响，正常）" % extra, flush=True)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    print()
    print("=" * 78, flush=True)
    if all_caught:
        print("【红】%d 处注入**全被逮到** ⇒ 判据有证明力 ⇒ exit=1" % len(INJECTIONS),
              flush=True)
        return 1
    print("【✗崩溃】有注入**没**让行为退化（%s）⇒ 判据没牙 ⇒ exit=2"
          % "；".join(problems), flush=True)
    return 2


def do_normal(scope, act_path):
    targets = []
    if act_path:
        targets.append(("指定", act_path))
    else:
        if scope in ("src", "all"):
            targets.append(("真源", SRC_ACT))
        if scope in ("product", "all"):
            targets.append(("产物", PROD_ACT))

    fails, unverifiable = [], []
    for role, path in targets:
        if not os.path.isfile(path):
            print("[被测] %-4s %s —— **文件不在** ⇒ 验不了" % (role, path), flush=True)
            unverifiable.append("%s：文件不在" % path)
            continue
        print("\n[被测] %-4s %s" % (role, path), flush=True)
        print("       MD5=%s  %d 字节" % (md5_of(path), os.path.getsize(path)), flush=True)
        if role == "产物" and md5_of(path) != md5_of(SRC_ACT):
            print("       【验不了】产物尚未重生成（与真源 MD5 不同）⇒ "
                  "等主控统一重生成后补跑本路", flush=True)
            unverifiable.append("产物：尚未重生成（等主控统一重生成后补跑）")
            continue
        rows = battery(path, role)
        show_rows("%s · 致命项断言" % role, rows)
        fails.extend(["%s：%s %s" % (role, aid, desc) for aid, desc, w, g, ok in rows if not ok])

    print()
    print("=" * 78, flush=True)
    if unverifiable:
        print("【验不了】%d 路：%s" % (len(unverifiable), unverifiable), flush=True)
    if fails:
        print("【红】失败 %d 项 -> %s" % (len(fails), fails), flush=True)
        print("★ 判据是活的（能抓住这个缺陷）", flush=True)
        return 1
    print("【绿】全过%s" % ("（有 %d 路验不了，等主控重生成后补跑）" % len(unverifiable)
                           if unverifiable else ""), flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红，exit=1）")
    ap.add_argument("--scope", default="all", choices=("src", "product", "all"),
                    help="src=真源｜ product=产物 ｜ all=两路（默认）")
    ap.add_argument("--act", default=None, help="跑指定的一份 act.py")
    args, _unknown = ap.parse_known_args()      # ★ 未知参数**静默忽略**（不许假红）

    print("== M19P4 判据 · act.py 的两条 pyflakes 致命项 ==")
    print("[口径] 致命项词表 = tools\\lint_gate.py 的 FATAL（从文本解析，同源不 import）")
    print("[口径] 运行期独立断言 = ast 抽 import + _prof ⇒ exec ⇒ 真调 _prof()")
    print("[模式] %s" % ("注入自证（--inject）" if args.inject
                          else ("指定：%s" % args.act if args.act else "正跑：%s" % args.scope)))
    try:
        return do_inject() if args.inject else do_normal(args.scope, args.act)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
