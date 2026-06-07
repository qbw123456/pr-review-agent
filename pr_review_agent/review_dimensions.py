"""Classify PR changes by review dimension and build agent clusters."""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .git_utils import (
    SKIP_INLINE_SUBSTRINGS,
    diff_one_file,
    is_reviewable_file,
    list_changed_files,
    list_reviewable_changed_files,
)

# Max files per dimension subagent cluster (same dimension, batched if exceeded).
DEFAULT_CLUSTER_MAX_FILES = 8


class ReviewDimension(str, Enum):
    LOCK = "lock"
    DOC = "doc"
    CONFIG = "config"
    API = "api"
    SECURITY = "security"
    LOGIC = "logic"


DIMENSION_LABELS: dict[ReviewDimension, str] = {
    ReviewDimension.LOCK: "Lock / 生成物",
    ReviewDimension.DOC: "文档",
    ReviewDimension.CONFIG: "配置 / 基础设施",
    ReviewDimension.API: "API / 签名",
    ReviewDimension.SECURITY: "安全",
    ReviewDimension.LOGIC: "逻辑 / 正确性",
}

# Priority when merging / displaying (higher risk first).
DIMENSION_PRIORITY: tuple[ReviewDimension, ...] = (
    ReviewDimension.API,
    ReviewDimension.SECURITY,
    ReviewDimension.LOGIC,
    ReviewDimension.CONFIG,
    ReviewDimension.DOC,
    ReviewDimension.LOCK,
)

_SECURITY_PATH_HINTS = (
    "auth",
    "security",
    "permission",
    "crypto",
    "password",
    "login",
    "credential",
    "token",
)

_SECURITY_DIFF_HINTS = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "token",
    "sql",
    "inject",
    "exec(",
    "eval(",
    "pickle",
    "subprocess",
    "os.system",
    "shell=true",
    "permission",
    "authorize",
    "authenticate",
)

_API_DIFF_PATTERNS = (
    re.compile(r"^[+-].*\bdef\s+\w+\s*\(", re.MULTILINE),
    re.compile(r"^[+-].*\b(async\s+def|class)\s+\w+", re.MULTILINE),
    re.compile(r"^[+-].*@(app|router)\.\w+", re.MULTILINE),
    re.compile(r"^[+-].*\b(export\s+)?(async\s+)?function\s+\w+", re.MULTILINE),
    re.compile(r"^[+-].*=>\s*", re.MULTILINE),
    re.compile(r"^[+-].*\btype\s+\w+\s*=", re.MULTILINE),
)

