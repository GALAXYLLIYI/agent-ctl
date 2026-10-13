# -*- coding: utf-8 -*-
"""从老线真源生成 legacy_tools（打包源），不再手工维护。

T14 阶段 2。设计要点：

1. **老线是唯一真源**（`dsh-viewer-plugin\\tools`）。功能一律取老线全量版，
   打包版缺的 T2/T3/T4/T5 整代功能与 targets 检查随之补齐。
2. **净化只有一类**：把写死的本机绝对路径改成环境变量可注入。
   实测老线代码行里只有 7 处绝对路径，全部列在 `RULES` 里，逐条精确替换。
   替换不上就**报错退出** —— 静默跳过会让人以为打包干净了。
3. 阶段 1 裁决为「废」的三段（`_force_foreground` 强抢前台、`approve_target`
   第二套放行账本、auth_popup 文案差异）**不得搬进来**，那等于把老线的
   fail-closed 拆掉。以老线为准天然就不会有，无需额外排除。
4. 落一份**源→包 md5 映射台账**，随包留档。
5. ★ S3-A（2026-10-13）：生成前还有**第二道闸** —— 个人信息形态检测
   （18 位身份证校验位 / 手机号号段 / 姓名清单与槽位形态），命中即报错退出，
   与「反平行副本检查」同一形状。详见 `scan_personal_info()`。

用法：
    python -X utf8 gen_legacy.py --out <目录> --verify <现有 legacy_tools 目录>
    python -X utf8 gen_legacy.py --out <目录> --dry-run
    python -X utf8 gen_legacy.py --out legacy_tools        # 刷新仓内产物（带防误改闸）

★ D1（2026-10-12，按 G3 追加节）：覆盖前**双基线**防误改 —— 详见「防误改」一节注释。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = r"<工作区>\Documents\dsh-viewer-plugin\tools"
# ★ D1：双基线台账 —— A₀（上次生成时产物的规范化 md5）与 T₀（生成时真源的规范化 md5）记在这。
#   台账**必须进 git**（G3 边界③：台账不在仓 = 基线可被无声篡改 = 共谋）。.gitignore 有专门白名单行。
LEDGER_DEFAULT = os.path.join(ROOT, "tools", "legacy_ledger.json")

# 要搬的文件（与 pack_dist.py 的过滤口径对齐）
# ★ D18：`gate_lock.py` 必须在这里登记 —— 它是 gate.py / auth_popup.py 的**伴生模块**
#   （跨进程锁的唯一定义处），不登记的话包内缺件 ⇒ 两个脚本 import 失败。
#   注意**一般不能**加前导下划线：pack_dist.guard 会把 `_` 开头的当临时脚本丢掉。
#   确需随包发的（如 S3-A 的 `_session.py`）必须在 pack_dist.KEEP_NAMES 里显式放行，
#   且在下面注明「为什么非它不可」—— 两处缺一，登记等于没登记。
FILES = ["gate.py", "gate_lock.py", "act.py", "perceive.py", "auth_popup.py", "brake.py",
         "cdp.py", "coord.py", "option_locator.py", "selftest.py", "guardian.py",
         "whitelist.json", "click_option.py", "formfill.py", "padd2.py",
         "preflight.py", "next_question.py", "minidump_probe.py",
         "minidump_stack.py", "pe_export_lookup.py", "radio_finder.py",
         "win_list.py", "zoomgrid.py",
         # ↓ 老线 act.py 的 load_post_compat() 要读它，打包缺了就是断链。
         #   旧 legacy_tools 里**没有**这个文件 —— T14 阶段 2 顺手补上。
         "post_compat.json",
         # ↓ S3-A（2026-10-13）：三个「产品文件强依赖、但没登记」的依赖补两个。
         #   · guardian.py:263/:278 在**函数体内** `json.load(open(POLICY))`
         #     ⇒ 装出去的包里一调 guardian 就 FileNotFoundError（不在 import 时崩）。
         #   · padd2.py:50 `import _session as S`（在 conn() 里）⇒ 同理，调用时才崩。
         #   ★ `_session.py` 带前导下划线：光登记进这里不够，pack_dist.guard 会把
         #     `_` 开头的当一次性脚本丢掉 —— 配套要在 pack_dist.KEEP_NAMES 里显式放行
         #     （同 `_md5台账.md` 的既有先例）。两处缺一，这条等于没修。
         "guardian_policy.json", "_session.py",
         # ★ `case_data.json` **故意不登记**：它装的是用户用纳税操作题留下的**真人员数据**，
         #   用户裁「数据留着（GUI 层操作的现成靶子）」但「包里的名字不许留」。
         #   它不进包 ⇒ 要在包里跑 padd2，得自备一份脱敏样例。
         #   兜底是 `scan_personal_info()`：真名/身份证/手机号形态进包即报错退出。
         "probe.py", "probe2.py", "probe3.py", "probe4.py", "probe5.py", "probe6.py",
           # ↓ T17 A3-(a)（2026-10-12 用户拍板）：动作可信闭环三件。
           #   它们是【新线】工具，但用户选了"随 gen_legacy 走"这条单一机制 ——
           #   于是必须登记在这里，否则打包时取不到、装出去的那份里没有。
           #   ★ 配套改动（缺一不生效）：core.py::_verify_py() 要加 legacy_tools\ 候选，
           #     因为它们会落在 legacy_tools\ 而不是 tools\。
           "验动作.py", "闭环.py", "bench.py"]

# ── 净化规则：逐条精确替换，不做模糊匹配 ───────────────────────────────
# 每条 = (文件名, 旧文本, 新文本, 为什么)
APP_DIR_BLOCK = (
    "# ★ 默认写进 %LOCALAPPDATA%\\AgentCtl\\shots，不写死本机路径。\n"
    "#   平台进程会用 AGENTCTL_SHOTS_DIR（或直接注入 SHOTDIR）指过来，\n"
    "#   这里只是「独立跑这个脚本时」的兜底，指向同一个地方。\n"
    "_APP_DIR = os.environ.get(\"AGENTCTL_APP_DIR\") or os.path.join(\n"
    "    os.environ.get(\"LOCALAPPDATA\", os.path.expanduser(\"~\")), \"AgentCtl\")\n"
)

RULES = [
    ("perceive.py",
     'SHOTDIR = r"<工作区>\\Documents\\agent-manual\\_监控\\shots"',
     APP_DIR_BLOCK + 'SHOTDIR = os.environ.get("AGENTCTL_SHOTS_DIR") or os.path.join(_APP_DIR, "shots")',
     "截图目录：不写死本机路径"),
    ("perceive.py",
     'DIFF_CACHE_DIR = r"<工作区>\\Documents\\agent-manual\\_监控\\perceive_cache"',
     'DIFF_CACHE_DIR = os.environ.get("AGENTCTL_CACHE_DIR") or os.path.join(_APP_DIR, "perceive_cache")',
     "树差量缓存目录：同上"),
    ("selftest.py",
     'SHOTS = r"<工作区>\\Documents\\agent-manual\\_监控\\shots"',
     APP_DIR_BLOCK + 'SHOTS = os.environ.get("AGENTCTL_SHOTS_DIR") or os.path.join(_APP_DIR, "shots")',
     "自测读截图目录：同上"),
    ("cdp.py",
     '"--user-data-dir=F:\\\\<用户>\\\\Documents\\\\_edge_cdp"',
     '"--user-data-dir=" + os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentCtl", "_edge_cdp")',
     "浏览器 profile：不写死本机路径"),
    ("coord.py",
     'import sys; sys.path.insert(0, r"<工作区>\\Documents\\dsh-viewer-plugin\\tools")',
     'import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))',
     "自插老线路径：包内要指向自己"),
    ("option_locator.py",
     'r"<工作区>\\Documents\\agent-manual\\_监控\\shots\\*_win2754900.png"',
     'os.path.join(os.environ.get("AGENTCTL_SHOTS_DIR", ""), "*_win2754900.png")',
     "选图目录：同上"),
    ("selftest.py",
     'os.path.join(r"<工作区>\\Documents\\agent-manual", f)',
     'os.path.join(os.environ.get("AGENTCTL_MANUAL_DIR", ""), f)',
     "手册目录（铁律 10 的页脚自测）：不写死本机路径；打包后没有手册就整段跳过"),
]


def md5(b):
    return hashlib.md5(b).hexdigest()


def apply_rules(name, data):
    """对该文件应用净化规则。返回 (新 bytes, [(规则说明, 是否命中)])。"""
    hit = []
    try:
        txt = data.decode("utf-8")
    except UnicodeDecodeError:
        return data, [(r[3], "跳过（非 utf-8）") for r in RULES if r[0] == name]
    for fn, old, new, why in RULES:
        if fn != name:
            continue
        if old in txt:
            txt = txt.replace(old, new)
            hit.append((why, "命中"))
        else:
            # 已净化过也算通过 —— 生成器要能对老线重跑而不报错
            if new.split("\n")[-1] in txt or "AGENTCTL" in txt:
                hit.append((why, "已是净化态（跳过）"))
            else:
                hit.append((why, "★未命中★"))
    data = txt.encode("utf-8")

    # 净化可能把硬编码串换成 os.environ.get(...) —— 那就凭空多出一个 os 用法。
    # 真源里 os 只在别的函数内 import 过（本函数用不到），所以这里就地补齐，
    # 否则包一装上就跑不了（NameError）。只补 os/subprocess 这两个净化会用到的。
    data, added = fix_missing_imports(data)
    if added:
        hit.append(("净化引入的 os/subprocess 用法：就地补 import %s" % "、".join(added), "已补"))
    return data, hit


def fix_missing_imports(data):
    """函数内用到 os/subprocess 却没导入的，就地补一行 import。

    用 AST 定位「哪个函数用了、缺什么」，再按行号插回源码 —— 不用正则猜作用域。
    找不到需要补的（绝大多数文件）时**原样返回，一个字节都不动**。
    """
    import ast

    try:
        src = data.decode("utf-8")
        tree = ast.parse(src)
    except (UnicodeDecodeError, SyntaxError):
        return data, []

    top_imported = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            top_imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            top_imported.add(n.module.split(".")[0])

    L = src.splitlines()
    inserts = []   # (行号, 需要补的模块名)
    added = []
    for n in tree.body:
        if not isinstance(n, ast.FunctionDef):
            continue
        # ★ 必须**两遍**：先把本函数里已有的 import 收齐，再看缺什么。
        #   一遍里同时 add/discard 是错的 —— ast.walk 是广度优先，函数体里
        #   import 的层级比深层表达式里的 Name 浅，于是 discard 先发生、add 后发生，
        #   结果「已导入」的名字又被当成「缺失」补一遍（2026-10-09 真踩过：
        #   cdp.py 两个函数各多出一组重复 import，pyflakes 报 redefinition）。
        already = set()
        for sub in ast.walk(n):
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                for a in sub.names:
                    already.add((a.asname or a.name).split(".")[0])
        want = set()
        for sub in ast.walk(n):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id in ("os", "subprocess") and sub.id not in top_imported:
                    want.add(sub.id)
        want -= already          # 本函数已经 import 过的，不再补
        if not want:
            continue
        # 插在**文档字符串之后**（否则 __doc__ 会变成 None），缩进与该函数一致
        first = n.body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            rest = n.body[1:]
            if not rest:
                continue          # 只有 docstring，用不到 os/subprocess，判据本身也矛盾
            first = rest[0]
        ln = getattr(first, "lineno", n.lineno) - 1
        indent = " " * (len(L[ln]) - len(L[ln].lstrip())) if ln < len(L) else "    "
        inserts.append((ln, indent, sorted(want)))
        added.extend(sorted(want))

    if not inserts:
        return data, []
    for ln, indent, names in sorted(inserts, reverse=True):
        for m in names:
            L.insert(ln, "%simport %s" % (indent, m))
    return ("\n".join(L) + ("\n" if src.endswith("\n") else "")).encode("utf-8"), sorted(set(added))


def check_parallel_copies():
    """反平行副本检查：`tools\\` 下若出现与 FILES 同名的文件，直接报错退出。

    ★ C3（2026-10-12）。为什么这是**主防线**而不只是顺手清理：
      1. `.gitignore` 里是 `tools/*` + 少量 `!` 白名单，所以白名单一撤，
         gen_legacy 重建出来的文件会在 `git status` 里**隐形** —— 改动不再被人看见。
      2. 历史上 `tools\\` 长期留着 `验动作.py` / `闭环.py` 两份 **tracked** 副本，
         那就是**第二真源**，而第二真源正是这个项目反复出病的根。
      3. 撤白名单只是拆引信；只要 tools\\ 下还能再出现 FILES 同名文件，
         「顺手改一份、忘了回写真源」的老病就会复发。
    ⇒ 所以生成前先扫一遍，发现就停下 —— 宁可不发包，也不要安静地生成第二份。
    """
    hits = []
    for f in FILES:
        p = os.path.join(ROOT, "tools", f)
        if os.path.exists(p):
            hits.append(p)
    return hits


def _report_parallel_copies(hits, src_dir=SRC):
    print()
    print("[X] 反平行副本检查：tools\\ 下存在与 FILES 同名的文件 —— 拒绝生成。")
    for p in hits:
        print("      - %s" % p)
    print("    这些名字的真源是 %s，产物应落在 legacy_tools\\（生成物，不进仓）。" % src_dir)
    print("    在 tools\\ 再留一份 = 第二真源。请删掉这些文件后重跑。")


# ───────────────── 个人信息形态检测（S3-A，2026-10-13）─────────────────
# ★ 为什么要有这一类闸：打包侧 pack_dist 已经有隐私扫描，但它**只扫最终那一份字节**；
#   而 gen_legacy 是「老线真源 → 打包源」的唯一搬运口。真源里的真名/证件号一旦被
#   净化规则搬进来，包里就有；等 pack_dist 拦，用户看到的是「打包失败」而不是
#   「哪一行有真名」。⇒ 在**搬运口**就报错退出，定位到 文件:行。
# ★ 形状与 check_parallel_copies 一致：命中即拒绝生成、非零退出，没有「警告后照发」。

# 18 位身份证：GB 11643-1999 校验位（权重 7 9 10 5 8 4 2 1 6 3 7 9 10 5 8 4 2）
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"
# 大陆手机号号段：第二位 3-9（130-199 是已放号的段），与 pack_dist.PAT_MOBILE 同一口径。
PAT_MOBILE_SHAPE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
PAT_IDCARD_SHAPE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

# 显式姓名清单：本单实测命中的两个真名（它们会随发布包发出去，用户裁「换占位」）。
# 形态检测抓的是**槽位**，清单抓的是**任何位置** —— 两者互补，别只留一个。
PERSON_NAMES = ["马歇尔", "汤晴"]

# 高信号「个人信息槽位」：命令行实参、调用实参、JSON 字段、标签行。
# 只在这些位置取候选，再用姓氏形态筛 —— 不对全文做 2-3 字中文扫描（实测噪音上千）。
NAME_SLOTS = [
    ("--only", re.compile(r"--only\s+([^\s\n]+)")),
    ("--person", re.compile(r"--person\s+([^\s\n]+)")),
    ("pick_text=", re.compile(r"pick_text\s*=\s*[\"']([^\"']+)")),
    ("name字段", re.compile(r"[\"']name[\"']\s*:\s*[\"']([^\"']+)")),
    ("姓名", re.compile(r"姓名\s*[:：]?\s*([^\s\n\"']+)")),
]
# 常见姓氏：百家姓（康熙本）去重成单字集合。
# ★ 为什么用全量而不是「前 120」：第一版只收了前 ~120，实测当场漏掉「汤」——
#   padd2.py:9 的 `--only 汤晴` 只被「显式清单」那条判据抓到，槽位形态那条漏了。
#   姓氏表不完整 = 判据有洞，而洞的方向正好是「漏检真名」。
SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻"
    "柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤"
    "滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄"
    "米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭"
    "梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓"
    "郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊惠甄家封芮羿储靳汲邴糜松井段"
    "富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹"
    "束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党"
    "翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎"
    "充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃"
    "利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后"
    "荆红游竺权逯盖益桓公"
)


def _mask(s):
    """报告里不回显原值：只留够定位的形状。日志本身不能成为新的泄漏点。"""
    s = str(s)
    return "%s*%s" % (s[:1], s[-2:]) if len(s) >= 3 else "*"


def _idcard_reason(tok):
    """返回 None = 是真身份证；否则返回「为什么不算」的短理由。"""
    try:
        datetime.date(int(tok[6:10]), int(tok[10:12]), int(tok[12:14]))
    except ValueError:
        return "出生日期段不是合法日期"
    tot = sum(int(tok[i]) * _ID_WEIGHTS[i] for i in range(17))
    if _ID_CHECK[tot % 11] != tok[17].upper():
        return "校验位不符（GB 11643）"
    return None


def scan_personal_info(src_dir=SRC, files=None):
    """扫 FILES 各文件的**真源字节**，返回命中列表（空 = 干净）。

    只扫源字节、不扫净化后的字节：净化只替换绝对路径，不会引入个人信息；
    而真源里的真名本来就该清掉（包是它的下游），源头干净才是治本。
    文件不存在 ⇒ 跳过（缺失由 main() 的 problems 单独报，不在这里重复）。
    """
    hits = []
    for f in (files if files is not None else FILES):
        p = os.path.join(src_dir, f)
        if not os.path.exists(p):
            continue
        try:
            txt = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue        # 非 utf-8 读不了的不猜（判据只对文本成立）
        for i, line in enumerate(txt.splitlines(), 1):
            for m in PAT_IDCARD_SHAPE.finditer(line):
                why = _idcard_reason(m.group())
                hits.append({"file": f, "line": i, "kind": "身份证号形态",
                             "mask": _mask(m.group()),
                             "why": "" if why is None else why + "（按形态拦，不放行）"})
            for m in PAT_MOBILE_SHAPE.finditer(line):
                hits.append({"file": f, "line": i, "kind": "手机号号段 1[3-9]xxxxxxxxx",
                             "mask": _mask(m.group()), "why": ""})
            for nm in PERSON_NAMES:
                if nm in line:
                    hits.append({"file": f, "line": i, "kind": "姓名（显式清单）",
                                 "mask": _mask(nm), "why": ""})
            for label, rx in NAME_SLOTS:
                for m in rx.finditer(line):
                    v = m.group(1).strip()
                    if _name_shape(v):
                        hits.append({"file": f, "line": i,
                                     "kind": "姓名（%s 槽位形态）" % label,
                                     "mask": _mask(v), "why": ""})
    return hits


# ★ 槽位值可能带「同名消歧后缀」：`pick_text="马歇尔[0001]"` 的真正姓名是前半段。
#   漏掉这一步的话，formfill 那种写法只能靠显式清单抓到（清单不可能穷举）。
_SUFFIX = re.compile(r"\[[^\]]*\]$")


def _name_shape(v):
    """槽位值去掉消歧后缀后，是否是「姓 + 1-3 个汉字」的姓名形态。"""
    v = _SUFFIX.sub("", v.strip())
    # 长度放到 4：复姓（欧阳/司马/上官…）+ 双名 = 4 个字，第一版只收 2-3
    # ⇒ 「欧阳明日」这种漏检（实测判据脚本当场抓到）。
    return (2 <= len(v) <= 4 and v[0] in SURNAMES
            and all("\u4e00" <= c <= "\u9fa5" for c in v[1:]))


def _report_personal_info(hits, src_dir=SRC):
    print()
    print("[X] 个人信息形态检测：%d 处 —— 拒绝生成。" % len(hits))
    for h in hits:
        print("      - %s:%d  %s  %s%s"
              % (h["file"], h["line"], h["kind"], h["mask"],
                 ("（%s）" % h["why"]) if h["why"] else ""))
    print("    命中的是真源 %s 里的**源**字节 —— 净化规则不会删它们，照发就是发出去。" % src_dir)
    print("    处置：把真名/证件号/手机号换成占位（如 张*[0001] / 某某 / 138****0001），")
    print("          或把该文件移出 FILES（不进包的东西不该在清单里）。")
    print("    ★ 数据本身可以留在真源里当地基（GUI 练习靶子），但**包里不许带真名**。")


# ───────────────────────── 防误改（D1，G3 追加节 2026-10-12） ─────────────────────────
# 要保护的不变量是「**产物自上次生成后未被碰过**」，不是「产物 == 真源 + 规则」。
# 后者是"反算"，是错的 —— 「真源更新、产物待刷」恰恰就是跑 gen_legacy 的合法原因，
# 按反算判别会**每次合法重生成都误报**（顾问原话）。
#
# 双基线：A₀ = 上次写产物时的规范化 md5（台账），T₀ = 生成时真源的规范化 md5（台账补记）。
# 判别表：
#   A == A₀            ⇒ 放行（不误伤"真源已更新、产物待刷"）
#   A ≠ A₀, fresh == A ⇒ **静默放行**（有人把产物改成与新生成一致 —— 结果已正确；
#                        这一格最容易漏测，负例②专测它）
#   A ≠ A₀, fresh ≠ A  ⇒ 真手改 ⇒ 处置（不是"拒绝 vs 静默"二选一，是分级 + 两颗牙）：
#       默认（无人值守）= 时间戳备份 + 重生成 + 漂移报告落盘 + 非零退出码
#                        —— 纯静默不合格；两颗牙 = 备份、报告+非零
#       --strict（有人值守）= 拒绝执行。"拒绝"的唯一增量价值是强制人看，
#                        而"退出码 + 报告"等价实现它，所以 strict 只留给人值守
#       --force（逃逸口）= 备份 + 覆盖，打印"你正在覆盖 N 个手改过的文件"
# 边界五条（如实列出，不藏）：
#   ① 台账缺失 ⇒ 退化成 (d)（备份+重生成+报告+非零），不崩
#   ② 双侧同时变、且手改结果 ≠ 新生成结果 ⇒ 机械判不了 ⇒ 只能"保不丢"（备份），裁决归人
#   ③ 台账共谋（台账本身被改）⇒ 台账必须进 git（见 LEDGER_DEFAULT 注释）
#   ④ 换行等非语义触碰 ⇒ 先规范化再比（_norm），不然误报
#   ⑤ 生成器规则自变（RULES 改了）⇒ 不拦：此时 A==A₀（产物没被碰），天然放行
# ★ 弃掉的错主意（写进注释，免得后人再提）：pre-commit 方案不可行 —— 三次事故全是
#   "生成时产物被重刷"，产物目录根本不走 commit 路径，pre-commit 一次都拦不到。
#   防线必须装在**生成器覆盖之前**（就是这里）。


def _norm(b):
    """规范化（边界④）：去 BOM、CRLF/CR→LF。只用于比较，不改写盘内容。"""
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def md5n(b):
    """规范化形态的 md5 —— 漂移判别的基线。"""
    return md5(_norm(b))


def load_ledger(path):
    """读台账。缺失/损坏 ⇒ None（边界①：调用方退化成 (d)，不崩）。

    ★ 台账**按产物目录分键**（dirs）：同一份 FILES 可能被写到不同 out 目录
      （dist\\legacy_gen 对照用、legacy_tools 刷新用、沙箱测试用）。
      若只记"最后一次"，就会出现：真源更新后先跑了对照目录、再刷 legacy_tools
      —— 产物（旧）≠ 台账（新）≠ ？ ⇒ 把**合法刷新误报成手改**。分目录记账就没这个坑。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict) and isinstance(d.get("dirs"), dict):
            return d
    except Exception:
        pass
    return None


