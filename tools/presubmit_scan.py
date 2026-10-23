# -*- coding: utf-8 -*-
'''presubmit_scan.py \u2014\u2014 开源/提交前安全扫描

直接扫 **git 索引里已暂存的内容**，不扫工作区。
理由：不信任 .gitignore 写对了，只信住它真的放进去了什么。

用法：
    python tools/presubmit_scan.py            # \u626b git \u7d22引\uff08提交前）
    python tools/presubmit_scan.py --worktree # \u626b工作区\uff08\u4fee改前\u770b\u6709什么\uff09

\u9000出码：0 = 通过；1 = 有阻断项。
建议挂成 pre-commit：  .git/hooks/pre-commit
'''
import argparse, os, re, subprocess, sys

# \u4e0d能写中文\uff0c否则 Windows \u4e0b控制台编码会乱
RULES = [
    ('phone', re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
     '\u624b机号'),
    ('keylit', re.compile(r'(?i)\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{20,})\b'),
     '\u5bc6\u94a5\u5b57\u9762\u91cf'),
    ('password', re.compile(r'(?i)(password|passwd|api[_-]?key|secret)\s*[=:]\s*["\'][^"\']{6,}["\']'),
     '\u5bc6\u7801\u8d4b值'),
    ('personalpath', re.compile(r'[A-Ff]:\\{1,2}Users\\{1,2}|[Ff]:\\{1,2}<用户>'),
     '\u4e2a人绝对\u8def\u5f84'),
    ('dshcred', re.compile(r'\.dsh\\\\\.credentials|\.credentials\.yaml'),
     'DSH \u51ed\u636e\u6587\u4ef6\u5f15\u7528'),
]
MAX = 3 * 1024 * 1024


def read_index(path):
    try:
        b = subprocess.run(['git', 'show', ':' + path], capture_output=True).stdout
    except Exception:
        return None
    return None if len(b) > MAX else b.decode('utf-8', errors='replace')


def read_worktree(path):
    try:
        if os.path.getsize(path) > MAX:
            return None
        return open(path, encoding='utf-8', errors='replace').read()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--worktree', action='store_true', help='scan working dir instead of git index')
    a = ap.parse_args()

    if a.worktree:
        names = []
        for root, dirs, files in os.walk('.'):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__')]
            for f in files:
                p = os.path.join(root, f).replace(chr(92), '/').lstrip('./')
                if os.path.splitext(f)[1].lower() in ('.py', '.md', '.json', '.txt', '.bat', '.ps1', '.yml', '.yaml', '.cfg', '.ini', '.toml'):
                    names.append(p)
        get = read_worktree
        scope = 'working tree'
    else:
        out = subprocess.run(['git', 'diff', '--cached', '--name-only'],
                             capture_output=True, text=True, encoding='utf-8').stdout
        names = [n for n in out.split('\n') if n.strip()]
        get = read_index
        scope = 'git index'

    hits = {}
    scanned = 0
    for n in names:
        src = get(n)
        if src is None:
            continue
        scanned += 1
        for i, line in enumerate(src.split('\n'), 1):
            for key, pat, label in RULES:
                if pat.search(line):
                    hits.setdefault(key, {}).setdefault(n, []).append(i)

    print('scanned %d files in %s' % (scanned, scope))
    print()
    blocked = False
    for key, pat, label in RULES:
        d = hits.get(key, {})
        n = sum(len(v) for v in d.values())
        if not n:
            print('  OK   %-12s 0' % label)
            continue
        blocked = True
        print('  XXX  %-12s %d' % (label, n))
        for path, lines in sorted(d.items(), key=lambda x: -len(x[1]))[:6]:
            print('         %-44s %s' % (path, lines[:6]))
    print()
    print('BLOCKED - do not push' if blocked else 'CLEAN')
    return 1 if blocked else 0


if __name__ == '__main__':
    sys.exit(main())
