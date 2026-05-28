"""PR review orchestration: per-file subagents + main integration pass."""

from __future__ import annotations

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


def run_pr_review_with_subagents(
    base: str,
    *,
    verbose: bool = True,
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

    summaries: list[tuple[str, str]] = []
    total = len(files)
    for i, path in enumerate(files, start=1):
        if verbose:
            print(f"\033[36m[{i}/{total}] 子 Agent 审查:\033[0m {path}")
        summary = run_file_review_subagent(path, base, verbose=verbose)
        summaries.append((path, summary))
        if verbose:
            print(f"\033[32m  ✓ 完成\033[0m ({len(summary)} chars)\n")

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
