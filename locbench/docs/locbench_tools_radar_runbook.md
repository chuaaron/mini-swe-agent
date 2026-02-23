# LocBench Tools-Radar 操作文档

本文档用于运行你最新落地的 Radar 方案：
- 独立模式：`tools_radar`
- 独立工具：`file_radar_search`
- 强制约束：调用 Radar 后，至少读取 1 个候选文件才能提交

---

## 1. 你现在新增了什么

1. 新模式：`--mode tools_radar`
2. 新工具：`@tool file_radar_search`
3. 新工具配置：`locbench/config/file_radar_search.yaml`
4. 新提示词模板：
   - `locbench/config/agent_tools_radar_neutral.yaml`
   - `locbench/config/agent_tools_radar_search_first.yaml`
   - `locbench/config/agent_tools_radar_search_fallback.yaml`
5. 新别名命令：`mini-extra locbench-tools-radar`
6. 新设计文档：`locbench/docs/locbench_radar_sniper_design_doc.md`

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

解释：
1. `radar_called`: 本样本是否调用过 `file_radar_search`
2. `blocked_submission_count`: 因未验证候选文件而被拦截提交的次数
3. `radar_verification_satisfied`: 是否完成“至少 1 个候选文件读取验证”

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
