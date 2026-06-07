"""Pure helpers in git_utils (no git subprocess)."""

from pathlib import Path

from pr_review_agent.git_utils import (
    PER_FILE_MAX,
    TOTAL_DIFF_BUDGET,
    _cap,
    build_no_changes_report,
    is_reviewable_file,
    should_inline_diff,
)
from pr_review_agent.review_strategy import run_label


def test_is_reviewable_py():
    assert is_reviewable_file("src/foo.py") is True


def test_is_reviewable_skips_lock():
    assert is_reviewable_file("package-lock.json") is False


def test_is_reviewable_skips_png():
    assert is_reviewable_file("assets/logo.png") is False


def test_is_reviewable_md():
    assert is_reviewable_file("README.md") is True


def test_should_inline_matches_reviewable():
    assert should_inline_diff("a.py") is True
    assert should_inline_diff("yarn.lock") is False


def test_cap_short_unchanged():
    assert _cap("hello", 10, "label") == "hello"


def test_cap_long_truncates():
    out = _cap("a" * 20, 10, "label")
    assert "truncated" in out
    assert "20" in out


def test_build_no_changes_report_mentions_base():
    report = build_no_changes_report("develop")
    assert "develop" in report
    assert "批准" in report


def test_run_label_legacy_vs_subagent():
    assert "单 Agent" in run_label(True)
    assert "子 Agent" in run_label(False)


def test_constants_match_chunking_docs():
    assert PER_FILE_MAX == 8_000
    assert TOTAL_DIFF_BUDGET == 100_000
