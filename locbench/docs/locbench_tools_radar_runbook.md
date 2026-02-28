# LocBench Tools-Radar 操作文档

本文档用于运行你最新落地的 Radar 方案：
- 独立模式：`tools_radar`
- 独立工具：`file_radar_search` + `list_symbols`
- `file_radar_search` 内置 `Top-N` 自动骨架摘要（默认 Top-3）
- 强制约束：调用 Radar 后，至少读取 1 个候选文件才能提交

---

## 1. 你现在新增了什么

1. 新模式：`--mode tools_radar`
2. 新工具：
   - `@tool file_radar_search`（文件候选召回）
   - `@tool list_symbols`（候选文件骨架提取，不返回正文）
3. 新工具配置：`locbench/config/file_radar_search.yaml`（含 Top-3 自动骨架参数）
4. 新提示词模板（已写入 list_symbols 使用规则）：
   - `locbench/config/agent_tools_radar_neutral.yaml`
   - `locbench/config/agent_tools_radar_search_first.yaml`
   - `locbench/config/agent_tools_radar_search_fallback.yaml`
5. 新增轨迹分析脚本：`minisweagent.locbench.analysis.list_symbols_metrics`
6. 新别名命令：`mini-extra locbench-tools-radar`
7. 新设计文档：`locbench/docs/locbench_radar_sniper_design_doc.md`

---

## 2. 前置条件

1. 配好本地路径与密钥：`locbench/config/local.yaml`
2. `paths.indexes_root` 和 `paths.model_root` 可用
3. Docker 镜像可用（tools_radar 仍走 tools runner）

可先检查：
```bash
PYTHONPATH=src python -m minisweagent.run_locbench --help
```
确认 `--mode` 中包含 `tools_radar`。

---

## 3. 最小可运行命令

### 3.1 统一入口（推荐）
```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --slice 0:1 \
  --workers 1 \
  --skip-missing \
  --redo-existing
```

### 3.2 mini-extra 别名
```bash
PYTHONPATH=src python -m minisweagent.run.mini_extra \
  locbench-tools-radar \
  --tools-prompt neutral \
  --slice 0:1 \
  --workers 1 \
  --skip-missing \
  --redo-existing
```

---

## 4. 三种提示词策略

`--tools-prompt` 当前支持三种值：
1. `neutral`
2. `search_first`
3. `search_fallback`

对应文件：
1. `locbench/config/agent_tools_radar_neutral.yaml`
2. `locbench/config/agent_tools_radar_search_first.yaml`
3. `locbench/config/agent_tools_radar_search_fallback.yaml`

示例：
```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt search_first \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

---

## 4.1 Radar + Skeleton 推荐执行链路

1. `@tool file_radar_search --query "..."`
2. （自动）从 Radar Top-N 候选中附带 compact skeleton（imports + symbols）
3. `@tool list_symbols --file "candidate.py" --include-signature`（可选，手动深挖）
4. `sed/rg/cat` 精读候选文件
5. 输出最终定位 JSON

说明：
1. `list_symbols` 只返回 `imports/includes` 与 `symbols(name/kind/start/end/signature?)`
2. `list_symbols` 仅允许查询 Radar 候选文件（`allowed_files` 约束）
3. `list_symbols` 不替代 bash 验证；提交前仍需 bash 读取候选文件

---

## 4.2 Top-3 自动骨架（Auto Skeleton）

`file_radar_search` 的返回现在默认包含：
1. 候选文件列表（path/score/evidence）
2. `Auto skeleton (Top-N, compact, no code body)` 段

自动骨架内容：
1. `🎯 Matched Symbols`（命中 query 的符号，置顶展示）
2. `📎 Other Context`（其余符号上下文）
3. `truncated` 标记（预算截断时展示）
4. `💡 SYSTEM HINTS FOR NEXT STEP`（强提示下一步用法）

预算与开关配置（`locbench/config/file_radar_search.yaml`）：
1. `auto_skeleton_enabled`
2. `auto_skeleton_topn`
3. `auto_skeleton_budget_chars`
4. `auto_skeleton_max_imports_per_file`
5. `auto_skeleton_max_symbols_per_file`
6. `auto_skeleton_include_signature`
7. `auto_skeleton_query_aware`

建议：
1. 默认保持 `topn=3`、`budget_chars=4000`
2. 若模型 token 压力大，可降到 `budget_chars=2500`
3. 默认 `auto_skeleton_max_imports_per_file=0`，彻底去掉 imports 噪音

---

## 5. 如何做多版本提示词实验

推荐做法是固定 `--tools-prompt`，用 `--agent-config` 切版本文件，搭配 `--method` 做实验隔离。

示例：
```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --agent-config locbench/config/experiments/agent_tools_radar_v2.yaml \
  --method miniswe_tools_radar__v2 \
  --shuffle --shuffle-seed 123 \
  --slice 0:50 \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

---

## 6. 输出路径

1. 轨迹与日志：
`locbench/outputs/<output_model_name>/<method>/<timestamp>/`

2. 定位结果 JSONL：
`locbench/results/loc_output/<output_model_name>/<method>/loc_outputs_<timestamp>.jsonl`

---

## 7. Radar 结果字段说明

