# PR Review Agent

基于 [learn-claude-code](https://github.com/anthropics/learn-claude-code) **s01 Agent Loop + s02 Tool Use + s03 Permission** 的 PR 代码审查 Agent。

## 当前能力（v0.4）

| 模块 | 对应章节 | 说明 |
|------|----------|------|
| `pr_review_agent/loop.py` | s01 | 多轮 tool loop |
| `pr_review_agent/tools.py` | s02 | bash / read_file / write_file / edit_file / glob + `safe_path` |
| `pr_review_agent/permissions.py` | s03 | 三道闸门：硬拒绝 / 规则匹配 / 用户确认 |
| `pr_review_agent/prompts.py` | — | PR 审查专用 system prompt |
| `pr_review_agent/git_utils.py` | — | 预取 `git diff` 注入审查请求 |
| `.github/workflows/pr-review.yml` | CI | PR 更新时自动 review 并评论 |

### 可靠性（v0.4）

- **空 diff**：相对 base 无变更时跳过 LLM，直接输出「无变更」报告
- **Actions 失败**：仍在 PR 留简短失败说明；成功/失败均可下载 `pr-review-report` artifact
- **审查深度**：提示词要求对每个变更的源码/配置文件至少 `read_file` 一次

### s03 权限行为

| 模式 | 行为 |
|------|------|
| `review` | 禁止 `write_file`/`edit_file`；危险 `bash` 自动拒绝；不弹 `[y/N]` |
| `chat` | 写仓库外 / 危险 bash 可 `Allow? [y/N]` 确认 |
| `chat` + 输入 `review` | 与 `review` 命令相同的只读策略 |

## 快速开始

```bash
cd pr-review-agent
pip install -r requirements.txt
copy .env.example .env   # 填入 ANTHROPIC_API_KEY 和 MODEL_ID
```

在 **Git 仓库根目录** 下运行（或 `cd` 到目标仓库）：

```bash
# 审查当前分支相对 main 的改动
python e:\agent_demo\pr-review-agent\main.py review

# 指定 base 分支并保存报告
python main.py review --base develop --output REVIEW.md

# 交互模式（输入 review 可快速审查）
python main.py chat
```

## GitHub Actions（PR 自动审查）

向 **feature → main** 的 Pull Request 在 **打开 / 更新** 时会自动：

1. checkout PR 分支代码  
2. 运行 `python main.py review`（相对 base 分支的 diff）  
3. 在 PR 页面 **创建或更新一条** 审查评论（不会每次 push 刷屏）

### 配置仓库 Secrets

在 GitHub 仓库 **Settings → Secrets and variables → Actions** 添加：

| Secret | 必填 | 说明 |
|--------|------|------|
| `ANTHROPIC_API_KEY` | 是 | 大模型 API Key |
| `MODEL_ID` | 是 | 如 `glm-5` 或 `claude-sonnet-4-6` |
| `ANTHROPIC_BASE_URL` | 否 | 智谱等兼容网关时填写 |

合并 workflow 到 `main` 后，对 targeting `main` 的 PR 即会生效。

手动触发：Actions 页选择 **PR Review** → **Run workflow**（需在有 PR 上下文时用于调试）。

> 提示：仅 `git push` 不会触发 PR 评论，须存在 **Open** 的 PR（`feature` → `main`）；`pr-review.yml` 须在 `main` 上。若无 Checks，可 Close → Reopen 或再 push。

## 项目结构

```
pr-review-agent/
├── main.py                 # CLI 入口
├── pr_review_agent/
│   ├── config.py           # 环境变量、Anthropic client
│   ├── loop.py             # s01 agent loop
│   ├── tools.py            # s02 工具与分发
│   ├── permissions.py      # s03 权限闸门
│   ├── prompts.py          # 审查提示词
│   └── git_utils.py        # git diff 预取
├── requirements.txt
├── .env.example
└── .github/workflows/pr-review.yml
```

## 后续扩展路线图

| 章节 | 计划 |
|------|------|
| s07 Skill Loading | 加载 `code-review` SKILL |
| s08 Context Compact | 大 diff 压缩 |
| s10 System Prompt | 提示词版本管理 |
| s06 Subagent | 大 PR 按文件分审 |
| s19 MCP | 外接工具（可选，PR 自动审查已用 Actions 实现） |

## 环境变量

见 `.env.example`：`ANTHROPIC_API_KEY`、`MODEL_ID` 必填；`ANTHROPIC_BASE_URL` 可选（兼容 API 网关）。
