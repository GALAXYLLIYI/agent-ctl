# -*- coding: utf-8 -*-
"""T1 窗1 判据：docs/AGENT接口.md §3-bis + contract/tools.json 的本单条目，逐条断言。

为什么要有这条：`docs/证据/check_consistency.py` 只查「工具名/错误码/端口/红线」，
它**看不见**本单新写的 SOP 与契约条目 —— 删掉那些条目它照样 rc=0。
所以另立这条，**并且必须能跑红**：拿 HEAD 版（git show，只读）或删掉条目的副本跑，逐条报红。

用法：
    python -X utf8 tests\check_t1_w1.py                       # 查仓库现状
    python -X utf8 tests\check_t1_w1.py --md 旧版.md --js 旧版.json ...   # 查任意副本（跑红对照用）

退出码：0 = 全绿；1 = 有条目红（逐条打印 ❌ 与原因）。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(r'<工作区>\Documents\agent-ctl')

ap = argparse.ArgumentParser()
ap.add_argument('--md', default=str(ROOT / 'docs/AGENT接口.md'))
ap.add_argument('--js', default=str(ROOT / 'contract/tools.json'))
ap.add_argument('--design', default=str(ROOT / 'docs/详细设计_v1.md'))
ap.add_argument('--baseline', default=str(ROOT / 'docs/基线摘要与开工任务书.md'))
ap.add_argument('--gate', default=str(ROOT / 'src/agentctl/gate.py'))
a = ap.parse_args()

md = Path(a.md).read_bytes().decode('utf-8')
js_txt = Path(a.js).read_bytes().decode('utf-8')
design = Path(a.design).read_bytes().decode('utf-8')
base = Path(a.baseline).read_bytes().decode('utf-8')
gate_src = Path(a.gate).read_bytes().decode('utf-8')
try:
    js = json.loads(js_txt)
except Exception as e:
    print('FATAL 契约不是合法 JSON: %s' % e)
    sys.exit(1)

fails = []
oks = []


def want(name, cond, why=''):
    (oks if cond else fails).append((name, why))


def has(hay, needle):
    return needle in hay


def absent(hay, needle):
    return needle not in hay


# 取 §3-bis 整段（从标题到下一个 '---' 之前的 §4 之前）
m = re.search(r'### 3-bis.*?(?=\n---\n\n## 4\. 最小可用流程)', md, re.S)
SOP = m.group(0) if m else ''

# ── ① SOP 本体 ────────────────────────────────────────────────
want('SOP-3bis-存在', bool(SOP), 'AGENT接口.md 里找不到 §3-bis 段')
want('SOP-唯一定义处', has(md, '唯一定义处') and has(md, '别处不要复述'),
     '缺「唯一定义处 / 别处不要复述」⇒ 第二真源风险回归')
want('SOP-坐标只有一套', has(SOP, '坐标只有一套：屏幕物理像素'),
     'SOP 未写死「只有一套」')
want('SOP-xy原样收不换算', has(SOP, '原样收，不需要任何换算'),
     'SOP 步骤② 未写「原样收，不需要任何换算」')
want('SOP-核点落在rect内', has(SOP, '确认这个点在 rect 里面') and has(SOP, 'ctl_windows --hwnd'),
     'SOP 未要求用 ctl_windows 的 rect 核点')
want('SOP-前置租约与证', has(SOP, 'ctl_lease --action acquire --actor') and has(SOP, 'code=10'),
     'SOP 缺「先拿租约 / code=10」前置')
want('SOP-禁eid与name', has(SOP, '不要用 `--eid`，也不要用 `--name`'),
     'SOP 未禁止网页场景用 --eid / --name')
want('SOP-核验code8', has(SOP, 'code=8'), 'SOP 缺「code=8 不许当成功」')
want('SOP-三步齐', has(SOP, '① 观测') and has(SOP, '② 在【同一次快照】里定位') and has(SOP, '③ 15 秒内点'),
     'SOP 的 ①②③ 步骤不全')

# ── ② capture_id TTL：文档写的秒数必须等于代码里的常量 ──────────
mm = re.search(r'CAPTURE_TTL\s*=\s*([0-9.]+)', gate_src)
code_ttl = float(mm.group(1)) if mm else None
want('TTL-代码里找得到常量', code_ttl is not None, 'gate.py 里没解析到 CAPTURE_TTL')
if code_ttl is not None:
    want('TTL-SOP写的是代码实测值', ('%g' % code_ttl) in SOP and 'CAPTURE_TTL' in SOP,
         'SOP 里的 TTL 与 gate.py CAPTURE_TTL=%g 不一致（或没给出处）' % code_ttl)
    want('TTL-契约ttl_sec等于代码值', js.get('conventions', {}).get('capture_binding', {}).get('ttl_sec') == code_ttl,
         '契约 capture_binding.ttl_sec 与 gate.py CAPTURE_TTL=%g 不一致' % code_ttl)
    want('TTL-契约带出处', 'CAPTURE_TTL' in js.get('conventions', {}).get('capture_binding', {}).get('ttl_source', ''),
         '契约 capture_binding.ttl_source 没写出处 gate.py:894')

# ── ③ 三处自相矛盾必须已被改掉 ────────────────────────────────
CONTRADICTIONS = [
    ('矛盾1-eid字段注释', '下一步 ctl_click --eid e120 就点它'),
    ('矛盾2-eid比坐标名字都强', '为什么它比坐标和名字都强'),
    ('矛盾3-§4.6bis旧流程', '--hwnd N --eid e120 --expect'),
    ('矛盾4-§7可靠度排序', '`eid` 优先 → 控件名 → 坐标'),
    ('矛盾5-§8第3条', '能用控件名就别用坐标；用坐标必须带 from_capture。'),
    ('矛盾6-§3现场换算', '现场换算'),
    ('矛盾7-§3无场景限定', '**能用控件名就别用坐标**'),
]
for nm, s in CONTRADICTIONS:
    want(nm, absent(md, s), '旧说法仍在：%s' % s)

# D1 的 roles 口径表（三条出口）必须写出来
for sym in ('t_tree', 'find()', 't_click'):
    want('D1-roles口径表-%s' % sym, has(md, sym),
         '缺 roles 口径表条目 %s ⇒ agent 不知道为什么 eid 不能跨调用用' % sym)
want('D1-不许跨调用', has(md, '不许跨调用'), '未写明「eid 不许跨调用使用」')

# ── ④ 边框条目（W1-B），必须带「接线未生效」前提句 ────────────
eb = js.get('conventions', {}).get('edge_border_hint')
want('边框-契约条目存在', isinstance(eb, dict), 'conventions.edge_border_hint 不存在')
if isinstance(eb, dict):
    want('边框-前提句', has(eb.get('_前提', ''), '若你未见边框，说明接线未生效'),
         '缺前提句「若你未见边框，说明接线未生效」⇒ 契约先于实现 = 对 agent 撒谎')
    want('边框-非传感信号', has(eb.get('_不是传感信号', ''), '无边框 ≠ 当前安全'),
         '缺「无边框 ≠ 当前安全」')
    for k in ('A', 'B', 'C', 'D', '红', '无边框'):
        want('边框-档位-%s' % k, k in eb, 'edge_border_hint 缺档位 %s' % k)
want('边框-文档交叉引用', has(md, '边框是给人看的，不是你的传感信号') and has(md, '以 `ctl_status` 为准'),
     'AGENT接口.md §0 缺「边框不是传感信号 / 以 ctl_status 为准」')

# ── ⑤ 契约其它 ────────────────────────────────────────────────
want('契约-schema_version=5', js.get('schema_version') == 5,
     'schema_version = %r（应为 5）' % js.get('schema_version'))
want('契约-三步唯一定义处指针',
     has(js.get('conventions', {}).get('_写动作三步_唯一定义处', ''), '§3-bis'),
     'conventions 缺「写动作三步唯一定义处」指针 ⇒ 第二真源风险')
disc = js.get('_agent_call_discipline', [])
want('契约-discipline第3条已改', any('网页场景' in x and '只用坐标' in x for x in disc),
     '_agent_call_discipline 第 3 条仍与新 SOP 打架')
cc = next((t for t in js.get('tools', []) if t.get('name') == 'ctl_click'), None)
want('契约-ctl_click存在', cc is not None, 'tools 里没有 ctl_click')
if cc:
    want('契约-ctl_click描述已重排', '不许跨调用使用' in cc.get('description', ''),
         'ctl_click.description 仍按旧的 eid-first 口径写')
    eid_desc = cc.get('input_schema', {}).get('properties', {}).get('eid', {}).get('description', '')
    want('契约-eid不再是首选', 'D1 修好之前不许跨调用使用' in eid_desc,
         'ctl_click.eid.description 仍标「首选」')

# ── ⑥ 码表一个字都不许动 ──────────────────────────────────────
want('码表-error_codes未动',
     sorted(int(k) for k in js.get('error_codes', {})) == [0, 3, 4, 5, 7, 8, 9, 10, 11, 12, 15],
     'error_codes 变了：%r' % sorted(js.get('error_codes', {})))
want('码表-event_codes未动',
     sorted(int(k) for k in js.get('event_codes', {}) if not k.startswith('_')) == [13],
     'event_codes 变了')

# ── ⑦ D3 全局禁令：三种「第二套坐标」写法一律不许出现在四份文件里 ──
BANNED = ['局部坐标', '投影像素', '减窗口原点', '窗口局部']
for fn, txt in (('AGENT接口.md', md), ('tools.json', js_txt), ('详细设计_v1.md', design), ('基线摘要', base)):
    for b in BANNED:
        want('D3禁令-%s-%s' % (fn, b), absent(txt, b), '出现被禁写法「%s」' % b)

# ── ⑧ 两份设计文档 ────────────────────────────────────────────
want('设计-§21.9存在', has(design, '### 21.9'), '详细设计_v1.md 没有 §21.9')
want('设计-D3记为不成立', has(design, '不成立') and has(design, 'DSH 裁定作废'),
     '§21.9 未把 D3 记成「不成立」')
want('设计-D1D2有根因', has(design, '三条出口的 `roles` 口径不同') and has(design, '深度不够'),
     '§21.9 未写出 D1/D2 的复核后根因')
want('基线-缺口条目存在', has(base, '当前未消的动作侧缺口'), '基线摘要 §1.4 未补动作侧缺口')
want('基线-指向§3-bis', has(base, '§3-bis'), '基线摘要未指向唯一定义处 §3-bis')

# ── ⑥-bis 本轮写的条目里不许写死代码行号（并行改动会让行号漂）────────
SEC219 = ''
m219 = re.search(r'### 21\.9.*?(?=\n---\n\n## 22\. )', design, re.S)
if m219:
    SEC219 = m219.group(0)
LINENO = re.compile(r'[A-Za-z_]+\.py:[0-9]+')
SCOPES = [('SOP 段', SOP), ('edge_border_hint', json.dumps(eb, ensure_ascii=False) if isinstance(eb, dict) else ''),
          ('capture_binding', json.dumps(js.get('conventions', {}).get('capture_binding', {}), ensure_ascii=False)),
          ('详细设计 §21.9', SEC219)]
for nm, blob in SCOPES:
    hits = LINENO.findall(blob)
    want('无写死行号-%s' % nm, not hits, '出现写死的代码行号 %s（并行改动必漂，改成按函数/常量名定位）' % hits)

# ── 输出 ──────────────────────────────────────────────────────
print('=' * 72)
print('T1 窗1 判据  ｜  md=%s' % a.md)
print('=' * 72)
for n, w in oks:
    print('  ✅ %s' % n)
for n, w in fails:
    print('  ❌ %s —— %s' % (n, w))
print('-' * 72)
print('绿 %d 条 / 红 %d 条' % (len(oks), len(fails)))
if fails:
    print('结论：❌ RED')
    sys.exit(1)
print('结论：✅ GREEN')
sys.exit(0)