# -*- coding: utf-8 -*-
r"""evidence.py —— 证据格式 **v0**：只定义结构 + 序列化/校验，**不被任何现有模块 import**。

★ 为什么单独成文件、而且**不接线**（T2 的硬约束）
  · 证据格式是**对外契约**（T3 的 `evidence_refs[]`、复核侧都依赖它）。住在 core.py 里
    就会被 7600 多行的动作路径牵连 ⇒ 一改字段就动到判定代码；单独成模块才敢让格式独立演进。
  · v0 只落**形状**：谁写、写什么类型、怎么序列化才稳定。**没有任何模块 import 本模块**
    ⇒ 加这个文件不改变任何现有行为（不碰 core.py / action_guard.py / 契约）。

★ 复算分两级（本模块最核心的区分，别混）
  · **结构层可复算**：`recompute_spec` 里给的是**纯数据参数**（`DIFF_STEP` / `HYSTERESIS` /
    `need` …），同样的参数 + 同样的前后帧，调**同一个** `framecache.diff_images` 就能得到
    同样的结论 ⇒ 只有这一类才允许 `recomputable=True`。
  · **像素层不可独立复算**：`recomputable` 必须 `False` —— 算法在 `framecache.diff_images`
    / `core._causal_verdict`（分块并查集、caret 形状滤除、判定区求交），**重写一遍必然漂移**。
    像素层不可独立复算，只能按 `recompute_spec` 用同一函数重算。
    这就是 `digest.py` 的原则：**签发侧与校验侧必须调同一个函数**（调两个函数等于没统一）。

★ 字段顺序 = 序列化顺序（见 `FIELDS`）。`to_json` 一律按 `FIELDS` 重排后输出
  （`sort_keys=False`）—— **不信输入 dict 的键序**，否则同一份证据换个实现就换字节序。

★ 与 `work\_actions\*\evidence.json` 的关系：那是**动作现场的回执**（字段更散：
  `hwnd` / `before_png` / `verdict_canonical` / `ts` …），本模块是它的**规范化子集 + 复算声明**。
  v0 **不读也不写**那些文件，别把两者当同一个东西。
"""
from __future__ import annotations

import json

#: 证据格式版本。**固定 0** —— 版本号由代码填，调用方传别的值直接报错（版本不归写证据的人定）。
SCHEMA_VERSION = 0

#: 字段顺序（**序列化顺序就按这个来**；改顺序 = 改格式）。
FIELDS = (
    "schema_version",
    "actor",
    "target",
    "frame_hash",
    "action",
    "wait_ms",
    "verdict",
    "reasons",
    "evidence_refs",
    "recompute_spec",
    "recomputable",
    "notes",
)

#: 调用方**必须**给的字段（`schema_version` 由代码填；`recomputable` / `notes` 有默认值）。
REQUIRED = (
    "actor",
    "target",
    "frame_hash",
    "action",
    "wait_ms",
    "verdict",
    "reasons",
    "evidence_refs",
    "recompute_spec",
)

#: `recompute_spec` 的必备键（少一个就不叫「可复算」；其余键随记录自带，v0 只认不校验）。
RECOMPUTE_SPEC_KEYS = ("DIFF_STEP", "HYSTERESIS", "need")

#: 键名里带这些词 ⇒ 这条记录**沾了像素/图像** ⇒ 不许声明 `recomputable=True`。
#: ★ 诚实边界：这是**下界**检查（防手滑），不是完备判定 —— 真正的判据是「这条记录离了像素还能不能复算」。
_PIXEL_HINTS = ("frame", "png", "pixel", "image", "img", "crop", "bbox", "shot")

#: 字段 → 期望类型。★ `bool` 是 `int` 的子类，所以 int 字段要单独排掉 bool（见 `_check_type`）。
_TYPES = {
    "schema_version": int,
    "actor": str,
    "target": dict,
    "frame_hash": str,
    "action": dict,
    "wait_ms": int,
    "verdict": str,
    "reasons": list,
    "evidence_refs": list,
    "recompute_spec": dict,
    "recomputable": bool,
    "notes": str,
}

#: 元素必须全是 `str` 的列表字段（`reasons[]` / `evidence_refs[]`）。
_STR_LISTS = ("reasons", "evidence_refs")


