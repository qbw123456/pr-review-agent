"""Incremental PR review: only re-run LLM on since_sha..HEAD delta."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import WORKDIR
from .git_utils import DiffScope, list_changed_files

LAST_SHA_MARKER_RE = re.compile(
    r"<!--\s*pr-review-agent:last_sha=([0-9a-fA-F]{4,40})\s*-->"
)
AGENT_COMMENT_MARKER = "<!-- pr-review-agent -->"


@dataclass(frozen=True)
class IncrementalReviewState:
    scope: DiffScope
    head_sha: str
    since_sha: str | None
    previous_report: str | None
    mode_label: str


def resolve_head_sha(workdir: Path | None = None) -> str:
    workdir = workdir or WORKDIR
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        raise RuntimeError("git rev-parse HEAD failed")
    return out


def is_ancestor(
    ancestor: str,
    descendant: str = "HEAD",
    *,
    workdir: Path | None = None,
) -> bool:
    workdir = workdir or WORKDIR
    r = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return r.returncode == 0


def parse_last_reviewed_sha(text: str) -> str | None:
    match = LAST_SHA_MARKER_RE.search(text or "")
    if not match:
        return None
    return match.group(1)


def strip_review_markers(text: str) -> str:
    cleaned = LAST_SHA_MARKER_RE.sub("", text or "")
    return cleaned.strip()


def extract_report_from_comment(comment_body: str) -> str:
    """Return Markdown report portion from a PR comment body."""
    body = comment_body or ""
    if AGENT_COMMENT_MARKER not in body:
        return strip_review_markers(body)

    lines = body.splitlines()
    out: list[str] = []
    past_header = False
    for line in lines:
        if not past_header:
            if line.strip().startswith("## ") and "PR Review Agent" in line:
                past_header = True
            continue
        if line.strip().startswith("审查范围："):
            continue
        if not past_header:
            continue
        out.append(line)

    report = "\n".join(out).strip()
    report = strip_review_markers(report)
    # Drop leading empty lines after optional scope line
    while report.startswith("\n"):
        report = report.lstrip("\n")
    return report


def append_review_marker(report: str, head_sha: str) -> str:
    body = strip_review_markers(report).rstrip()
    marker = f"<!-- pr-review-agent:last_sha={head_sha} -->"
    return f"{body}\n\n{marker}\n"


def incremental_enabled() -> bool:
    raw = os.getenv("REVIEW_INCREMENTAL", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def force_full_review() -> bool:
    raw = os.getenv("REVIEW_FULL", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def read_previous_report(path: str | None = None) -> str | None:
    p = (path or os.getenv("REVIEW_PREVIOUS_REPORT", "")).strip()
    if not p:
        return None
    file_path = Path(p)
    if not file_path.is_file():
        return None
    text = file_path.read_text(encoding="utf-8").strip()
    return text or None


def resolve_incremental_state(
    base: str,
    *,
    since_sha: str | None = None,
    previous_report: str | None = None,
    force_full: bool = False,
    workdir: Path | None = None,
) -> IncrementalReviewState:
    workdir = workdir or WORKDIR
    head_sha = resolve_head_sha(workdir)

    env_since = os.getenv("REVIEW_SINCE_SHA", "").strip()
    since = (since_sha or env_since or "").strip() or None
    prev = previous_report if previous_report is not None else read_previous_report()

    if force_full or force_full_review() or not incremental_enabled():
        return IncrementalReviewState(
            scope=DiffScope(base=base),
            head_sha=head_sha,
            since_sha=None,
            previous_report=prev,
            mode_label="全量审查（手动或 REVIEW_FULL）",
        )

    if not since:
        return IncrementalReviewState(
            scope=DiffScope(base=base),
            head_sha=head_sha,
            since_sha=None,
            previous_report=prev,
            mode_label="全量审查（首次或无上次 SHA）",
        )

    if since == head_sha:
        return IncrementalReviewState(
            scope=DiffScope(base=base, since_sha=since, head=head_sha),
            head_sha=head_sha,
            since_sha=since,
            previous_report=prev,
            mode_label="增量审查（无新 commit）",
        )

    if not is_ancestor(since, head_sha, workdir=workdir):
        return IncrementalReviewState(
            scope=DiffScope(base=base),
            head_sha=head_sha,
            since_sha=None,
            previous_report=prev,
            mode_label=f"全量审查（上次 SHA `{since[:7]}` 不可达，可能 rebase/force-push）",
        )

    delta_files = list_changed_files(workdir, base, scope=DiffScope(base=base, since_sha=since))
    if not delta_files:
        return IncrementalReviewState(
            scope=DiffScope(base=base, since_sha=since, head=head_sha),
            head_sha=head_sha,
            since_sha=since,
            previous_report=prev,
            mode_label=f"增量审查（`{since[:7]}..{head_sha[:7]}` 无文件变更）",
        )

    return IncrementalReviewState(
        scope=DiffScope(base=base, since_sha=since, head=head_sha),
        head_sha=head_sha,
        since_sha=since,
        previous_report=prev,
        mode_label=(
            f"增量审查（`{since[:7]}..{head_sha[:7]}`，"
            f"{len(delta_files)} 个变更文件）"
        ),
    )


def build_no_incremental_delta_report(
    *,
    base: str,
    since_sha: str,
    head_sha: str,
    previous_report: str | None,
) -> str:
    if previous_report:
        note = (
            f"## 总结\n\n"
            f"本次 push（`{since_sha[:7]}..{head_sha[:7]}`）**无新的文件变更**。"
            f"沿用上次审查结论。\n"
        )
        prev = strip_review_markers(previous_report)
        if prev.lstrip().startswith("## 总结"):
            return append_review_marker(prev, head_sha)
        return append_review_marker(f"{note}\n{prev}", head_sha)

    return append_review_marker(
        f"## 总结\n\n"
        f"相对上次审查 `{since_sha[:7]}` 至 `{head_sha[:7]}` 无文件变更，未调用大模型。\n\n"
        f"## 变更文件\n\n（无新增）\n\n"
        f"## 发现\n\n（无新增）\n\n"
        f"## 结论\n\n**批准** — 本次 push 无增量 diff。",
        head_sha,
    )
