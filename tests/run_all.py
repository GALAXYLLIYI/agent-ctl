# -*- coding: utf-8 -*-
r"""tests\run_all.py —— 判据总入口（窗C · 2026-10-15）

一条命令跑齐 tests\ 下沉淀的全部判据：

    python -X utf8 tests\run_all.py

退出码：
    0 = 全部判据**全部**绿
    1 = 至少一条红 / 判据文件缺失 / 判据崩了 / 超时

设计约束（派单 §三「只搬运 + 接线，不改判据逻辑」）：
  · 本文件**只做调度**：起子进程、透传输出、收退出码、汇总。
    任何一条判据的断言一个字都没改；本文件也不复刻任何一条断言。
  · 输出**原样透传**，不吞不改 —— 红/绿留档里能看到每条判据的原始输出。
  · **fail-closed**：判据文件不见了、抛异常了，一律算红。
    「少一条判据」绝不能被读成「全绿」（这正是退回单
    work\_T1_退回_W2_判据退出码.md 记的那个教训）。

★ 边界（**这不是缺陷，但必须知道**，窗M 2026-10-16 补）：
    本入口**只在「Windows + 有交互桌面会话」的机器上成立**。
      · w3 要真画边框窗口、真抓屏、真断言前台窗口不变 —— 无头会话 / 服务 / CI 跑不了；
      · wI（急停弹窗）同理要真弹一个置顶窗口出来（窗P2 又让它起 5 个真 daemon、
        逐个抢一次全局热键 —— 这一条现在要占约 2 分钟桌面，别在赶时间的时候跑它）；
      · 若干判据要 UIA（UI Automation）能枚举控件树。
    ⇒ **别把它当 CI 判据使**。要在无人值守环境里跑，先把 w3 / wI 这类
      「要桌面」的判据拆出去单列；本入口只负责「有桌面的 Windows 上把闸门跑齐」。
    ★ 另有一条更硬的可移植性约束：**每条判据都必须验「它自己所在的那份仓」**。
      把仓根写死在本机路径上的判据，在别的机器上会给出一份**与入库物无关的绿灯**
      —— 闸门会撒谎，这比没有闸门更坏。这条由 tests\_M_可移植性判据.py 守着。

判据清单（原散落处 → 现居处）：
    work\_T1_W2_判据.py         → tests\_T1_W2_判据.py
    docs\证据\check_t1_w1.py    → tests\check_t1_w1.py
    work\_T1_窗3_自证.py        → tests\_T1_窗3_自证.py
    work\_codex_接缝判据.py     → tests\_codex_接缝判据.py
    work\_2h_wA_档位口径判据.py → tests\_2h_wA_档位口径判据.py
    work\_2h_wB_D级绊线判据.py  → tests\_2h_wB_D级绊线判据.py
    （窗F 新增，就地落在 tests）    → tests\_2h_wF_配色冻结判据.py
    （P4 新增，就地落在 tests）     → tests\_P4_定位器夹具判据.py（配 tests\fixtures\ 两张固定图）
    （P1 新增，就地落在 tests）     → tests\_P1_租约吊销判据.py（第三方强制吊销单张租约）
    （P2 新增，就地落在 tests）     → tests\_P2_动作面刹车判据.py（动作面连续 N 次阈值刹车）
    （P0 新增，就地落在 tests）     → tests\_P0_自指保护判据.py（自指保护：终端按**身份**拦 /
                                      标题兜底仍拦 / 普通应用放行 / 终端 30 分钟临时放行 + 过期再拦）
    （窗U 落位，DSH 接线 2026-10-17）→ tests\_legacy台账对账.py（CHK-A：产物 vs A₀、真源 vs T₀；
                                      实现在 tools\gen_legacy.py，两处共用一份）
    （窗V 落位，Codex 接线 2026-10-17）→ tests\_元判据_断言弱化.py（元1：git diff HEAD --
                                      tests/ 的删除行里命中断言模式的行数 + 未跟踪判据与
                                      .bak 备份比条数 ⇒ 断言不许被悄悄删弱）
                                  tests\_元判据_判据接线.py（元2：tests\ 下每个判据样文件
                                      要么在本 CRITERIA 里、要么在它自己的 NOT_WIRED 里
                                      写明理由；且文档写的条数 == len(CRITERIA)）

★ 条数：**2026-10-17 之前 14 条 → 窗V 16（元1/元2）→ 窗W 17（写死）→ 门禁3 18（通道）→ 默认关闭 19 → 孤儿 20 → 视口 21 → 语义屏 22 → volatile 23**。
  ★ 这两条**不登记进 INJECTABLE**：它们的注入自证走各自的 --inject，
    不改「哪些判据支持注入」这一栏的语义（派单 §二）。

跑红（不需要改任何判据、不需要 git 写）：
  · 少一条：把 tests\ 下任一判据临时挪走再跑 ⇒ 该条报「判据文件不在」⇒ 退 1。
  · 注入式自证（★ 2026-10-16 窗M 起，**不再依赖 work\ 里那份红基线仓**）：
        python -X utf8 tests\run_all.py --inject
    逐条给「支持注入的判据」喂一份**注入了缺陷的副本**（落 %TEMP%，产品代码不动），
    **期望它们全部变红**；哪一条注入后还绿，就说明那条判据是死的。
    （接缝判据的 --inject 早就有了，wA / wB / wF 是本单补上的。）
  · 指向陈旧副本：wB 那条仍留 AGENTCTL_ROOT 覆盖口，可指到任意一份旧仓：
        $env:AGENTCTL_ROOT="<别的一份仓>"; python -X utf8 tests\run_all.py

参数：
    --list      只打印判据清单，不跑
    --inject    跑红自证模式（见上；期望可注入的判据全红，rc=0 表示「判据都是活的」）
    --timeout N 单条判据的秒数上限（默认 900；超时算红）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (键, 判据脚本, 固定参数, 这条判据查的是什么)
CRITERIA = (
    ("w1", "check_t1_w1.py", (),
     "T1 窗1：docs/AGENT接口.md §3-bis 与契约条目逐条对上"),
    ("接缝", "_codex_接缝判据.py", (),
     "契约（唯一真相源）与代码实际行为是否脱节（J1-J4）"),
    ("w2", "_T1_W2_判据.py", ("--new",),
     "T1 窗2：eid 绑签发它的那次快照 + bench b11 回归守（跑现役 src）"),
    ("w3", "_T1_窗3_自证.py", ("all",),
     "T1 窗3：uia_tree 坐标系标注 + ctrl_border --follow 接线自证"),
    ("wA", "_2h_wA_档位口径判据.py", (),
     "窗A·P3：边框判档口径 vs 闸门判档口径（默认 --target 现役 ctrl_border.py）"),
    ("wB", "_2h_wB_D级绊线判据.py", (),
     "窗B·P6/P5：D 级有意不解锁的机械绊线 + 发证上限口径"),
    ("wF", "_2h_wF_配色冻结判据.py", (),
     "窗F：边框配色/按档厚度/1px 反色描边**冻结钉子**（五个 RGB 写死）+ 契约颜色名同步"),
    ("P4", "_P4_定位器夹具判据.py", (),
     "P4：定位器**固定图夹具**（正向必须找到 4 个 + ★反向必须返回空）——堵 selftest.py:95-98 的盲区"),
    ("P1", "_P1_租约吊销判据.py", (),
     "P1：revoke_lease 第三方强制吊销单张租约（立刻拦/只影响目标/幂等/防手滑/"
     "急停联动/★模型无入口/留痕/renew 不许复活）"),
    ("P2", "_P2_动作面刹车判据.py", (),
     "P2：动作面异常刹车（连点同坐标/连续失败两级响应：★第 1 次只写事件不拉闸/"
     "第 2 次才拉闸 + ★防误报 + 阈值可配 + 接线 + 留痕 + 面板提示）"),
    ("wI", "_急停弹窗判据.py", (),
     "窗I+N+P2：急停**桌面置顶弹窗**（真出现/真置顶/无改状态控件/解除后消失/agent 碰不到它）"
     " + 窗P2 **三条入口**（退役线响亮拒绝 / 真·文档入口弹窗 / 模块入口不许秒退）"
     " + 抢占显式 + 收尾自证（flag 无 / 可见弹窗 0 / daemon 进程 0）"),
    ("P0", "_P0_自指保护判据.py", (),
     "P0：**唯一的防自授权闸门**——身份优先三重匹配（属主进程名 / 窗口类名 / 标题兜底）"
     "+ 终端默认拦但可人确认 30 分钟临时放行"),
    ("wM", "_M_可移植性判据.py", (),
     "窗M：**闸门不许验错对象** —— 仓根不许写死（源码面/文档面静态查 + "
     "复制一份仓到 %TEMP% 从副本实跑 w1/w2/w3，输出必须指向副本、不许出现真仓路径）"),
    ("台账", "_legacy台账对账.py", (),
     "窗U（Step5 必要条件 1）：CHK-A **产物重生一致** —— 产物 vs 台账 A₀（抓手改/缺件）"
     "+ 真源 vs 台账 T₀（抓真源更新后产物未刷）；★ 不许拿 T₀==A₀ 当口径（净化文件本该不等）"),
    ("元1", "_元判据_断言弱化.py", (),
     "窗V 元判据①：**tests\\ 里的断言不许被悄悄删弱** —— git diff HEAD 的删除行里"
     "命中断言模式（check(/attempt(/assert/unverifiable(/def check()的行数，"
     "白名单逐条带理由；未跟踪判据与 .bak 备份比 check(+attempt( 条数）"),
    ("元2", "_元判据_判据接线.py", (),
     "窗V 元判据②：**判据存在 ≠ 判据在跑** —— tests\\ 下每个判据样文件要么在本表里、"
     "要么在它自己的 NOT_WIRED 里写明理由；且《判据有效性清单》与框架文档写的条数"
     "== len(CRITERIA)"),
    ("写死", "_写死路径判据.py", (),
     "窗W：**写死本机路径的白名单门禁** —— 仓根写死在 tests/src/contract 必须为 0；"
     "其余命中必须落 A~G 七类白名单；命中数须等于白名单文档 §二（文档漂了也红）；--inject 能红"),
    ("通道", "_通道保真_判据.py", (),
     "门禁3：**通道保真** —— 屏幕类判据 {w3/wI/P0/端到端} 枚举完整（都在跑或明写不接）；"
     "manifest 每条都有截图→sha256→结论，且**重算一致**；采摘规程在场（API 读数只作辅助）"),
    ("默认关闭", "_默认关闭_判据.py", (),
     "用户裁：**上传的内容默认这个闸口关闭** —— 默认无通行证时 A/B/C/D **全拒**（对真 gate.check() 判）"
     "+ 装完不自动签发（install/main/panel/widget 四处静态查）"
     "+ README 明写「默认关闭 / 怎么开 / 怎么自验」"),
    ("视口", "_视口口径判据.py", (),
     "窗A2（F3）：**视口与文本口径** —— 默认只回视口内、全量带 in_view 标注（不裁剪）；"
     "裸 div 题干与兜底文本读得到（补前/补后两跑对照）；scroll 取不到是 null 不是 0"),
    ("语义屏", "_语义屏判据.py", (),
     "窗B1（F4）：**语义屏** —— 默认按注意力裁到 ≤40 但视口内可交互**必进**、omitted 诚实报数；"
     "uid 三连稳 + 标题变则版本变 + 版本只能回传；volatile 计时器不爆 STALE；"
     "source_confidence 取 min；must_vlm=¬R1∧¬R2∧¬R3；Chromium name_trust=low"),
    ("volatile", "_volatile守门_判据.py", (),
     "窗Z3 规则表落地后的守门：**可交互节点永不豁免**（SpinnerControl 曾在两边）；"
     "八例逐条对表（剩余 3 项=非 volatile / 3.5s=volatile / 第 7/20 步=非 / 共 166 题=非…）；"
     "注入 spinner 或 units 必须转红"),
    ("孤儿", "_ctrl_border_孤儿判据.py", (),
     "窗Y：**孤儿边框**不许出现 —— 父进程被硬杀 / 被 terminate / 优雅退出，"
     "边框都要 ≤10s 内收摊且屏幕上 0 个 AgentCtlCtrlBorder* 窗；"
     "另含 pid 复用（创建时间对不上）必须认亲失败并留日志、以及 fail-closed"),
    ("点错窗", "_点错窗判据.py", (),
     "第24条（2026-10-18 真窗验提升）：**点错窗必须被拒** —— 落点被别的窗盖住 ⇒ "
     "code=12/wrong_target、零像素、回执点名遮挡者；目标前台下正常放行且 verdict=met；"
     "--inject（把归属事实打成 None）必须变红"),
    ("帧过期", "_帧过期判据.py", (),
     "第25条（2026-10-18 §㊾ 0号最小裁判第一步）：**旧帧不许用来点** —— "
     "frame_id 版本匹配后还要判帧龄，超 TTL ⇒ code=12/判定=过期/零像素并给出帧龄与 TTL；"
     "新鲜帧正常放行；关掉 TTL 分支后不再判过期（证明拒绝确实来自 TTL）；"
     "--inject（把 _frame_ttl_ms 打成 0）必须变红"),
    ("输入判词", "_输入判词_判据.py", (),
     "第26条（2026-10-18 §㊾ A 项）：**点没点到 ≠ 点到了没生效** —— "
     "回执必须带 input{fg_ok,hit_ok,cursor,verdict}；目标前台下 verdict=accepted；"
     "code=8（画面没变）时仍判 accepted，而不是 unknown；"
     "--inject（_input_snapshot 恒 unknown）必须变红"),
    ("体检", "_体检_判据.py", (),
     "第27条（2026-10-18 §㊾ B 项）：**开工前体检不说谎** —— "
     "ctl_preflight 给 window/sensitive/channels/frame/auth + verdict/advice；"
     "空 hwnd ⇒ no_window；敏感窗（自己起的控制台）⇒ blocked_sensitive；"
     "--inject（_deny_sensitive_target 恒 None）必须变红"),
    ("MCPactor", "_MCPactor_判据.py", (),
     "第28条（2026-10-19 Codex 实跑发微信踩出）：**MCP 传输必须带得上 actor** —— "
     "actor 只交给 dispatch 的关键字参数（= mcp.py 从 AGENTCTL_ACTOR 取值的那条路）时，"
     "闸门必须认得，写动作不得再报 code=10「没带 actor」；"
     "★ 守门反向：actor 真没给时闸门仍必须拒（不许靠取消检查蒙混过关）；"
     "--inject（逐字复现修前 dispatch 形状）必须变红"),
    ("组合键", "_组合键通道_判据.py", (),
     "第29条（2026-10-19 Codex 实跑发微信踩出）：**组合键不许走后台 PostMessage** —— "
     "ctrl=True 时不得调 _post_keys（自绘控件丢掉 Ctrl 修饰位、按 wParam 直接打字母，"
     "而那种乱字符**和成功在像素上长得一样**判不出来）；"
     "普通键仍走路线 P（不许误伤）；--inject 必须变红"),
    ("计数", "_通行证计数_判据.py", (),
     "第30条（2026-10-19 Codex 实跑发微信踩出）：**新签的证从 0 起算** —— "
     "merge_pass 新签发时 acts 归零（旧计数留档 acts_carried，不丢审计）；"
     "弹窗批的次数照旧生效；窗口追加模式仍一分额度都不给。"
     "★ 本条不碰闸门，用户不在场也能复跑"),
    ("像素判成功", "_像素不判成功_判据.py", (),
     "第32条（2026-10-19 16:08 真跑撞出）：画面变了 ≠ 动作生效"
     "if <像素/指纹比较> 分支体内不许 return 0"
     "自述「判不出真相」与「用像素判生效」不得并存（注释行不算实现）"
     "★ 纯静态，不碰闸门，用户不在场也能跑；--inject 必变红"),
    ("靶标隔离", "_靶标隔离_判据.py", (),
     "第33条（2026-10-19 16:08 真跑撞出）：**判据不许拿用户真实状态当靶子**"
     "在真窗口发真键却没接管剪贴板 ⇒ 一定会把用户剪贴板内容打进靶标窗"
     "★ 本条当前**红了是故意的**：4 个命中都是真病灶，待整改"
     "★ 纯静态（ast），不需要通行证"),
    ("unknown不当成功", "_unknown不当成功_判据.py", (),
     "第34条（2026-10-19 16:28 扫 act.py 扫出）：**unknown 永不当成功**"
     "return 0 if changed is not False 把 None（判不出）也当成功"
     "五法 11(9)：未知永不当成功；四处已改为 changed is True"
     "★ 纯静态，不需要通行证；--inject 必变红"),
    ("失败可辨", "_失败态可辨_判据.py", (),
     "第31条（2026-10-19 §⑧ 元结论落地）：**失败态不许长得像「什么都没拿到」** —— "
     "warning 不许复读输入（★ rc=4 现在读「老脚本返回码 4」＝零信息增量，"
     "与 D1「恒定报没带 actor」同一病根形态）；同码不同因必须说出不同的话；"
     "失败回执必带非空定位字段；本判据 RC_MAP 与真源实际落码逐条对账。"
     "★ 纯逻辑，不碰闸门，用户不在场也能复跑"),
    ("工作站体检", "_工作站体检_判据.py", (),
     "第35条（2026-10-19 工单001）：**工作站体检不说谎** —— tools\\station_check.py 的"
     "字段齐全（10 组）/ consistent 与两 md5 自洽 / verdict 与数据自洽 / 退出码与结论一致 /"
     "真漂移⇒runtime_drift；--inject（把负例改成两边同文件）必须变红"),
    ("文案一致", "_文案与数据一致_判据.py", (),
     "第36条（2026-10-19 用户拍板成立，Codex 自曝的第 4 类病）：**日志说的话必须与回执数据一致** —— "
     "变了（code=0/verdict=met）不许说「没变」；没变（code=8/no_effect）必须说「没变/unknown」；"
     "内置已知坏样本自证；--inject（检查函数恒 True）必须变红"),

)

# ★ 窗M（2026-10-16）：哪些判据自带「注入式跑红」口。
#   --inject 模式只跑这些，**期望它们变红**；没登记的判据在那一栏里如实列成
#   「无注入口」，不计入通过 —— 免得「没跑」被读成「跑过了」。
INJECTABLE = {
    "接缝": ("--inject",),
    "wA": ("--inject",),
    "wB": ("--inject",),
    "wF": ("--inject",),
    # ★ 2026-10-17（DSH 亲跑发现）：**台账条与写死条都不进这一栏** —— 它们属于
    #   "**自证型** --inject"：跑两段（未改副本必须绿 + 注入必须被点名），
    #   **自证成立时返回 rc=0**。而本栏的语义是"**期望变红（rc≠0）**"⇒ 两者相反，
    #   留在栏里会被读成「注入后仍全绿 = 判据是死的」。
    #   ⇒ 本栏只收"期望变红"型；自证型的证据在它们自己输出里：
    #     台账 → work\_chkA_落位2_注入自证_20261017.txt
    #     写死 → tests\_写死路径判据.py --inject 自己的「注入后被判红并点名 …」
    #   （★ 写死条原按旧语义 rc=1 登记过；窗W 把语义改成"自证成功=绿"之后，
    #     我亲跑 run_all --inject 又看到它被判"死"⇒ 同日摘出，只留 4 条。）
    "点错窗": ("--inject",),
    "帧过期": ("--inject",),
    "输入判词": ("--inject",),
    "体检": ("--inject",),
    "工作站体检": ("--inject",),
    "MCPactor": ("--inject",),
    "组合键": ("--inject",),
    "失败可辨": ("--inject",),
    "像素判成功": ("--inject",),
    "靶标隔离": ("--inject",),
    "unknown不当成功": ("--inject",),
}




def _load_no_inject():
    """读 tests\_无注入口_声明.txt：返回 {判据键: 原因}。缺失就是空表（不豁免任何人）。"""
    d = {}
    try:
        with open(os.path.join(HERE, "_无注入口_声明.txt"), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "|" not in ln:
                    continue
                k, why = ln.split("|", 1)
                d[k.strip()] = why.strip()
    except Exception:
        pass
    return d


def out(msg=""):
    print(msg, flush=True)


def run_one(key, script, args, timeout):
    path = os.path.join(HERE, script)
    out("\n" + "#" * 78)
    out("# 判据 %s · %s %s" % (key, script, " ".join(args)))
    out("#" * 78)
    if not os.path.isfile(path):
        out("[红] %s 判据文件不在：%s" % (key, path))
        out("     ⇒ 少一条判据不能当全绿（fail-closed），这条记红。")
        return 1, 0.0, "判据文件不在"
    cmd = [sys.executable, "-X", "utf8", path] + list(args)
    t0 = time.monotonic()
    try:
        rc = subprocess.call(cmd, cwd=ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        out("[红] %s %s 超过 %ss 没跑完，按红记（fail-closed）" % (key, script, timeout))
        return 1, time.monotonic() - t0, "超时"
    except Exception as e:
        out("[红] %s 判据起不来：%s: %s" % (key, type(e).__name__, e))
        return 1, time.monotonic() - t0, "起不来"
    dt = time.monotonic() - t0
    if rc == 0:
        out("[绿] %s %s（rc=0，%.1fs）" % (key, script, dt))
        note = "绿"
    elif rc == 2:
        # ★ 2026-10-18 裁定 A：rc=2 = **验不了**（环境不满足：没真通行证 / 夹具就位不了…）。
        #   它是一等结果，**不算红**（否则"红"会贬值、无人值守永远跑不绿），**也不算绿**（单列并点名）。
        out("[验不了] %s %s（rc=2，%.1fs）—— 环境不满足；看它自己打的『验不了』条目"
            % (key, script, dt))
        note = "验不了（rc=2）"
    else:
        out("[红] %s %s（rc=%s，%.1fs）—— 细节看它自己打的红条目"
            % (key, script, rc, dt))
        note = "红（rc=%s）" % rc
    return rc, dt, note


def main(argv=None):
    ap = argparse.ArgumentParser(description="tests\\ 判据总入口")
    ap.add_argument("--list", action="store_true", help="只打印清单，不跑")
    ap.add_argument("--inject", action="store_true",
                    help="跑红自证：给支持注入的判据喂注入了缺陷的副本，期望它们全红")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="单条判据秒数上限（默认 900；超时算红）")
    a = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    out("=" * 78)
    out("判据总入口 · 仓 %s" % ROOT)
    out("=" * 78)
    out("清单（%d 条）：" % len(CRITERIA))
    for key, script, args, desc in CRITERIA:
        out("  %-4s %-24s %-8s %s" % (key, script, " ".join(args) or "（无参）", desc))
    if a.list:
        return 0
    out("")

    if a.inject:
        return run_inject(a.timeout)

    results = []
    for key, script, args, _desc in CRITERIA:
        rc, dt, note = run_one(key, script, args, a.timeout)
        results.append((key, script, rc, dt, note))

    out("")
    out("=" * 78)
    out("汇总")
    out("=" * 78)
    for key, script, rc, dt, note in results:
        out("  [%s] %-4s %-24s rc=%-4s %6.1fs  %s"
            % ("绿" if rc == 0 else ("验不了" if rc == 2 else "红"),
               key, script, rc, dt, note))
    reds = [r for r in results if r[2] not in (0, 2)]
    skips = [r for r in results if r[2] == 2]
    greens = [r for r in results if r[2] == 0]
    out("-" * 78)
    out("判据 %d 条：绿 %d，验不了 %d，红 %d ；总用时 %.1fs"
        % (len(results), len(greens), len(skips), len(reds),
           sum(r[3] for r in results)))
    if reds:
        out("结论：【红】—— %s" % "、".join(r[0] for r in reds))
        out("（退出码 1；每条判据的红条目细节都在它自己那段原始输出里）")
        return 1
    if skips:
        out("结论：【绿·有验不了】%s —— 这 %d 条是**环境不满足**（多为没真通行证 / 夹具就位不了），"
            "不是红、也不是绿；要全绿请补环境后重跑。"
            % ("、".join(r[0] for r in skips), len(skips)))
        return 0
    out("结论：【绿】全部判据全绿。")
    return 0


def run_inject(timeout):
    """--inject：**自动探测**每条判据是否自带注入口（2026-10-19 用户拍板：废除白名单）。

    为什么废白名单：白名单要人维护 —— 漏登记就会被读成"没有注入口"，
    而多登记又会把"死判据"洗成"有注入口"。用户原话：白名单影响效率。
    现在改成**探测**：每条都喂 `--inject` 跑一遍，
      · rc ≠ 0  ⇒ 如期变红（判据是活的）
      · rc ＝ 0 且输出里声明了 `NO_INJECT:` ⇒ 如实记"无注入口（已自证声明）"
      · rc ＝ 0 且没声明 ⇒ **红**（注入缺陷后仍全绿且不说清原因 = 判据是死的）
    代价：多跑 N-8 条（约 +2 分钟），换掉一个会烂的白名单 —— 值。
    """
    out("=" * 78)
    out("跑红自证模式（自动探测，无白名单） · 注入的是**临时副本**，产品代码一个字节不动")
    out("=" * 78)
    results, declared = [], []
    for key, script, args, _desc in CRITERIA:
        path = os.path.join(HERE, script)
        if not os.path.isfile(path):
            results.append((key, script, 1, 0.0, "判据文件不在"))
            continue
        cmd = [sys.executable, "-X", "utf8", path] + list(args) + ["--inject"]
        t0 = time.monotonic()
        try:
            p = subprocess.run(cmd, cwd=ROOT, timeout=timeout,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            results.append((key, script, 1, time.monotonic() - t0, "超时"))
            continue
        dt = time.monotonic() - t0
        blob = (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0:
            # ★ rc=2 = **验不了**（无通行证 / 无窗 / 夹具不满足）。
            #   它既不是「如期变红」（那会让"没证"冒充"判据是活的"），也不是「死的」。
            #   ★ 2026-10-19 实测教训：文案一致条在无证时 rc=2，被旧逻辑记成"如期变红"——假绿。
            if p.returncode == 2 and "unrecognized arguments" not in (p.stderr or ""):
                results.append((key, script, 2, dt, "验不了（rc=2，无证/无窗，注入结果待定）"))
                continue
            # ★ 判据用 argparse 且没定义 --inject 时，会以 rc=2 + "unrecognized arguments" 退出。
            #   那是"没有注入口"，不是"如期变红"——否则一条死判据会靠 argparse 洗成活的。
            if "unrecognized arguments" in (p.stderr or ""):
                declared.append("%s（argparse 不认 --inject）" % key)
                results.append((key, script, p.returncode, dt, "无注入口（已自证声明）"))
            else:
                results.append((key, script, p.returncode, dt, "如期变红"))
            continue
        elif "NO_INJECT:" in blob:
            line = [ln for ln in blob.splitlines() if "NO_INJECT:" in ln][0]
            declared.append("%s（%s）" % (key, line.split("NO_INJECT:")[-1].strip()[:40]))
            results.append((key, script, 0, dt, "无注入口（已自证声明）"))
        else:
            _decl = _load_no_inject()
            if key in _decl:
                declared.append("%s（%s）" % (key, _decl[key][:40]))
                results.append((key, script, 0, dt, "无注入口（已声明）"))
            else:
                results.append((key, script, 0, dt, "★注入后仍全绿且未声明 ⇒ 判据是死的"))
    out("")
    out("=" * 78)
    out("注入自证汇总")
    out("=" * 78)
    for key, script, rc, dt, note in results:
        out("  [%s] %-6s %-24s %6.1fs  %s"
            % ("红" if rc != 0 else ("无口" if "无注入口" in note else "绿"),
               key, script, dt, note))
    out("-" * 78)
    red = [r for r in results if r[2] != 0 and "无注入口" not in r[4]]
    dead = [r[0] for r in results if "判据是死的" in r[4]]
    out("探测 %d 条：如期变红 %d ｜ 无注入口（已声明）%d ｜ 注入后仍绿且未声明 %d"
        % (len(results), len([r for r in results if "如期变红" in r[4]]),
           len(declared), len(dead)))
    if declared:
        out("无注入口清单（如实列，不计入通过）：%s" % "、".join(declared))
    if dead:
        out("结论：【红】—— %s 注入后仍全绿且没声明 ⇒ 这些判据是死的" % "、".join(dead))
        return 1
    out("结论：【绿】没有「死的」判据（变红的如期变红，无注入口的都如实声明）｜总用时 %.1fs"
        % sum(r[3] for r in results))
    return 0

if __name__ == "__main__":
    sys.exit(main())
