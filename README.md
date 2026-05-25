# PR Review Agent

基于 [learn-claude-code](https://github.com/anthropics/learn-claude-code) **s01 Agent Loop + s02 Tool Use + s03 Permission** 的 PR 代码审查 Agent。

## 当前能力（v0.2）

| 模块 | 对应章节 | 说明 |
|------|----------|------|
| `pr_review_agent/loop.py` | s01 | 多轮 tool loop |
| `pr_review_agent/tools.py` | s02 | bash / read_file / write_file / edit_file / glob + `safe_path` |
| `pr_review_agent/permissions.py` | s03 | 三道闸门：硬拒绝 / 规则匹配 / 用户确认 |
| `pr_review_agent/prompts.py` | — | PR 审查专用 system prompt |
| `pr_review_agent/git_utils.py` | — | 预取 `git diff` 注入审查请求 |

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
└── .env.example
```

## 后续扩展路线图

| 章节 | 计划 |
|------|------|
| s07 Skill Loading | 加载 `code-review` SKILL |
| s08 Context Compact | 大 diff 压缩 |
| s10 System Prompt | 提示词版本管理 |
| s06 Subagent | 大 PR 按文件分审 |
| s19 MCP | GitHub PR 自动评论 |

## 环境变量

见 `.env.example`：`ANTHROPIC_API_KEY`、`MODEL_ID` 必填；`ANTHROPIC_BASE_URL` 可选（兼容 API 网关）。
