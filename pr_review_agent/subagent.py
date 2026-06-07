"""s06: per-dimension review subagents — isolated context, summary-only return."""

from __future__ import annotations

import time

from .config import WORKDIR
from .git_utils import diff_one_file
from .loop import agent_loop, extract_final_text
from .prompts import (
    build_dimension_cluster_prompt,
    build_file_review_prompt,
    build_subagent_system_prompt,
)
from .review_dimensions import (
    DIMENSION_LABELS,
    DimensionCluster,
    ReviewDimension,
    format_api_callers_block,
    find_callers_for_api_files,
)
from .tools import SUBAGENT_TOOL_HANDLERS, SUBAGENT_TOOLS
from .usage_stats import UsageTracker

SUBAGENT_DIFF_EMBED_MAX = 100_000
MAX_SUBAGENT_TURNS = 30


def _embed_diff(raw: str) -> tuple[str, str]:
    if len(raw) <= SUBAGENT_DIFF_EMBED_MAX:
        return raw, ""
    return (
        raw[:SUBAGENT_DIFF_EMBED_MAX],
        f"(embedded diff truncated at {SUBAGENT_DIFF_EMBED_MAX} chars; "
        f"total {len(raw)} — run git diff in bash for the full patch)",
    )


def run_file_review_subagent(
    path: str,
    base: str,
    *,
    verbose: bool = True,
    usage: UsageTracker | None = None,
    dimension: ReviewDimension = ReviewDimension.LOGIC,
    extra_context: str = "",
) -> tuple[str, UsageTracker]:
    """Review one changed file in a fresh context; return (summary, usage stats)."""
    tracker = usage or UsageTracker(label=path)
    raw_diff = diff_one_file(WORKDIR, base, path)
    embed, diff_note = _embed_diff(raw_diff)

    prompt = build_file_review_prompt(
        path=path,
        base=base,
        diff_text=embed or "(no diff output)",
        diff_note=diff_note,
        extra_context=extra_context,
    )
    messages = [{"role": "user", "content": prompt}]
    wall_start = time.perf_counter()
    agent_loop(
        messages,
        system=build_subagent_system_prompt(dimension),
        tools=SUBAGENT_TOOLS,
        tool_handlers=SUBAGENT_TOOL_HANDLERS,
        verbose=verbose,
        interactive=False,
        review_mode=True,
        max_turns=MAX_SUBAGENT_TURNS,
        usage=tracker,
    )
    tracker.wall_sec = time.perf_counter() - wall_start
    summary = extract_final_text(messages).strip()
    if not summary:
        summary = f"### 文件: `{path}`\n\n（子 Agent 未返回摘要）"
    return summary, tracker


def run_dimension_cluster_subagent(
    cluster: DimensionCluster,
    base: str,
    *,
    verbose: bool = True,
    usage: UsageTracker | None = None,
    extra_context: str = "",
) -> tuple[str, str, UsageTracker]:
    """Review a dimension cluster (one or more files) in a fresh context."""
    dim = cluster.dimension
    label = DIMENSION_LABELS[dim]
    if cluster.cluster_index:
        tracker_label = f"{dim.value}-{cluster.cluster_index}"
        display_label = f"{label} #{cluster.cluster_index + 1}"
    else:
        tracker_label = dim.value
        display_label = label

    tracker = usage or UsageTracker(label=tracker_label)
    file_diffs: list[tuple[str, str]] = []
    for path in cluster.files:
        raw = diff_one_file(WORKDIR, base, path)
        embed, _ = _embed_diff(raw)
        file_diffs.append((path, embed))

    context = extra_context
    if dim == ReviewDimension.API:
        caller_map = find_callers_for_api_files(WORKDIR, base, cluster.files)
        api_block = format_api_callers_block(caller_map)
        if api_block:
            context = f"{context}\n\n{api_block}".strip() if context else api_block

    prompt = build_dimension_cluster_prompt(
        dim,
        file_diffs,
        base,
        extra_context=context,
    )
    messages = [{"role": "user", "content": prompt}]
    wall_start = time.perf_counter()
    agent_loop(
        messages,
        system=build_subagent_system_prompt(dim),
        tools=SUBAGENT_TOOLS,
        tool_handlers=SUBAGENT_TOOL_HANDLERS,
        verbose=verbose,
        interactive=False,
        review_mode=True,
        max_turns=MAX_SUBAGENT_TURNS,
        usage=tracker,
    )
    tracker.wall_sec = time.perf_counter() - wall_start
    summary = extract_final_text(messages).strip()
    if not summary:
        files_list = ", ".join(f"`{p}`" for p in cluster.files)
        summary = f"### 维度: {label}\n\n（子 Agent 未返回摘要；文件: {files_list}）"
    return display_label, summary, tracker
