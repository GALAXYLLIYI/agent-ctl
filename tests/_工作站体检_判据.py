# -*- coding: utf-8 -*-
r"""tests\_工作站体检_判据.py —— 第 35 条判据：**工作站体检不说谎**。

工单：work\工单_001_工作站体检_20261019.md §1②

断言（真机、不 mock；被检对象是 tools\station_check.py，**子进程跑**）：
  ① 字段齐全：§1① 要求的 10 组字段都在，且类型对
  ② 自洽：runtime.consistent 必须==(act_py_md5 == repo_act_md5)；verdict 必须与数据一致
  ③ 退出码口径：verdict ∈ {ready,dirty_desktop} ⇒ rc=0；否则 rc≠0
  ④ advice 非空且 ≤60 字（"给了结论却不给怎么办"= 不合格）
  ⑤ 负例（真漂移）：把脚本副本的 RUNTIME_ACT 指向一个临时文件 ⇒ verdict 必须 runtime_drift 且 rc≠0
  ⑥ 注入（--inject）：负例那一步改成"两边指向同一文件"（= 不可能漂移）⇒ ⑤ 必然失败 ⇒ 本判据红

退出码：0 绿 / 1 红 / 2 验不了（脚本不在 / 解释器不可用）
★ 只读：判据只跑脚本，不 spawn 键鼠、不改产品代码。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "tools", "station_check.py")
GREEN, RED, SKIP = [], [], []
INJECT = False
ENUM = ("ready", "runtime_drift", "no_pass", "brake_on", "blind", "dirty_desktop")
NEED = [("channels", "uia"), ("channels", "ocr"), ("channels", "cdp"),
        ("runtime", "act_py_md5"), ("runtime", "repo_act_md5"), ("runtime", "consistent"),
        ("desk", "border_windows"), ("desk", "notepad_leftover"),
        ("env", "disk_free_gb"), ("env", "model_keys"),
        ("auth", "pass_present"), ("auth", "pass_expires"), ("auth", "lease_holder"),
        ("auth", "brake"), ("verdict",), ("advice",)]


def rec(name, ok, detail="", skip=False):
    print("[%s] %s —— %s" % ("验不了" if skip else ("绿" if ok else "红"), name, detail), flush=True)
    (SKIP if skip else (GREEN if ok else RED)).append(name)


def finish():
    print("\n" + "=" * 60, flush=True)
    print("汇总：绿 %d / 红 %d / 验不了 %d" % (len(GREEN), len(RED), len(SKIP)), flush=True)
    if SKIP:
        print("结论：【验不了】—— 不许读成绿", flush=True)
        return 2
    if RED:
        print("结论：【红】工作站体检与事实不符（该报的没报 / 该退的没退）", flush=True)
        return 1
    print("结论：【绿】体检字段齐全、自洽、退出码与结论一致，且真漂移能被抓住", flush=True)
    return 0


def run_script(path, timeout=120):
    p = subprocess.run([sys.executable, "-X", "utf8", path, "--json"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    try:
        data = json.loads(p.stdout)
    except Exception:
        data = None
    return p.returncode, data, (p.stdout or "")[:300], (p.stderr or "")[:300]


def main():
    if not os.path.exists(SCRIPT):
        rec("体检脚本存在", False, "缺 %s ⇒ 验不了" % SCRIPT, skip=True)
        return finish()

    # ① 正常跑
    rc, rep, out_head, err = run_script(SCRIPT)
    if rep is None:
        rec("① 能跑出 --json", False, "rc=%r stderr=%r" % (rc, err[:160]))
        return finish()
    missing = []
    _SENT = object()
    for path in NEED:
        cur = rep
        for k in path:
            cur = cur.get(k, _SENT) if isinstance(cur, dict) else _SENT
        # ★ 只判"字段在不在"，不判值（lease_holder=None 是合法值：没活跃租约）
        if cur is _SENT:
            missing.append(".".join(path))
    rec("① 字段齐全（工单元数据 10 组）", not missing, "缺: %r" % (missing[:5],))
    rec("① verdict 是枚举值", rep.get("verdict") in ENUM, "verdict=%r" % (rep.get("verdict"),))
    rec("① advice 非空且≤60字",
        bool(rep.get("advice")) and len(str(rep.get("advice"))) <= 60,
        "len=%d" % len(str(rep.get("advice") or "")))

    # ② 自洽
    rt = rep.get("runtime") or {}
    consist = bool(rt.get("act_py_md5")) and rt.get("act_py_md5") == rt.get("repo_act_md5")
    rec("② consistent 与两个 md5 一致", rt.get("consistent") == consist,
        "consistent=%r md5=%r/%r" % (rt.get("consistent"), rt.get("act_py_md5"),
                                     rt.get("repo_act_md5")))
    v = rep.get("verdict")
    auth = rep.get("auth") or {}
    if v == "runtime_drift":
        ok_v = not consist
    elif v == "no_pass":
        ok_v = (not auth.get("pass_present")) or auth.get("pass_valid") is False
    elif v == "brake_on":
        ok_v = bool(auth.get("brake"))
    elif v == "dirty_desktop":
        ok_v = int((rep.get("desk") or {}).get("notepad_leftover") or 0) > 0
    else:                       # ready / blind
        ok_v = True
    rec("② verdict 与数据自洽", ok_v, "verdict=%r" % (v,))

    # ③ 退出码口径
    want_rc0 = (v in ("ready", "dirty_desktop"))
    rec("③ 退出码与结论一致（可开工=0，否则≠0）", (rc == 0) == want_rc0,
        "verdict=%r rc=%r" % (v, rc))

    # ④ 负例：真漂移必须被报出来
    #   ★ 实测教训：副本必须放在**仓库内**（work\），否则脚本按 HERE/.. 算出的 ROOT 指向临时目录，
    #     连 agentctl 都 import 不到 ⇒ 直接崩，测出来的红是假红。
    src = open(SCRIPT, encoding="utf-8").read()
    workdir = os.path.join(ROOT, "work")
    fake = os.path.join(workdir, "_tmp_act_fake.py")
    with open(fake, "w", encoding="utf-8") as f:
        f.write("# fake act.py for drift test\n")
    patched = re.sub(r'^RUNTIME_ACT = .*$',
                     'RUNTIME_ACT = r"%s"' % fake.replace("\\", "\\\\"),
                     src, count=1, flags=re.M)
    if INJECT:
        # 注入：让"漂移"不可能发生（两边指同一个文件）⇒ ⑤ 必然失败 ⇒ 判据红
        patched = re.sub(r'^RUNTIME_ACT = .*$',
                         'RUNTIME_ACT = REPO_ACT', patched, count=1, flags=re.M)
    drift_py = os.path.join(workdir, "_tmp_station_drift.py")
    with open(drift_py, "w", encoding="utf-8") as f:
        f.write(patched)
    try:
        rc2, rep2, _o2, err2 = run_script(drift_py)
    finally:
        for p in (drift_py, fake):
            try:
                os.remove(p)
            except Exception:
                pass
    v2 = (rep2 or {}).get("verdict")
    rec("④ 负例：真的 md5 不一致 ⇒ verdict=runtime_drift",
        v2 == "runtime_drift", "verdict=%r rc=%r err=%r" % (v2, rc2, err2[:80]))
    rec("④ 负例：漂移时退出码 ≠0", rc2 != 0, "rc=%r" % (rc2,))
    return finish()


if __name__ == "__main__":
    INJECT = "--inject" in sys.argv
    sys.exit(main())
