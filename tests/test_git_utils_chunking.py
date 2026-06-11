"""collect_pr_context diff chunking and budget (mocked git)."""

from pathlib import Path
from unittest.mock import patch

from pr_review_agent.git_utils import (
    PER_FILE_MAX,
    TOTAL_DIFF_BUDGET,
    collect_pr_context,
    collect_pr_context_light,
)


@patch("pr_review_agent.git_utils._run_git", return_value="ok")
@patch("pr_review_agent.git_utils.diff_one_file")
@patch("pr_review_agent.git_utils.list_changed_files")
def test_per_file_truncation(mock_list, mock_diff, _git, tmp_path: Path):
    mock_list.return_value = ["huge.py"]
    mock_diff.return_value = "L" * (PER_FILE_MAX + 500)

    out = collect_pr_context(tmp_path, base="main")

    assert "truncated: true" in out
    assert "huge.py" in out
    assert str(PER_FILE_MAX + 500) in out


@patch("pr_review_agent.git_utils._run_git", return_value="ok")
@patch("pr_review_agent.git_utils.diff_one_file")
@patch("pr_review_agent.git_utils.list_changed_files")
def test_skip_non_reviewable(mock_list, mock_diff, _git, tmp_path: Path):
    mock_list.return_value = ["package-lock.json", "a.py"]
    mock_diff.return_value = "+line\n"

    out = collect_pr_context(tmp_path, base="main")

    assert "skipped: non-reviewable" in out
    assert "a.py" in out
    assert "package-lock.json" in out


@patch("pr_review_agent.git_utils._run_git", return_value="ok")
@patch("pr_review_agent.git_utils.diff_one_file")
@patch("pr_review_agent.git_utils.list_changed_files")
def test_total_budget_omits_later_files(mock_list, mock_diff, _git, tmp_path: Path):
    # Each inlined piece is capped at PER_FILE_MAX; 13 × 8000 > TOTAL_DIFF_BUDGET.
    n_files = (TOTAL_DIFF_BUDGET // PER_FILE_MAX) + 1
    mock_list.return_value = [f"f{i}.py" for i in range(n_files)]
    mock_diff.return_value = "x" * PER_FILE_MAX

    out = collect_pr_context(tmp_path, base="main")

    assert "omitted: total diff budget exceeded" in out
    assert "f0.py" in out
    assert f"f{n_files - 1}.py" in out


@patch("pr_review_agent.git_utils._run_git", return_value="status line")
@patch("pr_review_agent.git_utils.list_changed_files")
def test_collect_pr_context_light_no_chunked_diffs(mock_list, _git, tmp_path: Path):
    mock_list.return_value = ["a.py", "package-lock.json"]

    out = collect_pr_context_light(tmp_path, base="main")

    assert "1 reviewable" in out
    assert "per-file diffs (chunked)" not in out
