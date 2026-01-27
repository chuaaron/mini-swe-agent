# LocBench 测评速查表

## 快速开始

1) 配置本机路径（迁移只改这里）

```bash
cd /Users/chz/code/locbench/mini-swe-agent
cp locbench/config/local.yaml.example locbench/config/local.yaml
```

`locbench/config/local.yaml` 最小示例：

```yaml
paths:
  dataset_root: /abs/path/to/Loc-Bench_V1_dataset.jsonl
  repos_root: /abs/path/to/locbench_repos
  # tools/ir 需要：
  # indexes_root: /abs/path/to/locbench/indexes
  # model_root: /abs/path/to/locbench/models/CodeRankEmbed
  # output_model_name: openai_deepseek-v3.2

env:
  OPENAI_API_KEY: sk-xxxxxx
  # OPENAI_API_BASE: https://api.chatanywhere.tech/v1/chat/completions
```

2) 构建 Docker（bash/tools 必需）

```bash
cd /Users/chz/code/locbench/mini-swe-agent
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

3) 运行（任选一种）

```bash
# bash-only
PYTHONPATH=src python -m minisweagent.run_locbench --mode bash --slice 0:10 --workers 1 --redo-existing

# tools（需要已有索引+模型）
PYTHONPATH=src python -m minisweagent.run_locbench --mode tools --slice 0:1 --workers 1 --redo-existing

# ir-only（纯检索）
PYTHONPATH=src python -m minisweagent.run_locbench --mode ir --slice 0:1 --redo-existing
```

兼容入口：`mini-extra locbench` / `mini-extra locbench-tools` / `mini-extra locbench-code-search`

---

## 结果在哪里

- 结果：`locbench/results/loc_output/<model>/<method>/loc_outputs_<timestamp>.jsonl`
- 日志/轨迹：`locbench/outputs/<model>/<method>/<timestamp>/`

---

## 评分（简版）

```bash
python /Users/chz/code/locbench/evaluation/simple_eval.py \
  /Users/chz/code/locbench/mini-swe-agent/locbench/results/loc_output/<model>/<method>/loc_outputs_<timestamp>.jsonl \
  /Users/chz/code/locbench/data/Loc-Bench_V1_dataset.jsonl
```

---

## 迁移到新服务器

只需：
- 复制代码 + 数据集 + repos
- 修改 `locbench/config/local.yaml` 的 `dataset_root` 和 `OPENAI_API_KEY`
- tools/ir 再补 `indexes_root`、`model_root`

---

## 其他文档

- `locbench/docs/locbench_runbook.md`（完整命令）
- `locbench/docs/locbench_env_setup.md`（环境与迁移）
- `locbench/docs/locbench_ir_only_doc.md`（IR-only 说明）
- `locbench/docs/locbench_methods_design_doc.md`（方法设计）
