"""Review mode routing (legacy vs subagent) with mocked git."""

from pathlib import Path
from unittest.mock import patch

import pytest

from pr_review_agent.git_utils import PER_FILE_MAX
from pr_review_agent.review_strategy import (
    DEFAULT_LEGACY_MAX_FILES,
    legacy_max_files,
    normalize_review_mode,
    resolve_use_legacy,
)


def test_normalize_review_mode_auto():
    assert normalize_review_mode("auto") == "auto"


def test_normalize_review_mode_legacy_flag():
    assert normalize_review_mode("subagent", legacy_flag=True) == "legacy"


def test_normalize_review_mode_invalid_raises_system_exit():
    with pytest.raises(SystemExit):
        normalize_review_mode("bogus")


def test_legacy_max_files_default(monkeypatch):
    monkeypatch.delenv("REVIEW_LEGACY_MAX_FILES", raising=False)
    assert legacy_max_files() == DEFAULT_LEGACY_MAX_FILES


def test_legacy_max_files_from_env(monkeypatch):
    monkeypatch.setenv("REVIEW_LEGACY_MAX_FILES", "3")
    assert legacy_max_files() == 3


def test_legacy_max_files_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("REVIEW_LEGACY_MAX_FILES", "not-a-number")
    assert legacy_max_files() == DEFAULT_LEGACY_MAX_FILES


@patch("pr_review_agent.review_strategy.diff_one_file")
@patch("pr_review_agent.review_strategy.list_reviewable_changed_files")
def test_auto_legacy_small_pr(mock_list, mock_diff, tmp_path: Path):
    mock_list.return_value = [f"f{i}.py" for i in range(3)]
    mock_diff.return_value = "x" * 100

    use_legacy, reason, n = resolve_use_legacy("main", "auto", workdir=tmp_path)

    assert use_legacy is True
    assert n == 3
    assert "legacy" in reason


@patch("pr_review_agent.review_strategy.diff_one_file")
@patch("pr_review_agent.review_strategy.list_reviewable_changed_files")
def test_auto_subagent_too_many_files(mock_list, mock_diff, tmp_path: Path):
    mock_list.return_value = [f"f{i}.py" for i in range(7)]
    mock_diff.return_value = "small"

    use_legacy, reason, n = resolve_use_legacy("main", "auto", workdir=tmp_path)

    assert use_legacy is False
    assert n == 7
    assert "subagent" in reason


@patch("pr_review_agent.review_strategy.diff_one_file")
@patch("pr_review_agent.review_strategy.list_reviewable_changed_files")
def test_auto_subagent_oversized_single_file(mock_list, mock_diff, tmp_path: Path):
    mock_list.return_value = ["big.py", "small.py"]

    def fake_diff(_wd, _base, path):
        return "D" * (PER_FILE_MAX + 1) if path == "big.py" else "ok"

    mock_diff.side_effect = fake_diff

    use_legacy, reason, n = resolve_use_legacy("main", "auto", workdir=tmp_path)

    assert use_legacy is False
    assert n == 2
    assert "big.py" in reason
    assert str(PER_FILE_MAX) in reason


@patch("pr_review_agent.review_strategy.diff_one_file")
@patch("pr_review_agent.review_strategy.list_reviewable_changed_files")
def test_auto_legacy_respects_env_threshold(
    mock_list, mock_diff, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("REVIEW_LEGACY_MAX_FILES", "4")
    mock_list.return_value = [f"f{i}.py" for i in range(5)]
    mock_diff.return_value = "x"

    use_legacy, reason, _n = resolve_use_legacy("main", "auto", workdir=tmp_path)

    assert use_legacy is False
    assert "5" in reason
    assert "4" in reason


def test_manual_legacy_always_true(tmp_path: Path):
    with patch(
        "pr_review_agent.review_strategy.list_reviewable_changed_files",
        return_value=["a.py"] * 10,
    ):
        use_legacy, reason, n = resolve_use_legacy("main", "legacy", workdir=tmp_path)
    assert use_legacy is True
    assert n == 10
    assert "手动" in reason


def test_manual_subagent_always_false(tmp_path: Path):
    with patch(
        "pr_review_agent.review_strategy.list_reviewable_changed_files",
        return_value=["a.py"],
    ):
        use_legacy, reason, n = resolve_use_legacy("main", "subagent", workdir=tmp_path)
    assert use_legacy is False
    assert n == 1
    assert "手动" in reason
