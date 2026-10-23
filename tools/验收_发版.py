# -*- coding: utf-8 -*-
r"""发版验收器 —— **一条命令**跑完全部验收项，给**一张总表**，**退出码可判**。

★ 为什么要它（主控派单 A · 2026-10-23）：
  M19′→M19P→M19P2→M20 甲案→M19P-S2 整条链已收口入库，但**还没有「能验收的产品」形态的交付物**。
  验收散落在十几条判据里，人得自己记住每条的跑法、env 约定、期望退出码
  ⇒ 漏一条就可能把红当绿发出去。
  本文件把这套口径**写死成一张表 ＋ 一条命令**：跑法照各判据自己写明的用法，
  实测退出码逐条记录，对不上就是红。

★ ★ 三条铁律（写死在本文件里，不是注释里的口号）：
  ① **不许把红解释成绿**：逐项记录**原始退出码**，实测 ≠ 期望 ⇒ 该项判【红】。
     （判定只看退出码，不看输出里有没有"看起来像绿"的字样。）
  ② **不许把「没跑 / 验不了」当绿**：拿不到结论的项判【验不了】并写明原因，
     **在退出码上不算绿** —— 单独一档（exit 2），绝不并进 0。
  ③ **不许自己另发明跑法**：每条判据的跑法与 env 约定**照仓内判据 docstring 自己写明的用法**
     （本文件每项的"跑法"字段里都标了出处），本文件只负责按那个约定调用、记退出码、对表。

★ ★★ 第四道自保（本文件自己踩过坑才加的，**别删**）：
  第一版我把 argv 写漏了脚本路径 ⇒ 实际执行的是 `python -X utf8 --inject`
  （找不到文件 ⇒ Python 退 2）与 `python -X utf8`（无脚本 ⇒ 读空 stdin **直接退 0**）
  ⇒ **一排"绿"全是空跑出来的假绿**，而表格看上去一切正常。
  ⇒ 现在加两道闸：
    - 闸 A：**脚本路径必须存在**（且在仓内）⇒ 不存在 ⇒ 判【验不了】+「配置错误」，不跑。
    - 闸 B：**输出为空 ⇒ 判【验不了】+「没拿到输出」**，绝不判绿
      （"跑了但什么也没产出" = 没结论，不是结论为绿）。
  这两道闸就是本文件**自己给自己上的牙**：配置写错时，宁可报"验不了"，不许报"绿"。

★ 本文件**只读不写**（除 `--out` 自己那份证据）：
  不生成 legacy、不打包、不碰 legacy_tools\ / src\ / tests\ / live 目录、不重启任何进程。
  它调用的所有判据**都是离线打桩**的（假时钟 / 假抓帧 / %TEMP% 沙箱 AUTHDIR）⇒ 不碰真 GUI。

用法：
    python -X utf8 tools\验收_发版.py              # 跑全部，给总表
    python -X utf8 tools\验收_发版.py --list       # 只列清单（不跑）
    python -X utf8 tools\验收_发版.py --only m19   # 只跑某项（id 或唯一前缀）
    python -X utf8 tools\验收_发版.py --json       # 机读（JSON 到 stdout）
    python -X utf8 tools\验收_发版.py --verbose    # 附每条跑动的完整输出
    python -X utf8 tools\验收_发版.py --out <f>    # 另存 JSON 证据
    python -X utf8 tools\验收_发版.py --enable-pack # ★解禁打包项（默认**锁死**，见下）

★ 退出码（刻意分三档 —— 「没结论」绝不等于「绿」）：
    0 = **全绿**（红 0、验不了 0）
    1 = **有红项**
    2 = 无红项，但有【验不了 / 待通知】项 ⇒ **不是绿**

★ ★ 2026-10-24（卡 N2-W6「验收器在负载下偶发红」· 窗口b）：本文件改了**四件纯属本层的事**，
  **一处判据都没碰、一次红都没改绿**：
  ① **表格渲染修 bug**（本文件自己的锅，害主控误判了 3 小时）：
     原 `print_table` 的数据行写成 `"  ".join(<一个字符串>)` ⇒ Python 按**字符**迭代 ⇒
     **每个字符之间被插两个空格** ⇒ 整行逐字拆散、列宽按原串算而实际宽度爆炸 ⇒
     **列全部错位、关键档位被挤出视野**。
     现场长这样：主控看到「实测列 0/1/0/0 却判红」—— 那 0/1/0/0 是**期望列**的数字，
     真正红掉的那一档（实测=1）被拆散+截断藏起来了，**现场无从知道哪一档红**。
     ⇒ 现在数据行按列拼接（不拆散），并对**非绿项逐档点名**（不受列宽截断）。
  ② **超时与判红分开记**：「超时」是**验不了**（不是红、也不是绿），且**单独一档**可查：
     `run_one` 现在分「跑完 / 超时 / 崩」三种状态（以前超时与起不来都只写 `崩=True`，
     实测列一律显示「崩」，现场分不清「跑太慢」和「脚本坏了」）。
  ③ **每项可单独配超时**：ITEMS 里可写 `"超时": 秒数`（覆盖 `--timeout`）。
  ④ **每项跑在独立临时目录**：把 `TMPDIR/TEMP/TMP` 指向该项私有目录 ⇒
     `%TEMP%` 噪声与**并发的另一批跑批**互不干扰（跑完即删，不留残渣）。
  ⑤ **红压过验不了**（W2 已定位、本轮补上）：原聚合逻辑
     `if verdict == RED and res["判定"] != UNKNOWN` ⇒ **先「验不了」后「红」时，红被掩盖成验不了**。
     ⇒ 现在**红优先**（红显性化，不是把红改绿）。

★ ★ 2026-10-23：**打包项的锁死期已结束**（本项现为**默认真跑**）
  为什么当初锁死：打包会**现场重新生成 legacy 源**（pack_dist → gen_legacy.main_for_pack）⇒
  在窗口 c 的 act.py 在途改动落地前打包 = 把半成品写进产物 ⇒ 那段时间本项锁死、标【待主控通知】。
  现在前置**全部满足并已实测**：act.py 在途改动已落地 → 产物已重生成（CHK-A 生成前后**双腿全绿**）
  → live ↔ 仓内产物 **37/37 逐字节一致**（漂移闸 0 红）→ 致命项为零 → **已实际出包成功**
  （dist/agentctl.zip，verify_dist 独立复算通过）⇒ 打包是**幂等**的（重新生成的 legacy 源逐字节相同）。
  ⇒ `--enable-pack` **保留仅为兼容旧调用**，不再是必需；一条命令 `python -X utf8 tools\验收_发版.py` 即可出全绿总表。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
DEFAULT_TIMEOUT = 900

# ── 判定档位 ────────────────────────────────────────────────────────────
GREEN, RED, UNKNOWN = "绿", "红", "验不了"

# ══════════════════════════════════════════════════════════════════════
# 验收项表
#   跑法条目 = (跑法标签, 脚本相对路径, 额外 argv, 额外 env, 期望退出码, 出处说明)
#   ★ 脚本路径**必填且必须存在**（闸 A）；env 只作用于**这一次子进程**（不污染后面的跑法）。
#   ★ 每条的跑法**出处**写在"出处说明"里 —— 照仓内判据自己写明的用法，不自创。
# ══════════════════════════════════════════════════════════════════════
ITEMS = [
    {
        "id": "m19",
        "名称": "M19 四跑法（act 侧「等生效」三态）",
        "验什么": "act 在预算耗尽时不再把「没测出来」写成「确认没变」"
                  "（None⇒没测出来/可重试，False⇒确认没变）",
        "跑法": [
            ("正跑", "tests/_P3_M19_语义修复_判据.py", [], {}, 0,
             "判据 docstring 第 24 行：正跑（修好后=0）"),
            ("注入自证 --inject", "tests/_P3_M19_语义修复_判据.py", ["--inject"], {}, 1,
             "判据 docstring 第 25 行：--inject 必须红（注入被逮到 ⇒ exit=1）"),
            ("产品真代码模式", "tests/_P3_M19_语义修复_判据.py", [], {"M19_USE_PRODUCT": "1"}, 0,
             "判据 docstring 第 26 行：M19_USE_PRODUCT=1 跑产品真代码"),
            ("未知参数（不许假绿）", "tests/_P3_M19_语义修复_判据.py", ["--no-such-flag"], {}, 0,
             "判据 §10.6 台账更正：判据不解析参数，未知参数退出码必须跟正跑一致"),
        ],
        # ★ 每项可配超时（卡 N2-W6）：本项有 4 档、且含真钟档 ⇒ 单独放宽
        "超时": 1800,
    },
    {
        "id": "m19p2",
        "名称": "M19P2 同族判据（act 侧第四/第五处）",
        "验什么": "act 读回失败时（read_doc / read_selection 返回 None）"
                  "也报「没测出来」而不是「确认没变」；三处 act.py 一致",
        "跑法": [
            ("正跑", "tests/_P3_M19P2_同族判据.py", [], {}, 0, "判据 docstring：改后跑 ⇒ 绿"),
            ("注入自证 --inject", "tests/_P3_M19P2_同族判据.py", ["--inject"], {}, 1,
             "判据 docstring 第 45 行：--inject 期望 exit=1"),
        ],
    },
    {
        "id": "m20core",
        "名称": "M20 core 甲案判据（判决层加长观测）",
        "验什么": "core 在判「确认没变」前用自己的尺子再看一会儿（≤300ms）："
                  "慢应用被救回，「确认没变」语义不丢，闪烁不误判",
        "跑法": [
            ("正跑", "tests/_P3_M20_core加长观测_判据.py", [], {}, 0,
             "判据 docstring 第 45 行：正跑"),
            ("注入自证 --inject", "tests/_P3_M20_core加长观测_判据.py", ["--inject"], {}, 1,
             "判据 docstring 第 46 行：--inject 期望 exit=1"),
            ("产品真代码模式", "tests/_P3_M20_core加长观测_判据.py", [], {"M20_USE_PRODUCT": "1"}, 0,
             "判据 docstring 第 47 行：M20_USE_PRODUCT=1"),
            ("未知参数（不许假绿）", "tests/_P3_M20_core加长观测_判据.py", ["--no-such-flag"], {}, 0,
             "同 M19：未知参数退出码必须跟正跑一致"),
        ],
    },
    {
        "id": "m20act",
        "名称": "M20 act 干跑语义判据（演练只算不做）",
        "验什么": "--dry-run 不打字、不移光标、不滚轮；UIA 超时兜底不崩",
        "跑法": [
            ("正跑", "tests/_P3_M20_act干跑语义_判据.py", [], {}, 0,
             "判据 docstring 第 55 行：正跑"),
            ("注入自证 --inject", "tests/_P3_M20_act干跑语义_判据.py", ["--inject"], {}, 1,
             "判据 docstring 第 56 行：--inject 期望 exit=1"),
        ],
    },
    {
        "id": "m19s",
        "名称": "M19S live 同步判据（live 漂移自动闸）",
        "验什么": "live（本机真在跑的那份 legacy）与仓内产物漂移时能被闸住，且不误伤",
        "跑法": [
            ("正跑", "tests/_P3_M19S_live同步_判据.py", [], {}, 0, "判据 docstring §七：正跑"),
        ],
    },
    {
        "id": "m19ps2",
        "名称": "M19P-S2 三条判据（AUTHDIR 一致 / 注入必红 / 空读不写回）",
        "验什么": "签发方与新旧闸门对「账本在哪」给同一个答案；老线守卫「读失败不写回」保住",
        "跑法": [
            ("J1 AUTHDIR 一致 · 正跑", "tests/_authdir一致_判据.py", [], {}, 0,
             "J1 docstring 第 33 行：正跑"),
            ("J1 AUTHDIR 一致 · 注入自证", "tests/_authdir一致_判据.py", ["--inject"], {}, 1,
             "J1 docstring 第 33 行：--inject 期望 exit=1"),
            ("J2 注入必红 · 正跑", "tests/_authdir注入必红_判据.py", [], {}, 0,
             "J2 docstring：正跑（期望 exit=0）"),
            ("J3 空读不写回 · 正跑", "tests/_空读不写回_判据.py", [], {}, 0,
             "J3 docstring 第 28 行：正跑"),
            ("J3 空读不写回 · 注入自证", "tests/_空读不写回_判据.py", ["--inject"], {}, 1,
             "J3 docstring 第 28 行：--inject 期望 exit=1"),
        ],
    },
    {
        "id": "zhukong",
        "名称": "主控复核脚本（M19′ / M19P / M20 甲案 八组）",
        "验什么": "独立复核：MD5 三处全等、不变量零改动、常量零改动、行尾 LF、"
                  "合法改动块、「生效」出口清单、判据四跑法、二次判差检查",
        "跑法": [
            # ★ 2026-10-24（卡 N3-W7）**收编**：改指**仓内副本** `复核\M19\复核_M19_20221023.py`。
            #   改前指向 `work\_M19_语义修复_20221023\主控复核\…` —— work\ 整体被 .gitignore 排除、
            #   **不进仓** ⇒ 干净克隆 / 任何 worktree 上该项必然判【验不了】（脚本不存在）。
            #   仓内副本是 work 副本的**严格超集**：账本 approved_code_lines.json、账本工具.py、
            #   baseline_core_pre_M20.py 三份**逐字节相同**；脚本本体多 7 处改动，
            #   全部是「三处路径可移植化」（ROOT/LIVE 从写死盘符改为 __file__ 推 + 运行时定位 live）。
            ("复核八组", "复核/M19/复核_M19_20221023.py", [], {}, 0,
             "脚本 docstring：用法 python -X utf8 <本文件>，期望 exit=0 八组全绿"),
        ],
        # ★ 每项可配超时（卡 N2-W6）：八组复核最重 ⇒ 单独放宽
        "超时": 3600,
    },
    {
        "id": "lint",
        "名称": "lint_gate（pyflakes 致命项门槛）",
        "验什么": "产品代码里没有「运行到那行才崩」级的错（undefined name / 重定义 / 语法错）",
        "跑法": [
            ("扫 src\\agentctl", "tools/lint_gate.py", ["src/agentctl"], {}, 0,
             "lint_gate docstring 用法：python -X utf8 lint_gate.py src\\agentctl"),
        ],
    },
    {
        "id": "livesync",
        "名称": "check_live_sync（live ↔ 仓内产物 逐字节）",
        "验什么": "本机真在跑的那份 legacy 与仓内产物完全一致（没人在 live 上手改）",
        "跑法": [
            ("正跑", "tools/check_live_sync.py", [], {}, 0,
             "check_live_sync 用法：无参正跑（--inject 是红自证，不属验收口径）"),
        ],
    },
    {
        "id": "verifydist",
        "名称": "verify_dist（独立复算发布件 · 第二个裁判）",
        "验什么": "发布 zip 里没有敏感文件、没有本机运行数据、关键件齐全（独立于打包脚本重写一遍判据）",
        "前置": "dist/agentctl.zip 存在（未出包 ⇒ 验不了）",
        "跑法": [
            ("复算 dist/agentctl.zip", "tools/verify_dist.py", ["dist/agentctl.zip"], {}, 0,
             "verify_dist 用法：python tools/verify_dist.py <zip>，0=通过 1=不通过"),
        ],
    },
    {
        "id": "presubmit",
        "名称": "presubmit_scan（git 索引隐私扫描）",
        "验什么": "**已经进暂存区**的内容里没有手机号 / 密钥字面量 / 密码赋值 / 个人绝对路径 / 凭据文件引用",
        "跑法": [
            ("扫 git 索引", "tools/presubmit_scan.py", [], {}, 0,
             "presubmit_scan 用法：默认扫 git 索引（--worktree 是另一口径）"),
        ],
        # 索引为空时扫描结果空洞 ⇒ 不许当绿（铁律②）
        "空洞即验不了": ("scanned 0 files in git index",
                         "暂存区为空（无待提交内容）⇒ 扫了 0 个文件 ⇒ 结论空洞，不算绿"),
    },
    {
        "id": "packdist",
        "名称": "pack_dist（出包）",
        "验什么": "打出可发版的 zip（★ 2026-10-23 起**默认真跑**：锁死期结束 —— "
                  "act.py 在途改动已落地、产物已重生成、CHK-A 双腿全绿、live 漂移闸 0 红 ⇒ 打包幂等）",
        "跑法": [
            ("出包", "tools/pack_dist.py", [], {}, 0,
             "pack_dist 用法：python -X utf8 tools\\pack_dist.py --out <zip>"),
        ],
    },
]


# ── 小工具：中文宽度对齐的表格 ────────────────────────────────────────
def _w(s):
    """字符串的显示宽度（东亚宽字符算 2）。"""
    n = 0
    for ch in str(s):
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def _pad(s, width):
    s = str(s)
    return s + " " * max(0, width - _w(s))


def _fit(s, width):
    """超宽则截断（尾部加 …）。"""
    s = str(s)
    if _w(s) <= width:
        return s
    out = ""
    for ch in s:
        if _w(out + ch) > width - 1:
            break
        out += ch
    return out + "…"


# ── 跑一条 ─────────────────────────────────────────────────────────────
def run_one(script, argv, extra_env, timeout):
    """跑一条判定。返回 dict：实测退出码/输出/用时/状态（跑完|超时|崩）。

    ★ env 只在**这一次子进程**里给（`env=` 传副本）——
      绝不改 os.environ：改了就泄漏到后面的跑法
      （M19_USE_PRODUCT=1 漏给下一条 ⇒ 参考实现模式被悄悄换成产品模式）。

    ★ 2026-10-24（卡 N2-W6）：**状态分三档**——
      「跑完」= 拿到了退出码（绿或红，按退出码判）；
      「超时」= **验不了**（跑太慢，没拿到结论 ⇒ 不许当红、也不许当绿）；
      「崩」   = 压根起不来（路径/环境问题 ⇒ 验不了）。
      以前超时与「起不来」都只写 `崩=True` ⇒ 实测列一律显示「崩」⇒ 现场分不清
      「这台机器太慢」和「脚本坏了」⇒ 现在分开记。
    """
    env = os.environ.copy()
    env.update({k: str(v) for k, v in (extra_env or {}).items()})
    cmd = [PY, "-X", "utf8", script] + list(argv)
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                           timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
        return {"实测": p.returncode, "输出": out + err, "用时": round(time.time() - t0, 1),
                "崩": False, "状态": "跑完", "命令": " ".join(cmd)}
    except subprocess.TimeoutExpired as e:
        def _d(x):
            if isinstance(x, bytes):
                return x.decode("utf-8", "replace")
            return x or ""
        return {"实测": None, "输出": _d(e.stdout) + _d(e.stderr),
                "用时": round(time.time() - t0, 1), "崩": True, "状态": "超时",
                "命令": " ".join(cmd), "原因": "超时（%ds）" % timeout}
    except Exception as e:                                   # pragma: no cover
        return {"实测": None, "输出": "%s: %s" % (type(e).__name__, e),
                "用时": round(time.time() - t0, 1), "崩": True, "状态": "崩",
                "命令": " ".join(cmd), "原因": "起不来：%s: %s" % (type(e).__name__, e)}


def run_item(item, timeout, verbose, enable_pack):
    """跑一个验收项。返回 (item结果, [每条跑动的明细])。

    ★ 2026-10-24（卡 N2-W6）本层三件事：
      ① **每项独立临时目录**：把 `TMPDIR/TEMP/TMP` 指向该项私有目录 ⇒ %TEMP% 噪声与
         并发的另一批跑批互不干扰（跑完即删，不留残渣）。
      ② **每项可配超时**：ITEMS 里写 `"超时": 秒数` 覆盖全局 `--timeout`。
      ③ **红压过验不了** + **红在哪档当场点名**（红显性化，不改任何判据）。
    """
    res = {"id": item["id"], "名称": item["名称"], "验什么": item["验什么"],
           "判定": GREEN, "期望": [], "实测": [], "说明": "", "跑动": [],
           "红档": [], "验不了档": []}

    # ① 默认不跑项（打包：等主控通知）
    if "默认" in item and not (item["id"] == "packdist" and enable_pack):
        res["判定"] = UNKNOWN
        res["说明"] = "%s —— %s" % item["默认"]
        return res, []

    # ② 有前置条件的项：拿不到前置 ⇒ 验不了（不算绿）
    if "前置" in item:
        prereq = os.path.join(ROOT, "dist", "agentctl.zip")
        if not os.path.exists(prereq):
            res["判定"] = UNKNOWN
            res["说明"] = "验不了 —— %s" % item["前置"]
            return res, []

    # ③ 每项独立临时目录（隔离 %TEMP% 噪声与并发跑批）
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="验收_%s_" % item["id"])
    except Exception:
        tmpdir = None

    # ④ 每项可配超时（没配就用全局 --timeout）
    try:
        item_timeout = int(item.get("超时") or timeout)
    except (TypeError, ValueError):
        item_timeout = timeout

    try:
        for label, script, argv, extra_env, expect, why in item["跑法"]:
            # 闸 A：脚本必须存在（配置写错 ⇒ 验不了，不许跑出一个"像绿"的结果）
            spath = os.path.join(ROOT, script.replace("/", os.sep))
            if not os.path.isfile(spath):
                res["跑动"].append({"标签": label, "期望": expect, "实测": None, "判定": UNKNOWN,
                                    "输出": "", "用时": 0, "崩": True, "状态": "配置错误",
                                    "命令": script,
                                    "出处": why, "环境": extra_env or {},
                                    "原因": "配置错误：脚本不存在（%s）" % script})
                res["期望"].append("%s=%s" % (label, expect))
                res["实测"].append("%s=验不了" % label)
                res["验不了档"].append(label)
                res["判定"] = UNKNOWN
                res["说明"] = "配置错误：%s 不存在" % script
                continue

            # 本次跑动的 env = 判据约定的 env + 该项私有临时目录
            run_env = dict(extra_env or {})
            if tmpdir:
                run_env.update({"TMPDIR": tmpdir, "TEMP": tmpdir, "TMP": tmpdir})

            r = run_one(script, argv, run_env, item_timeout)
            r["标签"] = label
            r["期望"] = expect
            r["出处"] = why
            r["环境"] = extra_env or {}          # 判据约定的 env（可回指）
            r["临时目录"] = tmpdir or ""          # 本项私有临时目录（隔离用）
            if r["崩"]:
                verdict = UNKNOWN
                r["原因"] = r.get("原因", "崩")
            elif not (r.get("输出") or "").strip():
                # 闸 B：跑完但没输出 ⇒ 没结论，不许判绿
                verdict = UNKNOWN
                r["原因"] = "没拿到输出（脚本可能没真正执行）"
            elif r["实测"] == expect:
                verdict = GREEN
            else:
                verdict = RED
            r["判定"] = verdict
            r["不符"] = (verdict == RED)
            res["跑动"].append(r)
            res["期望"].append("%s=%s" % (label, expect))
            # 实测列：超时写「超时」、崩写「崩」——**不许把超时混成红**
            res["实测"].append("%s=%s" % (label, r["实测"] if not r["崩"]
                                          else (r.get("状态") or "崩")))
            if verdict == RED:
                res["红档"].append(label)
                # ★ 红优先（红显性化）：红**压过**验不了，绝不被「验不了」掩盖
                res["判定"] = RED
            elif verdict == UNKNOWN:
                res["验不了档"].append(label)
                if res["判定"] == GREEN:
                    res["判定"] = UNKNOWN
                    res["说明"] = "验不了 —— %s" % r.get("原因", "该跑动没拿到结论")
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ③ 「空洞即验不了」：扫了 0 个文件 ⇒ 结论不成立（铁律②）
    hole = item.get("空洞即验不了")
    if hole and res["判定"] == GREEN:
        marker, why = hole
        for r in res["跑动"]:
            if marker in (r.get("输出") or ""):
                res["判定"] = UNKNOWN
                res["验不了档"].append(r.get("标签") or "")
                res["说明"] = "验不了 —— %s" % why
                break

    # ★ 说明：红项必须写清**哪一档**不符（现场一眼可定位，不再靠猜）
    if res["判定"] == RED:
        res["说明"] = "红 —— %s" % ("；".join(
            "第%d档「%s」期望 %s 实测 %s" % (
                i + 1, d.get("标签"), d.get("期望"),
                d.get("实测") if d.get("实测") is not None else d.get("状态", "—"))
            for i, d in enumerate(res["跑动"]) if d.get("判定") == RED))
    elif res["判定"] == GREEN and not res["说明"]:
        res["说明"] = "全绿"
    return res, res["跑动"]


# ── 输出 ───────────────────────────────────────────────────────────────
def print_table(results, show_detail):
    cols = ["项", "期望", "实测", "判定"]
    w = [24, 46, 46, 8]
    print("=" * 132)
    print("发版验收总表 · %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("仓根：%s" % ROOT)
    print("=" * 132)
    print("  " + "  ".join(_pad(c, w[i]) for i, c in enumerate(cols)))
    print("  " + "  ".join("-" * w[i] for i in range(len(cols))))
    for r in results:
        # ★ 2026-10-24（卡 N2-W6）**修 bug**：原来这里写的是
        #   "  ".join(<一个拼好的字符串>) ⇒ Python 按**字符**迭代 ⇒ 每个字符间插两个空格
        #   ⇒ 整行逐字拆散、列宽按原串算而实际宽度爆炸 ⇒ 列错位、关键档位被挤出视野
        #   （主控据此误判成「实测列 0/1/0/0 却判红」——那 0/1/0/0 是**期望列**）。
        # ⇒ 现在按列拼接（每个元素一个参数），列宽才真正生效。
        print("  " + "  ".join((
            _pad(_fit(r["项"], w[0]), w[0]),
            _pad(_fit("; ".join(r["期望"]) or "—", w[1]), w[1]),
            _pad(_fit("; ".join(r["实测"]) or "—", w[2]), w[2]),
            _pad(r["判定"], w[3]),
        )))
        if r.get("说明"):
            print("      └─ %s" % r["说明"])
        # ★ 非绿项**逐档点名**（不受列宽截断 ⇒ 现场一眼看出「哪一档不符」）
        if r["判定"] == GREEN and not show_detail:
            continue
        for i, d in enumerate(r.get("跑动", [])):
            mark = {"绿": "✓", "红": "✗ 不符", "验不了": "? 验不了"}.get(d.get("判定"), "")
            got = d.get("实测") if d.get("实测") is not None else (d.get("状态") or "—")
            extra = ("（%s）" % d["原因"]) if d.get("原因") else ""
            print("      · 第%d档 %s：期望 %s / 实测 %s  %s%s"
                  % (i + 1, d.get("标签"), d.get("期望"), got, mark, extra))
    print("=" * 132)


def main():
    ap = argparse.ArgumentParser(
        description="发版验收器：一条命令跑完全部验收项，给一张总表，退出码可判")
    ap.add_argument("--list", action="store_true", help="只列清单（不跑）")
    ap.add_argument("--only", help="只跑某项（id 或唯一前缀）")
    ap.add_argument("--json", action="store_true", help="机读：JSON 到 stdout")
    ap.add_argument("--verbose", action="store_true", help="附每条跑动的完整输出")
    ap.add_argument("--out", help="另存 JSON 证据到该文件")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="每条跑动的超时秒数")
    ap.add_argument("--enable-pack", action="store_true",
                    help="★解禁打包项（默认锁死；主控说可以出包了才加）")
    a = ap.parse_args()

    items = ITEMS
    if a.only:
        hits = [x for x in items if x["id"] == a.only]
        if not hits:
            hits = [x for x in items if x["id"].startswith(a.only)]
        if len(hits) != 1:
            ids = ", ".join(x["id"] for x in items)
            print("--only 匹配不到唯一项：%r（可用 id：%s）" % (a.only, ids),
                  file=sys.stderr)
            return 2
        items = hits

    # ── --list：只列清单 ──
    if a.list:
        if a.json:
            print(json.dumps({"items": [{"id": x["id"], "名称": x["名称"],
                                          "验什么": x["验什么"],
                                          "跑法": [{"标签": l, "脚本": s, "期望": e, "出处": w}
                                                   for l, s, _a, _e, e, w in x["跑法"]]}
                                         for x in items]}, ensure_ascii=False, indent=1))
        else:
            print("=" * 132)
            print("发版验收清单 · 只列不跑 · %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
            print("=" * 132)
            for x in items:
                print()
                print("【%s】%s" % (x["id"], x["名称"]))
                print("    验的是：%s" % x["验什么"])
                if "默认" in x:
                    print("    ⚠ %s —— %s" % x["默认"])
                if "前置" in x:
                    print("    ⚠ 前置：%s" % x["前置"])
                for l, s, _a, _e, e, w in x["跑法"]:
                    print("    · %-22s 期望 exit=%s" % (l, e))
                    print("        跑法：python -X utf8 %s%s    （出处：%s）"
                          % (s, (" " + " ".join(_a)) if _a else "", w))
        return 0

    # ── 跑 ──
    results = []
    for x in items:
        r, detail = run_item(x, a.timeout, a.verbose, a.enable_pack)
        r["项"] = x["id"]
        results.append(r)
        if a.verbose:
            for d in detail:
                print("--- %s / %s（期望 %s，实测 %s）---" % (x["id"], d["标签"], d["期望"], d["实测"]))
                print(d.get("输出") or "")
                print("--- end ---", file=sys.stderr)

    n_red = sum(1 for r in results if r["判定"] == RED)
    n_unk = sum(1 for r in results if r["判定"] == UNKNOWN)
    n_ok = sum(1 for r in results if r["判定"] == GREEN)
    if n_red:
        concl = "有 %d 项红" % n_red
    elif n_unk:
        concl = "无红项，但有 %d 项「验不了/待通知」⇒ **不是绿**" % n_unk
    else:
        concl = "**全绿**"

    payload = {"结论": concl, "绿": n_ok, "红": n_red, "验不了": n_unk, "items": results,
               "退出码": (1 if n_red else (2 if n_unk else 0)),
               "时间": time.strftime("%Y-%m-%d %H:%M:%S"),
               "全局超时秒": a.timeout}
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print_table(results, a.verbose)
        print()
        print("结论：%s（绿 %d / 红 %d / 验不了 %d）" % (concl, n_ok, n_red, n_unk))
        print("退出码：%d（0=全绿；1=有红；2=无红但有验不了/待通知 ⇒ 不是绿）"
              % (1 if n_red else (2 if n_unk else 0)))

    if a.out:
        try:
            d = os.path.dirname(os.path.abspath(a.out))
            if d:
                os.makedirs(d, exist_ok=True)
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            print("证据已存：%s" % a.out)
        except Exception as e:
            print("证据落盘失败：%s" % e, file=sys.stderr)

    return 1 if n_red else (2 if n_unk else 0)


if __name__ == "__main__":
    sys.exit(main())
