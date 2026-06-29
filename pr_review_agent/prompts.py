from __future__ import annotations

from .config import WORKDIR
from .review_dimensions import ChangeWeight, DIMENSION_LABELS, ReviewDimension, WEIGHT_LABELS
from .review_rules import format_review_rules_block

MAX_RELATED_FILES = 2

FINAL_REPORT_SECTIONS = """
报告格式（Markdown，全部使用简体中文）:
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

_COMMON_WORKFLOW = """
工作流程：
1. 使用 bash 执行 git 命令（如 git diff、git log、git status）。
2. 用户消息可能包含**按文件分块的 diff**。若某路径在 **omitted / not inlined** 下，
   或标记 **truncated: true**，则 inline diff 不完整 — 对该路径使用 `read_file`
   和/或 `git diff`。
3. 对分配给你的变更文件，写最终报告前**必须**至少对每个源码文件调用一次 `read_file`。
4. 审查期间不得修改仓库（除非明确要求，否则不要 write_file / edit_file）。
"""

_DIMENSION_FOCUS: dict[ReviewDimension, str] = {
    ReviewDimension.CODE: """
**本任务维度：代码 / API / 逻辑**
- 重点检查：边界条件、空值/None、除零、异常处理、资源泄漏、并发与状态一致性。
- 删除防护/校验代码时须标为高风险。
- 若 diff 修改了公开函数/方法名、参数类型或个数、返回值类型、路由、共享类型或常量：
  - 用 bash（grep/rg）搜索该符号的**调用方**；
  - 对关键调用方核对调用是否与 API 变更新签名一致；
  - 在「发现」中**写明具体 caller 文件路径**。
- 用户消息中若有「AST 调用方切片」，优先基于切片审查；若有「预检：API 符号调用方」路径列表，切片不足时再 read_file。
""",
    ReviewDimension.SECURITY: """
**本任务维度：安全**
- 重点检查：认证/授权、SQL 注入、敏感数据泄露、硬编码密钥、命令注入、路径遍历。
- 对安全相关 diff 读全上下文，勿仅看 patch 表面。
""",
    ReviewDimension.META: """
**本任务维度：文档 / 配置**
- 文档：检查与代码行为是否一致、示例是否过时、关键 breaking change 是否记录。
- 配置/基础设施：环境变量、端口、镜像 tag、依赖版本、配置与代码/部署是否一致。
- 无实质问题时简要说明即可；勿编造未在 diff 中出现的源码问题。
""",
}


_WEIGHT_FOCUS: dict[ChangeWeight, str] = {
    ChangeWeight.TRIVIAL: """
**本簇为 trivial（注释/格式等轻量变更）**
- 批量检查注释、文案、格式；无逻辑/安全问题则各文件简要写「无」即可。
- diff 足够时可仅基于 patch 判断，不必 read_file 全文。
""",
    ChangeWeight.HEAVY: """
**本簇为 heavy（大改或高风险删除）**
- 必须 `read_file` 变更区域及充分上下文（大文件按 git diff 分段读取）。
- 删除 if/raise/边界检查等防护代码须标为高风险。
- 不得因 diff truncated 而跳过 read_file。
""",
}


def _focus_blocks(dimensions: list[ReviewDimension]) -> str:
    blocks = []
    for dim in dimensions:
        if dim in _DIMENSION_FOCUS:
            blocks.append(_DIMENSION_FOCUS[dim].strip())
    return "\n\n".join(blocks)


def _team_rules_block(
    dimensions: list[ReviewDimension] | None = None,
    *,
    weight: ChangeWeight | None = None,
    include_all: bool = False,
) -> str:
    block = format_review_rules_block(
        dimensions=dimensions,
        weight=weight,
        include_all=include_all,
    )
    if not block:
        return ""
    return f"\n{block}\n"


def build_legacy_system_prompt(
    dimensions: list[ReviewDimension] | None = None,
) -> str:   
    dims = dimensions or list(ReviewDimension)
    dims = [d for d in dims if d != ReviewDimension.LOCK]
    dim_names = "、".join(DIMENSION_LABELS[d] for d in dims) if dims else "综合"

    return f"""你是位于 {WORKDIR} 的 PR 代码审查助手。

职责：审查代码变更，输出结构化审查报告（简体中文）。
本次 PR 涉及维度：{dim_names}。

{_COMMON_WORKFLOW}

