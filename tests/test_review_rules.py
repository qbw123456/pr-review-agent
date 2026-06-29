"""Tests for review-rules.yaml loading and prompt injection."""

from __future__ import annotations

from pathlib import Path

import pytest

from pr_review_agent.prompts import (
    build_integration_system_prompt,
    build_legacy_system_prompt,
    build_subagent_system_prompt,
)
from pr_review_agent.review_dimensions import ChangeWeight, ReviewDimension
from pr_review_agent.review_rules import (
    clear_review_rules_cache,
    format_review_rules_block,
    load_review_rules,
)


@pytest.fixture(autouse=True)
def _clear_rules_cache():
    clear_review_rules_cache()
    yield
    clear_review_rules_cache()


def test_load_default_review_rules():
    config = load_review_rules(Path(__file__).resolve().parents[1] / "review-rules.yaml")
    assert config is not None
    assert len(config.rules) >= 3
    assert len(config.negative_examples) >= 2
    assert config.rules[0].id


def test_format_filters_by_dimension_and_weight():
    block = format_review_rules_block(
        dimensions=[ReviewDimension.CODE],
        weight=ChangeWeight.TRIVIAL,
    )
    assert "trivial-no-nitpicks" in block or "不要报 docstring" in block
    assert "lock-only-scope" not in block


def test_format_lock_dimension_includes_lock_rules():
    block = format_review_rules_block(dimensions=[ReviewDimension.LOCK])
    assert "lock-only-scope" in block or "lockfile" in block
    assert "lock-fabricated-security" in block or "SQL 注入" in block


def test_format_include_all_for_integration():
    block = format_review_rules_block(include_all=True)
    assert "团队审查规则" in block
    assert "负向样例" in block
    assert "lock-only-scope" in block or "lockfile" in block


def test_legacy_prompt_includes_team_rules():
    prompt = build_legacy_system_prompt([ReviewDimension.CODE, ReviewDimension.SECURITY])
    assert "review-rules.yaml" in prompt
    assert "changed-files-only" in prompt or "实际变更" in prompt


def test_subagent_prompt_includes_filtered_rules():
    prompt = build_subagent_system_prompt(
        ReviewDimension.CODE,
        weight=ChangeWeight.TRIVIAL,
    )
    assert "review-rules.yaml" in prompt
    assert "docstring" in prompt or "trivial" in prompt.lower()


def test_integration_prompt_includes_all_rules():
    prompt = build_integration_system_prompt()
    assert "review-rules.yaml" in prompt
    assert "负向样例" in prompt
