"""Build fixture git repos and score agent review reports against case.yaml."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

GOLDEN_ROOT = Path(__file__).resolve().parent
CASES_DIR = GOLDEN_ROOT / "cases"


@dataclass
class CaseMeta:
    id: str
    title: str
    description: str
    checks: dict[str, Any]
    route_expect: str = "any"
    expect_no_llm: bool = False
    base: str = "main"


@dataclass
class ScoreResult:
    case_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def list_case_ids() -> list[str]:
    if not CASES_DIR.is_dir():
        return []
    ids = []
    for path in sorted(CASES_DIR.iterdir()):
        if path.is_dir() and (path / "case.yaml").is_file():
            ids.append(path.name)
    return ids


def load_case_meta(case_id: str) -> CaseMeta:
    case_dir = CASES_DIR / case_id
    meta_path = case_dir / "case.yaml"
    if not meta_path.is_file():
        raise FileNotFoundError(f"Golden case not found: {case_id} ({meta_path})")
    raw = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    return CaseMeta(
        id=raw["id"],
        title=raw["title"],
        description=(raw.get("description") or "").strip(),
        checks=raw.get("checks") or {},
        route_expect=raw.get("route_expect", "any"),
        expect_no_llm=bool(raw.get("expect_no_llm", False)),
        base=raw.get("base", "main"),
    )


def _run_git(cmd: str, cwd: Path) -> None:
    r = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"git failed ({cmd}): {err}")


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def build_fixture_repo(case_id: str, target: Path) -> Path:
    """
    Create a git repo: `main` = cases/<id>/main/*, HEAD branch `feature` = cases/<id>/pr/*.
    Returns target directory (repo root).
    """
    case_dir = CASES_DIR / case_id
    main_src = case_dir / "main"
    pr_src = case_dir / "pr"
    if not main_src.is_dir() or not pr_src.is_dir():
        raise FileNotFoundError(f"Case {case_id} needs main/ and pr/ directories")

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    _copy_tree(main_src, target)

    _run_git("git init", target)
    _run_git('git config user.email "golden@test.local"', target)
    _run_git('git config user.name "Golden Fixture"', target)
    # Disable runner/global excludes so common names like api.py are not skipped.
    _null_excludes = "NUL" if os.name == "nt" else "/dev/null"
    _run_git(f"git config core.excludesFile {_null_excludes}", target)
    _run_git("git add -f -A", target)
    _run_git('git commit -m "baseline on main"', target)
    _run_git("git branch -M main", target)

    _copy_tree(pr_src, target)
    _run_git("git checkout -b feature", target)
    _run_git("git add -f -A", target)
    _run_git('git commit -m "PR changes with intentional bugs"', target)

    return target


def fixture_diff_stat(repo: Path, base: str = "main") -> str:
    r = subprocess.run(
        f"git diff {base}...HEAD --stat",
        shell=True,
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (r.stdout or "").strip()


# Match severity labels (e.g. "### 严重", "- **严重**"), not compounds like "严重程度".
_SEVERITY_FORBIDDEN_RE = re.compile(
    r"(?:^#{2,4}\s*严重\b"
    r"|[-*]\s*\*\*严重\*\*"
    r"|\*\*严重\*\*\s*[—\-:：])",
    re.MULTILINE,
)


def _mentions_forbidden(text: str, forbidden: str) -> bool:
    """Return True if *forbidden* appears in *text* per scoring rules."""
    lower = text.lower()
    key = forbidden.lower()
    if key == "严重":
        return _SEVERITY_FORBIDDEN_RE.search(text) is not None
    return key in lower


def score_report(case_id: str, report: str, *, meta: CaseMeta | None = None) -> ScoreResult:
    meta = meta or load_case_meta(case_id)
    checks = meta.checks
    text = report or ""
    lower = text.lower()
    failures: list[str] = []
    warnings: list[str] = []

    for path in checks.get("must_mention_files") or []:
        if path.lower() not in lower and f"`{path}`".lower() not in lower:
            failures.append(f"must_mention_files: missing {path!r}")

    needles = checks.get("must_contain_any") or []
    if needles and not any(str(n).lower() in lower for n in needles if n is not None):
        failures.append(f"must_contain_any: none matched any of {needles!r}")

    regexes = checks.get("must_contain_any_regex") or []
    if regexes and not any(re.search(p, text, re.IGNORECASE) for p in regexes):
        failures.append(f"must_contain_any_regex: no match for any of {regexes!r}")

    for forbidden in checks.get("must_not_mention") or []:
        if _mentions_forbidden(text, forbidden):
            failures.append(f"must_not_mention: found {forbidden!r}")

    if meta.expect_no_llm and "未调用大模型" not in text:
        warnings.append("expect_no_llm: report does not contain '未调用大模型' (LLM may still have run)")

    return ScoreResult(
        case_id=case_id,
        passed=len(failures) == 0,
        failures=failures,
        warnings=warnings,
    )
