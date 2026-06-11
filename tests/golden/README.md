# Golden PR 评测集

人工构造的「含 bug PR」fixture，用于评估 **审查 Agent 输出质量**（与 `test_*.py` 里的管道单元测试互补）。

## 用例

| ID | 说明 | 路由 (auto) |
|----|------|-------------|
| `obvious_none` | PR 删除 `None` 检查，`greet` 直接 `.upper()`（无注释剧透） | legacy |
| `divide_by_zero` | PR 删除空列表检查，`average` 可能除零（无注释剧透） | legacy |
| `cross_file_api` | API 改 `str`，调用方仍传 `int` | legacy |
| `lock_only` | 仅 `package-lock.json` 变更 | legacy |

每个 case 目录：

```text
cases/<id>/
  case.yaml    # 标题、描述、检查点
  main/        # 基线（较安全实现，模拟 main 分支）
  pr/          # PR 变更（删除防护 / 引入 bug，无 BUG 注释）
```

## 无 API：pytest（CI 默认）

构建临时 git 仓库、校验 diff 与路由、测试打分器：

```bash
pytest tests/test_golden_fixtures.py -q
```

## 有 API：跑真实 review

需要 `.env` 里配置 `ANTHROPIC_API_KEY`、`MODEL_ID`：

```bash
python scripts/run_golden_eval.py --list
python scripts/run_golden_eval.py --case obvious_none
python scripts/run_golden_eval.py --all
```

脚本会在临时目录 `git init` → `main` + `feature` 分支，在 fixture 根执行 `main.py review --base main`，再按 `case.yaml` 的 `checks` 打分。

## case.yaml 检查点

```yaml
checks:
  must_mention_files: [utils.py]      # 报告须提到这些路径
  must_contain_any: [None, 空]        # 至少命中其一（OR）
  must_not_mention: [calculator.py]   # 不得出现（防胡编）
```

LLM 输出非确定性：同一 case 多次运行措辞可能不同，以检查点为准，不要求全文一致。
