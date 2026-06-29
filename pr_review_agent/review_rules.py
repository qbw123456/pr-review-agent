"""Load team review rules and negative examples from review-rules.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import WORKDIR
from .review_dimensions import ChangeWeight, ReviewDimension

DEFAULT_RULES_FILENAME = "review-rules.yaml"

_DIMENSION_ALIASES: dict[str, ReviewDimension] = {
    "lock": ReviewDimension.LOCK,
    "meta": ReviewDimension.META,
    "code": ReviewDimension.CODE,
    "security": ReviewDimension.SECURITY,
}

_WEIGHT_ALIASES: dict[str, ChangeWeight] = {
    "trivial": ChangeWeight.TRIVIAL,
    "normal": ChangeWeight.NORMAL,
    "heavy": ChangeWeight.HEAVY,
}


@dataclass(frozen=True)
class ReviewRule:
    id: str
    text: str
    dimensions: frozenset[ReviewDimension] = field(default_factory=frozenset)
    weights: frozenset[ChangeWeight] = field(default_factory=frozenset)


@dataclass(frozen=True)
class NegativeExample:
    id: str
    bad: str
    reason: str
    dimensions: frozenset[ReviewDimension] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ReviewRulesConfig:
    rules: tuple[ReviewRule, ...] = ()
    negative_examples: tuple[NegativeExample, ...] = ()


def rules_file_path() -> Path:
    raw = os.getenv("REVIEW_RULES_PATH", "").strip()
    if raw:
        return Path(raw)
    return WORKDIR / DEFAULT_RULES_FILENAME


def _parse_dimensions(raw: Any) -> frozenset[ReviewDimension]:
    if not raw:
        return frozenset()
    out: set[ReviewDimension] = set()
    for item in raw:
        key = str(item).strip().lower()
        if key in _DIMENSION_ALIASES:
            out.add(_DIMENSION_ALIASES[key])
    return frozenset(out)


def _parse_weights(raw: Any) -> frozenset[ChangeWeight]:
    if not raw:
        return frozenset()
    out: set[ChangeWeight] = set()
    for item in raw:
        key = str(item).strip().lower()
        if key in _WEIGHT_ALIASES:
            out.add(_WEIGHT_ALIASES[key])
    return frozenset(out)


def _rule_matches(
    rule: ReviewRule,
    *,
    dimensions: frozenset[ReviewDimension] | None,
    weight: ChangeWeight | None,
) -> bool:
    if rule.dimensions:
        if not dimensions or not (rule.dimensions & dimensions):
            return False
    if rule.weights:
        if weight is None or weight not in rule.weights:
            return False
    return True


def _example_matches(
    example: NegativeExample,
    *,
    dimensions: frozenset[ReviewDimension] | None,
) -> bool:
    if example.dimensions:
        if not dimensions or not (example.dimensions & dimensions):
            return False
    return True


def load_review_rules(path: Path | None = None) -> ReviewRulesConfig | None:
    """Return parsed rules config, or None if the file is missing."""
    rules_path = path or rules_file_path()
    if not rules_path.is_file():
        return None
    raw = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    if not raw or not isinstance(raw, dict):
        return ReviewRulesConfig()

    rules: list[ReviewRule] = []
    for idx, item in enumerate(raw.get("rules") or []):
        if not isinstance(item, dict):
            continue
        if item.get("disabled"):
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        rule_id = str(item.get("id") or f"rule-{idx + 1}").strip()
        rules.append(
            ReviewRule(
                id=rule_id,
                text=text,
                dimensions=_parse_dimensions(item.get("dimensions")),
                weights=_parse_weights(item.get("weights")),
            )
        )

    examples: list[NegativeExample] = []
    for idx, item in enumerate(raw.get("negative_examples") or []):
        if not isinstance(item, dict):
            continue
        if item.get("disabled"):
            continue
        bad = (item.get("bad") or "").strip()
        reason = (item.get("reason") or "").strip()
        if not bad or not reason:
            continue
        example_id = str(item.get("id") or f"example-{idx + 1}").strip()
        examples.append(
            NegativeExample(
                id=example_id,
                bad=bad,
                reason=reason,
                dimensions=_parse_dimensions(item.get("dimensions")),
            )
        )

    return ReviewRulesConfig(
        rules=tuple(rules),
        negative_examples=tuple(examples),
    )


@lru_cache(maxsize=4)
def _cached_review_rules(resolved: str) -> ReviewRulesConfig | None:
    return load_review_rules(Path(resolved))


def get_review_rules() -> ReviewRulesConfig | None:
    path = rules_file_path().resolve()
    return _cached_review_rules(str(path))


def clear_review_rules_cache() -> None:
    _cached_review_rules.cache_clear()


def format_review_rules_block(
    *,
    dimensions: list[ReviewDimension] | None = None,
    weight: ChangeWeight | None = None,
    include_all: bool = False,
) -> str:
    """
    Build a prompt section from review-rules.yaml.

    When include_all is True, every rule/example is included (integration agent).
    Otherwise filter by active dimensions and optional weight.
    """
    config = get_review_rules()
    if not config:
        return ""

    dim_set: frozenset[ReviewDimension] | None
    if include_all or dimensions is None:
        dim_set = None
    else:
        dim_set = frozenset(dimensions)

    matched_rules = [
        r
        for r in config.rules
        if include_all
        or _rule_matches(r, dimensions=dim_set, weight=weight)
    ]
    matched_examples = [
        e
        for e in config.negative_examples
        if include_all or _example_matches(e, dimensions=dim_set)
    ]

    if not matched_rules and not matched_examples:
        return ""

    sections: list[str] = ["**团队审查规则（review-rules.yaml）**"]

    if matched_rules:
        sections.append("")
        sections.append("须遵守：")
        for rule in matched_rules:
            sections.append(f"- [{rule.id}] {rule.text.strip()}")

    if matched_examples:
        sections.append("")
        sections.append("负向样例（不要这样报）：")
        for ex in matched_examples:
            sections.append(f'- 误报示例：「{ex.bad}」')
            sections.append(f"  原因：{ex.reason}")

    return "\n".join(sections)
