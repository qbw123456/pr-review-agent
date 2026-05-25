"""Git helpers for PR review (pre-fetch diff for the agent)."""

import subprocess
from pathlib import Path


def collect_pr_context(workdir: Path, base: str = "main") -> str:
    """Gather git context to seed the review prompt."""
    sections: list[str] = []

    def run(cmd: str) -> str:
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
        out = ((r.stdout or "") + (r.stderr or "")).strip()
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

Use the git context below as a starting point. Read any changed files you need for context.
Produce the structured Markdown review report defined in your instructions.

{collect_pr_context(Path.cwd(), base=base)}"""
