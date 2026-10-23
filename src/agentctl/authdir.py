# -*- coding: utf-8 -*-
r"""authdir.py —— 通行证账本目录（AUTHDIR）的**唯一规则定义处**（S2 · 2026-10-23）

★ 为什么有这一个模块（治的病：三方各解析各的）
  改前**三方各解析各的**：
    · 老线闸门 `legacy_tools\gate.py`   ⇒ `env or HERE/_auth`        （**认** env）
    · 新线闸门 `src\agentctl\gate.py`    ⇒ `HOME/_auth`（HOME 走四层）  （**不认** env）
    · 签发方   `legacy_tools\auth_popup.py` ⇒ 写死 `BASE/_auth`         （**不认** env）
  ⇒ `AGENTCTL_AUTHDIR` 这个覆盖项**只覆盖三方中的 1/3** ⇒
    「签了证这边说没有」随时复现（工单 §8.3 实测口径）。
  ⇒ 本模块把规则收成**一处**定义，三方全部改走它。

★ 规则（顺序固定，**两侧逐条对应**）
    1) 环境变量 `AGENTCTL_AUTHDIR` 非空 ⇒ 用它（`expanduser` + 绝对化）；
    2) 否则默认 `%LOCALAPPDATA%\AgentCtl\_auth`
       —— 与 T10 统一的 `BRAKE_FLAG` 同一个 `_APP_DIR`（账本与急停同家）。
  读取侧**不创建**目录（只如实返回路径）；写入侧由各调用方惰性创建。

★ 本模块是**唯一规则正文**（不是「一份模板 + 几份拷贝」）
  · 新线：`src\agentctl\gate.py` 直接 `from agentctl.authdir import resolve`；
  · 老线：随包执行层**不能**反过来依赖 `src\` ⇒ `authdir.py` 由 `tools\gen_legacy.py`
    **生成**到 `legacy_tools\_src\`（真源侧）与 `legacy_tools\`（产物侧），
    老线 `gate.py` / `auth_popup.py` 只 `import authdir`、**不复制规则**。
  ⇒ 两处 `authdir.py` 是**生成物**（逐字节相同），台账 + CHK-A 照常盯它
    （P8 的机器盯防：被机器盯着的重复不算病）。
  ⇒ 本模块**不许 import gate**（gate 要 import 本模块 ⇒ 循环）⇒ 默认路径自己算。

★ 相对值怎么办
  `AGENTCTL_AUTHDIR` 给相对路径时按**当前工作目录**绝对化 —— 两侧进程的工作目录
  可能不同 ⇒ **跨进程一致性请给绝对路径**（工单与判据都用 `%TEMP%` 下的绝对路径）。
"""
from __future__ import annotations

import os

# 覆盖项（与老线/新线两边的环境变量名同一个字面量）
ENV_KEY = "AGENTCTL_AUTHDIR"
# 急停与账本同家用的应用目录名（%LOCALAPPDATA%\<APP_DIR_NAME>）
APP_DIR_NAME = "AgentCtl"
# 账本目录名（应用目录下的 _auth）
AUTH_DIR_NAME = "_auth"


def app_dir() -> str:
    """应用根目录：`%LOCALAPPDATA%\\AgentCtl`（`AGENTCTL_APP_DIR` 可覆盖）。

    与老线 `gate.py` / 新线 `gate.py` 里的 `_APP_DIR` **同一个口径**
    （急停文件 `BRAKE_FLAG` 也落在这儿 ⇒ 账本与急停同家）。
    """
    return os.environ.get("AGENTCTL_APP_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_DIR_NAME)


def default_dir() -> str:
    """默认账本目录：`<app_dir>/_auth`。"""
    return os.path.join(app_dir(), AUTH_DIR_NAME)


def resolve() -> str:
    """账本目录的**唯一**解析入口。返回绝对路径（目录不存在也照常返回，不创建）。"""
    v = (os.environ.get(ENV_KEY) or "").strip()
    if v:
        return os.path.abspath(os.path.expanduser(v))
    return os.path.abspath(default_dir())
