# PR Review Agent

基于 [learn-claude-code](https://github.com/anthropics/learn-claude-code) **s01 Agent Loop + s02 Tool Use + s03 Permission + s06 Subagent** 的 PR 代码审查 Agent。

## 当前能力（v0.6）

| 模块 | 对应章节 | 说明 |
|------|----------|------|
| `pr_review_agent/loop.py` | s01 | 多轮 tool loop（可注入不同 tools/system） |
| `pr_review_agent/tools.py` | s02 | bash / read_file / write_file / edit_file / glob |
| `pr_review_agent/permissions.py` | s03 | 三道闸门：硬拒绝 / 规则匹配 / 用户确认 |
| `pr_review_agent/git_utils.py` | s08 思路 | 按文件分块 inline diff；轻量上下文给集成阶段 |
| `pr_review_agent/subagent.py` | s06 | **每变更文件**独立子 Agent：全文 + diff + 最多 2 个关联文件 |
| `pr_review_agent/orchestrator.py` | s06 | 子 Agent 汇总 → **主 Agent 集成**跨文件风险 |
| `pr_review_agent/prompts.py` | — | 子 Agent / 集成 / 旧版单 Agent 提示词 |
| `.github/workflows/pr-review.yml` | CI | PR 更新时自动 review 并评论 |

### 审查流程（默认 `review`）

```text
1. 列出可审查的变更文件（.py / .ts / .yaml …，跳过 lock/二进制）
2. 每个文件 → 子 Agent（独立 messages）
      · read_file 目标文件全文（大文件可结合 diff 局部读）
      · bash git diff 补全补丁
      · 必要时最多再 read 2 个直接关联文件
      · 输出该文件的 Markdown 摘要
3. 主 Agent（集成）
      · 只接收各文件摘要 + 轻量 git stat
      · 合并发现、检查跨文件矛盾（bash/glob 快速核对）
      · 输出最终 ## 总结 / ## 发现 / ## 结论
```

### 可靠性

- **空 diff**：无变更时跳过 LLM
- **大 PR**：单文件子 Agent 内可持全文；主对话不堆所有 `read_file` 正文
- **超 50 个可审查文件**：只审前 50，其余在报告中提示人工复查
- **回退**：`review --legacy-single-agent` 使用 v0.5 单 Agent + inline diff 分块

### s03 权限行为

| 模式 | 行为 |
|------|------|
| `review` | 禁止 `write_file`/`edit_file`；危险 `bash` 自动拒绝 |
| `chat` | 输入 `review` 走与子 Agent 相同的分文件流程 |
| `chat` 其它 | 交互式单 Agent，可写文件（需确认） |

## 快速开始

```bash
cd pr-review-agent
pip install -r requirements.txt
copy .env.example .env   # 填入 ANTHROPIC_API_KEY 和 MODEL_ID
```

在 **Git 仓库根目录** 下运行：

```bash
# 默认：子 Agent 分文件审查 + 主 Agent 集成
python main.py review

python main.py review --base develop --output REVIEW.md

# 旧版单 Agent（inline diff 分块，主对话内 read 所有文件）
python main.py review --legacy-single-agent

python main.py chat   # 输入 review 触发分文件审查
```

## GitHub Actions（PR 自动审查）

PR **打开 / 更新** 时对 base 分支（通常 `main`）运行 `python main.py review`，在 PR 上更新一条审查评论。

Secrets：`ANTHROPIC_API_KEY`、`MODEL_ID`；可选 `ANTHROPIC_BASE_URL`（智谱等）。

## 项目结构

```
pr-review-agent/
├── main.py
├── pr_review_agent/
│   ├── config.py
│   ├── loop.py
│   ├── tools.py
│   ├── permissions.py
│   ├── prompts.py
│   ├── git_utils.py
│   ├── subagent.py       # s06 单文件审查
│   └── orchestrator.py   # s06 编排 + 集成
├── requirements.txt
└── .github/workflows/pr-review.yml
```

## 后续扩展

| 章节 | 计划 |
|------|------|
| s08 Context Compact | `chat` 长会话可选 L2/L3 |
| s07 Skill Loading | `code-review` SKILL |
| s19 MCP | 外接工具（可选） |

## 环境变量

见 `.env.example`：`ANTHROPIC_API_KEY`、`MODEL_ID` 必填；`ANTHROPIC_BASE_URL` 可选。
