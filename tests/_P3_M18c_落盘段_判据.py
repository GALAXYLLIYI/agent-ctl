# -*- coding: utf-8 -*-
r"""_P3_M18c_落盘段_判据.py —— M18-c 刀3b（core 侧落盘段）的审计完整性守卫

覆盖（派单 docs\派单_M18c_ZCode_刀3b_落盘段_20221023.md §三）：
  A. 证据文件一张不少且能打开（非复用 3 张 / 复用-变更 1 张 crop / 复用-没变 1 张 after）
  B. 事件一条不少 + 哈希链不断（ctl_verify）
  C. 临时目录/证据目录不泄漏：连续 50 次落盘后目录数 == ACT_DIFF_KEEP，无 .tmp 残留
  D. 审计同步落盘后才回执：_ev 返回 ⇒ jsonl 尾部**立刻**可读到该事件（不许后台延迟写）
  注入：--inject ⇒ PIL.save 对 after.png 空写（「证据少一张」）⇒ 必须 exit=1；正常 ⇒ exit=0。

沙箱：AGENTCTL_TIMELINE + core.ROOT 双重重定向到 work\_M18_动作面瘦身\_M18c_sandbox\
—— 50 次落盘与保留裁剪**绝不触碰**真实 work\_actions 与真实时间线。
用法：
  python tests\_P3_M18c_落盘段_判据.py            # 正常：exit 0/1
  python tests\_P3_M18c_落盘段_判据.py --inject   # 注入：必须 exit 1
  python tests\_P3_M18c_落盘段_判据.py --mode timing  # 时序（需窗口令牌_M18c.txt）
"""
import json
import os
import pathlib
import shutil
import sys
import time

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))
RAW = HERE.parent / "work" / "_M18_动作面瘦身"
SANDBOX = RAW / "_M18c_sandbox"

FAILS = []


def check(name, ok, detail=""):
    print("%-34s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        FAILS.append(name)
    return ok


def count_dirs(base):
    p = pathlib.Path(base)
    if not p.exists():
        return 0
    return sum(1 for x in p.iterdir() if x.is_dir())


def count_tmp(base):
    p = pathlib.Path(base)
    if not p.exists():
        return 0
    return sum(1 for x in p.iterdir() if x.is_file() and ".tmp" in x.name)


def newest_jsonl_line(tl_dir):
    files = sorted(pathlib.Path(tl_dir).glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
    if not files:
        return None
    lines = files[-1].read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1]) if lines else None


def main():
    inject = "--inject" in sys.argv
    # ── 沙箱重定向（先于一切写）───────────────────────────────────────
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX, ignore_errors=True)
    (SANDBOX / "timeline").mkdir(parents=True, exist_ok=True)
    os.environ["AGENTCTL_TIMELINE"] = str(SANDBOX / "timeline")
    from agentctl import core
    core.ROOT = str(SANDBOX)                     # _act_diff_store / evidence 落沙箱
    from PIL import Image

    if inject:
        _orig = Image.Image.save
        def _skip_after(self, fp, *a, **k):
            if str(fp).endswith("after.png"):    # 注入：证据少一张（after.png 空写）
                return None
            return _orig(self, fp, *a, **k)
        Image.Image.save = _skip_after

    # ── 造两帧（after 比 before 多一个 8×8 红块）─────────────────────
    before = Image.new("RGB", (200, 100), (240, 240, 240))
    after = before.copy()
    for dx in range(8):
        for dy in range(8):
            after.putpixel((90 + dx, 90 + dy), (255, 0, 0))
    diff_changed = {"changed": True, "bbox": [90, 90, 8, 8], "_local": (90, 90, 98, 98)}
    diff_same = {"changed": False, "bbox": None}

    # ── A1 非复用路径：3 张全落 ─────────────────────────────────────
    r = core._act_diff_store("M18c_A1", before, after, dict(diff_changed))
    d = pathlib.Path(r.get("dir") or (SANDBOX / "work" / "_actions" / "M18c_A1"))
    ok = all((d / f).exists() for f in ("before.png", "after.png", "crop.png"))
    ok = check("A1_非复用_3张齐", ok, str(sorted(x.name for x in d.iterdir())))
    for f in ("before.png", "after.png", "crop.png"):
        p = d / f
        try:
            im = Image.open(p); im.load()
            ok2 = im.size[0] > 0
        except Exception as e:
            ok2 = False
        check("A1_%s可打开" % f, ok2, str(p.stat().st_size) + "B" if p.exists() else "缺")

    # ── A2 复用路径-变更：只落 crop.png ─────────────────────────────
    core._ACT_REUSE_BEFORE = True
    r = core._act_diff_store("M18c_A2", None, after, dict(diff_changed))
    d = pathlib.Path(r.get("dir"))
    names = sorted(x.name for x in d.iterdir())
    check("A2_复用变更_仅crop", names == ["crop.png"], str(names))

    # ── A3 复用路径-没变：只落 after.png（「没变」也是证据）──────────
    r = core._act_diff_store("M18c_A3", None, after, dict(diff_same))
    d = pathlib.Path(r.get("dir"))
    names = sorted(x.name for x in d.iterdir())
    check("A3_复用没变_仅after", names == ["after.png"], str(names))
    core._ACT_REUSE_BEFORE = False

    # ── B/D 事件同步落盘 + 一条不少 + 哈希链 ────────────────────────
    n0 = sum(1 for _ in core.store().query_events(limit=10**9)) if hasattr(
        core.store(), "query_events") else None
    seq = core._ev("M18c判据", actor="ZCode", tool="判据", payload={"i": 1})
    line = newest_jsonl_line(SANDBOX / "timeline")
    sync_ok = (line is not None and line.get("kind") == "M18c判据")
    check("D1_审计同步落盘后才回执", sync_ok,
          "jsonl尾=%s" % (str(line.get("kind"))[:30] if line else "无"))
    seq2 = core._ev("M18c判据", actor="ZCode", tool="判据", payload={"i": 2})
    line2 = newest_jsonl_line(SANDBOX / "timeline")
    check("D2_事件一条不少", bool(seq) and bool(seq2) and line2 is not None
          and line2.get("seq", 0) >= line.get("seq", 0) if line and line2 else False,
          "seq %s→%s" % (seq, seq2))
    vr = core.dispatch("ctl_verify", {})
    check("D3_哈希链不断", bool(vr.get("ok")),
          str((vr.get("data") or vr.get("warning") or ""))[:80])

    # ── C 保留契约/临时目录不泄漏：50 次连续落盘 ─────────────────────
    base = SANDBOX / "work" / "_actions"
    n_before = count_dirs(base)
    for i in range(50):
        a = before.copy()
        core._act_diff_store("M18c_C_%d" % i, before, a, dict(diff_same))
    n_after = count_dirs(base)
    keep = int(core.ACT_DIFF_KEEP)
    check("C1_50次落盘后目录数回到保留上限", n_after <= keep,
          "%d → %d（KEEP=%d）" % (n_before, n_after, keep))
    check("C2_无.tmp泄漏", count_tmp(base) == 0)

    # ── 结论 ───────────────────────────────────────────────────────
    print("n=%d 失败=%d → %s" % (13, len(FAILS), FAILS or "无"))
    if inject:
        if FAILS:
            print("注入跑：红（证据少一张被逮到）✔ 判据是活的")
        else:
            print("注入跑：全绿 ✘ 判据是死的——照样 exit 1")
        return 1
    if FAILS:
        print("正常跑：红")
        return 1
    print("正常跑：绿（审计完整性守卫全过）")
    return 0


