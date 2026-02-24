# LocBench 全流程操作手册（含 Radar / Oracle）

本文档是当前实验的统一执行手册，覆盖环境、运行、A/B、结果检查和常见问题。

## 1. 一次性准备

1. 进入项目根目录：
```bash
cd /Users/chz/code/locbench/mini-swe-agent
```
2. 本地配置：
```bash
cp locbench/config/local.yaml.example locbench/config/local.yaml
```
3. 检查 `locbench/config/local.yaml` 关键项：
   - `paths.dataset_root`
   - `paths.repos_root`
   - `paths.output_root`
   - `paths.output_model_name`
   - `env.OPENAI_API_KEY`
4. 若跑 `tools` / `tools_radar` / `ir`，还需：
   - `paths.indexes_root`
   - `paths.model_root`
5. 构建镜像：
```bash
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

## 2. 快速自检

1. 命令可用性：
```bash
PYTHONPATH=src python -m minisweagent.run_locbench --help
```
2. 单测（关键路径）：
```bash
PYTHONPATH=src pytest -q \
  tests/run/test_locbench_tools_radar_guard.py \
  tests/run/test_locbench_oracle_sniper.py
```

## 3. 各模式标准命令

## 3.1 Bash-only

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --slice 0:20 \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

## 3.2 Tools（code_search）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --tools-prompt neutral \
  --slice 0:20 \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

## 3.3 Tools-Radar（file_radar_search）

`tools-prompt` 可选：`neutral` / `search_first` / `search_fallback`

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --slice 0:20 \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

## 3.4 Oracle-Sniper（你现在的新实验）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt oracle_sniper \
  --method miniswe_tools_oracle_sniper \
  --slice 0:20 \
  --workers 4 \
  --shuffle --shuffle-seed 123 \
  --skip-missing \
  --redo-existing
```

## 3.5 IR-only

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode ir \
  --slice 0:20 \
  --skip-missing \
  --redo-existing
```

## 4. 提示词版本对比（A/B）

推荐做法：固定 `--tools-prompt`，用 `--agent-config` 切不同提示词文件，并用 `--method` 隔离输出目录。

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --agent-config locbench/config/experiments/agent_tools_radar_v2.yaml \
  --method miniswe_tools_radar__v2 \
  --slice 0:50 \
  --shuffle --shuffle-seed 123 \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

## 5. 对比实验模板

## 5.1 tools vs tools_radar

```bash
SEED=123
SLICE=0:50

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --tools-prompt neutral \
  --method miniswe_tools__ab \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 4 --skip-missing --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --method miniswe_tools_radar__ab \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 4 --skip-missing --redo-existing
```

## 5.2 tools_radar vs oracle_sniper

```bash
SEED=123
SLICE=0:50

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --method miniswe_tools_radar__ab2 \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 4 --skip-missing --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt oracle_sniper \
  --method miniswe_tools_oracle_sniper__ab2 \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 4 --skip-missing --redo-existing
```

## 6. 输出路径与字段

1. 轨迹目录：
`locbench/outputs/<model>/<method>/<timestamp>/`
2. 定位结果：
`locbench/results/loc_output/<model>/<method>/loc_outputs_<timestamp>.jsonl`
3. 汇总：
`locbench/outputs/<model>/<method>/<timestamp>/run_summary.json`

`tools_radar` / `oracle_sniper` 重点字段：
1. `radar_called`
2. `blocked_submission_count`
3. `inspected_files`
4. `radar_verification_satisfied`
5. `oracle_sniper_mode`
6. `oracle_file_provided`
7. `oracle_primary_file`
8. `oracle_verification_satisfied`

## 7. 结果快速检查

假设你有一个 `run_summary.json` 路径，先看核心指标：

```bash
python - <<'PY'
import json
from pathlib import Path

summary = Path("locbench/outputs/<model>/<method>/<timestamp>/run_summary.json")
data = json.loads(summary.read_text())
stats = data.get("stats_overall", {})
keys = [
    "pass_rate",
    "radar_called_count",
    "verification_compliance_rate",
    "oracle_file_provided_rate",
    "oracle_verification_compliance_rate",
    "entity_hit_rate_given_oracle_file",
    "steps_to_success_in_oracle_mean",
]
for k in keys:
    print(f"{k}: {stats.get(k)}")
PY
```

## 8. 常见问题

1. `paths.indexes_root and paths.model_root must be set`：
   - 非 oracle_sniper 的 `tools/tools_radar/ir` 会强校验这两个路径。
2. `Tool config not found`：
   - 检查 `locbench/config/code_search.yaml` 或 `locbench/config/file_radar_search.yaml`。
3. Oracle 模式里模型反复输出 `@tool`：
   - 已有运行时硬拦截，报错会明确要求改用 bash。
4. 提交一直被拦截：
   - 没有读取候选文件或 `file_hint` 未在读取历史中出现。

## 9. 推荐执行顺序

1. 先跑 `slice 0:5` 冒烟。
2. 再跑固定 seed 的 50 条 A/B。
3. 最后跑主实验（500 条或全量）。
4. 全部实验完成后再做论文图表汇总。
