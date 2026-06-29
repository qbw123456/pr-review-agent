"""Orchestrator limits (mocked; no LLM or real subagents)."""

from unittest.mock import patch

from pr_review_agent.orchestrator import (
    MAX_CLUSTERS_FOR_REVIEW,
    run_pr_review_with_subagents,
    subagent_max_workers,
)
from pr_review_agent.review_dimensions import (
    DimensionCluster,
    PRDimensionPlan,
    ReviewDimension,
)


def _make_plan_with_files(files: list[str]) -> PRDimensionPlan:
    clusters = [
        DimensionCluster(dimension=ReviewDimension.CODE, files=[f], cluster_index=i)
        for i, f in enumerate(files)
    ]
    return PRDimensionPlan(
        base="main",
        all_changed=files,
        reviewable_files=files,
        files_by_dimension={ReviewDimension.CODE: files},
        active_dimensions=[ReviewDimension.CODE],
        lock_only=False,
        clusters=clusters,
    )


@patch("pr_review_agent.orchestrator.log_usage_enabled", return_value=False)
@patch("pr_review_agent.orchestrator.extract_final_text", return_value="## done")
@patch("pr_review_agent.orchestrator.agent_loop")
@patch("pr_review_agent.orchestrator._run_dimension_subagents")
@patch("pr_review_agent.orchestrator.collect_pr_context_light", return_value="ctx")
@patch("pr_review_agent.orchestrator.build_pr_dimension_plan")
@patch("pr_review_agent.orchestrator.has_pr_changes", return_value=True)
def test_subagent_review_caps_cluster_count(
    _has_changes,
    mock_plan,
    _light,
    mock_subagents,
    _loop,
    _extract,
    _log_usage,
):
    files = [f"f{i}.py" for i in range(55)]
    mock_plan.return_value = _make_plan_with_files(files)
    mock_subagents.return_value = ([], [])

    run_pr_review_with_subagents("main", verbose=False)

    reviewed = mock_subagents.call_args[0][0]
    assert len(reviewed) == MAX_CLUSTERS_FOR_REVIEW

    request = _loop.call_args[0][0][0]["content"]
    for i in range(MAX_CLUSTERS_FOR_REVIEW, 55):
        assert f"f{i}.py" in request


def test_subagent_max_workers_bounds(monkeypatch):
    monkeypatch.delenv("REVIEW_SUBAGENT_WORKERS", raising=False)
    assert subagent_max_workers() == 4
    assert subagent_max_workers(override=99) == 16
    assert subagent_max_workers(override=0) == 1
