# SWE-QA-Bench 测评套件（Cheat Sheet）

## Quick Start

### 1) 环境准备
- 已安装 Docker
- 已安装 Python 3.10+

### 2) 配置本地环境（只改这两项即可）
```bash
cd /Users/chz/code/locbench/mini-swe-agent
cp swe_qa_bench/config/local.yaml.example swe_qa_bench/config/local.yaml
vim swe_qa_bench/config/local.yaml
```

`local.yaml` 最小示例（只改 dataset_root 和 OPENAI_API_KEY）：
```yaml
paths:
  # 必改：数据集路径
  dataset_root: /Users/chz/code/locbench/SWE-QA-Bench/SWE-QA-Bench/datasets
  # repos_root 可省略，默认 = dataset_root/repos

env:
  # 必改：API Key
  OPENAI_API_KEY: sk-xxxxxx
  OPENAI_API_BASE: https://api.chatanywhere.tech/v1/chat/completions
```

### 3) 跑起来（单条通路）
```bash
cd /Users/chz/code/locbench/mini-swe-agent
PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode bash --repos requests --slice 0:1 --workers 1 \
  --run-id 20260131_120000
```

## Command Cheat Sheet

### A) Baseline（Bash-only）
```bash
PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode bash --repos requests --workers 4
```

### B) Tools（bash + code_search）
```bash
PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode tools --repos requests --workers 1 \
  --run-id 20260131_120000
```
说明：首次 tools 运行会为该 repo 自动构建索引（只建当前 repo，不会全量建）。

### E) Resume（断点续跑）
```bash
PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode tools --repos requests --slice 0:200 --workers 4 \
  --resume 20260131_120000
```
说明：`--resume` 会接着指定 `run_id` 跑，并跳过已有答案（不会覆盖）。

### C) 切片调试
```bash
PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode bash --repos requests --slice 10:20
```

### D) 评分（LLM-as-judge）
```bash
PYTHONPATH=src python -m minisweagent.swe_qa_bench.score_from_yaml \
  --config /Users/chz/code/locbench/mini-swe-agent/swe_qa_bench/config/score_bash.yaml
```

### D2) 批量评分多个实验（答案在不同 run_id）
```bash
PYTHONPATH=src python -m minisweagent.swe_qa_bench.score \
  --dataset-root /Users/chz/code/locbench/SWE-QA-Bench/SWE-QA-Bench/datasets \
  --answers-roots /abs/path/to/swe_qa_bench/results/answers \
  --judge-model deepseek-v3.2 \
  --judge-api-base https://api.chatanywhere.tech/v1/chat/completions \
  --judge-api-key sk-xxxxxx
```
说明：
- `answers_roots` 可传 results 根目录、`answers/` 目录，或 `answers/<model>/<method>/<run_id>` 目录（逗号分隔）。
- `candidate_model` / `method` 可选，填写则作为过滤条件；不填则默认全选。

## Outputs

你只需要看这两个目录：

1) 最终答案（用于评分，按 run_id 分目录）
```
/Users/chz/code/locbench/mini-swe-agent/swe_qa_bench/results/answers/<model>/<method>/<run_id>/<repo>.jsonl
```

2) 运行日志与轨迹（用于排错，按 run_id 分目录）
```
/Users/chz/code/locbench/mini-swe-agent/swe_qa_bench/results/outputs/<model>/<method>/<run_id>/
```

3) 评分输出（按 run_id 分目录）
```
/Users/chz/code/locbench/mini-swe-agent/swe_qa_bench/results/scores/<model>/<method>/<run_id>/
```

## Migration

换服务器只做三步：
1) 拉代码（git clone / git pull）
2) 确保数据集在新机器上（SWE-QA-Bench/datasets）
3) 新建 `swe_qa_bench/config/local.yaml`，只改：
   - `paths.dataset_root`
   - `env.OPENAI_API_KEY`

其他路径（indexes/models/results）默认使用项目内相对路径，无需修改。