def ledger_key(path):
    """台账键 = 相对仓库根的路径。★台账要进 git —— 键/值里不能出现 `F:\\<用户名>\\`
    这种本机绝对路径（R2 (c) 类泄漏，台账自己绝不能成为泄漏点）。
    仓库外的目录（沙箱等）relpath 会带 ../，跨盘符失败才退回绝对路径。"""
    ap = os.path.abspath(path)
    try:
        return os.path.relpath(ap, ROOT).replace("\\", "/")
    except ValueError:
        return ap


def dir_entries(ledger, out_dir):
    """取该产物目录的基线条目（可能为空 dict）。"""
    return (ledger or {}).get("dirs", {}).get(ledger_key(out_dir), {}).get("files", {})


def guard_dir(out_dir, made, src_dir, ledger):
    """覆盖前逐文件双基线判别。返回漂移记录列表（空表 = 没有真手改）。

    只针对 out_dir 里**已存在**的文件 —— 不存在 = 首次生成，没有可保护的基线，放行
    （工单停单条款点名的"首次生成不得误伤"）。
    """
    drifts = []
    ents = dir_entries(ledger, out_dir)
    for f in sorted(made):
        op = os.path.join(out_dir, f)
        if not os.path.exists(op):
            continue
        cur = open(op, "rb").read()
        a = md5n(cur)
        ent = ents.get(f)
        if ledger is None or not isinstance(ent, dict) or not ent.get("A0"):
            drifts.append({"file": f, "A": a, "A0": None,
                           "why": "台账缺失或该目录/文件无基线（边界① ⇒ 退化成 (d)）"})
            continue
        if a == ent["A0"]:
            continue                     # A == A₀ ⇒ 放行
        if md5n(made[f]) == a:
            continue                     # A≠A₀ 但 fresh==A ⇒ 静默放行（负例②那一格）
        drifts.append({"file": f, "A": a, "A0": ent["A0"],
                       "why": "产物自上次生成被手改（A≠A₀ 且 fresh≠A）"})
    return drifts


