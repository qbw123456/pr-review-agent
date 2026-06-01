"""PR review orchestration: per-file subagents + main integration pass."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import WORKDIR
from .git_utils import (
    build_no_changes_report,
    collect_pr_context_light,
    has_pr_changes,
    list_reviewable_changed_files,
)
from .loop import agent_loop, extract_final_text
from .prompts import build_integration_request, build_integration_system_prompt
from .subagent import run_file_review_subagent
from .tools import INTEGRATION_TOOL_HANDLERS, INTEGRATION_TOOLS
from .usage_stats import (
    ReviewRunStats,
    UsageTracker,
    log_usage_enabled,
    print_usage_report,
)

MAX_FILES_FOR_SUBAGENT_REVIEW = 50
DEFAULT_SUBAGENT_WORKERS = 4
MAX_SUBAGENT_WORKERS = 16


def subagent_max_workers(override: int | None = None) -> int:
    if override is not None:
        return max(1, min(MAX_SUBAGENT_WORKERS, override))
    raw = os.getenv("REVIEW_SUBAGENT_WORKERS", "").strip()
    if not raw:
        return DEFAULT_SUBAGENT_WORKERS
    try:
        n = int(raw)
        return max(1, min(MAX_SUBAGENT_WORKERS, n))
    except ValueError:
        return DEFAULT_SUBAGENT_WORKERS


def _review_one_file(
    path: str,
    base: str,
    *,
    verbose: bool,
) -> tuple[str, str, UsageTracker]:
    summary, tracker = run_file_review_subagent(path, base, verbose=verbose)
    return path, summary, tracker


def _run_file_subagents(
    files: list[str],
    base: str,
    *,
    verbose: bool = True,
    max_workers: int | None = None,
) -> tuple[list[tuple[str, str]], list[UsageTracker]]:
    workers = subagent_max_workers(max_workers)
    total = len(files)
    if total == 0:
        return [], []

    trackers: list[UsageTracker] = []

    if workers == 1 or total == 1:
        summaries: list[tuple[str, str]] = []
        for i, path in enumerate(files, start=1):
            if verbose:
                print(f"\033[36m[{i}/{total}] 子 Agent 审查:\033[0m {path}")
            path, summary, tracker = _review_one_file(path, base, verbose=verbose)
            summaries.append((path, summary))
            trackers.append(tracker)
            if verbose:
                print(
                    f"\033[32m  ✓ 完成\033[0m {path} "
                    f"({len(summary)} chars, {tracker.api_calls} calls, "
                    f"{tracker.total_tokens:,} tok, {tracker.wall_sec:.1f}s)\n"
                )
        return summaries, trackers

    if verbose:
        print(
            f"\033[36m并行子 Agent 审查:\033[0m {total} 个文件，"
            f"workers={min(workers, total)}\n"
        )

    print_lock = threading.Lock()
    by_path: dict[str, str] = {}
    by_tracker: dict[str, UsageTracker] = {}
    done = 0

    def _submit(path: str) -> tuple[str, str, UsageTracker]:
        return _review_one_file(path, base, verbose=False)

    pool_workers = min(workers, total)
    with ThreadPoolExecutor(max_workers=pool_workers) as executor:
        futures = {executor.submit(_submit, path): path for path in files}
        for future in as_completed(futures):
            path = futures[future]
            try:
                path, summary, tracker = future.result()
            except Exception as exc:
                summary = (
                    f"### 文件: `{path}`\n\n"
                    f"（子 Agent 失败: {exc}）"
                )
                tracker = UsageTracker(label=path)
            by_path[path] = summary
            by_tracker[path] = tracker
            done += 1
            if verbose:
                with print_lock:
                    print(
                        f"\033[32m  ✓ [{done}/{total}]\033[0m {path} "
                        f"({len(summary)} chars, {tracker.api_calls} calls, "
                        f"{tracker.total_tokens:,} tok, {tracker.wall_sec:.1f}s)"
                    )

    if verbose:
        print()
    summaries = [(path, by_path[path]) for path in files]
    trackers = [by_tracker[path] for path in files]
    return summaries, trackers


def run_pr_review_with_subagents(
    base: str,
    *,
    verbose: bool = True,
    max_workers: int | None = None,
) -> str:
    """Run per-file subagent reviews, then one integration agent for the final report."""
    if not has_pr_changes(WORKDIR, base):
        return build_no_changes_report(base)

    files = list_reviewable_changed_files(WORKDIR, base)
    if not files:
        return build_no_changes_report(base)

    skipped: list[str] = []
    if len(files) > MAX_FILES_FOR_SUBAGENT_REVIEW:
        skipped = files[MAX_FILES_FOR_SUBAGENT_REVIEW:]
        files = files[:MAX_FILES_FOR_SUBAGENT_REVIEW]

    workers = subagent_max_workers(max_workers)
    run_start = time.perf_counter()
    summaries, file_trackers = _run_file_subagents(
        files, base, verbose=verbose, max_workers=max_workers
    )

    integrate = UsageTracker(label="integrate")
    messages = [
        {
            "role": "user",
            "content": build_integration_request(
                base=base,
                file_summaries=summaries,
                skipped_files=skipped,
                light_context=collect_pr_context_light(WORKDIR, base),
            ),
        }
    ]
    if verbose:
        print("\033[35m主 Agent 集成审查…\033[0m\n")
    wall_start = time.perf_counter()
    agent_loop(
        messages,
        system=build_integration_system_prompt(),
        tools=INTEGRATION_TOOLS,
        tool_handlers=INTEGRATION_TOOL_HANDLERS,
        verbose=verbose,
        interactive=False,
        review_mode=True,
        max_turns=25,
        usage=integrate,
    )
    integrate.wall_sec = time.perf_counter() - wall_start

    stats = ReviewRunStats(
        route=f"subagent（workers={workers}，{len(files)} 文件）",
        workers=workers,
        phases=[*file_trackers, integrate],
    )
    clock_wall = time.perf_counter() - run_start
    print_usage_report(stats)
    if verbose and log_usage_enabled():
        print(
            f"\033[36m[usage]\033[0m 端到端时钟时间（含并行等待）: {clock_wall:.1f}s\n"
        )

    return extract_final_text(messages)