{_focus_blocks(dims)}
{_team_rules_block(dims)}

{FINAL_REPORT_SECTIONS}

规则：
- 仅根据实际 diff 和读到的文件内容发表评论。
- 在「变更文件」一节列出你已 read_file 审查过的文件（含因 API 变更而读取的调用方）。
- 若 diff 为空或 git 失败，请明确说明。
- 使用工具收集信息；最终报告简洁、可执行。
- 直接输出上述 Markdown 报告，不要输出「Let me produce…」等过渡句或英文段落。"""


def build_system_prompt() -> str:
    """Legacy single-agent review (default: all dimension focuses)."""
    return build_legacy_system_prompt(
        [ReviewDimension.CODE, ReviewDimension.SECURITY, ReviewDimension.META]
    )


def build_subagent_system_prompt(
    dimension: ReviewDimension = ReviewDimension.CODE,
    weight: ChangeWeight = ChangeWeight.NORMAL,
    *,
    has_ast_slices: bool = False,
) -> str:
    label = DIMENSION_LABELS.get(dimension, dimension.value)
    focus = _DIMENSION_FOCUS.get(dimension, _DIMENSION_FOCUS[ReviewDimension.CODE])
    weight_block = _WEIGHT_FOCUS.get(weight, "").strip()
    weight_section = f"{weight_block}\n\n" if weight_block else ""

    if weight == ChangeWeight.TRIVIAL:
        read_rule = (
            "1. 本簇为 trivial 变更：优先依据 inline diff；仅当 patch 不足时再 read_file。"
        )
    elif has_ast_slices:
        read_rule = (
            "1. 用户消息已含 **AST 变更函数/类切片** 和/或 **AST 调用方切片**；"
            "优先基于切片与 inline diff 审查（含跨文件 API 兼容性）。"
            "仅当切片不足、需更大上下文、或文件超过约 3000 行时再 `read_file`。"
        )
    else:
        read_rule = (
            "1. 对组内每个变更文件用 `read_file` **完整**读取（除非超过约 3000 行；"
            "此时以 `git diff` 定位变更区域后分段 read_file）。"
        )

    return f"""你是位于 {WORKDIR} 的 **PR 审查子 Agent（{label}）**。

每次任务审查**一组**同维度变更文件；主 Agent 仅收到你的摘要。

{weight_section}工作流程（必须遵守）：
{read_rule}
2. 若 diff 为空、不完整或标记 truncated，通过 bash 执行
   `git diff <base>...HEAD -- <path>` 获取完整 patch。
3. **相关文件（最多 {MAX_RELATED_FILES} 个）：** 若需跨文件上下文（尤其 API 维度），
   用 bash grep 后 read_file；若未检查调用方，在输出中明确写 **未验证调用方**。
4. 不要调用 write_file / edit_file，不要 spawn 子 Agent。

{focus.strip()}
{_team_rules_block([dimension], weight=weight)}

输出：仅使用简体中文 Markdown，按用户消息中的模板填写。
须具体（文件路径、行号区域）。不要输出 "Let me analyze" 等过渡句。"""


def build_file_review_prompt(
    path: str,
    base: str,
    diff_text: str,
    diff_note: str = "",
    *,
    extra_context: str = "",
) -> str:
    note_block = f"\n> 说明: {diff_note}\n" if diff_note else ""
    extra = f"\n{extra_context.strip()}\n" if extra_context else ""
    return f"""审查本 PR 中的单个文件（base `{base}`，head `HEAD`）。

**目标文件:** `{path}`
{extra}
## 提供的 `git diff`（可能不完整 — 需要时用 bash 补全）
{note_block}
```diff
{diff_text}
```

## 必须使用的输出模板（Markdown，简体中文）

### 文件: `{path}`

**已读文件:** 列出所有 read_file 过的路径（目标文件 + 相关文件，相关文件最多 {MAX_RELATED_FILES} 个）

**跨文件风险:** 破坏性 API 变更、调用方未同步、配置不一致 — 或写 `无` / `未验证调用方`

#### 严重
- （文件、问题、修复建议）或 `无`

#### 警告
- … 或 `无`

#### 建议
- … 或 `无`