`loc_outputs_*.jsonl`（单条样本）新增字段：
1. `radar_called`
2. `radar_tool_calls`
3. `radar_tool_output_chars`
4. `blocked_submission_count`
5. `radar_candidate_files`
6. `radar_verified_files`
7. `radar_verification_satisfied`
8. `auto_skeleton_enabled`
9. `auto_skeleton_topn`
10. `auto_skeleton_budget_chars`
11. `auto_skeleton_truncated`
12. `auto_skeleton_files`

解释：
1. `radar_called`: 本样本是否调用过 `file_radar_search`
2. `blocked_submission_count`: 因未验证候选文件而被拦截提交的次数
3. `radar_verification_satisfied`: 是否完成“至少 1 个候选文件读取验证”
4. `auto_skeleton_files`: Radar 自动附带的骨架摘要（每个文件的 imports/symbols 预览与截断信息）

---

## 8. Run Summary 新增指标

`run_summary.json -> stats_overall` 在 Radar 模式下会新增：
1. `radar_called_count`
2. `verification_compliance_rate`
3. `blocked_submission_count`
4. `avg_tool_output_chars`
5. `premature_submit_rate`

用于衡量：
1. 是否真正执行“先雷达后验证”
2. 提交前的违规倾向
3. 工具输出长度是否明显下降

---

## 9. A/B 对比建议（旧 tools vs 新 tools_radar）

关键是控制变量：
1. 同一模型
2. 同一 `slice`
3. 同一 `shuffle_seed`
4. 同一 `workers`

对比命令示例：
```bash
SEED=123
SLICE=0:50

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --tools-prompt neutral \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 4 --skip-missing --redo-existing \
  --method miniswe_tools__ab

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 4 --skip-missing --redo-existing \
  --method miniswe_tools_radar__ab
```

---

## 10. 常见问题

1. 报错 `paths.indexes_root and paths.model_root must be set...`
   - 在 `locbench/config/local.yaml` 补齐：
   - `paths.indexes_root`
   - `paths.model_root`

2. 报错 `Tool config not found`
   - 检查 `locbench/config/file_radar_search.yaml` 路径是否存在
   - 或显式传 `--tool-config`

3. 提交一直被拦截
   - 说明调用过 Radar 后，没有对候选文件执行读取类 bash 命令
   - 至少执行一次 `rg/cat/sed/nl/head/tail` 并命中候选文件路径

---

## 11. 推荐执行顺序

1. `tools_radar + neutral` 小样本冒烟（`slice 0:5`）
2. `search_first` 与 `search_fallback` 各跑同一批样本
3. 固定 seed 做 `tools` vs `tools_radar` A/B
4. 重点看 `verification_compliance_rate` 与 `pass_rate` 是否同步改善

---

## 12. list_symbols 轨迹分析（使用率/命中率/准确率增益）

运行完成后，可直接基于 `trajectories/*.traj.json` 自动统计：
1. `list_symbols_usage_rate`
2. `list_symbols_hit_rate`（调用文件命中 GT 文件）
3. `accuracy_uplift`（使用 vs 未使用 的正确率差）

命令：
```bash
PYTHONPATH=src python -m minisweagent.locbench.analysis.list_symbols_metrics \
  --run-dir locbench/outputs/<model>/<method>/<timestamp> \
  --dataset data/Loc-Bench_V1_dataset.jsonl
```

产物：
1. `<run-dir>/list_symbols_metrics.json`
2. `<run-dir>/list_symbols_instance_metrics.csv`

核心指标：
1. `list_symbols_usage_rate_all`
2. `list_symbols_usage_rate_given_radar`
3. `list_symbols_call_hit_rate`
4. `list_symbols_instance_hit_rate_given_used`
5. `accuracy_uplift_used_vs_not_used_given_radar_pp`

---

## 13. Auto Skeleton A/B（可直接复制）

目标：只改 `auto_skeleton_enabled`，其余条件保持一致，做 `tools_radar + neutral` 对照。

### 13.1 准备两份 tool config

```bash
cd /Users/chz/code/locbench/mini-swe-agent

cp locbench/config/file_radar_search.yaml locbench/config/file_radar_search_auto.yaml
cp locbench/config/file_radar_search.yaml locbench/config/file_radar_search_no_auto.yaml

python - <<'PY'
from pathlib import Path
p = Path("locbench/config/file_radar_search_no_auto.yaml")
s = p.read_text(encoding="utf-8")
s = s.replace("auto_skeleton_enabled: true", "auto_skeleton_enabled: false")
p.write_text(s, encoding="utf-8")
print("updated", p)
PY
```

### 13.2 对照组（关闭自动骨架）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --tool-config locbench/config/file_radar_search_no_auto.yaml \
  --method miniswe_tools_radar__neutral__no_auto_skeleton \
  --slice 0:560 \
  --shuffle --shuffle-seed 123 \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

### 13.3 实验组（开启 Top-3 自动骨架）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --tool-config locbench/config/file_radar_search_auto.yaml \
  --method miniswe_tools_radar__neutral__auto_skeleton_top3 \
  --slice 0:560 \
  --shuffle --shuffle-seed 123 \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

### 13.4 快速验证自动骨架是否生效

```bash
rg -n "Auto skeleton \\(Top-" \
  locbench/outputs/*/miniswe_tools_radar__neutral__auto_skeleton_top3/*/trajectories/*.traj.json | head
```
