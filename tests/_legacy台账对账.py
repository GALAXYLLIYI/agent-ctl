# -*- coding: utf-8 -*-
r"""tests\_legacy台账对账.py —— CHK-A 落位二（窗U 设计，本地 agent 2026-10-17 补接线）

第三方 Step5 收口门禁**必要条件 1**「产物重生一致」要求：
**CHK-A 必须在 `gen_legacy` 与 `run_all` 两处都有真实运行输出。**

本判据就是 run_all 那一处。它**不重写**对账逻辑 —— 直接 import `tools\gen_legacy.py`
里的 `chk_a()` / `report_chk_a()`（★ 一份实现服务两处：复制第二份实现，
就等于给自己再开「判据存在 ≠ 判据在跑」那个洞）。

判据两条腿（与 gen_legacy 内一致）：
  ① 产物 vs A₀：`legacy_tools\*` 现内容的规范化 md5 == 台账里「上次写盘时」的 md5
     ⇒ 抓「产物被手改 / 台账有登记而仓里缺件」。
  ② 真源 vs T₀：真源现内容 == 台账里「上次生成时真源」的 md5
     ⇒ 抓「真源改过但产物没刷（过期）」。

★ **不是** T₀ == A₀：被 RULES 净化的 5 个文件（cdp/coord/option_locator/perceive/selftest）
  两者合法不等，拿它们互比会把正常仓判红（本地 agent 2026-10-16 亲踩，pc 回了 5 条假警报）。

退出码：
  0 = 两条腿都对齐
  1 = 红：有任何漂移 / 台账缺失 / 真源不可达（★ 4c：环境错误不许读成绿）
  2 = 用法错误（不该出现在 run_all 里）

环境变量：
  AGENTCTL_ROOT       覆盖仓根（窗M 的口径：判据必须能指向"另一份仓"自证，
                      默认自推导本文件所在仓 —— 不许写死本机路径）
  AGENTCTL_LEGACY_SRC 覆盖真源目录（默认取台账里记的相对路径）

--inject：注入式跑红自证（把产物复制到 %TEMP%、改一个字节 ⇒ 腿① 必须变红）。
          不碰产品代码、不碰真台账。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("AGENTCTL_ROOT") or os.path.dirname(HERE)

PRODUCT_DIR = os.path.join(ROOT, "legacy_tools")
LEDGER = os.path.join(ROOT, "tools", "legacy_ledger.json")
GEN = os.path.join(ROOT, "tools", "gen_legacy.py")


def out(msg=""):
    print(msg, flush=True)


def load_gen():
    """按路径加载 gen_legacy（不往 sys.path 塞东西，免得起重名）。"""
    if not os.path.isfile(GEN):
        out("[红] 生成器不在：%s" % GEN)
        out("     ⇒ CHK-A 是「产物 vs 台账」的对账，没有生成器就拿不到 A₀ 口径，判红。")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("_gen_legacy_for_chk_a", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 只定义函数/常量，不执行生成（main 在 __main__ 下）
    return mod


def verdict(v, mod, src_override=None):
    """跑判据、打印、返回 rc。"""
    red = mod.report_chk_a(v, hard_src=True)
    if v["src_unreachable"] and not src_override:
        out("【红】腿②没验过：%s" % v["src_unreachable"])
        out("     ⇒ 4c：环境错误不许读成绿。请把真源放到位，或用 AGENTCTL_LEGACY_SRC 指过去。")
        red = True
    if v["n"] == 0:
        out("【红】台账里没有这个产物目录的任何基线（n=0）—— 等于没有可对的东西。")
        red = True
    if not v["prod_red"] and not v["src_unreachable"]:
        out("【绿】CHK-A 两条腿都对齐：产物 = A₀（%d 项）、真源 = T₀ ⇒ 无未登记手改、无过期产物。"
            % v["n"])
    return 1 if red else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="CHK-A 产物台账对账（run_all 侧）")
    ap.add_argument("--inject", action="store_true", help="注入式跑红自证（改副本，期望变红）")
    a = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    mod = load_gen()
    src_override = os.environ.get("AGENTCTL_LEGACY_SRC")
    if not os.path.isfile(LEDGER):
        out("[红] 台账不在：%s" % LEDGER)
        out("     ⇒ 台账必须进 git（G3 边界③）；缺台账 = 基线不可核，判红。")
        return 1
    ledger = mod.load_ledger(LEDGER)

    if a.inject:
        return do_inject(mod, ledger, src_override)

    out("=" * 78)
    out("CHK-A · 台账对账（run_all 侧） · 仓 %s" % ROOT)
    out("=" * 78)
    out("产物目录：%s" % PRODUCT_DIR)
    out("台账    ：%s" % LEDGER)
    v = mod.chk_a(PRODUCT_DIR, ledger, src_dir=src_override or None,
                  tag="run_all · 审计口径")
    return verdict(v, mod, src_override)


def do_inject(mod, ledger, src_override):
    """注入式跑红自证：产物 copies 到 %TEMP%，改一个字节 ⇒ 腿① 必须报出那一个文件。

    对照组（不改）= 0 漂移；实验组（改 1 字节）= 恰好 1 条漂移且点名那个文件。
    产品代码与真台账**一个字节不动**。
    """
    tmp = tempfile.mkdtemp(prefix="agentctl_chkA_inject_")
    out("=" * 78)
    out("CHK-A · 注入式跑红自证 · 临时区 %s" % tmp)
    out("=" * 78)
    try:
        dst = os.path.join(tmp, "legacy_tools")
        shutil.copytree(PRODUCT_DIR, dst)
        # ★ 台账键 = 相对仓根的路径；副本在 %TEMP% ⇒ 键不同，必须替它造一条同内容的基线，
        #   否则 chk_a 找不到条目、n=0 ⇒ 注入也报「0 漂移」＝假绿（第一版就是这么翻车的）。
        base = (ledger.get("dirs", {}).get("legacy_tools") or {})
        led2 = {"dirs": {mod.ledger_key(dst): dict(base)}}
        src_dir = src_override or None
        v0 = mod.chk_a(dst, led2, src_dir=src_dir, tag="注入前·对照")
        out("[对照] 未改动的副本：腿① 漂移 %d 项（应为 0）" % len(v0["prod_red"]))
        if v0["prod_red"]:
            out("[红] 副本还没注入就已经漂移 ⇒ 真仓本身脏，注入自证不成立。")
            for name, a0, now, why in v0["prod_red"]:
                out("      - %-24s %s" % (name, why))
            return 1

        # 挑一个台账里有 A₀ 的文件注入一个字节
        target = "act.py" if os.path.isfile(os.path.join(dst, "act.py")) else None
        if target is None:
            cand = [n for n in sorted(ledger.get("dirs", {}).get("legacy_tools", {})
                                      .get("files", {})) if os.path.isfile(os.path.join(dst, n))]
            if not cand:
                out("[红] 副本里找不到任何台账登记的文件 ⇒ 注入自证做不了。")
                return 1
            target = cand[0]
        p = os.path.join(dst, target)
        os.chmod(p, 0o666)
        with open(p, "ab") as fh:
            fh.write(b"\n# injected-defect\n")
        out("[注入] 在副本的 %s 末尾加了一行 ⇒ 腿① 必须报它" % target)

        v1 = mod.chk_a(dst, led2, src_dir=src_dir, tag="注入后·实验")
        names = [n for n, _a0, _now, _why in v1["prod_red"]]
        out("[实验] 腿① 漂移 %d 项：%s" % (len(names), "、".join(names) or "（空）"))
        ok = (names == [target])
        if not ok:
            out("[红] 注入后没有**恰好**报出 %s ⇒ 这条判据是死的/不准。" % target)
            return 1
        out("【绿】注入自证成立：未改的副本 0 漂移，注入 1 字节后点名 %s ⇒ CHK-A 是活的。" % target)
        return 0
    finally:
        for dp, dns, fns in os.walk(tmp):
            for fn in fns:
                try:
                    os.chmod(os.path.join(dp, fn), 0o666)
                except Exception:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