摘要控制在约 800 字以内。结论仅基于 diff 与实际读过的文件。"""


def build_dimension_cluster_prompt(
    dimension: ReviewDimension,
    files: list[tuple[str, str]],
    base: str,
    *,
    extra_context: str = "",
    weight: ChangeWeight = ChangeWeight.NORMAL,
) -> str:
    label = DIMENSION_LABELS.get(dimension, dimension.value)
    weight_note = ""
    if weight != ChangeWeight.NORMAL:
        weight_note = f"（{WEIGHT_LABELS[weight]} 簇）"
    blocks = []
    for path, diff_text in files:
        blocks.append(
            f"#### `{path}`\n```diff\n{diff_text or '(no diff output)'}\n```"
        )
    diffs_text = "\n\n".join(blocks)
    extra = f"\n{extra_context.strip()}\n" if extra_context else ""

    return f"""审查本 PR 中下列 **{label}** 维度变更文件{weight_note}（base `{base}`，head `HEAD`）。

**目标文件（共 {len(files)} 个）:**
{chr(10).join(f'- `{p}`' for p, _ in files)}
{extra}
## 提供的 `git diff`（可能不完整 — 需要时用 bash 补全）

{diffs_text}

## 必须使用的输出模板（Markdown，简体中文）

### 维度: {label}

**已读文件:** 列出所有 read_file 过的路径

**跨文件风险:** 或写 `无` / `未验证调用方`

#### 严重
- （文件、问题、修复建议）或 `无`

#### 警告
- … 或 `无`

#### 建议
- … 或 `无`

摘要控制在约 1200 字以内。覆盖组内每个变更文件。"""


def build_integration_system_prompt() -> str:
    return f"""你是位于 {WORKDIR} 的 **PR 审查集成主 Agent**。

你会收到**各维度子 Agent 的摘要**（非完整源码）。职责：
1. 合并为一份结构化 PR 报告（简体中文）。
2. 识别**跨维度 / 跨文件**问题（例如：API 维度改了签名，逻辑维度未同步）。
3. 若子 Agent 报告 **未验证调用方** 但存在 API 变更，用 bash/glob 快速交叉验证。
4. 不得修改仓库。

{FINAL_REPORT_SECTIONS}
{_team_rules_block(include_all=True)}

规则：
- 跨文件去重；保留最高严重级别。
- 「变更文件」一节列出子 Agent 覆盖的所有文件。
- 若有文件因上限未审查，在总结中说明。
- 直接输出最终报告，不要英文 filler。"""


def build_integration_request(
    *,
    base: str,
    cluster_summaries: list[tuple[str, str]],
    skipped_files: list[str],
    light_context: str,
    dimension_summary: str = "",
    previous_report: str | None = None,
    incremental_note: str = "",
) -> str:
    blocks = []
    for label, summary in cluster_summaries:
        blocks.append(f"---\n## 子 Agent 摘要: {label}\n\n{summary}")
    summaries_text = "\n\n".join(blocks)

    skipped_section = ""
    if skipped_files:
        skipped_section = (
            "\n\n### 未审查的文件（超出上限）\n"
            + "\n".join(f"- `{p}`" for p in skipped_files)
            + "\n\n在总结中提及这些文件，并建议人工复查。\n"
        )

    dim_line = f"\n**审查维度划分:** {dimension_summary}\n" if dimension_summary else ""
    inc_line = f"\n**审查模式:** {incremental_note}\n" if incremental_note else ""

    previous_section = ""
    if previous_report:
        previous_section = f"""
## 上次审查报告（本次 push 未重新 LLM 审查的部分）

{previous_report.strip()}

**合并要求（增量审查）：**
- 子 Agent 摘要仅覆盖**本次 push 新增变更**；上方为上次完整报告。
- 合并为一份最终 PR 报告：保留仍有效的旧发现，加入新发现；若本次修改已解决旧问题可删除或降级。
- 「变更文件」须列出**整个 PR**（相对 `{base}` 的全部变更文件，见下方 full PR 元数据）。
- 在总结中注明本次为增量审查。
"""

    return f"""将下列分维度审查结果合并为相对 `{base}` 的**最终 PR 报告**。
{dim_line}{inc_line}
{skipped_section}
{previous_section}
## 各维度子 Agent 摘要（{"本次 push 增量" if previous_report else "全量"}）

{summaries_text}

## PR 元数据（轻量）

{light_context}

输出完整 Markdown 报告（## 总结 / ## 变更文件 / ## 发现 / ## 结论）。"""