_DEF_NAME_RE = re.compile(r"^[+-]\s*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)


def is_lock_file(path: str) -> bool:
    lower = path.lower().replace("\\", "/")
    return any(skip in lower for skip in SKIP_INLINE_SUBSTRINGS)


def _looks_like_api_change(diff: str) -> bool:
    if not diff:
        return False
    return any(p.search(diff) for p in _API_DIFF_PATTERNS)


def _looks_like_security(path: str, diff: str) -> bool:
    lower = path.lower().replace("\\", "/")
    if any(h in lower for h in _SECURITY_PATH_HINTS):
        return True
    diff_lower = diff.lower()
    return any(h in diff_lower for h in _SECURITY_DIFF_HINTS)


def classify_file(path: str, diff: str = "") -> ReviewDimension:
    """Assign a single primary review dimension to a changed path."""
    lower = path.lower().replace("\\", "/")

    if is_lock_file(path):
        return ReviewDimension.LOCK

    if lower.endswith(".md"):
        return ReviewDimension.DOC

    if lower.endswith((".yaml", ".yml", ".toml", ".env", ".env.example")):
        return ReviewDimension.CONFIG

    if lower.endswith(".json") and "lock" not in lower:
        return ReviewDimension.CONFIG

    if lower.endswith(("dockerfile",)) or "docker-compose" in lower:
        return ReviewDimension.CONFIG

    if _looks_like_security(path, diff):
        return ReviewDimension.SECURITY

    if _looks_like_api_change(diff):
        return ReviewDimension.API

    if is_reviewable_file(path):
        return ReviewDimension.LOGIC

    return ReviewDimension.CONFIG


def cluster_max_files() -> int:
    import os

    raw = os.getenv("REVIEW_CLUSTER_MAX_FILES", "").strip()
    if not raw:
        return DEFAULT_CLUSTER_MAX_FILES
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_CLUSTER_MAX_FILES


@dataclass
class DimensionCluster:
    dimension: ReviewDimension
    files: list[str] = field(default_factory=list)
    cluster_index: int = 0


@dataclass
class PRDimensionPlan:
    base: str
    all_changed: list[str]
    reviewable_files: list[str]
    files_by_dimension: dict[ReviewDimension, list[str]]
    active_dimensions: list[ReviewDimension]
    lock_only: bool
    clusters: list[DimensionCluster] = field(default_factory=list)

    def dimension_summary(self) -> str:
        parts = []
        for dim in self.active_dimensions:
            paths = self.files_by_dimension.get(dim, [])
            if paths:
                label = DIMENSION_LABELS[dim]
                parts.append(f"{label}({len(paths)})")
        lock_n = len(self.files_by_dimension.get(ReviewDimension.LOCK, []))
        if lock_n:
            parts.append(f"{DIMENSION_LABELS[ReviewDimension.LOCK]}({lock_n})")
        return "、".join(parts) if parts else "（无）"


def _sort_dimensions(dims: set[ReviewDimension]) -> list[ReviewDimension]:
    ordered = [d for d in DIMENSION_PRIORITY if d in dims]
    for d in dims:
        if d not in ordered:
            ordered.append(d)
    return ordered


def _build_clusters(
    files_by_dimension: dict[ReviewDimension, list[str]],
) -> list[DimensionCluster]:
    max_per = cluster_max_files()
    clusters: list[DimensionCluster] = []
    for dim in DIMENSION_PRIORITY:
        paths = files_by_dimension.get(dim, [])
        if not paths or dim == ReviewDimension.LOCK:
            continue
        for i in range(0, len(paths), max_per):
            chunk = paths[i : i + max_per]
            clusters.append(
                DimensionCluster(
                    dimension=dim,
                    files=chunk,
                    cluster_index=i // max_per,
                )
            )
    return clusters


def build_pr_dimension_plan(workdir: Path, base: str = "main") -> PRDimensionPlan:
    all_changed = list_changed_files(workdir, base)
    reviewable = list_reviewable_changed_files(workdir, base)

    files_by_dimension: dict[ReviewDimension, list[str]] = {
        d: [] for d in ReviewDimension
    }

    for path in all_changed:
        diff = diff_one_file(workdir, base, path) if is_reviewable_file(path) else ""
        dim = classify_file(path, diff)
        files_by_dimension[dim].append(path)

    active = _sort_dimensions(
        {d for d, paths in files_by_dimension.items() if paths and d != ReviewDimension.LOCK}
    )

    plan = PRDimensionPlan(
        base=base,
        all_changed=all_changed,
        reviewable_files=reviewable,
        files_by_dimension=files_by_dimension,
        active_dimensions=active,
        lock_only=len(reviewable) == 0 and len(all_changed) > 0,
    )
    plan.clusters = _build_clusters(files_by_dimension)
    return plan


def extract_api_symbols_from_diff(diff: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in _DEF_NAME_RE.finditer(diff):
        name = match.group(1)
        if name.startswith("_") or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _run_grep(workdir: Path, pattern: str) -> str:
    quoted = shlex.quote(pattern)
    for cmd in (
        f"rg -l --glob '!**/.git/**' {quoted} .",
        f"grep -rl --exclude-dir=.git {quoted} .",
    ):
        r = subprocess.run(
            cmd,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if r.returncode in (0, 1):
            return (r.stdout or "").strip()
    return ""


def find_callers_for_api_files(
    workdir: Path,
    base: str,
    api_files: list[str],
) -> dict[str, list[str]]:
    """Return map symbol -> caller file paths (repo-relative, excluding defining file)."""
    result: dict[str, list[str]] = {}
    for path in api_files:
        diff = diff_one_file(workdir, base, path)
        for symbol in extract_api_symbols_from_diff(diff):
            raw = _run_grep(workdir, symbol)
            callers: list[str] = []
            for line in raw.splitlines():
                rel = line.strip().lstrip("./").replace("\\", "/")
                if not rel or rel == path:
                    continue
                if rel.endswith(".py") or rel.endswith((".js", ".ts", ".tsx", ".jsx")):
                    if rel not in callers:
                        callers.append(rel)
            if callers:
                result[symbol] = callers[:10]
    return result


def format_api_callers_block(caller_map: dict[str, list[str]]) -> str:
    if not caller_map:
        return ""
    lines = ["## 预检：API 符号调用方（自动 grep，供审查参考）", ""]
    for symbol, paths in sorted(caller_map.items()):
        lines.append(f"- `{symbol}` → " + ", ".join(f"`{p}`" for p in paths))
    lines.append("")
    lines.append(
        "请 read_file 关键调用方并在报告中写明路径；若调用方仍按旧约定使用，标为跨文件风险。"
    )
    return "\n".join(lines)