def _typename(value) -> str:
    """给报错用的类型名（中文报错里要让人一眼看出给的是什么）。"""
    return type(value).__name__


def _check_type(name: str, value) -> None:
    """单个字段的类型检查；不合格抛 `ValueError`（信息里**必须**带字段名）。"""
    want = _TYPES[name]
    if want is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, want)
    if not ok:
        raise ValueError("证据字段 %s 类型不对：要 %s，给的是 %s"
                         % (name, want.__name__, _typename(value)))
    if name in _STR_LISTS:
        for i, item in enumerate(value):
            if not isinstance(item, str):
                raise ValueError("证据字段 %s 第 %d 项必须是 str，给的是 %s"
                                 % (name, i, _typename(item)))


def _check_recompute_spec(spec: dict) -> None:
    """`recompute_spec` 的必备键与参数类型（三个键都得是**正整数**，缺失/给错就报错）。"""
    missing = [k for k in RECOMPUTE_SPEC_KEYS if k not in spec]
    if missing:
        raise ValueError("证据字段 recompute_spec 缺键：%s（至少要有 %s）"
                         % ("、".join(missing), " / ".join(RECOMPUTE_SPEC_KEYS)))
    for k in RECOMPUTE_SPEC_KEYS:
        v = spec[k]
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError("证据字段 recompute_spec[%s] 必须是 int，给的是 %s"
                             % (k, _typename(v)))
        if v < 1:
            raise ValueError("证据字段 recompute_spec[%s] 必须是正整数，给的是 %r" % (k, v))


def _check_pixel_claim(rec: dict) -> None:
    """`recomputable=True` 的守门：`recompute_spec` 里出现像素类键名就拒绝。

    ★ 语义（格式 v0 的硬规矩）：只有**纯数据层可复算**才允许 True；凡涉及像素/图像参数的
      记录必须为 False。本检查拦的是「手滑把像素参数塞进 spec 还声称可复算」这一种；
      调用方仍要自己守这条语义 —— v0 没有能力从任意 dict 里证明「这里面没有像素」。
    """
    if rec.get("recomputable") is not True:
        return
    bad = [k for k in rec.get("recompute_spec", {})
           if any(hint in str(k).lower() for hint in _PIXEL_HINTS)]
    if bad:
        raise ValueError("证据字段 recomputable 不许为 True：recompute_spec 里含像素/图像参数 %s"
                         "（像素层不可独立复算，必须 recomputable=False）" % "、".join(sorted(bad)))


def validate(rec) -> None:
    """校验一条证据记录：合格返回 `None`，不合格抛 `ValueError`（中文、指明字段名）。

    检查顺序：是不是 dict → 有没有未知字段 → 缺不缺必填 → 类型 → 值域/语义。
    """
    if not isinstance(rec, dict):
        raise ValueError("证据必须是 dict，给的是 %s" % _typename(rec))
    unknown = [k for k in rec if k not in FIELDS]
    if unknown:
        raise ValueError("证据里有 v0 不认识的字段：%s（可写字段见 evidence.FIELDS）"
                         % "、".join(sorted(unknown)))
    missing = [k for k in ("schema_version",) + REQUIRED if k not in rec]
    if missing:
        raise ValueError("证据缺必填字段：%s" % "、".join(missing))
    for name in FIELDS:
        _check_type(name, rec[name])
    if rec["schema_version"] != SCHEMA_VERSION:
        raise ValueError("证据字段 schema_version 必须是 %d（当前格式版本），给的是 %r"
                         % (SCHEMA_VERSION, rec["schema_version"]))
    if not str(rec["actor"]).strip():
        raise ValueError("证据字段 actor 不许为空（谁做的事得写得出来）")
    if not str(rec["verdict"]).strip():
        raise ValueError("证据字段 verdict 不许为空（空判词不是判词；判不出来请写 unknown）")
    if rec["wait_ms"] < 0:
        raise ValueError("证据字段 wait_ms 必须 >= 0，给的是 %r" % (rec["wait_ms"],))
    _check_recompute_spec(rec["recompute_spec"])
    _check_pixel_claim(rec)


