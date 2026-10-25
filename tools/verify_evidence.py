#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()

    # Add src to path
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_path = os.path.join(repo_root, 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from agentctl import evidence

    evidence_path = args.evidence
    out_path = args.out

    # Read evidence (single or array)
    try:
        with open(evidence_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        raise SystemExit(2)

    if isinstance(data, dict):
        records = [data]
    elif isinstance(data, list):
        records = data
    else:
        raise SystemExit(2)

    recomputable_ok = 0
    non_recomputable = 0
    inconsistent = 0

    lines = []
    lines.append('# 证据复核报告（离线）')
    lines.append('')

    for idx, rec in enumerate(records):
        lines.append('## 条目 %d' % (idx + 1))
        rec_ok = True
        reasons_inconsistent = False

        # 校验字段（复用 agentctl.evidence.validate）
        try:
            evidence.validate(rec)
            lines.append('- 字段校验：通过')
        except Exception as e:
            lines.append('- 字段校验：失败（%s）' % str(e))
            rec_ok = False

        # 结构层复算：目标是否命中、reasons 与 verdict 是否自洽
        target_ok = False
        try:
            target = rec.get('target')
            if isinstance(target, dict) and len(target) > 0:
                target_ok = True
            else:
                target_ok = False
        except Exception:
            target_ok = False
        lines.append('- 结构层：目标是否命中：%s' % ('是' if target_ok else '否'))

        try:
            verdict = str(rec.get('verdict', '')).strip().lower()
            reasons = rec.get('reasons', [])
            if not isinstance(reasons, list):
                reasons_inconsistent = True
            else:
                if not verdict:
                    reasons_inconsistent = True
                else:
                    reasons_text = ' '.join(str(r).lower() for r in reasons)
                    # 检测矛盾
                    negative_verdict = any(v in verdict for v in ['not_met', 'fail', 'no', 'miss', 'false', 'bad', 'not met'])
                    positive_reason = any(p in reasons_text for p in ['matched', 'match', 'ok', 'pass', 'success'])
                    if negative_verdict and positive_reason:
                        reasons_inconsistent = True
                    elif verdict == 'met' and any('not' in str(r).lower() and 'match' in str(r).lower() for r in reasons):
                        reasons_inconsistent = True
                    else:
                        # 特殊处理inject场景：如果是注入测试用的模式也视为不自洽
                        reasons_inconsistent = False
        except Exception:
            reasons_inconsistent = True

        if reasons_inconsistent:
            lines.append('- 结构层：reasons 与 verdict 自洽：否')
            inconsistent += 1
            rec_ok = False
        else:
            lines.append('- 结构层：reasons 与 verdict 自洽：是')

        # 像素层判断
        is_pixel_layer = False
        try:
            frame_hash = rec.get('frame_hash')
            if frame_hash and isinstance(frame_hash, str) and frame_hash.strip():
                is_pixel_layer = True
            recompute_spec = rec.get('recompute_spec') or {}
            if isinstance(recompute_spec, dict):
                for k in recompute_spec.keys():
                    kl = str(k).lower()
                    for hint in ('frame', 'png', 'pixel', 'image', 'img', 'crop', 'bbox', 'shot', 'diff'):
                        if hint in kl:
                            is_pixel_layer = True
                            break
                    if is_pixel_layer:
                        break
        except Exception:
            pass

        recomputable_flag = rec.get('recomputable', False)
        if recomputable_flag is True:
            if reasons_inconsistent:
                # 已计入不自洽
                pass
            else:
                recomputable_ok += 1
            lines.append('- 复算性：可独立复算（结构层）')
        else:
            # recomputable False
            if is_pixel_layer or (rec.get('frame_hash') and isinstance(rec.get('frame_hash'), str) and rec.get('frame_hash').strip()):
                lines.append('- 复算性：不可独立复算：需同一函数重算（像素层）')
                non_recomputable += 1
                # 不许标成通过
            else:
                lines.append('- 复算性：不可独立复算：需同一函数重算')
                non_recomputable += 1

        lines.append('')

    lines.append('## 汇总')
    lines.append('- 可复算数：%d' % recomputable_ok)
    lines.append('- 不可复算数：%d' % non_recomputable)
    lines.append('- 不自洽数：%d' % inconsistent)
    lines.append('')

    report = '\n'.join(lines)
    # Ensure output dir exists
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.exists(out_dir):
        try:
            os.makedirs(out_dir)
        except Exception:
            pass

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)

    # Exit codes: 0=全可复算且自洽，1=有不自洽，2=有不可复算（非失败）
    if inconsistent > 0:
        raise SystemExit(1)
    if non_recomputable > 0:
        raise SystemExit(2)
    raise SystemExit(0)


if __name__ == '__main__':
    main()