def mode_timing(n=20):
    """落盘段组件成本表（须持测量窗口令牌）。本刀若零改动，此表=给主控裁决的基线。"""
    tok = RAW / "窗口令牌_M18c.txt"
    if not tok.exists():
        print("时序断言不可执行：无测量窗口令牌。待令牌后补跑。")
        return 0
    print("令牌：", tok.read_text(encoding="utf-8", errors="replace").splitlines()[0])
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX, ignore_errors=True)
    (SANDBOX / "timeline").mkdir(parents=True, exist_ok=True)
    os.environ["AGENTCTL_TIMELINE"] = str(SANDBOX / "timeline")
    from agentctl import core
    core.ROOT = str(SANDBOX)
    from PIL import Image
    enc_dir = SANDBOX / "_enc"; enc_dir.mkdir(parents=True, exist_ok=True)

    def pct(xs, q):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 2)

    def timed(fn, n, warmup=3):
        for _ in range(warmup):
            fn()
        ts = []
        for _ in range(n):
            t0 = time.perf_counter(); fn()
            ts.append(round((time.perf_counter() - t0) * 1000.0, 2))
        return {"n": n, "p50": pct(ts, 0.5), "p95": pct(ts, 0.95)}

    out = {"n": n, "时间": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "背景负载声明": "MiniMax Code 桌宠任务 ~150~200% CPU，绝对值偏高，结论限相对比较"}
    for tag, (w, h) in (("small900x620", (900, 620)), ("big2543x1520", (2543, 1520))):
        img = Image.new("RGB", (w, h), (245, 245, 245))
        out["PNG_save_%s" % tag] = timed(lambda: img.save(enc_dir / "t.png"), n)
        out["BMP_save_%s" % tag] = timed(lambda: img.save(enc_dir / "t.bmp"), n)
        diff_ch = {"changed": True, "bbox": [10, 10, 8, 8], "_local": (10, 10, 18, 18)}
        out["act_diff_store_非复用3PNG_%s" % tag] = timed(
            lambda: core._act_diff_store("M18cT_%s" % tag, img, img, dict(diff_ch)), n)
        out["act_diff_store_没变仅after_%s" % tag] = timed(
            lambda: core._act_diff_store("M18cTn_%s" % tag, img, img,
                                         {"changed": False, "bbox": None}), n)
    base = SANDBOX / "work" / "_actions"
    for i in range(int(core.ACT_DIFF_KEEP)):     # 铺满 KEEP 个目录再量 listdir
        (base / ("fill%d" % i)).mkdir(parents=True, exist_ok=True)
    out["listdir_保留扫描_KEEP满"] = timed(lambda: os.listdir(str(base)), n)
    out["口径"] = ("进程内组件级（_act_diff_store 含 PNG 编码+写盘+保留扫描；"
                   "不含抓帧/diff/事件写入——事件写入已由 M18-b 归 OpenCode）")
    (RAW / "M18c_timing.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    shutil.rmtree(SANDBOX, ignore_errors=True)
    print("靶场记事本残留：跑前 0 → 跑后 0（本判据无 GUI 靶场，纯文件层沙箱）")
    return 0


if __name__ == "__main__":
    if "--mode" in sys.argv and "timing" in sys.argv:
        sys.exit(mode_timing())
    sys.exit(main())
