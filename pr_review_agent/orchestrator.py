"""PR review orchestration: per-file subagents + main integration pass."""

from __future__ import annotations

import os
import threading
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

# Avoid runaway API cost on huge PRs
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


def _review_one_file(path: str, base: str, *, verbose: bool) -> tuple[str, str]:
    summary = run_file_review_subagent(path, base, verbose=verbose)
    return path, summary


def _run_file_subagents(
    files: list[str],
    base: str,
    *,
    verbose: bool = True,
    max_workers: int | None = None,
) -> list[tuple[str, str]]:
    workers = subagent_max_workers(max_workers)
    total = len(files)
    if total == 0:
        return []

    # Single file or workers=1: serial with full per-file tool logging
    if workers == 1 or total == 1:
        summaries: list[tuple[str, str]] = []
        for i, path in enumerate(files, start=1):
            if verbose:
                print(f"\033[36m[{i}/{total}] 子 Agent 审查:\033[0m {path}")
            path, summary = _review_one_file(path, base, verbose=verbose)
            summaries.append((path, summary))
            if verbose:
                print(f"\033[32m  ✓ 完成\033[0m {path} ({len(summary)} chars)\n")
        return summaries

    if verbose:
        print(
            f"\033[36m并行子 Agent 审查:\033[0m {total} 个文件，"
            f"workers={min(workers, total)}\n"
        )

    print_lock = threading.Lock()
    by_path: dict[str, str] = {}
    done = 0

    def _submit(path: str) -> tuple[str, str]:
        # Suppress interleaved tool logs from concurrent agent_loop runs
        return _review_one_file(path, base, verbose=False)

    pool_workers = min(workers, total)
    with ThreadPoolExecutor(max_workers=pool_workers) as executor:
        futures = {executor.submit(_submit, path): path for path in files}
        for future in as_completed(futures):
            path = futures[future]
            try:
                path, summary = future.result()
            except Exception as exc:
                summary = (
                    f"### 文件: `{path}`\n\n"
                    f"（子 Agent 失败: {exc}）"
                )
            by_path[path] = summary
            done += 1
            if verbose:
                with print_lock:
                    print(
                        f"\033[32m  ✓ [{done}/{total}]\033[0m {path} "
                        f"({len(summary)} chars)"
                    )

    if verbose:
        print()
    return [(path, by_path[path]) for path in files]


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

    summaries = _run_file_subagents(
        files, base, verbose=verbose, max_workers=max_workers
    )

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
    agent_loop(
        messages,
        system=build_integration_system_prompt(),
        tools=INTEGRATION_TOOLS,
        tool_handlers=INTEGRATION_TOOL_HANDLERS,
        verbose=verbose,
        interactive=False,
        review_mode=True,
        max_turns=25,
    )
    return extract_final_text(messages)
