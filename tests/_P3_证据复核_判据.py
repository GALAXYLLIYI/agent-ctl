#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import tempfile


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_path = os.path.join(repo_root, 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from agentctl import evidence

    # Fixture 1: 合法结构记录 ⇒ 可复算且自洽
    rec1 = evidence.make_record(
        actor="DSH",
        target={"hwnd": 1234},
        frame_hash="ab12cd34",
        action={"tool": "ctl_click"},
        wait_ms=450,
        verdict="met",
        reasons=["matched"],
        evidence_refs=["ref1"],
        recompute_spec={"DIFF_STEP": 4, "HYSTERESIS": 2, "need": 1},
        recomputable=True,
        notes="ok",
    )

    # Fixture 2: verdict 与 reasons 矛盾 ⇒ rc=1
    # Make it pass validate but be inconsistent in structure
    rec2 = dict(rec1)
    rec2['verdict'] = "not_met"
    rec2['reasons'] = ["matched"]  # contradictory
    rec2['recomputable'] = True
    # ensure it validates
    evidence.validate(rec2)

    # Fixture 3: recomputable:false ⇒ rc=2 且报告含“不可独立复算”
    rec3 = evidence.make_record(
        actor="DSH",
        target={"hwnd": 5678},
        frame_hash="ff00aa11",
        action={"tool": "ctl_click"},
        wait_ms=300,
        verdict="met",
        reasons=["matched"],
        evidence_refs=["ref2"],
        recompute_spec={"DIFF_STEP": 4, "HYSTERESIS": 2, "need": 1},
        recomputable=False,
        notes="pixel",
    )

    # Normal run
    if '--inject' not in sys.argv:
        fixtures = [rec1, rec2, rec3]
        fd, path = tempfile.mkstemp(suffix='.json', prefix='evidence_')
        os.close(fd)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(fixtures, f, ensure_ascii=False)
            fd2, out_path = tempfile.mkstemp(suffix='.md', prefix='rep_')
            os.close(fd2)
            try:
                verifier = os.path.join(repo_root, 'tools', 'verify_evidence.py')
                proc = subprocess.run([sys.executable, '-X', 'utf8', verifier, '--evidence', path, '--out', out_path], cwd=repo_root)
                rc = proc.returncode
                # Check report contains "不可独立复算"
                try:
                    with open(out_path, 'r', encoding='utf-8') as f:
                        report = f.read()
                    if '不可独立复算' not in report:
                        print('REPORT_MISSING_PHRASE', flush=True)
                        raise SystemExit(99)
                except SystemExit:
                    raise
                except Exception:
                    print('REPORT_READ_ERR', flush=True)
                    raise SystemExit(98)
                print(rc, flush=True)
                raise SystemExit(rc)
            finally:
                try:
                    os.unlink(out_path)
                except Exception:
                    pass
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
    else:
        # --inject: 把矛盾记录判成通过（应该必红）
        bad = dict(rec2)
        bad['verdict'] = "met"  # try to make it look ok
        bad['reasons'] = ["matched"]
        evidence.validate(bad)
        fixtures_bad = [bad]
        fd, path = tempfile.mkstemp(suffix='.json', prefix='evidence_bad_')
        os.close(fd)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(fixtures_bad, f, ensure_ascii=False)
            fd2, out_path = tempfile.mkstemp(suffix='.md', prefix='rep_bad_')
            os.close(fd2)
            try:
                verifier = os.path.join(repo_root, 'tools', 'verify_evidence.py')
                proc = subprocess.run([sys.executable, '-X', 'utf8', verifier, '--evidence', path, '--out', out_path], cwd=repo_root)
                rc = proc.returncode
                print(rc, flush=True)
                # Must be red (non-zero)
                if rc == 0:
                    raise SystemExit(97)
                raise SystemExit(rc)
            finally:
                try:
                    os.unlink(out_path)
                except Exception:
                    pass
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass


if __name__ == '__main__':
    main()
