# -*- coding: utf-8 -*-
r"""tests\_inject.py —— 「能跑红」的自证注入器（窗M · 2026-10-16）

★ 为什么要有这个文件（病根）：
    以前跑红靠 work\ 里的两份材料 ——
        work\_wD_红基线_repo（wB 拿 AGENTCTL_ROOT 指过去）
        work\_2h_档位口径_红基线_ctrl_border.py（wA/wF 拿 --target 指过去）
    而 .gitignore **整个排除了 work\** ⇒ 新 clone 的机器**能跑绿、不能复现跑红**，
    判据的可证伪性只剩一句文档背书（窗口A 干净 clone 自证 §4②）。
    ⇒ 改成**注入式**（照 tests\_窗口D_接缝判据.py --inject 的现成范例）：
        把现役文件**复制一份**到 %TEMP% → 在**副本**上注入一个已知缺陷 →
        判据原样跑那份副本 ⇒ 同一套断言必然报红。

★ 铁律：
    · 注入只发生在临时副本上，**产品代码一个字节都不改**（src/ 只被 copytree 读）；
    · 副本路径一律打印出来，谁都能复核「改的是副本不是现役」；
    · 本文件**不含任何断言**，只是「复制 + 追加」的手脚 —— 判红判绿仍由各判据自己的断言决定。

用法（三条判据都已接上）：
    python -X utf8 tests\_2h_wA_档位口径判据.py --inject   # 必红（档位首字母陷阱）
    python -X utf8 tests\_2h_wF_配色冻结判据.py --inject   # 必红（C 档配色漂移一格）
    python -X utf8 tests\_2h_wB_D级绊线判据.py  --inject   # 必红（契约里塞进拖拽工具）
    python -X utf8 tests\run_all.py --inject               # 一次跑齐上面三条，**期望它们都红**
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BORDER_SRC = os.path.join(ROOT, "src", "agentctl", "ctrl_border.py")

# ── 注入段①：复现旧版「首字母陷阱」（wA 用）────────────────────────────
#   真版 _tiers_from_scopes()：闸门认不出的项一律当没给权限 ⇒ fail-closed 显示红。
#   注入版：认不出来也**猜成最高档 D** —— 边框亮紫说「能拖拽」，闸门一档都不放行。
BORDER_TIER_TRAP = '''

# ===== 以下为窗M 注入段（跑红自证专用，**勿入产品代码**）=====
_real_tiers_from_scopes = _tiers_from_scopes


def _tiers_from_scopes(raw):          # noqa: F811  故意覆盖真版
    """注入的缺陷：闸门认不出的 scope 也猜成最高档 D（旧版首字母陷阱）。"""
    tiers, junk, err = _real_tiers_from_scopes(raw)
    if not tiers:
        return ("D",), junk, err
    return tiers, junk, err
'''

# ── 注入段②：配色漂移一格（wF 用）──────────────────────────────────────
#   真版五个 RGB 是用户 2026-10-15 拍板的冻结值；注入版把 C 档蓝通道 +1。
BORDER_COLOR_DRIFT = '''

# ===== 以下为窗M 注入段（跑红自证专用，**勿入产品代码**）=====
_c_rgb, _c_desc = TIERS["C"]
TIERS["C"] = ((_c_rgb[0], _c_rgb[1], _c_rgb[2] + 1), _c_desc)
'''


def tmpdir(prefix="_注入红基线_"):
    """一个干净的临时目录（副本落这儿；判据自己会打印路径）。"""
    return tempfile.mkdtemp(prefix=prefix)


def shadow_file(src, tmp, name, snippet=""):
    """复制一份 src 到 tmp\name，可选在**副本尾部**追加注入段，返回副本路径。"""
    dst = os.path.join(tmp, name)
    shutil.copyfile(src, dst)
    if snippet:
        with io.open(dst, "a", encoding="utf-8") as f:
            f.write(snippet)
    return dst


def shadow_ctrl_border(tmp, snippet="", name="ctrl_border_注入缺陷.py"):
    """现役 ctrl_border.py 的**注入了缺陷的副本**（wA / wF 的 --target 指它）。"""
    return shadow_file(BORDER_SRC, tmp, name, snippet)


def shadow_repo(tmp):
    r"""造一份**影子仓**（src/ + contract/ + legacy_tools/act.py），

    供 AGENTCTL_ROOT 指过去 —— 取代从前那份躺在 work\ 里、不进仓的红基线仓。
    只 copytree 读现役文件，**不写现役仓任何一个字节**。
    """
    for rel in ("src", "contract"):
        s = os.path.join(ROOT, rel)
        d = os.path.join(tmp, rel)
        if os.path.isdir(d):
            shutil.rmtree(d)
        shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    dst_dir = os.path.join(tmp, "legacy_tools")
    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir)
    shutil.copyfile(os.path.join(ROOT, "legacy_tools", "act.py"),
                    os.path.join(dst_dir, "act.py"))
    return tmp


def inject_drag_tool(contract_path):
    """往**影子仓的契约副本**里塞一个可调用的拖拽工具 ⇒ wB 的 A1 必须红。

    注入的是「把 D 级拖拽偷偷放回契约」这个**最要命**的方向：
    一旦它进了 tools 数组，MCP tools/list 就会把它当 callable 端出去。
    """
    with io.open(contract_path, encoding="utf-8") as f:
        js = json.load(f)
    tools = js.get("tools")
    ghost = {"name": "ctl_drag",
             "description": "注入的拖拽工具（窗M 跑红自证用，真仓里没有）",
             "input_schema": {"type": "object", "properties": {}}}
    if isinstance(tools, list):
        tools.append(ghost)
    elif isinstance(tools, dict):
        tools["ctl_drag"] = ghost
    else:
        js["tools"] = [ghost]
    with io.open(contract_path, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)
    return contract_path
