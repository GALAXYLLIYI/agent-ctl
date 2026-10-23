# -*- coding: utf-8 -*-
r"""tests\_空读不写回_判据.py —— J3：通行证**读不出来**时必须拒绝，且**不许把证写残**（原 S1 的判据保留）

★ 判什么（工单 §7.4 J3）
  沙箱 AUTHDIR 内构造**半截/损坏**的 `auth_pass.json` ⇒ 两侧 `gate.require()` 必须
  `GateDenied`，且**该文件字节不变（前后 md5 相等）**。
  另加**瞬时读失败**子例（老线是「判定时读得到、写回时读不到」那一格 ——
  D18·R2 守卫真正保护的那一格；新线只有一次读，单读失败等价）。

★ 为什么这条现在还有牙（工单 §1 P3 的复核结论）
  复核（A3）认定「读空仍写回」在**两线均已堵**（老线 D18·R2 守卫 / 新线空 `p` 即拒）。
  ⇒ 本条不是去补那个洞，而是**把「洞还在」这件事钉住**：谁把守卫摘了，判据当场红。

★ 三组用例
  ① 控制组：有效通行证 ⇒ 两侧都放行、且计数确实写回了（acts+1）
     —— 证明沙箱真的在读那张证（否则「拒了」可能是假阳性）。
  ② 损坏文件：半截 JSON ⇒ 两侧拒绝 + 文件字节不变。
  ③ 瞬时读失败：有效证 + 注入读失败 ⇒ 两侧拒绝 + 文件字节不变。

★ 注入自证（--inject）：把**老线 D18·R2 守卫**（`require()` 里那段「读失败/读不到 ⇒ 拒绝且不写回」）
  从**临时副本**里摘掉 ⇒ 第 ③ 组必须失效（证被打残、文件被改写）⇒ 判据当场红。
  ⇒ 证明这条判据抓得住「守卫被摘掉」这个病（现役代码一个字节不动）。

★ 沙箱纪律：AUTHDIR / 急停文件 / 应用目录**全部**指到 %TEMP% ⇒ 不碰用户正在用的 `_auth`。

用法：
    python -X utf8 tests\_空读不写回_判据.py             # 正跑（期望 exit=0）
    python -X utf8 tests\_空读不写回_判据.py --inject    # 注入自证（期望 exit=1）

退出码：
    0 = 全绿 ｜ 1 = 有红（含「注入被逮到」）｜ 2 = 注入没让行为退化（判据没牙）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
LEGACY = os.path.join(ROOT, "legacy_tools")

LEGACY_COMPANIONS = ("gate.py", "gate_lock.py", "auth_popup.py", "authdir.py")


# ── 探针代码（在子进程里跑：各自的闸门、自己的沙箱、自己的读）────────────
def _probe_src(legacy_dir, fail_at, scope_expr, is_new):
    head = (
        "import sys, builtins\n"
        "sys.path.insert(0, %r)\n" % (SRC if is_new else legacy_dir) +
        ("from agentctl import gate\n" if is_new else "import gate\n") +
        "_real = builtins.open\n"
        "_hits = {'n': 0}\n"
        "def _faulty(file, *a, **k):\n"
        "    if str(file).replace('\\\\', '/').endswith('auth_pass.json'):\n"
        "        _hits['n'] += 1\n"
        "        if _hits['n'] == %d:\n" % fail_at +
        "            raise OSError('injected: transient read failure')\n"
        "    return _real(file, *a, **k)\n"
        "if %d:\n" % fail_at +
        "    builtins.open = _faulty\n"
        "print('PASSFILE:' + str(getattr(gate, 'PASSFILE', None)))\n"
        "try:\n"
        "    gate.require(%s)\n" % scope_expr +
        "    print('RESULT:ALLOWED')\n"
        "except gate.GateDenied as e:\n"
        "    print('RESULT:DENIED:' + str(e)[:300])\n"
        "except Exception as e:\n"
        "    print('RESULT:ERROR:%s:%s' % (type(e).__name__, str(e)[:300]))\n"
    )
    return head


def child_env(sandbox, tmp):
    e = os.environ.copy()
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    e["AGENTCTL_AUTHDIR"] = sandbox
    e["AGENTCTL_APP_DIR"] = os.path.join(tmp, "appdir")
    e["AGENTCTL_BRAKE_FLAG"] = os.path.join(tmp, "_急停.flag")   # 不许碰真急停
    return e


def md5_of(path):
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def run_probe(legacy_dir, fail_at, is_new, env):
    code = _probe_src(legacy_dir, fail_at, "'B'" if is_new else "gate.B", is_new)
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT, env=env, timeout=120)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    res = ""
    passfile = ""
    for ln in out.splitlines():
        if ln.startswith("RESULT:"):
            res = ln
        elif ln.startswith("PASSFILE:"):
            passfile = ln[len("PASSFILE:"):].strip()
    if not res:
        tail = (err or out or "").splitlines()
        res = "RESULT:ERROR:探针无输出:" + (tail[-1][:200] if tail else "?")
    return res, passfile


def valid_pass():
    """一张两侧都认的通行证（范围用**完整串**，老线按完整串比、新线按短代号比）。"""
    return {
        "scope": "B级 点击滚动",
        "scopes": ["B级 点击滚动"],
        "expires": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 3600)),
        "expires_at": time.time() + 3600,
        "issued_at": time.time(),
        "acts": 0,
        "acts_limit": 20,
    }


BROKEN_PASS = '{"scope": "B级 点击滚动", "scopes": ["B级 点击滚动", '   # 半截 JSON


def write_pass(sandbox, obj_text):
    os.makedirs(sandbox, exist_ok=True)
    p = os.path.join(sandbox, "auth_pass.json")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(obj_text)
    return p


# ── 注入：把老线 D18·R2 守卫从临时副本里摘掉 ─────────────────────────────
_GUARD_START = "            p, err = _load_checked(PASSFILE)"
_GUARD_END = '            p["acts"] = p.get("acts", 0) + 1'


def _copy_writable(src, dst):
    """拷一份并**清掉只读位**（产物是 gen_legacy 设的只读；副本要能改）。

    ★ 只读位是 Windows 的文件属性，shutil.copy2 会原样带过去 ⇒ 不清掉就写不进去。
    """
    shutil.copy2(src, dst)
    try:
        os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def strip_guard(legacy_dir, dst):
    """拷一份老线树到 dst，并把 require() 里的 D18·R2 守卫摘掉。

    返回 (是否真的摘掉了, 说明)。★ 摘不掉 = 注入没下上 ⇒ 判据必须红。
    """
    os.makedirs(dst, exist_ok=True)
    for f in LEGACY_COMPANIONS:
        s = os.path.join(legacy_dir, f)
        if os.path.isfile(s):
            _copy_writable(s, os.path.join(dst, f))
    gp = os.path.join(dst, "gate.py")
    if not os.path.isfile(gp):
        return False, "老线 gate.py 不在（%s）" % legacy_dir
    with open(gp, "r", encoding="utf-8") as f:
        txt = f.read()
    i = txt.find(_GUARD_START)
    j = txt.find(_GUARD_END, i if i >= 0 else 0)
    if i < 0 or j < 0:
        return False, "老线 gate.py 里找不到 D18·R2 守卫（形态变了？）⇒ 注入无处可下"
    new = txt[:i] + _GUARD_START + "\n" + txt[j:]
    with open(gp, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    return True, "已摘掉老线 require() 里的 D18·R2 守卫（拒绝且不写回那一段）"


# ── 主流程 ────────────────────────────────────────────────────────────────
def collect(rows, legacy_dir, sandbox, env, tmp, tag, legacy_fail_at, new_fail_at,
            want_legacy, want_new, want_unchanged):
    """跑两侧探针并记账。返回失败条数。

    ★ 沙箱自证：探针会报出它**自己**读到的那张证（gate.PASSFILE）——
      不是沙箱里那张 ⇒ 这条直接判红（「探针没读沙箱那张证」）。
      ⇒ 改前的新线闸门**不认** AGENTCTL_AUTHDIR ⇒ 它会读到用户真在用的那张证 ⇒ 当场红。
    """
    bad = 0
    want_pass = os.path.join(sandbox, "auth_pass.json")
    for role, is_new, fail_at, want in (("老线闸门", False, legacy_fail_at, want_legacy),
                                        ("新线闸门", True, new_fail_at, want_new)):
        pf = os.path.join(sandbox, "auth_pass.json")
        before = md5_of(pf)
        res, passfile = run_probe(legacy_dir, fail_at, is_new, env)
        after = md5_of(pf)
        ok_denied = res.startswith("RESULT:DENIED")
        ok_allowed = res.startswith("RESULT:ALLOWED")
        if want == "DENIED":
            ok = ok_denied
        else:
            ok = ok_allowed
        # 沙箱自证：读的那张证必须是沙箱里那张
        in_sandbox = (os.path.normcase(os.path.abspath(passfile or "")) ==
                      os.path.normcase(os.path.abspath(want_pass)))
        if not in_sandbox:
            ok = False
        if want_unchanged:
            ok = ok and (before == after)
            note = "文件 md5 %s → %s（要求不变）" % (before, after)
        else:
            # 放行 ⇒ 计数必须真的写回（证明它读的就是沙箱那张证）
            ok = ok and (before != after)
            note = "文件 md5 %s → %s（放行后计数写回）" % (before, after)
        if not in_sandbox:
            note = "★探针没读沙箱那张证（它读的是 %s）★ " % (passfile or "?") + note
        rows.append(("%s · %s" % (tag, role),
                     "%s%s" % (want, "＋文件字节不变" if want_unchanged else "＋计数写回"),
                     res + "｜" + note, ok))
        if not ok:
            bad += 1
    return bad


def do_normal():
    print("== J3 判据 · 通行证读不出来必须拒绝且不写回 ==", flush=True)
    print("[口径] 两侧各起独立子进程；沙箱 AUTHDIR / 急停 / 应用目录全在 %TEMP%", flush=True)
    rows = []
    bad = 0
    tmp = tempfile.mkdtemp(prefix="_J3_空读_")
    try:
        sandbox = os.path.join(tmp, "authdir")
        env = child_env(sandbox, tmp)
        # ① 控制组：有效证 ⇒ 两侧放行、计数真的写回
        write_pass(sandbox, json.dumps(valid_pass(), ensure_ascii=False))
        bad += collect(rows, LEGACY, sandbox, env, tmp, "① 控制组（有效证）", 0, 0,
                       "ALLOWED", "ALLOWED", False)
        # ② 损坏/半截文件
        write_pass(sandbox, BROKEN_PASS)
        bad += collect(rows, LEGACY, sandbox, env, tmp, "② 半截/损坏的证", 0, 0,
                       "DENIED", "DENIED", True)
        # ③ 瞬时读失败（老线：写回前那次读；新线：唯一那次读）
        write_pass(sandbox, json.dumps(valid_pass(), ensure_ascii=False))
        bad += collect(rows, LEGACY, sandbox, env, tmp, "③ 瞬时读失败", 2, 1,
                       "DENIED", "DENIED", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for label, want, got, ok in rows:
        print("  %-46s 期望=%-22s 实得=%-58s %s"
              % (label, want, got[:58], "PASS" if ok else "FAIL"), flush=True)
    print()
    print("=" * 78, flush=True)
    if bad:
        print("【红】%d 条不成立（详见上面 FAIL 行）" % bad, flush=True)
        return 1
    print("【绿】两侧读不出来都拒绝、且证一个字节都没被改写（%d 条断言）" % len(rows),
          flush=True)
    return 0


def do_inject():
    print("== J3 判据 · 注入自证（摘掉老线 D18·R2 守卫）==", flush=True)
    tmp = tempfile.mkdtemp(prefix="_J3_inject_")
    dst = os.path.join(tmp, "legacy")
    try:
        applied, why = strip_guard(LEGACY, dst)
        print("注入：%s" % (why if applied else "★没下上★ " + why), flush=True)
        if not applied:
            print()
            print("=" * 78, flush=True)
            print("【红】注入无处可下 ⇒ 判据抓不住这个病", flush=True)
            return 1
        sandbox = os.path.join(tmp, "authdir")
        env = child_env(sandbox, tmp)
        write_pass(sandbox, json.dumps(valid_pass(), ensure_ascii=False))
        pf = os.path.join(sandbox, "auth_pass.json")
        before = md5_of(pf)
        res, passfile = run_probe(dst, 2, False, env)
        after = md5_of(pf)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("注入副本 · 瞬时读失败：%s" % res, flush=True)
    print("            探针读到的那张证：%s" % (passfile or "?"), flush=True)
    print("            文件 md5 %s → %s" % (before, after), flush=True)
    print()
    print("=" * 78, flush=True)
    if res.startswith("RESULT:DENIED") and before == after:
        print("【✗崩溃】摘掉守卫后仍然「拒绝且不写回」⇒ 注入没让行为退化 ⇒ 判据没牙", flush=True)
        return 2
    print("【红】注入被逮到：摘掉 D18·R2 守卫后 ⇒ %s" % res, flush=True)
    print("            文件 md5 %s → %s（证被打残/被改写 ⇒ 正是那个病）" % (before, after),
          flush=True)
    print("★ 判据有证明力：守卫一旦被摘，它当场就红。", flush=True)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="只跑注入自证（必须红）")
    args = ap.parse_args()
    for need in (os.path.join(SRC, "agentctl", "gate.py"),
                 os.path.join(LEGACY, "gate.py")):
        if not os.path.isfile(need):
            print("[验不了] 仓内文件不在：%s" % need, flush=True)
            return 2
    return do_inject() if args.inject else do_normal()


if __name__ == "__main__":
    raise SystemExit(main())
