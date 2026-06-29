"""Choose legacy single-agent vs per-file subagents for PR review."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from .config import WORKDIR
from .git_utils import PER_FILE_MAX, DiffScope, diff_one_file, list_reviewable_changed_files

ReviewMode = Literal["auto", "legacy", "subagent"]

# At or below this count of reviewable changed files, auto uses legacy (faster, cheaper).
DEFAULT_LEGACY_MAX_FILES = 6


def legacy_max_files() -> int:
    raw = os.getenv("REVIEW_LEGACY_MAX_FILES", "").strip()
    if not raw:
        return DEFAULT_LEGACY_MAX_FILES
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return DEFAULT_LEGACY_MAX_FILES


def normalize_review_mode(mode: str, *, legacy_flag: bool = False) -> ReviewMode:
    if legacy_flag:
        return "legacy"
    m = (mode or "auto").strip().lower()
    if m not in ("auto", "legacy", "subagent"):
        raise SystemExit(
            f"Invalid review mode: {mode!r}. Use auto, legacy, or subagent."
        )
    return m  # type: ignore[return-value]


def _oversized_diff_for_legacy(
    workdir: Path,
    base: str,
    files: list[str],
    *,
    scope: DiffScope | None = None,
) -> tuple[str, int] | None:
    """First reviewable path whose diff exceeds PER_FILE_MAX (inline cap), or None."""
    for path in files:
        size = len(diff_one_file(workdir, base, path, scope=scope))
        if size > PER_FILE_MAX:
            return path, size
    return None


def resolve_use_legacy(
    base: str,
    mode: ReviewMode,
    *,
    workdir: Path | None = None,
    scope: DiffScope | None = None,
) -> tuple[bool, str, int]:
    """
    Return (use_legacy, reason_for_user, reviewable_file_count).
    """
    workdir = workdir or WORKDIR
    files = list_reviewable_changed_files(workdir, base, scope=scope)
    n = len(files)
    threshold = legacy_max_files()

    if mode == "legacy":
        return True, f"legacy（手动指定）— {n} 个可审查文件", n
    if mode == "subagent":
        return False, f"subagent（手动指定）— {n} 个可审查文件", n
    # auto
    if n > threshold:
        return (
            False,
            f"auto → subagent（{n} 个可审查文件 > {threshold}）",
            n,
        )
    oversized = _oversized_diff_for_legacy(workdir, base, files, scope=scope)
    if oversized is not None:
        path, size = oversized
        return (
            False,
            f"auto → subagent（`{path}` diff {size} 字符 > {PER_FILE_MAX}）",
            n,
        )
    return (
        True,
        f"auto → legacy（{n} 个可审查文件 ≤ {threshold}，且单文件 diff ≤ {PER_FILE_MAX}）",
        n,
    )


def run_label(use_legacy: bool) -> str:
    return "单 Agent（inline diff 分块）" if use_legacy else "子 Agent 分文件 + 主 Agent 集成"