def write_drift_report(out_dir, src_dir, drifts, mode, ts):
    """漂移报告落盘（两颗牙之一）。返回报告路径。"""
    rep_dir = os.path.join(ROOT, "work")
    os.makedirs(rep_dir, exist_ok=True)
    p = os.path.join(rep_dir, "_legacy_drift_%s.md" % ts)
    L = ["# legacy 产物漂移报告（gen_legacy 防误改层）", "",
         "- 时间：%s｜模式：%s" % (ts, mode),
         "- 产物目录：%s" % os.path.abspath(out_dir),
         "",
         "| 文件 | 产物 md5(A) | 台账基线(A₀) | 判定 | 该去哪改（真源） |",
         "|---|---|---|---|---|"]
    for d in drifts:
        L.append("| `%s` | `%s` | `%s` | %s | `%s` |"
                 % (d["file"], d["A"][:16], (d["A0"] or "N/A")[:16], d["why"],
                    os.path.join(src_dir, d["file"])))
    L += ["",
          "★ 裁决归人（边界②）：手改若值得保留，**搬回真源**再重生成 ——",
          "  改产物没有用，下次生成会把它洗掉（这正是三次事故的成因）。",
          "  本报告与 .bak_<ts> 备份保证\"保不丢\"。"]
    open(p, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return p


def set_readonly(dir_path, names):
    """第二层：生成**收尾**统一设只读。必须由生成器来做 —— 靠人事先设没用，
    生成器写文件会把属性洗掉（顾问点明）。Windows 只读是劝告性的，
    但拦得住\"顺手改产物\"的多数路径；真正的硬闸是第一层（覆盖前判别）。"""
    for f in names:
        try:
            os.chmod(os.path.join(dir_path, f), stat.S_IREAD)
        except OSError:
            pass


def clear_readonly(path):
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass


def verify_working_tree(ledger_path=None, art_dir=None):
    """D1 第三层：打包前的**提示性**校验（pack_dist 调用）。

    拿仓内 legacy_tools 与台账 A₀ 比，返回 (漂移数, 明细)。
    ★ 只警告、不拦：打包源是现场从真源生成的，仓内产物状态不影响包内容；
      而「真源更新了、仓内产物没刷」是合法状态（G3），拦 = 误伤。
    台账缺失 ⇒ 返回 (-1, [说明])，提示先刷新生成基线。
    """
    ledger_path = ledger_path or LEDGER_DEFAULT
    art_dir = art_dir or os.path.join(ROOT, "legacy_tools")
    ledger = load_ledger(ledger_path)
    if ledger is None:
        return -1, ["台账缺失或损坏（%s）—— 无法校验；先跑 gen_legacy --out legacy_tools 建立基线"
                    % ledger_path]
    ents = dir_entries(ledger, art_dir)
    if not ents:
        return -1, ["台账里没有 %s 的基线 —— 先跑 gen_legacy --out legacy_tools 建立" % art_dir]
    out = []
    for f, ent in sorted(ents.items()):
        p = os.path.join(art_dir, f)
        if not os.path.exists(p):
            out.append("%s：台账里有、仓内 legacy_tools 缺这个文件" % f)
        elif md5n(open(p, "rb").read()) != ent.get("A0"):
            out.append("%s：产物与台账 A₀ 不一致（手改过，或真源更新后产物未刷）" % f)
    return len(out), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "legacy_gen"))
    ap.add_argument("--verify", help="与该目录逐字节对比（自证模式）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不落盘")
    # ↓ D1（G3）：双基线防误改参数
    ap.add_argument("--src", default=SRC, help="真源目录（默认老线真源；沙箱测试可覆盖）")
    ap.add_argument("--ledger", default=LEDGER_DEFAULT,
                    help="双基线台账 JSON（必须进 git，G3 边界③）")
    ap.add_argument("--force", action="store_true",
                    help="逃逸口：手改产物照样覆盖（仍先做时间戳备份）")
    ap.add_argument("--strict", action="store_true",
                    help="有人值守：发现漂移拒绝执行、一字节不写（默认=无人值守处置）")
    a = ap.parse_args()

    src_dir = a.src
    print("源（老线真源）: %s" % src_dir)
    print("输出          : %s" % a.out)
    print("台账          : %s" % a.ledger)
    print()

    # ★ C3：反平行副本检查（生成前第一道闸）
    dup = check_parallel_copies()
    if dup:
        _report_parallel_copies(dup, src_dir)
        return 1

    # ★ S3-A：个人信息形态检测（第二道闸，与上面同一形状：命中即停、非零退出）
    pi = scan_personal_info(src_dir)
    if pi:
        _report_personal_info(pi, src_dir)
        return 1

    ledger_md = ["# 源→包 md5 映射台账", "",
                 "- 生成器：`tools\\gen_legacy.py`（老线真源 → 打包源，自动生成，勿手工改）",
                 "- 净化只做一件事：绝对路径 → 环境变量可注入", "",
                 "| 文件 | 源 md5 | 源字节 | 包 md5 | 包字节 | 净化 |",
                 "|---|---|---:|---|---:|---|"]
    problems = []
    made = {}
    srchash = {}   # D1：T₀ = 生成时真源的规范化 md5（G3 要求补记的就是这个）

    for f in FILES:
        src = os.path.join(src_dir, f)
        if not os.path.exists(src):
            print("  [缺失] %s（老线没有，跳过）" % f)
            problems.append(f)
            continue
        raw = open(src, "rb").read()
        data, hits = apply_rules(f, raw)
        desc = "；".join("%s=%s" % (w, s) for w, s in hits) if hits else "无规则"
        for w, s in hits:
            if s == "★未命中★":
                problems.append("%s: %s" % (f, w))
        ledger_md.append("| `%s` | `%s` | %d | `%s` | %d | %s |"
                         % (f, md5(raw), len(raw), md5(data), len(data), desc))
        made[f] = data
        srchash[f] = md5n(raw)
        flag = "★未命中★" in desc and "无规则" not in desc
        print("  %s %-20s %6d → %6d B  %s"
              % ("[X]" if flag else "[OK]", f, len(raw), len(data), desc))

    if problems:
        print()
        print("[X] 有 %d 处问题：" % len(problems))
        for p in problems:
            print("    - %s" % p)

    # ── D1 主防线：覆盖前双基线判别（G3 追加节）──
    ts = time.strftime("%Y%m%d_%H%M%S")
    ledger = load_ledger(a.ledger)
    drifts = guard_dir(a.out, made, src_dir, ledger)
    if drifts:
        mode = ("strict(拒绝)" if a.strict else
                "force(覆盖)" if a.force else "无人值守(备份+重生成+报告+非零)")
        print()
        print("⚠ [D1] %d 个文件自上次生成后被手改过（处置：%s）：" % (len(drifts), mode))
        for d in drifts:
            print("    - %-20s A=%s A₀=%s  %s"
                  % (d["file"], d["A"][:12], (d["A0"] or "N/A")[:12], d["why"]))
            print("      手改若值得保留，去改真源再重生成：%s" % os.path.join(src_dir, d["file"]))
        if a.strict and not a.dry_run:
            rep = write_drift_report(a.out, src_dir, drifts, "strict", ts)
            print("[X] strict：拒绝执行，未写任何文件。漂移报告：%s" % rep)
            return 3
    if a.dry_run:
        print("(dry-run：不落盘)")
        return 1 if (problems or drifts) else 0

    os.makedirs(a.out, exist_ok=True)
    drift_names = {d["file"] for d in drifts}
    for f, data in made.items():
        p = os.path.join(a.out, f)
        if os.path.exists(p):
            clear_readonly(p)
            if f in drift_names:
                shutil.copy2(p, p + ".bak_" + ts)   # 牙一：时间戳备份（.bak* 已被 gitignore）
        open(p, "wb").write(data)
    led = os.path.join(a.out, "_md5台账.md")
    if os.path.exists(led):
        clear_readonly(led)   # 上一轮收尾把它也设了只读 —— 不先清写不进去（2026-10-12 沙箱实测踩过）
    open(led, "w", encoding="utf-8").write("\n".join(ledger_md) + "\n")

    # D1 第二层：收尾统一设只读（生成器自己写会洗掉属性，所以必须由它收尾来设）
    set_readonly(a.out, list(made.keys()) + ["_md5台账.md"])

    # 台账更新：**只动本目录的条目**（dirs 按产物目录分键，别的目录不受牵连）；
    # A₀ = 本次写盘内容的规范化 md5；T₀ = 生成时真源的规范化 md5。
    # 顺手清掉已不存在的目录条目（沙箱/临时目录用完即蒸发，不积垃圾）。
    if not isinstance(ledger, dict) or not isinstance(ledger.get("dirs"), dict):
        ledger = {"_note": "gen_legacy 双基线台账：dirs[产物目录(相对仓库根)].files[文件] = "
                           "{A0: 上次写产物时的规范化md5(去BOM/换行统一), T0: 生成时真源的规范化md5}。"
                           "★必须进 git（G3 边界③）；手改 A0 等于共谋 —— 防线会失效，"
                           "改台账唯一合法途径是跑一次生成。键与 src 用相对路径：本文件进仓，"
                           "不得携带本机绝对路径（R2 (c) 类）。",
                  "dirs": {}}
    ledger["dirs"] = {k: v for k, v in ledger["dirs"].items()
                      if os.path.isdir(os.path.join(ROOT, k) if not os.path.isabs(k) else k)}
    ledger["dirs"][ledger_key(a.out)] = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "src": ledger_key(src_dir),
        "files": {f: {"A0": md5n(data), "T0": srchash.get(f), "bytes_out": len(data)}
                  for f, data in made.items()},
    }
    with open(a.ledger, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=1)

    if drifts:
        rep = write_drift_report(a.out, src_dir, drifts,
                                 ("force" if a.force else "无人值守"), ts)
        print("⚠ [D1] 漂移报告已落盘：%s" % rep)
        if not a.force:
            print("⚠ [D1] 已备份并重生成，但**非零退出**提醒人复核（G3：报告+退出码=强制人看）。")

    print()
    print("已写出 %d 个文件 + md5 台账 → %s（收尾已设只读；双基线台账 → %s）"
          % (len(made), a.out, a.ledger))

    # 自证模式：与现有手工维护版逐字节对比
    if a.verify:
        print()
        print("=" * 70)
        print("自证：生成结果 vs 现有 %s" % a.verify)
        print("=" * 70)
        same = diff = only_new = only_old = 0
        for f, data in sorted(made.items()):
            op = os.path.join(a.verify, f)
            if not os.path.exists(op):
                print("  [新增] %-20s 现有目录里没有" % f)
                only_new += 1
                continue
            od = open(op, "rb").read()
            if od == data:
                same += 1
            else:
                diff += 1
                print("  [不同] %-20s 现有 %d B / 生成 %d B  md5 %s vs %s"
                      % (f, len(od), len(data), md5(od)[:12], md5(data)[:12]))
        for f in sorted(os.listdir(a.verify)):
            fp = os.path.join(a.verify, f)
            if os.path.isfile(fp) and f not in made and not f.startswith("_"):
                print("  [仅现有] %-18s 本次未生成" % f)
                only_old += 1
        print()
        print("  逐字节相同 %d / 不同 %d / 仅生成 %d / 仅现有 %d" % (same, diff, only_new, only_old))

    # D1：漂移未走 --force ⇒ 非零退出（牙二：报告 + 非零 = 等价于"强制人看"）
    return 1 if (problems or (drifts and not a.force)) else 0


