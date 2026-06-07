"""Golden PR fixtures: git setup and report scoring (no LLM in default CI)."""

from pathlib import Path

import pytest

from pr_review_agent.review_strategy import resolve_use_legacy

from tests.golden.loader import (
    build_fixture_repo,
    list_case_ids,
    load_case_meta,
    score_report,
)


@pytest.fixture
def golden_repo(tmp_path: Path):
    """Build a disposable git repo; teardown is automatic via tmp_path."""

    def _build(case_id: str) -> Path:
        return build_fixture_repo(case_id, tmp_path / case_id)

    return _build


@pytest.mark.parametrize("case_id", list_case_ids())
def test_golden_fixture_builds_git(case_id: str, golden_repo):
    repo = golden_repo(case_id)
    assert (repo / ".git").exists()
    stat = __import__("subprocess").run(
        "git diff main...HEAD --name-only",
        shell=True,
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert stat.returncode == 0
    assert stat.stdout.strip(), f"{case_id}: expected non-empty diff vs main"


@pytest.mark.parametrize("case_id", list_case_ids())
def test_golden_route_expect(case_id: str, golden_repo):
    meta = load_case_meta(case_id)
    if meta.route_expect == "any":
        pytest.skip("no route_expect")
    repo = golden_repo(case_id)
    use_legacy, _reason, _n = resolve_use_legacy("main", "auto", workdir=repo)
    if meta.route_expect == "legacy":
        assert use_legacy is True, _reason
    elif meta.route_expect == "subagent":
        assert use_legacy is False, _reason


def test_score_report_pass_sample():
    meta = load_case_meta("obvious_none")
    report = """
## 发现
- **严重** `utils.py`: 未检查 `None`，`name.upper()` 可能 AttributeError
## 结论
请求修改
"""
    result = score_report("obvious_none", report, meta=meta)
    assert result.passed, result.failures


def test_score_report_fail_missing_file():
    meta = load_case_meta("obvious_none")
    report = "## 发现\n无问题\n"
    result = score_report("obvious_none", report, meta=meta)
    assert not result.passed
    assert any("utils.py" in f for f in result.failures)


def test_score_report_lock_only_allows_severity_degree_phrase():
    meta = load_case_meta("lock_only")
    report = """
## 发现
无需标注严重程度的问题。锁定文件的变更属于常规操作。

## 结论
批准 — 变更为标准的依赖锁定文件更新，无代码质量问题。
"""
    result = score_report("lock_only", report, meta=meta)
    assert result.passed, result.failures


def test_score_report_lock_only_fails_severity_section():
    meta = load_case_meta("lock_only")
    report = """
## 发现

### 严重
- **calculator.py** — 编造的问题

## 结论
需要修改
"""
    result = score_report("lock_only", report, meta=meta)
    assert not result.passed
    assert any("严重" in f for f in result.failures)
    assert any("calculator.py" in f for f in result.failures)


def test_list_cases_non_empty():
    assert len(list_case_ids()) >= 4
