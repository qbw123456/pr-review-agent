"""Git helpers for PR review (pre-fetch diff for the agent)."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Per-file diff cap in the initial user message
PER_FILE_MAX = 8_000
# Total inlined diff body budget (excludes status/stat/headers)
TOTAL_DIFF_BUDGET = 100_000
# Cap for lightweight git sections
SECTION_MAX = 10_000

REVIEWABLE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".toml",
    ".sql",
    ".sh",
    ".html",
    ".css",
    ".vue",
    ".rb",
    ".php",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
}

SKIP_INLINE_SUBSTRINGS = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "go.sum",
    ".min.js",
    ".min.css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".zip",
    ".jar",
)


def _run_git(cmd: str, workdir: Path) -> str:
    r = subprocess.run(
        cmd,
        shell=True,
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return ((r.stdout or "") + (r.stderr or "")).strip()


@dataclass(frozen=True)
class DiffScope:
    """Diff range for PR review: full PR (base...HEAD) or incremental (since..HEAD)."""

    base: str = "main"
    since_sha: str | None = None
    head: str = "HEAD"

    @property
    def is_incremental(self) -> bool:
        return bool(self.since_sha)

    @property
    def diff_spec(self) -> str:
        if self.since_sha:
            return f"{self.since_sha}..{self.head}"
        return f"{self.base}...{self.head}"

    def describe(self) -> str:
        if self.is_incremental:
            return f"`{self.diff_spec}`（增量）"
        return f"`{self.base}...{self.head}`（全 PR）"


def _resolve_scope(base: str, scope: DiffScope | None) -> DiffScope:
    return scope if scope is not None else DiffScope(base=base)


def _cap(text: str, limit: int, label: str) -> str:
    if not text:
        return "(no output)"
    if len(text) <= limit:
        return text
    return (
        f"{text[:limit]}\n\n"
        f"... [truncated: {label}, total {len(text)} chars, "
        f"use read_file or git diff -- <path>]"
    )


def list_changed_files(
    workdir: Path,
    base: str = "main",
    *,
    scope: DiffScope | None = None,
) -> list[str]:
    """Paths changed in the active diff scope, excluding empty lines."""
    s = _resolve_scope(base, scope)
    out = _run_git(f"git diff {s.diff_spec} --name-only", workdir)
    if not out or out == "(no output)":
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def has_pr_changes(
    workdir: Path,
    base: str = "main",
    *,
    scope: DiffScope | None = None,
) -> bool:
    return len(list_changed_files(workdir, base, scope=scope)) > 0


def is_reviewable_file(path: str) -> bool:
    """Whether this changed path should get a dedicated file review subagent."""
    lower = path.lower().replace("\\", "/")
    if any(skip in lower for skip in SKIP_INLINE_SUBSTRINGS):
        return False
    return any(lower.endswith(ext) for ext in REVIEWABLE_SUFFIXES)


def should_inline_diff(path: str) -> bool:
    """Whether to run per-file git diff for this path (vs omitted/skipped only)."""
    return is_reviewable_file(path)


def list_reviewable_changed_files(
    workdir: Path,
    base: str = "main",
    *,
    scope: DiffScope | None = None,
) -> list[str]:
    return [
        p
        for p in list_changed_files(workdir, base, scope=scope)
        if is_reviewable_file(p)
    ]


def diff_one_file(
    workdir: Path,
    base: str,
    path: str,
    *,
    scope: DiffScope | None = None,
) -> str:
    s = _resolve_scope(base, scope)
    quoted = shlex.quote(path)
    return _run_git(f"git diff {s.diff_spec} -- {quoted}", workdir)


def build_no_changes_report(base: str = "main") -> str:
    return f"""## 总结

当前分支相对 `{base}` **没有可审查的代码变更**（`git diff {base}...HEAD` 为空）。未调用大模型。

## 变更文件

（无）

## 发现

（无 — 无 diff）

## 结论

**批准** — 无相对 `{base}` 的增量改动，无需代码审查。"""


def build_lock_only_report(base: str = "main", *, changed_files: list[str] | None = None) -> str:
    """Template report when only lock / non-reviewable files changed."""
    files = changed_files or []
    file_lines = "\n".join(f"- `{p}`" for p in files) if files else "（无）"
    stat = ""
    if files:
        try:
            stat = _cap(
                _run_git(f"git diff {base}...HEAD --stat", Path.cwd()),
                SECTION_MAX,
                "diff stat",
            )
        except Exception:
            stat = ""

    stat_block = f"\n\n```\n{stat}\n```" if stat else ""

    return f"""## 总结

当前分支相对 `{base}` 仅有 **lock / 生成物** 等非源码审查类变更，无可审查的 `.py` 等源码 diff。未调用大模型。

## 变更文件

{file_lines}{stat_block}

## 发现

无

## 结论

