"""Git helpers for PR review (pre-fetch diff for the agent)."""

import subprocess
from pathlib import Path

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


def list_changed_files(workdir: Path, base: str = "main") -> list[str]:
    """Paths changed in base...HEAD (three-dot diff), excluding empty lines."""
    out = _run_git(f"git diff {base}...HEAD --name-only", workdir)
    if not out or out == "(no output)":
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def has_pr_changes(workdir: Path, base: str = "main") -> bool:
    return len(list_changed_files(workdir, base)) > 0


def build_no_changes_report(base: str = "main") -> str:
    return f"""## 总结

当前分支相对 `{base}` **没有可审查的代码变更**（`git diff {base}...HEAD` 为空）。未调用大模型。

## 变更文件

（无）

## 发现

（无 — 无 diff）

## 结论

**批准** — 无相对 `{base}` 的增量改动，无需代码审查。"""


def collect_pr_context(workdir: Path, base: str = "main") -> str:
    """Gather git context to seed the review prompt."""
    sections: list[str] = []

    def run(cmd: str) -> str:
        out = _run_git(cmd, workdir)
        return out[:30000] if out else "(no output)"

    sections.append(f"### git status\n```\n{run('git status -sb')}\n```")
    sections.append(
        f"### changed files (vs {base})\n```\n{run(f'git diff {base}...HEAD --name-only')}\n```"
    )
    sections.append(
        f"### diff stat (vs {base})\n```\n{run(f'git diff {base}...HEAD --stat')}\n```"
    )
    sections.append(
        f"### full diff (vs {base})\n```diff\n{run(f'git diff {base}...HEAD')}\n```"
    )

    return "\n\n".join(sections)


def build_review_request(base: str = "main") -> str:
    return f"""Review the current branch changes against `{base}`.

Use the git context below as a starting point.
You must read_file every changed source/config file (see system prompt) before the final report.
Produce the structured Markdown review report defined in your instructions.

{collect_pr_context(Path.cwd(), base=base)}"""
