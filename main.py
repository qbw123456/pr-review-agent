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
from pr_review_agent.git_utils import (
    DiffScope,
    build_lock_only_report,
    build_no_changes_report,
    build_review_request,
    has_pr_changes,
)
import time

from pr_review_agent.loop import agent_loop, extract_final_text
from pr_review_agent.usage_stats import ReviewRunStats, UsageTracker, print_usage_report
from pr_review_agent.orchestrator import finalize_review_report, run_pr_review_with_subagents
from pr_review_agent.prompts import build_legacy_system_prompt
from pr_review_agent.incremental_review import (
    build_no_incremental_delta_report,
    resolve_incremental_state,
)
from pr_review_agent.ast_context import format_caller_ast_context_block
from pr_review_agent.review_dimensions import (
    build_pr_dimension_plan,
    filter_api_like_files,
    format_api_callers_block,
    find_callers_for_api_files,
)
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
    since_sha: str | None = None,
    force_full: bool = False,
    previous_report: str | None = None,
) -> str:
    """Run PR review; returns Markdown report text."""
    incremental = resolve_incremental_state(
        base,
        since_sha=since_sha,
        previous_report=previous_report,
        force_full=force_full,
    )
    scope = incremental.scope
    full_scope = DiffScope(base=base)

    review_mode = normalize_review_mode(mode, legacy_flag=legacy_flag)
    use_legacy, route_reason, _n = resolve_use_legacy(
        base, review_mode, scope=scope
    )

    print(f"PR Review Agent — {run_label(use_legacy)}，对比 `{base}` @ {WORKDIR}")
    print(f"路由: {route_reason}")
    print(f"审查模式: {incremental.mode_label}")

    if not has_pr_changes(WORKDIR, base, scope=full_scope):
        return finalize_review_report(build_no_changes_report(base), incremental)

    if scope.is_incremental and not has_pr_changes(WORKDIR, base, scope=scope):
        assert incremental.since_sha
        return finalize_review_report(
            build_no_incremental_delta_report(
                base=base,
                since_sha=incremental.since_sha,
                head_sha=incremental.head_sha,
                previous_report=incremental.previous_report,
            ),
            incremental,
        )

    plan = build_pr_dimension_plan(WORKDIR, base, scope=scope)
    print(f"维度: {plan.dimension_summary()}\n")

    if plan.lock_only:
        return finalize_review_report(
            build_lock_only_report(base, changed_files=plan.all_changed),
            incremental,
        )

    if plan.lock_only:
        return build_lock_only_report(base, changed_files=plan.all_changed)

    if use_legacy:
        user_content = build_review_request(base=base, scope=scope)
        if scope.is_incremental and incremental.previous_report:
            user_content = (
                f"**增量审查模式：** {incremental.mode_label}\n\n"
                f"## 上次审查报告（本次 push 未重新审查的文件）\n\n"
                f"{incremental.previous_report.strip()}\n\n"
                f"---\n\n"
                f"## 本次 push 增量 diff\n\n"
                f"{user_content}\n\n"
                f"请合并上一份报告与本次增量审查，输出相对 `{base}` 的**完整 PR 报告**"
                f"（变更文件须覆盖整个 PR，不仅是本次 push）。"
            )
        api_like_files = filter_api_like_files(
            WORKDIR, base, plan.reviewable_files, scope=scope
        )
        if api_like_files:
            caller_map = find_callers_for_api_files(
                WORKDIR, base, api_like_files, scope=scope
            )
            caller_ast_block = format_caller_ast_context_block(WORKDIR, caller_map)
            if caller_ast_block:
                user_content = f"{user_content}\n\n{caller_ast_block}"
            api_block = format_api_callers_block(
                caller_map,
                has_caller_ast_slices=bool(caller_ast_block),
            )
            if api_block:
                user_content = f"{user_content}\n\n{api_block}"

        messages = [{"role": "user", "content": user_content}]
        tracker = UsageTracker(label="legacy")
        wall_start = time.perf_counter()
        agent_loop(
            messages,
            system=build_legacy_system_prompt(plan.active_dimensions),
            verbose=not quiet_tools,
            interactive=False,
            review_mode=True,
            usage=tracker,
        )
        tracker.wall_sec = time.perf_counter() - wall_start
        route_suffix = "增量" if scope.is_incremental else route_reason
        print_usage_report(
            ReviewRunStats(
                route=f"legacy（{route_suffix}；{plan.dimension_summary()}）",
                phases=[tracker],
            )
        )
        return finalize_review_report(extract_final_text(messages), incremental)

    report = run_pr_review_with_subagents(
        base,
        verbose=not quiet_tools,
        max_workers=workers,
        incremental=incremental,
    )
    return finalize_review_report(report, incremental)


def cmd_review(
    base: str,
    output: Path | None,
    quiet_tools: bool,
    *,
    mode: str,
    legacy_flag: bool,
    workers: int | None,
    since_sha: str | None,
    force_full: bool,
    previous_report: Path | None,
) -> int:
    prev_text = None
    if previous_report and previous_report.is_file():
        prev_text = previous_report.read_text(encoding="utf-8")

    report = run_review(
        base,
        mode=mode,
        legacy_flag=legacy_flag,
        quiet_tools=quiet_tools,
        workers=workers,
        since_sha=since_sha,
        force_full=force_full,
        previous_report=prev_text,
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
    review_p.add_argument(
        "--since-sha",
        default=None,
        metavar="SHA",
        help="Incremental review: only diff since this commit (env REVIEW_SINCE_SHA)",
    )
    review_p.add_argument(
        "--full-review",
        action="store_true",
        help="Disable incremental review; re-review entire PR diff",
    )
    review_p.add_argument(
        "--previous-report",
        type=Path,
        default=None,
        help="Previous report Markdown for incremental merge (env REVIEW_PREVIOUS_REPORT)",
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
            since_sha=args.since_sha,
            force_full=args.full_review,
            previous_report=args.previous_report,
        )
    if args.command == "chat":
        return cmd_chat()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
