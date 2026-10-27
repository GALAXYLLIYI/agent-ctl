# -*- coding: utf-8 -*-
"""主控复核 · M19′ 等生效语义修复 + M19P 任务② + M20 core 甲案（2026-10-23）

★★★ 仓内副本（`复核\\M19\\复核_M19_20221023.py`，2026-10-23 复制进仓）★★★：
  活件版在 `work\\_M19_语义修复_20221023\\主控复核\\`，而 `work\\` 整个被 .gitignore 排除
  ⇒ 干净克隆上原本**没有**这套护栏 ⇒「形状白名单 + 数量账本」双保险只在本机生效。
  本副本与活件版的**唯一**差异 =「三处路径可移植化」（ROOT / BAK / LIVE，见下方常量区）：
  ① 仓根从 __file__ 推（+ AGENTCTL_ROOT 覆盖口）—— 与 tests\\check_t1_w1.py 同一套口径；
     ⛔ 不写死盘符：写死了，干净克隆上验的是开发机的工作树，不是入库物（闸门会撒谎）。
  ② 基线快照指向**同包**（不再指向 work\\）⇒ 干净克隆上也带得走。
  ③ live 改为运行时定位（live 是本机状态、不在仓里）⇒ 定位不到 =「验不了」，
     明确打印并跳过该格，**不判红、不静默跳过**（口径同 tools\\check_live_sync.py 的退出码 2）。
  ★ 目录布局与活件**同形**（M19\\ ≡ 主控复核\\，backup_before\\ 仍是它的兄弟目录）
    ⇒ 账本里那条相对路径 `../backup_before/…` 原样指得到
    ⇒ **账本 / 账本工具 / 两份基线快照与活件逐字节相同**（账本一个字节都没改，
      整本指纹仍是 793d5886…；段校验值与整本指纹都无需重算）。

★ 独立复核：不采信执行者自检（铁律「干完必须独立复查一次」）。
★ 八组检查：①三处 MD5 全等 ②不变量零改动 ③常量零改动 ④行尾 LF
  ⑤M19′/M19P 合法改动块 ⑥「生效」出口清单 ⑦判据四种跑法退出码 ⑧二次判差检查（性能不变式）
用法：python -X utf8 <本文件>

★ 代改说明（本文件是主控产物，由 MiniMax Code / 窗口 B 代主控改，2026-10-23）：
  改前 exit=1，唯一红项是「无越界改动块」——**不是产品缺陷**，是白名单没跟上：
  ① ALLOWED_NEW_CODE 是 M19′/零成本化期写的 ⇒ 没收 M19P 任务②的合法新行；
  ② 旧行条件只认 生效/_budget_verdict/last_d ⇒ W1/W2/W3 的旧行只含 changed ⇒ 仍判越界；
  ③ 加 ⑤-b「改动真代码行清单」（数量约束）——白名单是**形状**约束，
     `return 0` / `else:` 这类**裸行**在 act.py 里本来就有几十处
     ⇒ 不加数量约束就会放过真越界；
  ④ ⑤ 标题与比对口径（tokenize 剥掉 COMMENT/STRING）写清。

★ 第二轮（11:45 窗口 B）：把 ⑤-b 的清单**外置成账本**（approved_code_lines.json），
  治「基线随产品漂移」：每来一刀只**往账本里追加一段**，**不用再改脚本逻辑**。
  账本自带段校验值 sha256 + 整本指纹 ledger_sha256 ⇒ 手改账本当场被抓住；
  报告每次都打印账本摘要 + 整本指纹 ⇒ 改账本必然在提交里留痕、可比对。
  白名单（形状）+ 账本（数量）**双保险**不变：两条都过才放行。

★ 第三轮（12:11 窗口 B）：M20 core 甲案（提交 a19fbad）落地后同步基线与账本：
  ① 不变量表里 core.py 的指纹更新为**甲案后**指纹（5BB4A068… → 863B1509…），
     这是下一刀的**合法变更**，不是红线违规；另**新纳入** src/agentctl/framecache.py
     （甲案只**引用**其既有常量 WAIT_BUDGET_MS=300，没改它 ⇒ 零改动指纹核）；
     其余四个受保护文件（contract/tools.json、src/agentctl/gate.py、
     src/agentctl/action_guard_policy.json、legacy_tools/gate.py）指纹**不动**；
  ② 账本加「M20_core加长观测」段：**101 行文本**里按 tokenize 口径数出
     **65 行真代码行**（注释/文档字符串不算），并给账本加「files」段绑定
     （act.py / core.py 各自的基线 + 覆盖它的交付段）⇒ 逐文件比对，
     哪个文件的改动越界一眼能分清。
  ⇒ 只改本复核脚本 + 同目录的账本/账本工具/基线快照；
    **未碰** act.py（真源/产物/live）、**未碰** core.py、**未碰**任何判据（tests/ 一字未动）。

★ 第四轮（2026-10-23 14:45 窗口 B）：M19P-S2（AUTHDIR 收编，678a19f）落地后同步基线与账本：
  ① ②组不变量基线更新：src/agentctl/gate.py → C79B1350…、legacy_tools/gate.py → 04F4A1A2…
     （**回文件核过的值**；这是下一刀的合法变更，不是红线违规）；
     另**新纳入**三份 authdir.py（141022EA…，三处逐字节全等）——
     它是 S2 收编后的规则唯一源，不纳入就等于留三份没被盯着的重复（P8 的反面）。
  ② 账本加 4 段（按文件绑定）：M19P-S2_gate_src(17) / M19P-S2_gate_legacy(5) /
     M19P-S2_auth_popup(1) / M19P-S2_authdir(15，本单**新建** ⇒ 基线取空文件，
     段 = 该文件全部真代码行)。**行内容照窗口 e 的《真代码行清单》录入**
     （并与「pre-S2 基线 vs 当前」的实测 diff 逐行对账，**行行相等**）。
     ★ **act 段一个字没动**（窗口 c 在途改 act.py，那一段等它交卷后由主控统一追加）。
  ③ 基线解析允许「本地快照缺失 ⇒ 回落 git」不再误报红（md5 仍逐份校验）。
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from collections import Counter

# ★★★ 仓内副本（`复核\M19\`）的**三处路径可移植化** —— 与活件版的唯一差异 ★★★
# 活件版（work\_M19_语义修复_20221023\主控复核\）把仓根与 live 写死成了盘符绝对路径。
# 照抄进仓，两个后果都要命：
#   ① 干净克隆上验的是**开发机的工作树**，不是入库物 ⇒ 绿灯与入库物无关（闸门会撒谎）；
#   ② 仓内多出盘符绝对路径 ⇒ 撞 tests\_写死路径判据.py 的命中计数自证。
# ⇒ 下面三处按仓内既有约定改（tests\check_t1_w1.py / tests\run_all.py 同一套口径，
#   由 tests\_M_可移植性判据.py A 段守着「仓根不许写死」）：
#   ROOT：从 __file__ 推（复核\M19\ 的上一级 = 仓根）+ AGENTCTL_ROOT 覆盖口
#   BAK ：指向**同包**的基线快照（不再指向 work\ ⇒ 干净克隆上也带得走）
#   LIVE：运行时定位（live 是本机状态、不在仓里）⇒ 见 resolve_live()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("AGENTCTL_ROOT") or os.path.dirname(os.path.dirname(HERE))
BAK = os.path.join(HERE, "backup_before", "act_1_src__legacy_tools__act.py")
SRC = os.path.join(ROOT, "legacy_tools", "_src", "act.py")
PROD = os.path.join(ROOT, "legacy_tools", "act.py")

BASE_ACT = "91AE6D554048912F8CA21DC113AD077F"
INVARIANTS = {
    # ★ 2026-10-24 措辞清理（用户→用户）：contract 与两份闸门属**合法文案变更** ⇒ 指纹更新
    "contract/tools.json": "1EC27C68DE56BE7E1492B86A247FFCF1",
    # ★ M20 core 甲案（a19fbad）后的 core.py：判决层「加长观测」是**合法变更** ⇒ 指纹更新
    # ★ 2026-10-24 N5-W11：加长观测预算 300→900（可配 AGENTCTL_ACT_EXT_OBSERVE_MS）是**合法变更**
    #   ⇒ 863B1509… → F8669E4C…（只改这一个常量与注释；三态语义/HYSTERESIS/尺寸守卫一字未动）
    # ★ W15（2026-10-24）：措辞口径统一后 core.py 变更属合法变更 ⇒ F8669E4C… → 00F5422A…
    "src/agentctl/core.py": "81F3E6332700E01010E2546462B61D37",
    # ★ W12（2026-10-24）：动作面刹车策略拆分 fail/same_target 两类计数 ⇒ 策略 json 合法变更
    #   73F5F0DD… → BB104E5D…
    "src/agentctl/action_guard_policy.json": "34E470F8D97474C132926280CF4D173E",
    # ★ M19P-S2（AUTHDIR 收编，678a19f）后的两份闸门：解析口径收编是**合法变更** ⇒ 指纹更新
    "src/agentctl/gate.py": "F0F4750392CCE945A859379D936E87EC",
    "legacy_tools/gate.py": "ED7F3595F0BBA5D48EB38BA5DCE75754",
    # ★ M20 引入的保护：甲案只**引用** framecache 的既有常量（WAIT_BUDGET_MS=300），没改它
    "src/agentctl/framecache.py": "AB7788D98580C179CCE77F4AEC7750E3",
    # ★ 新纳入保护：M19P-S2 新建的 AUTHDIR 规则正文（三处**逐字节全等** = 141022EA…）
    #   纳入的理由：① 它是 S2 收编后的**规则唯一源**，不改它就等于绕过收编；
    #     ② 两份 legacy 拷贝是**生成物**（不纳入就成了没被盯着的重复＝P8 的反面）；
    #     ③ 三处同指纹 ⇒ 任何一处被单独改都会当场变红。
    "src/agentctl/authdir.py": "141022EA097888F247B724D94F481CA9",
    "legacy_tools/_src/authdir.py": "141022EA097888F247B724D94F481CA9",
    "legacy_tools/authdir.py": "141022EA097888F247B724D94F481CA9",
}
CONSTS = ("POLL_BUDGET_MS", "POLL_BUDGET_MS_FILL", "POLL_INTERVAL_MS",
          "POLL_HYSTERESIS", "POLL_SCALE")

fails = []

# ★ M19P（2026-10-23 主控修订）——本检查器此前有两处**误报**，根因都是
#   「用整块文本 grep」而不是「剥掉注释/字符串后看真代码」：
#   ⑤ 越界：diff 把 docstring 切成碎块 ⇒ in_doc 状态丢失 ⇒ 注释被当代码；
#      且 M19P 的合法新增行（last_d = None / last_d = d）不在 M19′ 期的白名单里。
#   ⑧ 二次判差：docstring 里写了 `_img_diff(base_img, last)`（**举例说明**，不是调用）
#      ⇒ 整块文本 grep 命中 ⇒ 谎报「二次判差」⇒ 实际真代码零次调用。
# ⇒ 修法：改用 tokenize 剥掉 COMMENT/STRING，只对**真代码行**判定。
#
# ★★ 比对口径（照此判，别改回整块文本 grep）：
#   ① 白名单比对的是**真代码行**：tokenize 剥掉 COMMENT / STRING（含 docstring）
#      之后剩下的那一行文本，**strip 掉缩进**再 match（见 code_line_map）。
#   ② 判「调用点」同样只看真代码：_rc_from_changed 的 docstring 里写了
#      `return 0 if changed is True else 8`、W1/W2/W3 的注释里写了 rc=5/rc=8
#      —— 那是**举例说明**，不是调用 ⇒ 不许当代码判。
#   ③ 纯注释行 / 文档字符串行**不算**真代码行 ⇒ diff 块里只有注释时直接放行（见 ⑤）。
def _exact(line):
    """精确形状：整行逐字匹配（复杂行交给 re.escape，避免手写正则漏转义）。"""
    return re.compile("^%s$" % re.escape(line))


ALLOWED_NEW_CODE = (
    # —— M19′（等生效语义修复；原样保留）——
    re.compile(r'^def _budget_verdict\(last_d\):$'),
    re.compile(r'^if not last_d:$'),
    re.compile(r'^if last_d\.get\("changed"\) is True:$'),
    re.compile(r'^return False$'),
    re.compile(r'^return None$'),
    re.compile(r'^last_d = (None|d)$'),
    re.compile(r'^return \{"生效": _budget_verdict\(last_d\), "轮次": rounds,$'),
    # —— M19P 任务②（2026-10-23 本次追加）：M19′ 期写的白名单没收这一批合法新行
    #    ⇒ 把任务②的合法改动误判成「越界」。合法真代码行只有两处来源：
    #      · 新助手 _rc_from_changed 的定义 + 函数体（docstring 已被 tokenize 剥掉）
    #      · W1/W2/W3 三个调用点的 return 行
    re.compile(r'^def _rc_from_changed\(changed, dry=False\):$'),
    re.compile(r'^if dry:$'),
    re.compile(r'^if changed is True:$'),
    re.compile(r'^if changed is None:$'),
    re.compile(r'^return 0$'),
    re.compile(r'^return 5$'),
    re.compile(r'^return 8$'),
    re.compile(r'^return _rc_from_changed\(changed\)$'),
    re.compile(r'^return _rc_from_changed\(changed, dry=dry\)$'),
    # —— M19P2（2026-10-23 同族两处：X1 do_type 的 UIA 读回 / X2 do_drag 的选区读回）——
    #    窗口c 11:25:20 交卷（判据全绿、主控已核）；本段随交卷补进白名单。
    _exact('if after is None:'),
    _exact('changed, ok, nl_only = None, False, False'),
    _exact('mark = "❓ 读不回来＝**没测出来**（本次不报成功，请重新观测后重试）"'),
    re.compile(r'^else:$'),
    _exact('ok = (after == want) or (_norm_nl(after) == _norm_nl(want))'),
    _exact('nl_only = (after != want)'),
    _exact('changed = True if ok else False'),
    _exact('mark = ("✅ 读回逐字符一致（仅换行表示不同）" if nl_only'),
    _exact('else "✅ 读回逐字符一致") if ok else "❌ 读回不一致"'),
    _exact('f"（{len(before)} → {len(after) if after is not None else \'None\'} 字）")'),
    _exact('changed = None if sel is None else (True if len(sel) > 0 else False)'),
    _exact('tail = ""'),
    re.compile(r'^elif sel is None:$'),
    _exact('tail = "　选区**读不回来**＝**没测出来**（本次不报成功，请重新观测后重试）"'),
    _exact('tail = "　选中了 %d 字：「%s」" % (len(sel), sel[:30])'),
    _exact('log(f"{\'（演练）\' if dry else \'\'}✋ 拖拽 [{frm} → {to}] 窗口「{info[\'title\']}」" + tail)'),
    # —— M20 act 安全批（2026-10-23 ae62a18；X1 干跑打字闸 / X2 滚动干跑闸 / X3 root=None 兜底）——
    #    ⚠ 第 3 条**故意留活口**（True|False）：`r, False` 那种「形状相同但不在清单里」的
    #    行只能靠**数量账本**抓住（P15 探针就是冲它来的）⇒ 形状白名单放过它是对的。
    re.compile(r'^if root is None:$'),
    re.compile(r'^r = info\["rect"\]$'),
    re.compile(r'^x, y, rr, ok_rect = \(r\[0\] \+ r\[2\]\) // 2, \(r\[1\] \+ r\[3\]\) // 2, r, (True|False)$'),
    re.compile(r'^x, y, rr, ok_rect = _center\(root\)$'),
    re.compile(r'^if not dry:$'),
    re.compile(r'^user32\.SetCursorPos\(x, y\)$'),
    re.compile(r'^n_scan, fb_chars = send_text_scan\(text\)$'),
    re.compile(r'^if scan_code:$'),
    re.compile(r'^send_text\(text\)$'),
    re.compile(r'^time\.sleep\(0\.12\)$'),
    _exact('f"{\'（演练：光标未移动）\' if dry else \'\'}] 窗口「{info[\'title\']}」")'),
    _exact('log(f"{\'（演练）\' if dry else \'\'}🖱 滚动 [SendInput 滚轮 {amount} 格，光标置于 ({x},{y})"'),
    # —— M19P3（2026-10-23 本次追加）：谎报成功三条（F3 日志诚实度 / F5 read 读失败 /
    #    F7 do_type 前置读失败）—— 逐字精确匹配，23 行与外置账本 M19P3 段一一对应。
    #    ★ 数量由 ⑤-b 兜底：账本 M19P3 段 = 23 行 + removed 新增 1 行，
    #      白名单放行了「形状对但不在清单里」的同形行时，数量账本仍会报红（P15 探针同款）。
    _exact('ev = ("　⚠⚠ 判不出来（指纹取不到）＝**没测出来**" if changed is None'),
    _exact('else ("　✔ 页面已变化" if changed else "　⚠ 页面无变化"))'),
    _exact('def _read_fail_rc(how):'),
    _exact('h = (how or "")'),
    _exact('if h.startswith("未获授权"):'),
    _exact('return 9, "unauthorized"'),
    _exact('if h.startswith("目标窗口不响应"):'),
    _exact('return 5, "hung"'),
    _exact('if h.startswith("找不到文本编辑区"):'),
    _exact('return 4, "no_ctrl"'),
    _exact('if h.startswith("读不到文本"):'),
    _exact('return 5, "unreadable"'),
    _exact('return 5, "unknown"'),
    _exact('if before is None:'),
    _exact('_rc, _kind = _read_fail_rc(how)'),
    _exact('if _kind != "no_ctrl":'),
    _exact('return _tell_next(_rc, f"打不了字：{how}", hwnd,'),
    _exact('extra=("本次是**读失败**（不是「目标没有文本区」）⇒ "'),
    _exact('"再重跑；真要盲打须显式加 --force-send。"))'),
    _exact('if txt is None:'),
    _exact('_rd_rc, _rd_kind = _read_fail_rc(how)'),
    _exact('log(f"⛔ 读取失败（{_rd_kind}）：{how}")'),
    _exact('return _rd_rc'),
    # —— M19P4（2026-10-23 本次追加）：两条 pyflakes 致命项 ——
    #    · 删掉顶层 import 块里**重复**的 `import sys`（模块顶部 :14 已 import 过）
    #    · 补 `import tempfile`（`_pf()` 用 `tempfile.gettempdir()` 却没 import ⇒ 开 profile 必崩）
    #    新增真代码行只有 `import tempfile` 这一行；被删的旧行 `import sys` 已登记进
    #    外置账本的 removed["act.py"]（24 → 25 行）⇒ 数量账本照旧兜底。
    _exact('import tempfile'),
    # —— M20 act 安全批 · X4/X5（2026-10-24 本次追加 · 窗口 e · N1-W4）——
    #    X4：do_click 的**就绪闸真被读** —— `_rdy = _wait_ready(...)` 原来算了却从不看
    #        （pyflakes 早就报过 'local variable _rdy is assigned to but never used'）
    #        ⇒「界面还在动但矩形恰好没变」照样点下去。现在：就绪=True 才动手；
    #        False（还在动）/ None（没测出来）⇒ 一律拒（rc=5，unknown 永不当成功）。
    #    X5：targets 的**失败不再用空列表兼任** —— 新增 TargetsUnavailable + strict 参数
    #        （未获授权⇒9 / UIA 失败⇒5）+ _targets_rc（空列表⇒4，不再是 0）+ _targets_meta
    #        （枚举失败/已截断标记）+ main 的 targets 出口。
    #    —— 逐字精确匹配，42 行（39 种）与外置账本「M20_act安全批（X4/X5）」段一一对应；
    #    旧侧 6 行（5 种）已登记进账本 removed["act.py"]（25 → 31 行）。
    #    ★ 数量由 ⑤-b 兜底：账本该段 = 42 行，多一行少一行都报红 ⇒ 白名单没有放宽。
    _exact('"      下一步：等界面停下（或先 scroll 到位）再重试。")'),
    _exact('"可操作区(④)": bands, **_targets_meta(ts)},'),
    _exact('"可操作区(④)": {}, **_targets_meta([], err=e.why)},'),
    _exact('"失败原因": (err or ""),'),
    _exact('"已截断": bool(len(ts) >= limit)}'),
    _exact('"枚举失败": bool(err),'),
    _exact('5, f"**反竞态拒绝**：控件「{c.Name}」{_rdy_why}", hwnd,'),
    _exact('_rdy_ok = (_rdy or {}).get("就绪")'),
    _exact('_rdy_why = ("界面在动（预算内没稳）" if _rdy_ok is False'),
    _exact('auto, root = _uia(hwnd)'),
    _exact('class TargetsUnavailable(Exception):'),
    _exact('def __init__(self, why, rc, kind):'),
    _exact('def _targets_meta(ts, limit=40, err=None):'),
    _exact('def _targets_rc(ts, limit=40):'),
    _exact('def targets(hwnd, limit=40, with_geo=False, strict=False):'),
    _exact('else "就绪没测出来（抓不到画面）")'),
    _exact('ensure_ascii=False, indent=1))'),
    _exact('except Exception as e:'),
    _exact('except TargetsUnavailable as e:'),
    _exact('extra="就绪轮询说界面还没稳 ⇒ 宁可不点也不点错。\\n"'),
    _exact('if _rdy_ok is not True:'),
    _exact('if not ts:'),
    _exact('if strict:'),
    _exact('print(json.dumps({"窗口": info, "白名单": "OK", "可交互控件": [],'),
    _exact('raise'),
    _exact('raise TargetsUnavailable("UIA 枚举失败：%s" % (e,), 5, "uia_failed")'),
    _exact('raise TargetsUnavailable("未获授权（没有通行证/感知授权）", 9, "unauthorized")'),
    _exact('return 0'),
    _exact('return 4'),
    _exact('return _targets_rc(ts)'),
    _exact('return _tell_next('),
    _exact('return _tell_next(e.rc, f"列不出可点控件：{e.why}", a.hwnd)'),
    _exact('return {"可交互控件数": len(ts),'),
    _exact('self.kind = kind'),
    _exact('self.rc = rc'),
    _exact('self.why = why'),
    _exact('super().__init__(why)'),
    _exact('try:'),
    _exact('ts, geo = targets(a.hwnd, with_geo=True, strict=True)'),
)

# ★ 旧行侧（被删/被替换的真代码行）：**改由外置账本的 removed[文件] 逐行列举**（不再用关键词近似）。
#   为什么换掉旧的关键词近似（OLD_LINE_KEYWORDS）：
#     act 安全批的旧行是 `user32.SetCursorPos(x, y)` / `time.sleep(0.12)` / `if scan_code:`
#     / `send_text(text)` / `else:` 一类 —— **一个业务关键词都不含**
#     ⇒ 关键词近似要么放得太松（放行真越界），要么得堆一堆无意义关键词。
#   ⇒ 改成精确清单：账本里「登记了哪些旧行」＝「只允许删改哪些旧行」，
#     ⑤-b 还会对旧侧**逐行计数**（多一行少一行都报红）。

# ★★ 「已批准改动真代码行」**外置账本**（2026-10-23 窗口B 改，治「基线随产品漂移」）：
#     账本文件 = approved_code_lines.json（与本脚本同目录），按**交付段**分组，
#     并用 files 段把「交付段」绑定到「受控文件」（act.py / core.py …）。
#     · 每来一刀只往账本里**追加一段**（M19′/M19P/M19P2/M20/以后 M21…），
#       **不用再改脚本逻辑** ⇒ 不会再出现「脚本基线过期 ⇒ 误报越界」。
#     · 段内带 sha256、整本带 ledger_sha256 ⇒ 手改账本当场被抓住（见 load_ledger）。
#     · 账本在仓内 ⇒ 改账本必然在提交里留痕；报告每次都会打印账本摘要 + 整本指纹。
#   ★ 刻意的行为：新交付若没往账本加段 ⇒ 本脚本**一定报红** ⇒ 逼主控显式批准新范围。
#     （这是「白名单放行但清单没记」的护栏，不是误报。）
LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "approved_code_lines.json")


def _canon_lines(lines):
    """段内真代码行 → 规范化文本（按行文本排序，拼「行文本\\t次数」，用 \\n 连接）。"""
    return "\n".join("%s\t%d" % (k, lines[k]) for k in sorted(lines))


def load_ledger(path=LEDGER_PATH):
    """读**外置账本** ⇒ (files, segs_by_name, removed_map, 错误列表, 摘要行)。

    ★ 账本被手改的检测（三道）：
      ① 每段带 sha256 ⇒ 改了段内行却没重算 ⇒ 当场报红；
      ② 整本带 ledger_sha256 ⇒ 改了段名/加删段/换基线/改旧侧清单却没重算 ⇒ 当场报红；
      ③ 数量账本本身（⑤-b）⇒ 就算校验值被一并改对，「实际改动行 vs 账本行」也对不上 ⇒ 照样报红。
    ⇒ 改账本既绕不过校验值、也绕不过数量比对；且账本在仓内 ⇒ 改动必留痕。
    """
    errs = []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {}, {}, {}, ["账本读不到：%s（%s）" % (os.path.basename(path), e)], ""
    files = data.get("files") or {}
    segs = data.get("segments") or []
    removed = data.get("removed") or {}
    if not files:
        errs.append("账本里没有 files（结构不对）")
    if not segs:
        errs.append("账本里没有 segments（结构不对）")
    by_name = {}
    for i, seg in enumerate(segs, 1):
        name = (seg or {}).get("name") or "（无名段）"
        lines = (seg or {}).get("lines")
        if not isinstance(lines, dict) or not lines:
            errs.append("第 %d 段「%s」没有合法的 lines" % (i, name))
            continue
        want = seg.get("sha256")
        got = hashlib.sha256(_canon_lines(lines).encode("utf-8")).hexdigest()
        if want != got:
            errs.append("第 %d 段「%s」段校验值不符（账本 %s ≠ 实算 %s）"
                        % (i, name, str(want)[:12], got[:12]))
        by_name[name] = lines
    # 整本指纹（顺序：files（按文件名）→ segments（按段顺序）→ removed（按文件名））
    parts = []
    for fname in sorted(files):
        finfo = files[fname] or {}
        b = finfo.get("baseline") or {}
        parts.append("FILE\t%s\t%s\t%s\t%s" % (fname, finfo.get("target", ""),
                                                b.get("md5", ""),
                                                ",".join(finfo.get("segments") or [])))
    for seg in segs:
        name = (seg or {}).get("name") or "（无名段）"
        lines = (seg or {}).get("lines")
        if isinstance(lines, dict):
            parts.append("SEG\t%s\t%s" % (name, _canon_lines(lines)))
    for fname in sorted(removed):
        parts.append("REM\t%s\t%s" % (fname, _canon_lines(removed[fname] or {})))
    whole = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    fp = data.get("ledger_sha256")
    if fp != whole:
        errs.append("账本整本指纹不符（账本 %s ≠ 实算 %s）"
                    % (str(fp)[:12], whole[:12]))
    summary = []
    for fname in sorted(files):
        finfo = files[fname] or {}
        segs_txt = []
        for sn in (finfo.get("segments") or []):
            lines = by_name.get(sn) or {}
            segs_txt.append("%s:%d行" % (sn, sum(lines.values())))
        summary.append("%s(%s，旧侧%d行)" % (fname, "＋".join(segs_txt),
                                            sum((removed.get(fname) or {}).values())))
    return files, by_name, removed, errs, \
        (" ｜ ".join(summary) + " ｜ 整本指纹 " + whole[:12])


def resolve_baseline(entry, fname, ledger_dir):
    """取基线内容：先本地快照（按 md5 校验）→ 再 git ref（按 md5 校验）→ 都没有就报错。

    ★ 本地快照缺失**不算错**（只要 git 来源能按 md5 对上）；只有两条路都取不到才报红。
    """
    errs = []
    want = (entry.get("md5") or "").upper()
    p = entry.get("path")
    if p:
        fp = os.path.normpath(os.path.join(ledger_dir, p))
        if os.path.exists(fp):
            b = open(fp, "rb").read()
            got = hashlib.md5(b).hexdigest().upper()
            if got == want:
                return b.decode("utf-8"), "本地快照 %s" % os.path.basename(fp), errs
            errs.append("%s 基线快照 MD5 不符（文件 %s ≠ 账本 %s）"
                        % (fname, got[:12], want[:12]))
    ref = entry.get("git_ref")
    if ref:
        r = subprocess.run(["git", "-C", ROOT, "show", ref], capture_output=True)
        if r.returncode == 0:
            got = hashlib.md5(r.stdout).hexdigest().upper()
            if got == want:
                return r.stdout.decode("utf-8"), "git %s" % ref, errs
            errs.append("%s git 基线 MD5 不符（%s ≠ 账本 %s）"
                        % (fname, got[:12], want[:12]))
        else:
            errs.append("%s git 基线取不到：%s" % (fname, ref))
    errs.append("%s 基线无法解析（快照与 git 都没有）" % fname)
    return None, None, errs


def code_line_map(text):
    """用 tokenize 剥掉注释与字符串 ⇒ 返回 {行号: 该行真代码文本}。"""
    keep, store = {}, {}
    cut = {}          # 行号 -> 注释起始列（用来把行尾注释切掉）
    raw = text.splitlines()
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            cut[tok.start[0]] = tok.start[1]
            continue
        if tok.type in (tokenize.STRING, tokenize.NL,
                        tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                        tokenize.ENDMARKER):
            continue
        for ln in range(tok.start[0], tok.end[0] + 1):
            keep[ln] = True
        store[tok.start[0]] = raw[tok.start[0] - 1].rstrip()
    # 把行尾注释切掉（白名单要拿**纯代码**文本比对）
    for ln, col in cut.items():
        if ln in store:
            store[ln] = store[ln][:col].strip()
        else:
            # 该行只有注释（纯注释行）⇒ 不算真代码
            keep.pop(ln, None)
    return keep, store


def changed_code_lines(base_text, cur_text):
    """基线 → 现在的 diff ⇒ (新侧真代码行 Counter, 旧侧真代码行 Counter)。

    ★ 新侧 = 被改动/新增的行；旧侧 = 被删/被替换的行。两边都要与账本**逐行计数**对上。
    """
    _, oc = code_line_map(base_text)
    _, nc = code_line_map(cur_text)
    ol, nl = base_text.splitlines(), cur_text.splitlines()
    sm = difflib.SequenceMatcher(None, ol, nl, autojunk=False)
    added, removed = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            if (i + 1) in oc:
                removed.append(oc[i + 1].strip())
        for j in range(j1, j2):
            if (j + 1) in nc:
                added.append(nc[j + 1].strip())
    return Counter(added), Counter(removed)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest().upper()


def resolve_live():
    """定位 live（**真在跑的那份** legacy 里的 act.py）—— 层序照 tools\\check_live_sync.py。

    ★ live 是**本机状态**、不在仓里（声明层指针 build\\local_legacy_home.txt 本身未 tracked）
      ⇒ 干净克隆上定位不到是**正常**的 ⇒ 按「验不了」处理：明确打印、不判红、不静默跳过。
      （与 tools\\check_live_sync.py 的退出码 2 =「验不了」、发包侧「跳过并提示」同一处置原则。）
    ★ 返回 (路径 or None, 来源/原因说明)
    """
    p = os.environ.get("AGENTCTL_LIVE_ACT")                      # ① 显式覆盖
    if p and os.path.isfile(p):
        return p, "env AGENTCTL_LIVE_ACT"
    ptr = os.path.join(ROOT, "build", "local_legacy_home.txt")   # ② 声明层指针（本机状态）
    try:
        with open(ptr, encoding="utf-8") as f:
            d = f.read().strip()
    except OSError:
        d = ""
    if d:
        cand = os.path.join(d.replace("/", os.sep), "act.py")
        if os.path.isfile(cand):
            return cand, "指针 build\\local_legacy_home.txt"
    return None, "定位不到（live 是本机状态、不在仓里；干净克隆上必然定位不到）"


def check(label, got, want, extra=""):
    ok = (got == want)
    if not ok:
        fails.append(label)
    print("  %-54s %-34s %s %s" % (label, str(got)[:34],
                                    "PASS" if ok else "FAIL(want=%s)" % (want,), extra))
    return ok


def main():
    print("== M19′ 主控复核 ==")

    # ── 先读外置账本：基线与「批准清单」都从这儿来（脚本里不再写死任何行清单）──
    ledger_dir = os.path.dirname(os.path.abspath(LEDGER_PATH))
    files, segs_by_name, removed_map, lerr, lsum = load_ledger()
    print("   外置账本：%s" % LEDGER_PATH)
    print("   账本摘要：%s" % (lsum or "（读不到）"))
    for e in lerr:
        check("账本校验 · %s" % e[:56], False, True)

    # 逐文件解析：基线 + 当前内容 + 改动的真代码行（新侧/旧侧）
    resolved = {}
    for fname, finfo in (files or {}).items():
        btxt, bsrc, errs = resolve_baseline(finfo.get("baseline") or {},
                                             fname, ledger_dir)
        for e in errs:
            check("基线解析 · %s" % e[:56], False, True)
        tgt = os.path.join(ROOT, (finfo.get("target") or "").replace("/", os.sep))
        if btxt is None or not os.path.exists(tgt):
            check("受控文件缺失 · %s" % fname, False, True)
            continue
        ctxt = open(tgt, encoding="utf-8").read()
        added, removed = changed_code_lines(btxt, ctxt)
        resolved[fname] = {
            "base": btxt, "cur": ctxt, "target": finfo.get("target"),
            "base_src": bsrc, "added": added, "removed": removed,
        }
        print("   受控文件 %-8s 目标=%-26s 基线=%s" % (fname, finfo.get("target"), bsrc))

    print("\n① 三处 act.py MD5（须全等，且不等于改前基线 %s）" % BASE_ACT[:8])
    m_src, m_prod = md5(SRC), md5(PROD)
    live_path, live_note = resolve_live()
    m_live = md5(live_path) if live_path else None
    print("   真源 %s\n   产物 %s\n   live %s" % (m_src, m_prod,
                                              m_live or "（%s）" % live_note))
    check("真源 == 产物", m_src, m_prod)
    if m_live is None:
        print("   [跳过] 真源 == live —— %s" % live_note)
        print("          （live 是本机状态、不在仓里 ⇒ 干净克隆上这一格**验不了**；"
              "仓内两处仍全等。live 漂移由 tools\\check_live_sync.py 专责）")
    else:
        check("真源 == live", m_src, m_live)
    check("%s != 改前基线（确实改了）" % ("三处" if m_live else "两处"),
          m_src != BASE_ACT, True)

    print("\n② 不变量（契约/闸门/core/策略/帧缓存）零改动")
    for rel, want in INVARIANTS.items():
        check("零改动 " + rel, md5(os.path.join(ROOT, rel.replace("/", os.sep))), want)

    print("\n③ act.py 常量零改动（对基线逐字核）")
    old = resolved.get("act.py", {}).get("base") or open(BAK, encoding="utf-8").read()
    new = resolved.get("act.py", {}).get("cur") or open(SRC, encoding="utf-8").read()
    for c in CONSTS:
        o = re.findall(r"^%s\s*=.*$" % c, old, re.M)
        n = re.findall(r"^%s\s*=.*$" % c, new, re.M)
        check("常量 %s 未变" % c, n, o)

    print("\n④ 行尾 LF（CRLF 计数必须为 0）")
    for p in ([SRC, PROD] + ([live_path] if live_path else [])):
        b = open(p, "rb").read()
        check("CRLF=0 " + os.path.basename(os.path.dirname(p)), b.count(b"\r\n"), 0)

    print("\n⑤ M19′/M19P 合法改动块")
    ol, nl = old.splitlines(), new.splitlines()
    sm = difflib.SequenceMatcher(None, ol, nl, autojunk=False)
    allowed_out = []
    other = []
    changed_new = []          # ★ 所有「被改动的**新**真代码行」（⑤-b 清单用）
    # 旧行侧白名单 = 账本 removed[act.py] 登记过的行（被删/被替换的真代码行）
    removed_lines = set((removed_map.get("act.py") or {}).keys())
    # ★ 用 tokenize 剥掉注释与字符串 ⇒ 只留真代码行（M19P 主控修订：
    #   旧版按行前缀猜 docstring，diff 把 docstring 切碎时会误判为越界）
    _, old_code = code_line_map(old)
    _, new_code = code_line_map(new)

    def _code_only(lines, start_lineno, code_map):
        """取 [start_lineno, start_lineno+len(lines)) 区间内的**真代码行**。"""
        out = []
        for i, ln in enumerate(lines):
            no = start_lineno + i
            if no in code_map:
                out.append(code_map[no])
        return out

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        seg_old = ol[i1:i2]
        seg_new = nl[j1:j2]
        blob = "\n".join(seg_old + seg_new)
        old_code_lines = _code_only(seg_old, i1 + 1, old_code)
        new_code_lines = _code_only(seg_new, j1 + 1, new_code)
        changed_new.extend(c.strip() for c in new_code_lines)
        only_comment = (not old_code_lines) and (not new_code_lines)
        if only_comment:
            allowed_out.append((tag, i1 + 1, i2, j1 + 1, j2, "[仅注释/文档字符串] " + blob[:100]))
        elif all(any(p.match(c.strip()) for p in ALLOWED_NEW_CODE) for c in new_code_lines) \
                and all(c.strip() in removed_lines for c in old_code_lines):
            # ★ 白名单精确匹配真代码行（M19′ 出口 + M19P 的 last_d 复用 + 任务②的助手/调用点
            #   + M19P2 同族两处 + M20 act 安全批）；
            #   旧行侧：**必须逐行在账本 removed[act.py] 里登记过**（不是关键词近似）。
            allowed_out.append((tag, i1 + 1, i2, j1 + 1, j2, blob[:120]))
        else:
            other.append((tag, i1 + 1, i2, j1 + 1, j2, blob[:200],
                          "新真代码行=%r" % (new_code_lines,)))
    for a in allowed_out:
        print("   [允许] %s 旧:%d-%d 新:%d-%d | %s" % a)
    for o in other:
        print("   [越界] %s 旧:%d-%d 新:%d-%d\n%s\n   %s" % o)
    check("无越界改动块", len(other), 0)

    # ⑤-b 逐文件：改动真代码行清单（白名单是**形状**约束，本条是**数量**约束）
    #   合法改动恰好是账本里那几段批准的真代码行：多一行、少一行都报红。
    #   ★ 这一条才是「放宽白名单后仍不放行真越界」的兜底：
    #     白名单里的 `return 0` / `else:` / `if not dry:` 是裸行，act.py 里另有几十处合法同形
    #     ⇒ 只靠白名单，凭空插一行必被放过；加上数量约束 ⇒ 插一行必报红。
    #   ★ 旧侧（被删/被替换的行）同样逐行计数：只允许删账本登记过的行。
    for fname in sorted(resolved):
        info = resolved[fname]
        names = (files.get(fname) or {}).get("segments") or []
        want = Counter()
        for sn in names:
            want.update(segs_by_name.get(sn, {}))
        got = info["added"]
        delta = ["%r 多%d/少%d" % (k, got.get(k, 0) - want.get(k, 0),
                                  want.get(k, 0) - got.get(k, 0))
                 for k in sorted(set(got) | set(want))
                 if got.get(k, 0) != want.get(k, 0)]
        seg_txt = "＋".join("%s:%d行" % (n, sum((segs_by_name.get(n) or {}).values()))
                            for n in names)
        check("⑤-b 新侧改动真代码行清单 · %s" % fname, got == want, True,
              ("%d 行（%s）" % (sum(got.values()), seg_txt)) if not delta
              else ("%d 行（账本应 %d 行：%s）｜差异：" % (sum(got.values()),
                                               sum(want.values()), seg_txt))
              + " | ".join(delta))
        rem_want = Counter(removed_map.get(fname) or {})
        rem_got = info["removed"]
        rem_delta = ["%r 多%d/少%d" % (k, rem_got.get(k, 0) - rem_want.get(k, 0),
                                      rem_want.get(k, 0) - rem_got.get(k, 0))
                     for k in sorted(set(rem_got) | set(rem_want))
                     if rem_got.get(k, 0) != rem_want.get(k, 0)]
        check("⑤-b 旧侧（被删/被替换）真代码行清单 · %s" % fname,
              rem_got == rem_want, True,
              ("%d 行" % sum(rem_got.values())) if not rem_delta
              else ("%d 行（账本登记 %d 行）｜差异：" % (sum(rem_got.values()),
                                              sum(rem_want.values())))
              + " | ".join(rem_delta))

    print("\n⑥ act.py 里所有「生效」出口（逐条列，主控自己读）")
    for i, line in enumerate(new.splitlines(), 1):
        if "生效" in line and ("return" in line or "\"生效\"" in line):
            print("   %5d: %s" % (i, line.strip()[:110]))

    print("\n⑦ 判据四种跑法（退出码）")
    j = os.path.join(ROOT, "tests", "_P3_M19_语义修复_判据.py")
    # ★ 期望值说明（主控 10:0x 实测后更正）：判据**不解析**参数，未知参数会被静默忽略
    #   ⇒ 未知参数的退出码必须**跟正跑一致**（修好后=0，缺陷在=1），不是固定 1。
    #   ★ 上一轮台账里「未知参数 exit=1（无假绿）」是**缺陷仍在时**的基线，修好后应变成 0。
    cases = [("正跑", [], 0), ("--inject（注入必须红）", ["--inject"], 1),
             ("M19_USE_PRODUCT=1（走产品真代码）", ["__ENV__USE_PRODUCT"], 0),
             ("未知参数（须与正跑一致，不许假绿）", ["--no-such-flag"], 0)]
    for label, args, want in cases:
        env = dict(os.environ)
        if args and args[0] == "__ENV__USE_PRODUCT":
            env["M19_USE_PRODUCT"] = "1"
            args = []
        p = subprocess.run([sys.executable, "-X", "utf8", j] + args, cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        print("   [%s] exit=%d (want %d)" % (label, p.returncode, want))
        check("判据退出码 · " + label, p.returncode, want)

    print("\n⑧ 性能不变式：预算耗尽路径不得**二次**判差")
    helper = re.search(r"def _budget_verdict\(.*?\n(?=\n\ndef |\n\nclass )", new, re.S)
    hblock = helper.group(0) if helper else ""
    # ★ 只看**真代码行**（tokenize 剥掉 docstring/注释）——
    #   docstring 里写 `_img_diff(base_img, last)` 是**举例说明**，不是调用。
    if helper:
        hstart = new[:helper.start()].count("\n") + 1
        _, new_code2 = code_line_map(new)
        hcode = [new_code2[i] for i in sorted(new_code2)
                 if hstart <= i <= hstart + hblock.count("\n")]
    else:
        hcode = []
    calls_diff = any(("_img_diff(" in c) or ("diff_images(" in c) for c in hcode)
    reuses = any("last_d" in c for c in hcode)
    print("   _budget_verdict 块：%s" % ("（没找到）" if not hblock else "有"))
    print("   真代码行：%s" % (" | ".join(hcode) if hcode else "（无）"))
    print("   二次判差=%s ｜ 复用已算判差=%s" % (calls_diff, reuses))
    check("不得二次判差（否则每次动作 +13.84ms）", calls_diff, False)

    print("\n== 汇总 ==")
    if fails:
        print("【红】%d 项失败：%s" % (len(fails), fails))
        return 1
    print("【绿】全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
