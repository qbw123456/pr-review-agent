#!/usr/bin/env python3
"""
Run golden PR agent evaluation (calls LLM — requires .env).

Usage:
  pip install -r requirements-dev.txt
  python scripts/run_golden_eval.py --list
  python scripts/run_golden_eval.py --case obvious_none
  python scripts/run_golden_eval.py --all
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.golden.loader import (  # noqa: E402
    build_fixture_repo,
    fixture_diff_stat,
    list_case_ids,
    load_case_meta,
    score_report,
)


def run_one_case(case_id: str, *, keep_repo: bool = False) -> bool:
    meta = load_case_meta(case_id)
    print(f"\n{'=' * 60}")
    print(f"Case: {case_id} — {meta.title}")
    print(meta.description.strip() or "(no description)")
    print("=" * 60)

    tmp = tempfile.mkdtemp(prefix=f"golden_{case_id}_")
    repo = build_fixture_repo(case_id, Path(tmp))
    print(f"Fixture repo: {repo}")
    print(fixture_diff_stat(repo))
    print()

    out_file = repo / "GOLDEN_REVIEW.md"
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "main.py"),
        "review",
        "--base",
        meta.base,
        "--quiet-tools",
        "-o",
        str(out_file),
    ]
    env = os.environ.copy()
    proc = subprocess.run(cmd, cwd=repo, env=env, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(f"[FAIL] main.py review exited {proc.returncode}", file=sys.stderr)
        if not keep_repo:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        return False

    report = out_file.read_text(encoding="utf-8") if out_file.is_file() else ""
    if not report.strip():
        print("[FAIL] empty report")
        if not keep_repo:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        return False

    result = score_report(case_id, report, meta=meta)
    for w in result.warnings:
        print(f"[warn] {w}")
    if result.passed:
        print(f"[PASS] {case_id}")
    else:
        print(f"[FAIL] {case_id}")
        for f in result.failures:
            print(f"  - {f}")
        print("\n--- report preview (first 1200 chars) ---")
        print(report[:1200])
        if len(report) > 1200:
            print("...")

    if keep_repo:
        print(f"\nRepo kept at: {repo}")
    else:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    return result.passed


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden PR agent evaluation")
    parser.add_argument("--list", action="store_true", help="List case ids")
    parser.add_argument("--case", action="append", metavar="ID", help="Run one case (repeatable)")
    parser.add_argument("--all", action="store_true", help="Run all cases")
    parser.add_argument("--keep-repo", action="store_true", help="Do not delete temp git repo")
    args = parser.parse_args()

    if args.list:
        for cid in list_case_ids():
            meta = load_case_meta(cid)
            print(f"{cid}: {meta.title}")
        return 0

    case_ids = args.case or (list_case_ids() if args.all else None)
    if not case_ids:
        parser.print_help()
        return 1

    ok = 0
    for cid in case_ids:
        if run_one_case(cid, keep_repo=args.keep_repo):
            ok += 1
    total = len(case_ids)
    print(f"\nSummary: {ok}/{total} passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
