#!/usr/bin/env python3
"""
PR Review Agent — s01 loop + s02 tools + s03 permissions + s06 per-file subagents

Usage:
  pip install -r requirements.txt
  copy .env.example to .env and fill in keys

  # Review vs main (auto: ≤6 files and each diff ≤8KB → legacy, else subagents)
  python main.py review
  python main.py review --base develop --output REVIEW.md

  # Interactive mode
  python main.py chat
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import readline

    readline.parse_and_bind("set bind-tty-special-chars off")
    readline.parse_and_bind("set input-meta on")
    readline.parse_and_bind("set output-meta on")
    readline.parse_and_bind("set convert-meta off")
except ImportError:
    pass

from pr_review_agent.config import WORKDIR, require_env
from pr_review_agent.git_utils import build_no_changes_report, build_review_request, has_pr_changes
from pr_review_agent.loop import agent_loop, extract_final_text
from pr_review_agent.orchestrator import run_pr_review_with_subagents
from pr_review_agent.review_strategy import (
    normalize_review_mode,
    resolve_use_legacy,
    run_label,
)


def _write_report(report: str, output: Path | None) -> None:
    print("\n" + "=" * 60 + "\n")
    print(report)
    if output:
        output.write_text(report, encoding="utf-8")
        print(f"\n[Saved to {output}]")


def run_review(
    base: str,
    *,
    mode: str = "auto",
    legacy_flag: bool = False,
    quiet_tools: bool = False,
    workers: int | None = None,
) -> str:
    """Run PR review; returns Markdown report text."""
    review_mode = normalize_review_mode(mode, legacy_flag=legacy_flag)
    use_legacy, route_reason, _n = resolve_use_legacy(base, review_mode)

    print(f"PR Review Agent — {run_label(use_legacy)}，对比 `{base}` @ {WORKDIR}")
    print(f"路由: {route_reason}\n")

    if not has_pr_changes(WORKDIR, base):
        return build_no_changes_report(base)

    if use_legacy:
        messages = [{"role": "user", "content": build_review_request(base=base)}]
        agent_loop(
            messages,
            verbose=not quiet_tools,
            interactive=False,
            review_mode=True,
        )
        return extract_final_text(messages)

    return run_pr_review_with_subagents(
        base, verbose=not quiet_tools, max_workers=workers
    )


def cmd_review(
    base: str,
    output: Path | None,
    quiet_tools: bool,
    *,
    mode: str,
    legacy_flag: bool,
    workers: int | None,
) -> int:
    report = run_review(
        base,
        mode=mode,
        legacy_flag=legacy_flag,
        quiet_tools=quiet_tools,
        workers=workers,
    )
    _write_report(report, output)
    return 0


def cmd_chat() -> int:
    print("PR Review Agent — interactive (s01+s02+s03)")
    print(f"Workspace: {WORKDIR}")
    print("Commands: review  → auto review vs main")
    print("          q / exit → quit\n")

    history: list = []
    while True:
        try:
            query = input("\033[36mreview >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        stripped = query.strip()
        if stripped.lower() in ("q", "exit", ""):
            break
        if stripped.lower() == "review":
            report = run_review("main", mode="auto", quiet_tools=False)
            print("\n" + report + "\n")
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history, interactive=True, review_mode=False)
        print(extract_final_text(history))
        print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PR Review Agent (s01 + s02 + s03 + s06 subagents)"
    )
    sub = parser.add_subparsers(dest="command")

    review_p = sub.add_parser("review", help="Review current branch vs base")
    review_p.add_argument("--base", default="main", help="Base branch (default: main)")
    review_p.add_argument(
        "--output", "-o", type=Path, default=None, help="Write report to file"
    )
    review_p.add_argument(
        "--quiet-tools", action="store_true", help="Hide tool call previews"
    )
    review_p.add_argument(
        "--mode",
        choices=["auto", "legacy", "subagent"],
        default="auto",
        help=(
            "auto: legacy when ≤N files and each diff ≤8KB, else subagents "
            "(N=REVIEW_LEGACY_MAX_FILES or 6)"
        ),
    )
    review_p.add_argument(
        "--legacy-single-agent",
        action="store_true",
        help="Same as --mode legacy (deprecated alias)",
    )
    review_p.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Parallel subagent workers (subagent path only; default 4, "
            "env REVIEW_SUBAGENT_WORKERS; use 1 for serial)"
        ),
    )

    sub.add_parser("chat", help="Interactive chat with review tools")

    args = parser.parse_args()
    if args.command in ("review", "chat"):
        require_env()
    if args.command == "review":
        return cmd_review(
            args.base,
            args.output,
            args.quiet_tools,
            mode=args.mode,
            legacy_flag=args.legacy_single_agent,
            workers=args.workers,
        )
    if args.command == "chat":
        return cmd_chat()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