**批准** — 变更为锁定文件或生成物更新，无源码逻辑需深度审查。"""


def collect_pr_context(
    workdir: Path,
    base: str = "main",
    *,
    scope: DiffScope | None = None,
) -> str:
    """Gather git context with per-file chunked diffs (no silent full-diff truncation)."""
    s = _resolve_scope(base, scope)
    files = list_changed_files(workdir, base, scope=s)
    sections: list[str] = []

    status = _cap(_run_git("git status -sb", workdir), SECTION_MAX, "git status")
    sections.append(f"### git status\n```\n{status}\n```")

    name_only = _cap(
        _run_git(f"git diff {s.diff_spec} --name-only", workdir),
        SECTION_MAX,
        "name-only",
    )
    sections.append(
        f"### changed files ({len(files)} vs {s.describe()})\n```\n{name_only}\n```"
    )

    stat = _cap(
        _run_git(f"git diff {s.diff_spec} --stat", workdir),
        SECTION_MAX,
        "diff stat",
    )
    sections.append(f"### diff stat\n```\n{stat}\n```")

    inlined: list[str] = []
    omitted: list[str] = []
    budget_used = 0

    sections.append("### per-file diffs (chunked)\n")

    for path in files:
        if budget_used >= TOTAL_DIFF_BUDGET:
            omitted.append(f"{path} (omitted: total diff budget exceeded)")
            continue

        if not should_inline_diff(path):
            omitted.append(f"{path} (skipped: non-reviewable type for inline diff)")
            continue

        raw = diff_one_file(workdir, base, path, scope=s)
        if not raw:
            inlined.append(f"#### `{path}`\n\n(no diff text)\n")
            continue

        truncated = len(raw) > PER_FILE_MAX
        piece = raw[:PER_FILE_MAX] if truncated else raw

        if budget_used + len(piece) > TOTAL_DIFF_BUDGET:
            omitted.append(f"{path} (omitted: total diff budget exceeded)")
            budget_used = TOTAL_DIFF_BUDGET
            continue

        budget_used += len(piece)
        block = f"#### `{path}`\n```diff\n{piece}\n```"
        if truncated:
            block += (
                f"\n\n> **truncated: true** — file diff has {len(raw)} chars; "
                f"showing first {PER_FILE_MAX}. "
                f"MUST `read_file` `{path}` or "
                f"`git diff {s.diff_spec} -- {path}` before the final report.\n"
            )
        inlined.append(block)

    if inlined:
        sections.append("\n\n".join(inlined))
    else:
        sections.append("(no per-file diffs inlined)\n")

    if omitted:
        sections.append(
            "### omitted / not inlined\n"
            "These paths have **no** (or incomplete) diff body in this message. "
            "For source files you must still `read_file` per system prompt; "
            "use `git diff` when you need the exact patch:\n\n"
            + "\n".join(f"- {line}" for line in omitted)
        )

    return "\n\n".join(sections)


def collect_pr_context_light(
    workdir: Path,
    base: str = "main",
    *,
    scope: DiffScope | None = None,
    full_pr_scope: DiffScope | None = None,
) -> str:
    """Lightweight git context for the integration agent (no per-file diff bodies)."""
    s = _resolve_scope(base, scope)
    full = full_pr_scope or DiffScope(base=base)
    files = list_changed_files(workdir, base, scope=s)
    reviewable = [p for p in files if is_reviewable_file(p)]
    full_files = list_changed_files(workdir, base, scope=full)
    sections: list[str] = []

    status = _cap(_run_git("git status -sb", workdir), SECTION_MAX, "git status")
    sections.append(f"### git status\n```\n{status}\n```")

    name_only = _cap(
        _run_git(f"git diff {s.diff_spec} --name-only", workdir),
        SECTION_MAX,
        "name-only",
    )
    sections.append(
        f"### changed files in review scope ({len(files)} total, "
        f"{len(reviewable)} reviewable vs {s.describe()})\n"
        f"```\n{name_only}\n```"
    )

    stat = _cap(
        _run_git(f"git diff {s.diff_spec} --stat", workdir),
        SECTION_MAX,
        "diff stat",
    )
    sections.append(f"### diff stat (review scope)\n```\n{stat}\n```")

    if full.diff_spec != s.diff_spec:
        full_name_only = _cap(
            _run_git(f"git diff {full.diff_spec} --name-only", workdir),
            SECTION_MAX,
            "full-pr-name-only",
        )
        sections.append(
            f"### full PR changed files ({len(full_files)} total vs {full.describe()})\n"
            f"```\n{full_name_only}\n```"
        )
        full_stat = _cap(
            _run_git(f"git diff {full.diff_spec} --stat", workdir),
            SECTION_MAX,
            "full-pr-stat",
        )
        sections.append(f"### full PR diff stat\n```\n{full_stat}\n```")
    return "\n\n".join(sections)


def build_review_request(base: str = "main", *, scope: DiffScope | None = None) -> str:
    s = _resolve_scope(base, scope)
    scope_note = ""
    if s.is_incremental:
        scope_note = (
            f"\n> **增量审查**：下列 diff 仅为 `{s.diff_spec}`（本次 push 新增变更），"
            f"非整个 PR。全 PR 范围仍为 `{s.base}...HEAD`。\n"
        )
    return f"""Review the current branch changes against `{base}`.
{scope_note}
Git context below uses **per-file chunked diffs** (not one monolithic diff).
Paths marked **truncated** or listed under **omitted / not inlined** are incomplete here —
you MUST `read_file` (and/or `git diff {s.diff_spec} -- <path>`) before the final report.
You must read_file every changed reviewable source/config file in this scope (see system prompt).

{collect_pr_context(Path.cwd(), base=base, scope=s)}

Produce the structured Markdown review report defined in your instructions."""
