# LocBench 变更留痕（2026-02-26）

本文档记录 2026-02-26 在 `tools_radar` 方案上的实现改动与验证结果，供复现实验与论文附录使用。

---

## 1. 改动目标

1. 解决协议噪声：`found 2 actions` 导致的无效拦截。
2. 增加 Skeleton 阶段：在 Radar 候选文件上提供 AST 骨架信息（不泄露正文）。
3. 增加自动评估：从轨迹统计 `list_symbols` 使用率、命中率、以及对正确率增益。

---

## 2. 核心代码改动

### 2.1 解析器去噪（协议噪声修复）

目标：当模型在同一回复里重复输出相同 bash action 时，不再触发 `found 2 actions`。

改动文件：
1. `src/minisweagent/agents/default.py`
2. `src/minisweagent/agents/tool_agent.py`
3. `tests/agents/test_default.py`
4. `tests/run/test_locbench_tools_radar_guard.py`

行为变化：
1. 多个 action 内容完全一致：按 1 个 action 处理。
2. 多个 action 内容不同：仍然抛 `FormatError`。

---

### 2.2 新工具：`list_symbols`（Skeleton）

新增文件：
1. `src/minisweagent/tools/list_symbols/tool.py`
2. `src/minisweagent/tools/list_symbols/__init__.py`
3. `tests/run/test_list_symbols_tool.py`

接口：
1. `@tool list_symbols --file "<path>" [--include-signature]`

返回内容（无正文）：
1. `imports/includes`: `[{line, text}]`
2. `symbols`: `[{name, kind, start, end, signature?}]`

约束：
1. 仅允许访问 `allowed_files`（由 Radar 候选注入）。
2. 未先建立候选时拒绝调用（`allowed_files is empty`）。
3. 路径必须在 repo 内，禁止 `..` 逃逸。

---

### 2.3 Runner 接入（Radar -> Skeleton）

改动文件：
1. `src/minisweagent/locbench/runners/tools_runner.py`
2. `locbench/config/agent_tools_radar_neutral.yaml`
3. `locbench/config/agent_tools_radar_search_first.yaml`
4. `locbench/config/agent_tools_radar_search_fallback.yaml`

接入点：
1. `tools_radar` 模式下注册两个工具：
   - `file_radar_search`
   - `list_symbols`
2. 执行工具时把当前 `candidate_files` 注入为 `allowed_files`。
3. Prompt 中明确链路：`file_radar_search -> list_symbols -> bash精读`。

---

### 2.4 新分析脚本：`list_symbols` 轨迹指标

新增文件：
1. `src/minisweagent/locbench/analysis/__init__.py`
2. `src/minisweagent/locbench/analysis/list_symbols_metrics.py`
3. `tests/run/test_list_symbols_metrics_analysis.py`

命令：
```bash
PYTHONPATH=src python -m minisweagent.locbench.analysis.list_symbols_metrics \
  --run-dir locbench/outputs/<model>/<method>/<timestamp> \
  --dataset data/Loc-Bench_V1_dataset.jsonl
```

输出：
1. `<run-dir>/list_symbols_metrics.json`
2. `<run-dir>/list_symbols_instance_metrics.csv`

指标：
1. `list_symbols_usage_rate_all`
2. `list_symbols_usage_rate_given_radar`
3. `list_symbols_call_hit_rate`
4. `list_symbols_instance_hit_rate_given_used`
5. `accuracy_uplift_used_vs_not_used_given_radar_pp`

---

## 3. 文档改动

改动文件：
1. `locbench/docs/locbench_tools_radar_runbook.md`
2. `locbench/docs/README.md`
3. `locbench/docs/locbench_radar_symbols_changes_20260226.md`（本文件）

---

## 4. 测试与验证记录

### 4.1 单测

执行命令：
```bash
PYTHONPATH=src pytest -q tests/run/test_list_symbols_metrics_analysis.py tests/run/test_list_symbols_tool.py
```

结果：
1. `7 passed`

---

### 4.2 关键回归测试

执行命令：
```bash
PYTHONPATH=src pytest -q \
  tests/run/test_list_symbols_tool.py \
  tests/run/test_locbench_tools_radar_guard.py \
  tests/run/test_locbench_oracle_sniper.py \
  tests/run/test_file_radar_search_index_policy.py
```

结果：
1. `19 passed`

---

### 4.3 实跑样例验证（分析脚本）

执行命令：
```bash
PYTHONPATH=src python -m minisweagent.locbench.analysis.list_symbols_metrics \
  --run-dir locbench/outputs/Qwen3-Coder-30B-A3B-Instruct/miniswe_tools_radar__search_first/20260226_000614 \
  --dataset data/Loc-Bench_V1_dataset.jsonl
```

输出摘要（该 run）：
1. `total_instances = 12`
2. `radar_called_count = 11`
3. `list_symbols_used_count = 0`
4. `list_symbols_usage_rate_all = 0.0`
5. `accuracy_overall = 0.75`

说明：
1. 该实验时间点早于 `list_symbols` 工具上线，因此使用率为 0，属预期。

---

## 5. 复现建议

1. 使用新 `tools_radar` prompt 跑一组新实验（同一 seed、同一 slice）。
2. 跑完立即执行 `list_symbols_metrics` 脚本并归档 JSON/CSV。
3. 对比 `list_symbols_used` vs `not_used_given_radar` 的正确率差。