def main_for_pack(out_dir):
    """给 pack_dist.py 调用的入口：生成到 out_dir，有未命中规则就返回非 0。

    与 main() 的区别只是不做 verify（打包时没有"现有手工版"可比），
    但**未命中规则一律当作失败** —— 净化规则失效还照发包，等于把本机路径发出去。

    ★ D1：本入口**不碰双基线台账、不设只读** —— out_dir 是 dist\\_legacy_for_pack
      临时区（pack_dist 每次先 rmtree 再重建，设只读会挡删除；临时区也没人"顺手改"，
      没有可保护的基线）。要保护的是**仓内 legacy_tools\\**，走 main() 的 --out 路径。
    """
    print("从老线真源生成 legacy 打包源：%s" % SRC)
    # ★ C3：同一条检查，打包路径上也要有（pack_dist 走的是这个入口）
    dup = check_parallel_copies()
    if dup:
        _report_parallel_copies(dup)
        return 1
    # ★ S3-A：同一条个人信息形态检测，打包路径上也必须有 ——
    #   pack_dist 自己那道隐私闸只扫最终字节，这里能把「哪一行」指出来。
    pi = scan_personal_info(SRC)
    if pi:
        _report_personal_info(pi, SRC)
        return 1
    os.makedirs(out_dir, exist_ok=True)
    ledger = ["# 源→包 md5 映射台账", "",
              "- 生成器：`tools\\gen_legacy.py`（老线真源 → 打包源，自动生成，勿手工改）",
              "- 净化只做一件事：绝对路径 → 环境变量可注入", "",
              "| 文件 | 源 md5 | 源字节 | 包 md5 | 包字节 | 净化 |",
              "|---|---|---:|---|---:|---|"]
    problems = []
    n = 0
    for f in FILES:
        src = os.path.join(SRC, f)
        if not os.path.exists(src):
            problems.append("%s：老线没有这个文件" % f)
            continue
        raw = open(src, "rb").read()
        data, hits = apply_rules(f, raw)
        desc = "；".join("%s=%s" % (w, s) for w, s in hits) if hits else "无规则"
        for w, s in hits:
            if s == "★未命中★":
                problems.append("%s：%s 未命中" % (f, w))
        open(os.path.join(out_dir, f), "wb").write(data)
        ledger.append("| `%s` | `%s` | %d | `%s` | %d | %s |"
                      % (f, md5(raw), len(raw), md5(data), len(data), desc))
        n += 1
    open(os.path.join(out_dir, "_md5台账.md"), "w", encoding="utf-8").write("\n".join(ledger) + "\n")
    print("  生成 %d 个文件 + md5 台账" % n)
    if problems:
        print("  [X] %d 处问题：" % len(problems))
        for p in problems:
            print("      - %s" % p)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
