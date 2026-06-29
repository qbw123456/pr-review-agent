"""Tests for incremental PR review helpers."""

from __future__ import annotations

from pr_review_agent.incremental_review import (
    append_review_marker,
    build_no_incremental_delta_report,
    extract_report_from_comment,
    parse_last_reviewed_sha,
    strip_review_markers,
)
from pr_review_agent.git_utils import DiffScope


def test_parse_last_reviewed_sha():
    body = "Hello\n<!-- pr-review-agent:last_sha=abc123def -->\n"
    assert parse_last_reviewed_sha(body) == "abc123def"


def test_append_review_marker():
    out = append_review_marker("## 总结\n\nok", "deadbeef")
    assert "deadbeef" in out
    assert parse_last_reviewed_sha(out) == "deadbeef"


def test_extract_report_from_comment():
    body = """<!-- pr-review-agent -->
## 🤖 PR Review Agent（自动审查）

审查范围：`feature` → `main`

## 总结

previous findings

## 结论

批准
<!-- pr-review-agent:last_sha=abc123 -->
"""
    report = extract_report_from_comment(body)
    assert "## 总结" in report
    assert "previous findings" in report
    assert "pr-review-agent" not in report


def test_build_no_incremental_delta_report_reuses_previous():
    prev = "## 总结\n\nold\n\n## 结论\n\n批准"
    out = build_no_incremental_delta_report(
        base="main",
        since_sha="aaa1111",
        head_sha="bbb2222",
        previous_report=prev,
    )
    assert "old" in out
    assert parse_last_reviewed_sha(out) == "bbb2222"


def test_diff_scope_incremental():
    s = DiffScope(base="main", since_sha="abc1234")
    assert s.is_incremental
    assert s.diff_spec == "abc1234..HEAD"


def test_strip_review_markers():
    text = "report\n<!-- pr-review-agent:last_sha=abcd1234 -->\n"
    assert strip_review_markers(text) == "report"
