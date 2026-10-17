# -*- coding: utf-8 -*-
r"""tests\_默认关闭_判据.py —— 「闸口默认关闭」的判据（DSH 2026-10-17，用户裁）

用户的话：「上传的内容默认这个闸口关闭」。
本判据把这句话变成**可跑的三条**：

  [1] **默认无通行证 ⇒ 一切档位全拒**（对真 `gate.check()` 判，不是读文档）。
      做法：把 `gate.PASSFILE` 临时指向一个**不存在**的文件（=刚装完的状态），
      然后对 A/B/C/D 四个档位逐个 `check()` —— 必须**全部 False**，且理由说人话；
      `gate.require()` 必须抛 `GateDenied`。
  [2] **没有任何"自动签发"口**：`install.ps1` / `src\agentctl_main.py` /
      `src\panel_main.py` / `src\agentctl\widget.py` 里**不许出现**写通行证的代码
      （出现 `auth_pass` 字样即红）——装完不会替用户签一张。
  [3] **说明书写了这件事**：`README.md` 必须有「默认关闭」段，且含
      ① 默认无通行证=全拒 ② 怎么开（弹窗+密钥）③ 怎么自验（本判据的命令）。

退出码：0=绿；1=红；2=验不了（导不进 gate / 找不到 README）。
★ 本判据**不碰真通行证**：只在内存里改 PASSFILE 指针，跑完还原。
"""
import importlib.util
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("AGENTCTL_ROOT") or os.path.dirname(HERE)
GATE = os.path.join(ROOT, "src", "agentctl", "gate.py")
README = os.path.join(ROOT, "README.md")

verdicts = []


def rec(title, ok, detail="", skip=False):
    verdicts.append((title, "验不了" if skip else ("绿" if ok else "红"), detail))
    print("  [%s] %s%s" % ("验不了" if skip else ("绿" if ok else "红"), title,
                           ("  ← " + detail) if detail else ""), flush=True)


def load_gate():
    if not os.path.isfile(GATE):
        return None
    spec = importlib.util.spec_from_file_location("_gate_for_default_closed", GATE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    g = load_gate()
    if g is None:
        rec("导得进闸门模块（src\\agentctl\\gate.py）", False, "找不到 %s" % GATE, skip=True)
        return finish()

    # [1] 默认无通行证 ⇒ 全拒
    print("[1] 默认状态：没有通行证 ⇒ A/B/C/D 全拒（对真 gate.check() 判）")
    real_pass, real_dir = getattr(g, "PASSFILE", None), getattr(g, "AUTHDIR", None)
    tmpdir = os.path.join(ROOT, "work", "_默认关闭_沙箱")
    os.makedirs(tmpdir, exist_ok=True)
    fake = os.path.join(tmpdir, "auth_pass.json")
    if os.path.exists(fake):
        os.remove(fake)
    try:
        g.AUTHDIR = tmpdir
        g.PASSFILE = fake           # ★ 指到一个**不存在**的文件 = 刚装完的状态
        for scope in "ABCD":
            try:
                ok, why, code = g.check(scope)
            except TypeError:       # 老签名 check(scope) -> (ok, why)
                ok, why = g.check(scope)[:2]
                code = "?"
            good = (ok is False)
            rec("默认无通行证 ⇒ %s 档被拒" % scope, good,
                "放行了！（理由=%r）" % (why,) if not good else (why or "")[:60])
        try:
            g.require("B")
            rec("默认无通行证 ⇒ gate.require 抛 GateDenied", False, "居然放行了")
        except getattr(g, "GateDenied", Exception):
            rec("默认无通行证 ⇒ gate.require 抛 GateDenied", True)
        except Exception as e:
            rec("默认无通行证 ⇒ gate.require 抛 GateDenied", False, "%s: %s" % (type(e).__name__, e))
    finally:
        g.PASSFILE, g.AUTHDIR = real_pass, real_dir
        try:
            if os.path.exists(fake):
                os.remove(fake)
        except Exception:
            pass

    # [2] 没有自动签发口
    print("[2] 装完不会替用户签一张：没有自动签发的代码")
    targets = ["install.ps1", os.path.join("src", "agentctl_main.py"),
               os.path.join("src", "panel_main.py"),
               os.path.join("src", "agentctl", "widget.py")]
    hit = []
    for rel in targets:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            rec("静态：%s 在场" % rel, False, "文件不在")
            continue
        t = io.open(p, encoding="utf-8", errors="replace").read()
        if "auth_pass" in t:
            hit.append(rel)
    rec("静态：install/main/panel/widget 里没有写 auth_pass 的代码", not hit,
        ("发现：%s" % "、".join(hit)) if hit else "四处都干净")

    # [3] 说明书写了
    print("[3] 说明书里写明「默认关闭」")
    if not os.path.isfile(README):
        rec("README 在场", False, "找不到", skip=True)
        return finish()
    rd = io.open(README, encoding="utf-8", errors="replace").read()
    rec("README 有「默认关闭」段", "默认关闭" in rd)
    rec("README 说明「默认无通行证 ⇒ 一切动作被拒」",
        bool(re.search(r"默认[^\n]{0,40}(全拒|一律拒|被拒|拒绝)", rd)))
    rec("README 说明「怎么开」（弹窗 + 密钥）",
        ("弹窗" in rd and "密钥" in rd))
    rec("README 给出「怎么自验」的命令", "_默认关闭_判据.py" in rd)
    return finish()


def finish():
    print()
    print("=" * 70)
    red = [v for v in verdicts if v[1] == "红"]
    skip = [v for v in verdicts if v[1] == "验不了"]
    print("汇总：绿 %d / 红 %d / 验不了 %d"
          % (len(verdicts) - len(red) - len(skip), len(red), len(skip)))
    for t, s, d in verdicts:
        if s != "绿":
            print("  [%s] %s —— %s" % (s, t, d))
    if red:
        print("结论：【红】「默认关闭」不成立")
        return 1
    if skip:
        print("结论：【验不了】—— 有检查项这次没做成（原因见上）；★ 不许读成绿")
        return 2
    print("结论：【绿】默认无通行证 ⇒ 全档位拒绝；装完不会自动签发；说明书已写明")
    return 0


if __name__ == "__main__":
    sys.exit(main())