def make_record(**kwargs) -> dict:
    """造一条 v0 证据记录（**唯一**的合法入口）→ 返回按 `FIELDS` 排好序的 dict。

    用法：
        rec = make_record(actor="DSH", target={"hwnd": 1234}, frame_hash="ab…",
                          action={"tool": "ctl_click"}, wait_ms=450,
                          verdict="met", reasons=[], evidence_refs=[], recompute_spec=...)

    规则：
      · 缺必填字段 / 未知字段 / 类型不对 / `schema_version` 不是 0 ⇒ `ValueError`；
      · `recomputable` **默认 False**（可复算要**主动声明**，不能默认给 —— 默认 True 等于替人背书）；
      · `notes` 默认空串。最终一律过 `validate()`。
    """
    unknown = [k for k in kwargs if k not in FIELDS]
    if unknown:
        raise ValueError("证据里有 v0 不认识的字段：%s（可写字段见 evidence.FIELDS）"
                         % "、".join(sorted(unknown)))
    missing = [k for k in REQUIRED if k not in kwargs]
    if missing:
        raise ValueError("证据缺必填字段：%s" % "、".join(missing))
    if "schema_version" in kwargs and kwargs["schema_version"] != SCHEMA_VERSION:
        raise ValueError("证据字段 schema_version 固定为 %d，不许传 %r"
                         % (SCHEMA_VERSION, kwargs["schema_version"]))
    rec = {"schema_version": SCHEMA_VERSION}
    for name in FIELDS[1:]:
        if name in kwargs:
            rec[name] = kwargs[name]
    rec.setdefault("recomputable", False)
    rec.setdefault("notes", "")
    rec = {name: rec[name] for name in FIELDS}
    validate(rec)
    return rec


def to_json(rec) -> str:
    """记录 → JSON 文本（UTF-8 安全：`ensure_ascii=False`，中文不转义）。

    ★ `sort_keys=False` 且按 `FIELDS` 重排 ⇒ **字段顺序稳定**，同一份证据永远同一串字节。
    """
    validate(rec)
    ordered = {name: rec[name] for name in FIELDS}
    return json.dumps(ordered, ensure_ascii=False, sort_keys=False)


def from_json(s) -> dict:
    """JSON 文本 → 记录 dict（未知字段**忽略**，但把忽略过什么记进 `notes`）。

    规则：
      · `str` 或 UTF-8 `bytes`；不是合法 JSON / 顶层不是对象 ⇒ `ValueError`；
      · 缺 `schema_version` 按 0 补、缺 `recomputable` 按 False 补、缺 `notes` 按空串补
        （v0 是当前唯一格式，没有别的版本可猜）；
      · 未知字段记成 `notes` 里的一句「忽略未知字段：…」（**不丢事实**，也不让它们混进记录）；
      · 最终一律过 `validate()`。
    """
    if isinstance(s, (bytes, bytearray)):
        try:
            s = bytes(s).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("证据不是 UTF-8 文本：%s" % exc) from None
    if not isinstance(s, str):
        raise ValueError("from_json 要 str（UTF-8 文本），给的是 %s" % _typename(s))
    try:
        raw = json.loads(s)
    except Exception as exc:                       # noqa: BLE001 —— 任何解析失败都算「不是证据」
        raise ValueError("证据不是合法 JSON：%s" % exc) from None
    if not isinstance(raw, dict):
        raise ValueError("证据 JSON 顶层必须是对象，给的是 %s" % _typename(raw))
    unknown = sorted(k for k in raw if k not in FIELDS)
    rec = {name: raw[name] for name in FIELDS if name in raw}
    rec.setdefault("schema_version", SCHEMA_VERSION)
    rec.setdefault("recomputable", False)
    rec.setdefault("notes", "")
    if unknown:
        note = "忽略未知字段：%s" % "、".join(unknown)
        old = str(rec.get("notes") or "").strip()
        rec["notes"] = (old + "｜" + note) if old else note
    rec = {name: rec[name] for name in FIELDS}
    validate(rec)
    return rec


def describe() -> dict:
    """口径说明（给文档/排查/复核用；判据若引这几行，别悄悄改）。"""
    return {
        "格式": "证据格式 v0",
        "字段顺序": list(FIELDS),
        "复算": "结构层可声明 recomputable=True；像素层一律 False",
        "像素层": "不可独立复算，只能按 recompute_spec 用同一函数重算",
        "未知字段": "from_json 忽略并记入 notes；validate/make_record 直接报错",
    }
