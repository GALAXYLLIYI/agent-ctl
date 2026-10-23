# -*- coding: utf-8 -*-
r"""tests\_authdir注入必红_判据.py —— J2：J1 的**注入必红**见证（工单 §7.4 J2）

★ 判什么
  把**签发方**（auth_popup.py）的账本解析改回**写死 `BASE/_auth`**（= 当前病灶），
  J1 那套「三方一致」的比较**必须判红**并点名签发方那一条 ⇒ 证明 J1 不是恒绿的死判据。
  改在**临时副本**上，现役一个字节不动；恢复后（现役树）必须绿。

★ 为什么单独立一条（而不是只靠 J1 --inject）
  · J1 --inject 是 J1 自己的注入口（run_all --inject 里它「如期变红」）；
  · 本条是**独立登记的见证**：现役绿 + 注入红，两段都验 ⇒ 谁跑了都能看见证据。
  ⇒ 本条的**正跑就是注入自证**（注入是它的动作）⇒ 自证成立返回 rc=0，
    并按本仓惯例声明 `NO_INJECT:`（它没有第二个注入口）。

★ 口径
  · 现役树（不注入）必须三方一致 ⇒ 否则红（说明 J1 的口径有问题，注入对照不成立）。
  · 注入副本必须**红**，且红的那几条必须是**签发方** ⇒ 证明红的原因是「签方写死」。

用法：
    python -X utf8 tests\_authdir注入必红_判据.py      # 正跑（期望 exit=0）

退出码：
    0 = 自证成立（现役绿 + 注入红且点名签发方）｜ 1 = 红（自证不成立）｜ 2 = 环境不满足
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
J1_PATH = os.path.join(HERE, "_authdir一致_判据.py")


def load_j1():
    """按文件路径 import J1（文件名带中文 ⇒ 不用普通 import 语句）。"""
    spec = importlib.util.spec_from_file_location("j1_authdir", J1_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("打不开 J1 判据：%s" % J1_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    if not os.path.isfile(J1_PATH):
        print("[验不了] J1 判据不在：%s" % J1_PATH, flush=True)
        return 2
    j1 = load_j1()
    print("== J2 判据 · J1 的注入必红（签发方改回写死 BASE/_auth）==", flush=True)
    print("[口径] 两段：现役树必须绿 + 注入副本必须红（且红在签发方那几条）", flush=True)

    # ── ① 现役树：必须绿 ────────────────────────────────────────────────
    rows, ok = j1.run_cases(tag="现役")
    j1.show_rows("① 现役树（未注入）", rows)
    if not ok:
        print()
        print("=" * 78, flush=True)
        print("【红】现役树本身就不一致 ⇒ 注入对照不成立（先修 J1 的口径）", flush=True)
        return 1

    # ── ② 注入副本：必须红，且红在签发方 ────────────────────────────────
    tmp = tempfile.mkdtemp(prefix="_J2_inject_")
    dst = os.path.join(tmp, "legacy")
    try:
        applied, why = j1.inject_popup(j1.LEGACY, dst)
        print("\n注入：%s" % (why if applied else "★没下上★ " + why), flush=True)
        if not applied:
            print()
            print("=" * 78, flush=True)
            print("【红】注入无处可下 ⇒ 判据抓不住这个病", flush=True)
            return 1
        inj_rows, inj_ok = j1.run_cases(legacy_dir=dst, tag="注入副本")
        j1.show_rows("② 注入后的副本", inj_rows)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78, flush=True)
    if inj_ok:
        print("【红】注入后仍然全绿 ⇒ J1 抓不住这个病 ⇒ 自证不成立", flush=True)
        return 1
    hit = [r for r in inj_rows if not r[3] and "签发方" in r[0]]
    if not hit:
        print("【红】注入后有红，但不是「签发方」那几条 ⇒ 判据口径可能变了", flush=True)
        return 1
    print("【红】（注入侧）%d 条转红，且点名签发方：%s"
          % (len(hit), "；".join(r[0] for r in hit)), flush=True)
    print("【绿】自证成立：现役绿 + 注入红 ⇒ J1 有牙（判据是活的）", flush=True)
    print("NO_INJECT: 本条的正跑**就是**注入自证（注入在临时副本上做）⇒ 无第二个注入口",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
