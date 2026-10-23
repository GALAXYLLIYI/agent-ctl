# -*- coding: utf-8 -*-
"""账本工具 · 「已批准改动真代码行」外置账本的维护助手（2026-10-23 · 窗口 B）

账本文件：approved_code_lines.json（与本脚本同目录）
主控复核脚本 ⑤-b 的**数量/清单**依据（形状依据是脚本里的 ALLOWED_NEW_CODE，两条都过才放行）。

★ 这个工具**不能**从 act.py / core.py 自动推导「批准哪些行」——它只会：
    · check  : 校验账本（段校验值 + 整本指纹）并打印摘要；
    · hash   : 把你手写好的一段 lines 算成规范化文本 + sha256（免得手算错）；
    · append : 把一段**你写好的** lines 追加进账本，并挂到某个受控文件下
               （重算段校验值与整本指纹，不留手抄的余地）；
    · addfile: 登记一个受控文件（目标路径 + 基线怎么取 + 基线指纹）；
    · removed: 登记某个受控文件的「旧侧」（被删/被替换的真代码行）清单。
  ⇒ 批准哪些行，永远是**主控自己写进账本**的；工具不构成后门。

用法：
    python -X utf8 账本工具.py check
    python -X utf8 账本工具.py hash    <段.json>
    python -X utf8 账本工具.py append  <段.json> --file <文件名>
    python -X utf8 账本工具.py addfile <文件.json>
    python -X utf8 账本工具.py removed <文件名> <段.json>
    python -X utf8 账本工具.py remove  <段名>
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "approved_code_lines.json")


def canon_lines(lines):
    """段规范化文本：按行文本排序，拼「行文本\\t次数」，用 \\n 连接。"""
    return "\n".join("%s\t%d" % (k, lines[k]) for k in sorted(lines))


def seg_sha256(lines):
    return hashlib.sha256(canon_lines(lines).encode("utf-8")).hexdigest()


def whole_sha256(files, segments, removed):
    """整本指纹：先 files（按文件名排序）→ 再 segments（按段顺序）→ 再 removed（按文件排序）。"""
    parts = []
    for fname in sorted(files):
        f = files[fname]
        b = f.get("baseline") or {}
        parts.append("FILE\t%s\t%s\t%s\t%s" % (fname, f.get("target", ""),
                                                b.get("md5", ""),
                                                ",".join(f.get("segments") or [])))
    for s in segments:
        parts.append("SEG\t%s\t%s" % (s.get("name", ""),
                                      canon_lines(s.get("lines") or {})))
    for fname in sorted(removed or {}):
        parts.append("REM\t%s\t%s" % (fname, canon_lines(removed[fname] or {})))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def load():
    with open(LEDGER, encoding="utf-8") as f:
        return json.load(f)


def save(data):
    data["ledger_sha256"] = whole_sha256(data.get("files") or {},
                                         data.get("segments") or [],
                                         data.get("removed") or {})
    with open(LEDGER, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return data["ledger_sha256"]


def cmd_check():
    data = load()
    files = data.get("files") or {}
    segs = data.get("segments") or []
    removed = data.get("removed") or {}
    bad = 0
    print("账本：%s" % LEDGER)
    print("整本指纹（账本内写的是）：%s" % data.get("ledger_sha256"))
    for i, seg in enumerate(segs, 1):
        lines = seg.get("lines") or {}
        got = seg_sha256(lines)
        want = seg.get("sha256")
        ok = (got == want)
        bad += 0 if ok else 1
        print("  第%d段 %-22s %3d 行  段校验值 %s  %s"
              % (i, seg.get("name"), sum(lines.values()), got[:16],
                 "OK" if ok else "★不符（账本写 %s）" % str(want)[:16]))
        print("        证据：%s" % seg.get("evidence", ""))
    for fname in sorted(files):
        segs_txt = []
        for sn in (files[fname].get("segments") or []):
            lines = next((s.get("lines") for s in segs if s.get("name") == sn), {}) or {}
            segs_txt.append("%s:%d行" % (sn, sum(lines.values())))
        print("  文件 %-8s 段=%s ｜ 旧侧登记 %d 行"
              % (fname, "＋".join(segs_txt), sum((removed.get(fname) or {}).values())))
    whole = whole_sha256(files, segs, removed)
    ok_all = (whole == data.get("ledger_sha256"))
    print("  整本指纹（实算）：        %s  %s"
          % (whole[:16], "OK" if ok_all else "★不符"))
    print("  合计批准真代码行：%d 行（旧侧登记 %d 行）"
          % (sum(sum((s.get("lines") or {}).values()) for s in segs),
             sum(sum(v.values()) for v in removed.values())))
    if bad or not ok_all:
        print("★ 账本校验不通过（复核脚本会报红）")
        return 1
    print("账本校验通过 ✓")
    return 0


def cmd_hash(path):
    with open(path, encoding="utf-8") as f:
        seg = json.load(f)
    lines = seg.get("lines") or {}
    canon = canon_lines(lines)
    print("—— 段名：%s（%d 行）" % (seg.get("name", "?"), sum(lines.values())))
    print("—— 规范化文本（照抄进 sha256 计算）——")
    print(canon)
    print("—— 段 sha256 = %s" % seg_sha256(lines))
    data = load()
    segs = list(data.get("segments") or []) + [{"name": seg.get("name", "?"),
                                                 "lines": lines}]
    print("—— 追加该段后整本 ledger_sha256 = %s"
          % whole_sha256(data.get("files") or {}, segs, data.get("removed") or {}))
    print("★ 记得把这两串填回账本对应字段，否则复核脚本会报红。")
    return 0


def cmd_append(path, fname):
    with open(path, encoding="utf-8") as f:
        seg = json.load(f)
    lines = seg.get("lines") or {}
    if not lines:
        raise SystemExit("段文件里没有 lines")
    data = load()
    files = data.setdefault("files", {})
    segs = data.setdefault("segments", [])
    name = seg.get("name") or "（无名段）"
    if any(s.get("name") == name for s in segs):
        raise SystemExit("账本里已经有段「%s」" % name)
    if fname not in files:
        raise SystemExit("账本 files 里没有「%s」" % fname)
    segs.append({
        "name": name,
        "desc": seg.get("desc", ""),
        "evidence": seg.get("evidence", ""),
        "lines": dict(sorted(lines.items())),
        "sha256": seg_sha256(lines),
    })
    files[fname].setdefault("segments", []).append(name)
    fp = save(data)
    print("已追加段「%s」（%d 行 / %d 种）→ files[%s].segments"
          % (name, sum(lines.values()), len(lines), fname))
    print("  段校验值 = %s" % seg_sha256(lines))
    print("  整本 ledger_sha256 = %s" % fp)
    return 0


def cmd_addfile(path):
    """登记一个受控文件：目标路径 + 基线（怎么取 + 指纹）+ 它覆盖的交付段列表。"""
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    name = spec.get("name")
    target = spec.get("target")
    baseline = spec.get("baseline") or {}
    if not name or not target or not baseline.get("md5"):
        raise SystemExit("文件规格不完整（要 name / target / baseline.md5）")
    data = load()
    files = data.setdefault("files", {})
    files[name] = {
        "target": target,
        "baseline": baseline,
        "segments": list(spec.get("segments") or []),
        "desc": spec.get("desc", ""),
    }
    fp = save(data)
    print("已登记受控文件「%s」" % name)
    print("  target   = %s" % target)
    print("  基线     = %s（md5 %s）" % (baseline.get("git_ref") or baseline.get("path"),
                                        baseline["md5"]))
    print("  覆盖段   = %s" % (files[name]["segments"] or "（待 append）"))
    print("  整本 ledger_sha256 = %s" % fp)
    return 0


def cmd_remove(name):
    """撤回一个段（写错了要能改）：从 segments 与 files[*].segments 里摘掉，重算指纹。"""
    data = load()
    files = data.get("files") or {}
    segs = data.get("segments") or []
    hit = [s for s in segs if s.get("name") == name]
    if not hit:
        raise SystemExit("账本里没有段「%s」" % name)
    data["segments"] = [s for s in segs if s.get("name") != name]
    for fname in files:
        segs_txt = files[fname].get("segments") or []
        if name in segs_txt:
            files[fname]["segments"] = [x for x in segs_txt if x != name]
    fp = save(data)
    print("已撤回段「%s」" % name)
    print("  整本 ledger_sha256 = %s" % fp)
    return 0


def cmd_removed(fname, path):
    with open(path, encoding="utf-8") as f:
        seg = json.load(f)
    lines = seg.get("lines") or {}
    if not lines:
        raise SystemExit("文件里没有 lines")
    data = load()
    removed = data.setdefault("removed", {})
    old_n = sum((removed.get(fname) or {}).values())
    removed[fname] = dict(sorted(lines.items()))
    fp = save(data)
    print("已登记 removed[%s]：%d 行 → %d 行" % (fname, old_n, sum(lines.values())))
    print("  整本 ledger_sha256 = %s" % fp)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "check":
        sys.exit(cmd_check())
    elif cmd == "hash":
        if len(sys.argv) < 3:
            raise SystemExit("用法：账本工具.py hash <段.json>")
        sys.exit(cmd_hash(sys.argv[2]))
    elif cmd == "append":
        if len(sys.argv) < 4 or "--file" not in sys.argv:
            raise SystemExit("用法：账本工具.py append <段.json> --file <文件名>")
        sys.exit(cmd_append(sys.argv[2], sys.argv[sys.argv.index("--file") + 1]))
    elif cmd == "removed":
        if len(sys.argv) < 4:
            raise SystemExit("用法：账本工具.py removed <文件名> <段.json>")
        sys.exit(cmd_removed(sys.argv[2], sys.argv[3]))
    elif cmd == "remove":
        if len(sys.argv) < 3:
            raise SystemExit("用法：账本工具.py remove <段名>")
        sys.exit(cmd_remove(sys.argv[2]))
    elif cmd == "addfile":
        if len(sys.argv) < 3:
            raise SystemExit("用法：账本工具.py addfile <文件.json>")
        sys.exit(cmd_addfile(sys.argv[2]))
    else:
        raise SystemExit("未知命令：%s（可用：check / hash / append / removed / remove / addfile）" % cmd)
