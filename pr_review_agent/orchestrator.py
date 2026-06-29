"""PR review orchestration: per-dimension subagents + main integration pass."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import WORKDIR
from .git_utils import (
    DiffScope,
    build_lock_only_report,
    build_no_changes_report,
    collect_pr_context_light,
    has_pr_changes,
)
from .incremental_review import IncrementalReviewState
from .loop import agent_loop, extract_final_text
from .prompts import build_integration_request, build_integration_system_prompt
from .review_dimensions import (
    DimensionCluster,
    build_pr_dimension_plan,
    cluster_display_label,
    format_api_callers_block,
    find_callers_for_api_files,
)
from .subagent import run_dimension_cluster_subagent
from .tools import INTEGRATION_TOOL_HANDLERS, INTEGRATION_TOOLS
from .usage_stats import (
    ReviewRunStats,
    UsageTracker,
    log_usage_enabled,
    print_usage_report,
)

MAX_CLUSTERS_FOR_REVIEW = 50
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


def _cluster_label(cluster: DimensionCluster) -> str:
    return cluster_display_label(cluster)


def _review_one_cluster(
    cluster: DimensionCluster,
    base: str,
    *,
    verbose: bool,
    scope: DiffScope | None = None,
) -> tuple[str, str, UsageTracker]:
    display_label, summary, tracker = run_dimension_cluster_subagent(
        cluster, base, verbose=verbose, scope=scope
    )
    return display_label, summary, tracker


def _run_dimension_subagents(
    clusters: list[DimensionCluster],
    base: str,
    *,
    verbose: bool = True,
    max_workers: int | None = None,
    scope: DiffScope | None = None,
) -> tuple[list[tuple[str, str]], list[UsageTracker]]:
    workers = subagent_max_workers(max_workers)
    total = len(clusters)
    if total == 0:
        return [], []

    trackers: list[UsageTracker] = []

    if workers == 1 or total == 1:
        summaries: list[tuple[str, str]] = []
        for i, cluster in enumerate(clusters, start=1):
            label = _cluster_label(cluster)
            if verbose:
                files = ", ".join(cluster.files)
                print(f"\033[36m[{i}/{total}] 维度子 Agent ({label}):\033[0m {files}")
            display_label, summary, tracker = _review_one_cluster(
                cluster, base, verbose=verbose, scope=scope
            )
            summaries.append((display_label, summary))
            trackers.append(tracker)
            if verbose:
                print(
                    f"\033[32m  ✓ 完成\033[0m {display_label} "
                    f"({len(summary)} chars, {tracker.api_calls} calls, "
                    f"{tracker.total_tokens:,} tok, {tracker.wall_sec:.1f}s)\n"
                )
        return summaries, trackers

    if verbose:
        print(
            f"\033[36m并行维度子 Agent 审查:\033[0m {total} 个簇，"
            f"workers={min(workers, total)}\n"
        )

    print_lock = threading.Lock()
    by_index: dict[int, tuple[str, str]] = {}
    by_tracker: dict[int, UsageTracker] = {}
    done = 0

    def _submit(idx: int, cluster: DimensionCluster) -> tuple[int, str, str, UsageTracker]:
        display_label, summary, tracker = _review_one_cluster(
            cluster, base, verbose=False, scope=scope
        )
        return idx, display_label, summary, tracker

    pool_workers = min(workers, total)
    with ThreadPoolExecutor(max_workers=pool_workers) as executor:
        futures = {
            executor.submit(_submit, i, cluster): i
            for i, cluster in enumerate(clusters)
        }
        for future in as_completed(futures):
            idx = futures[future]
            label = _cluster_label(clusters[idx])
            try:
                idx, display_label, summary, tracker = future.result()
            except Exception as exc:
                display_label = label
                summary = f"### {label}\n\n（子 Agent 失败: {exc}）"
                tracker = UsageTracker(label=clusters[idx].dimension.value)
            by_index[idx] = (display_label, summary)
            by_tracker[idx] = tracker
            done += 1
            if verbose:
                with print_lock:
                    print(
                        f"\033[32m  ✓ [{done}/{total}]\033[0m {display_label} "
                        f"({len(summary)} chars, {tracker.api_calls} calls, "
                        f"{tracker.total_tokens:,} tok, {tracker.wall_sec:.1f}s)"
                    )

    if verbose:
        print()
    summaries = [by_index[i] for i in range(total)]
    trackers = [by_tracker[i] for i in range(total)]
    return summaries, trackers


def run_pr_review_with_subagents(
    base: str,
    *,
    verbose: bool = True,
    max_workers: int | None = None,
    incremental: IncrementalReviewState | None = None,
) -> str:
    """Run per-dimension subagent reviews, then one integration agent for the final report."""
    scope = incremental.scope if incremental else DiffScope(base=base)
    full_scope = DiffScope(base=base)

    if not has_pr_changes(WORKDIR, base, scope=full_scope):
        return build_no_changes_report(base)

    plan = build_pr_dimension_plan(WORKDIR, base, scope=scope)
    if plan.lock_only:
        return build_lock_only_report(base, changed_files=plan.all_changed)

    if scope.is_incremental and not plan.all_changed:
        from .incremental_review import build_no_incremental_delta_report

        assert incremental is not None and incremental.since_sha
        return build_no_incremental_delta_report(
            base=base,
            since_sha=incremental.since_sha,
            head_sha=incremental.head_sha,
            previous_report=incremental.previous_report,
        )

    clusters = plan.clusters
    skipped: list[str] = []
    if len(clusters) > MAX_CLUSTERS_FOR_REVIEW:
        skipped_clusters = clusters[MAX_CLUSTERS_FOR_REVIEW:]
        clusters = clusters[:MAX_CLUSTERS_FOR_REVIEW]
        for c in skipped_clusters:
            skipped.extend(c.files)

    workers = subagent_max_workers(max_workers)
    if verbose:
        print(f"维度划分: {plan.dimension_summary()}\n")

    run_start = time.perf_counter()
    summaries, cluster_trackers = _run_dimension_subagents(
        clusters, base, verbose=verbose, max_workers=max_workers, scope=scope
    )

    integrate = UsageTracker(label="integrate")
    previous_report = incremental.previous_report if incremental else None
    incremental_note = incremental.mode_label if incremental and scope.is_incremental else ""
    messages = [
        {
            "role": "user",
            "content": build_integration_request(
                base=base,
                cluster_summaries=summaries,
                skipped_files=skipped,
                light_context=collect_pr_context_light(
                    WORKDIR,
                    base,
                    scope=scope,
                    full_pr_scope=full_scope if scope.is_incremental else None,
                ),
                dimension_summary=plan.dimension_summary(),
                previous_report=previous_report if scope.is_incremental else None,
                incremental_note=incremental_note,
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
        route=(
            f"subagent（维度×{len(clusters)} 簇，workers={workers}"
            + ("，增量" if scope.is_incremental else "")
            + "）"
        ),
        workers=workers,
        phases=[*cluster_trackers, integrate],
    )
    clock_wall = time.perf_counter() - run_start
    print_usage_report(stats)
    if verbose and log_usage_enabled():
        print(
            f"\033[36m[usage]\033[0m 端到端时钟时间（含并行等待）: {clock_wall:.1f}s\n"
        )

    return extract_final_text(messages)


def finalize_review_report(report: str, incremental: IncrementalReviewState | None) -> str:
    from .incremental_review import append_review_marker

    head = incremental.head_sha if incremental else None
    if not head:
        try:
            from .incremental_review import resolve_head_sha

            head = resolve_head_sha()
        except RuntimeError:
            return report
    return append_review_marker(report, head)
