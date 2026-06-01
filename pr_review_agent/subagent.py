"""s06: per-file review subagents — isolated context, summary-only return."""

from __future__ import annotations

import time

from .config import WORKDIR
from .git_utils import diff_one_file
from .loop import agent_loop, extract_final_text
from .prompts import build_file_review_prompt, build_subagent_system_prompt
from .tools import SUBAGENT_TOOL_HANDLERS, SUBAGENT_TOOLS
from .usage_stats import UsageTracker

# Cap diff embedded in the subagent's first user message (full patch via bash if longer)
SUBAGENT_DIFF_EMBED_MAX = 100_000
MAX_SUBAGENT_TURNS = 30


def run_file_review_subagent(
    path: str,
    base: str,
    *,
    verbose: bool = True,
    usage: UsageTracker | None = None,
) -> tuple[str, UsageTracker]:
    """Review one changed file in a fresh context; return (summary, usage stats)."""
    tracker = usage or UsageTracker(label=path)
    raw_diff = diff_one_file(WORKDIR, base, path)
    embed = raw_diff
    diff_note = ""
    if len(raw_diff) > SUBAGENT_DIFF_EMBED_MAX:
        embed = raw_diff[:SUBAGENT_DIFF_EMBED_MAX]
        diff_note = (
            f"(embedded diff truncated at {SUBAGENT_DIFF_EMBED_MAX} chars; "
            f"total {len(raw_diff)} — run git diff in bash for the full patch)"
        )

    prompt = build_file_review_prompt(
        path=path,
        base=base,
        diff_text=embed or "(no diff output)",
        diff_note=diff_note,
    )
    messages = [{"role": "user", "content": prompt}]
    wall_start = time.perf_counter()
    agent_loop(
        messages,
        system=build_subagent_system_prompt(),
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
