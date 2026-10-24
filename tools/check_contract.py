# -*- coding: utf-8 -*-
r"""check_contract.py —— 契约（contract/tools.json）体检。

为什么要有这个脚本：
    契约是 **agent 直接读的 API 说明**（谁在调 agent，agent 就读这份 JSON）。
    一旦它坏了，坏的方式不是「报错」，而是**静默给出一份错的契约** ——
    那比没有契约更坏。所以这道闸门独立存在，且口径很窄：

      ① 文件能读（utf-8-sig / utf-8 都认）
      ② **全层级无重复键**（json.load 默认「后者覆盖前者」，重复键会被静默吃掉）
      ③ 顶层有 tools 数组，每条工具至少 name / description / input_schema
      ④ 工具名唯一

★ 为什么退出码非 0 就是红：
    判据要么成立要么不成立，**没有「大概成立」**。
    「这次没验到」必须报红，不能当成绿 —— 这条纪律见 tests\run_all.py 的 fail-closed 约束。

用法：
    python -X utf8 tools\check_contract.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(ROOT, 'contract', 'tools.json')


def _no_dup(pairs):
    """object_pairs_hook：同名键出现两次就当场报错，而不是后者覆盖前者。"""
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError('重复键：%r' % k)
        seen[k] = v
    return seen


def main() -> int:
    problems = []
    if not os.path.exists(CONTRACT):
        print('[红] 契约文件不存在：%s' % CONTRACT)
        return 1
    try:
        with open(CONTRACT, 'rb') as f:
            raw = f.read()
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_no_dup)
    except ValueError as e:
        print('[红] 契约里有重复键：%s' % e)
        return 1
    except Exception as e:
        print('[红] 契约读不出来：%r' % e)
        return 1
    print('[OK] 契约可读，且全层级无重复键')

    tools = data.get('tools')
    if not isinstance(tools, list) or not tools:
        print('[红] 契约里没有 tools 数组（或为空）')
        return 1

    names = []
    for i, t in enumerate(tools):
        where = 'tools[%d]' % i
        if not isinstance(t, dict):
            problems.append('%s 不是对象' % where)
            continue
        for key in ('name', 'description', 'input_schema'):
            if key not in t:
                problems.append('%s 缺字段 %r' % (where, key))
        name = t.get('name')
        if name:
            names.append(name)
    if problems:
        for p in problems:
            print('[红] %s' % p)
        return 1

    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        print('[红] 工具名重复：%s' % '、'.join(dup))
        return 1

    print('[OK] %d 个工具，字段齐全，工具名唯一' % len(names))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
