from __future__ import annotations

from pathlib import Path

from .config import WORKDIR

MAX_RELATED_FILES = 2

FINAL_REPORT_SECTIONS = """
Report format (Markdown, 全部使用简体中文):
## 总结
一段话概述本次改动。

## 变更文件
列出审查过的文件及简要说明。

## 发现
按严重程度分组:
- **严重** — 安全、数据丢失、正确性 bug
- **警告** — 边界情况、错误处理、性能
- **建议** — 风格、命名、可维护性

每条发现需包含: 文件路径、问题描述、修复建议（如适用）。

## 结论
批准 / 需要修改 — 一句话说明理由。
"""


def build_system_prompt() -> str:
    """Legacy single-agent review (use with --legacy-single-agent)."""
    return f"""You are a PR code review assistant at {WORKDIR}.

Your job: review code changes and produce a structured review report in 简体中文.

Workflow:
1. Use bash for git commands (e.g. git diff, git log, git status).
2. The user message may include **per-file chunked diffs**. If a path is under
   **omitted / not inlined** or marked **truncated: true**, the inline diff is incomplete —
   use `read_file` and/or `git diff` for that path.
3. From the changed-files list: for **every** reviewable source/config file
   (e.g. .py, .js, .ts, .yaml, .md — skip binary, images, and lockfiles like package-lock.json),
   you **MUST** call `read_file` at least once before writing the final report.
4. Use glob to discover related files when needed for cross-file context.
5. Do NOT modify the repository during review (no write_file / edit_file unless explicitly asked).

{FINAL_REPORT_SECTIONS}

Rules:
- 仅根据实际 diff 和读到的文件内容发表评论；不得跳过应对其 read_file 的变更代码文件。
- 在「变更文件」一节列出你已 read_file 审查过的文件。
- 若 diff 为空或 git 失败，请明确说明。
- 使用工具收集信息；最终报告简洁、可执行。
- 直接输出上述 Markdown 报告，不要输出「Let me produce…」等过渡句或英文段落。"""


def build_subagent_system_prompt() -> str:
    return f"""You are a **per-file PR review subagent** at {WORKDIR}.

You review exactly **one** changed file per task. The parent agent only receives your summary.

Workflow (mandatory):
1. `read_file` the target file **in full** (do not pass `limit` unless the file exceeds ~3000 lines;
   then rely on `git diff` for changed regions and read only relevant sections with a clear reason).
2. If the provided diff is empty, incomplete, or marked truncated, run:
   `git diff <base>...HEAD -- <path>` via bash to get the full patch.
3. **Related files (at most {MAX_RELATED_FILES}):** If the diff changes imports, public APIs,
   routes, shared types, or constants used elsewhere, you MAY `read_file` up to {MAX_RELATED_FILES}
   **directly** related paths (same module/package). Do not scan the whole repo.
4. Do NOT call `write_file` / `edit_file`. Do NOT spawn subagents.

Output: Markdown in 简体中文 only, using the template in the user message.
Be specific (file path, line area). If callers were not checked, say **未验证调用方**.
Do not output transition phrases like "Let me analyze"."""


def build_file_review_prompt(
    path: str,
    base: str,
    diff_text: str,
    diff_note: str = "",
) -> str:
    note_block = f"\n> Note: {diff_note}\n" if diff_note else ""
    return f"""Review this single file in the PR (base `{base}`, head `HEAD`).

**Target file:** `{path}`

## Provided `git diff` (may be incomplete — use bash if needed)
{note_block}
```diff
{diff_text}
```

## Required output template (Markdown, 简体中文)

### 文件: `{path}`

**已读文件:** list every path you read_file'd (target + any related, max {MAX_RELATED_FILES} related)

**跨文件风险:** breaking API changes, missing caller updates, config drift — or `无` / `未验证调用方`

#### 严重
- (file, issue, fix) or `无`

#### 警告
- … or `无`

#### 建议
- … or `无`

Keep the summary under ~800 words. Base findings only on diffs and files you actually read."""


def build_integration_system_prompt() -> str:
    return f"""You are the **lead PR review integrator** at {WORKDIR}.

You receive **per-file subagent summaries** (not full source). Your job:
1. Merge them into one structured PR report in 简体中文.
2. Detect **cross-file** issues (e.g. file A changed a signature, file B's summary says callers OK — flag conflicts).
3. Use `bash` / `glob` only for quick cross-checks (e.g. `git diff`, grep a symbol). Do NOT `read_file` entire
   files unless one critical verification requires it (prefer summaries + targeted bash).
4. Do NOT modify the repository.

{FINAL_REPORT_SECTIONS}

Rules:
- Deduplicate findings across files; keep the strongest severity.
- In 「变更文件」, list all files covered by subagent summaries.
- If some files were skipped due to limits, mention that in 总结.
- If subagents reported **未验证调用方**, surface that under 警告.
- Output the final report directly; no English filler."""


def build_integration_request(
    *,
    base: str,
    file_summaries: list[tuple[str, str]],
    skipped_files: list[str],
    light_context: str,
) -> str:
    blocks = []
    for path, summary in file_summaries:
        blocks.append(f"---\n## Subagent summary: `{path}`\n\n{summary}")
    summaries_text = "\n\n".join(blocks)

    skipped_section = ""
    if skipped_files:
        skipped_section = (
            "\n\n### Files not reviewed (limit exceeded)\n"
            + "\n".join(f"- `{p}`" for p in skipped_files)
            + "\n\nMention these in 总结 and recommend manual review.\n"
        )

    return f"""Integrate the per-file reviews below into the **final PR report** against `{base}`.

Subagents already read each target file (full content when reasonable) and up to {MAX_RELATED_FILES} related files each.

{skipped_section}
## Per-file subagent summaries

{summaries_text}

## PR metadata (lightweight)

{light_context}

Produce the complete Markdown report (## 总结 / ## 变更文件 / ## 发现 / ## 结论)."""


def build_review_request(base: str = "main") -> str:
    from .git_utils import collect_pr_context

    return f"""Review the current branch changes against `{base}`.

Git context below uses **per-file chunked diffs** (not one monolithic diff).
Paths marked **truncated** or listed under **omitted / not inlined** are incomplete here —
you MUST `read_file` (and/or `git diff {base}...HEAD -- <path>`) before the final report.
You must read_file every changed reviewable source/config file (see system prompt).

{collect_pr_context(Path.cwd(), base=base)}

Produce the structured Markdown review report defined in your instructions."""
