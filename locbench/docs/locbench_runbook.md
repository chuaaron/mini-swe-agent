# LocBench 实验命令操作文档（迁移友好版）

本文档集中维护 **跑实验的命令**，适用于本地与服务器迁移场景。
三种方法共享同一批样本时，建议使用 `--shuffle --shuffle-seed` 固定随机顺序。

---

## 0. 本地配置（迁移只改这里）

```bash
cd /Users/chz/code/locbench/mini-swe-agent
cp locbench/config/local.yaml.example locbench/config/local.yaml
```

编辑 `locbench/config/local.yaml`：
- 必填：`paths.dataset_root`（Loc-Bench JSONL）
- 必填：`paths.repos_root`（locbench_repos）
- 必填：`env.OPENAI_API_KEY`
- Tools/IR 可选：`paths.indexes_root`、`paths.model_root`

---

## 1. 构建 Docker 镜像（bash/tools 必需）

```bash
cd /Users/chz/code/locbench/mini-swe-agent
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

---

## 2. 三种方法的运行命令

### 2.1 Bash-only（基线）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --slice 0:1 \
  --workers 1 \
  --redo-existing
```

### 2.2 Tools（bash + code_search）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --slice 0:1 \
  --workers 1 \
  --redo-existing
```

### 2.3 IR-only（纯检索）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode ir \
  --slice 0:1 \
  --redo-existing
```

> 兼容入口：`mini-extra locbench` / `mini-extra locbench-tools` / `mini-extra locbench-code-search`

---

## 3. 同一批随机样本（三种方法对比）

```bash
SEED=123
SLICE=0:20

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 1 --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 1 --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode ir \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --redo-existing
```

---

## 4. 常见筛选方式

单条实例：
```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --filter 'UXARRAY__uxarray-1117' \
  --workers 1 --redo-existing
```

切片运行：
```bash
--slice 0:10
```

---

## 5. 输出路径说明

- 轨迹：`mini-swe-agent/locbench/outputs/<model>/<method>/<timestamp>/<instance_id>/<instance_id>.traj.json`
- 日志：`mini-swe-agent/locbench/outputs/<model>/<method>/<timestamp>/minisweagent.log`
- 结果：`mini-swe-agent/locbench/results/loc_output/<model>/<method>/loc_outputs_<timestamp>.jsonl`

---

## 6. 评估命令（快速）

```bash
python /Users/chz/code/locbench/evaluation/simple_eval.py \
  <loc_outputs.jsonl> \
  /Users/chz/code/locbench/data/Loc-Bench_V1_dataset.jsonl
```

---

## 7. 常用参数速查

- `--shuffle` + `--shuffle-seed`：固定随机顺序
- `--slice A:B`：切片运行
- `--filter REGEX`：按 instance_id 过滤
- `--redo-existing`：覆盖已有结果
- `--workers N`：并行数
- `--keep-worktrees`：保留 worktree

---

## 8. 清理与维护

- 删除 worktree：`rm -rf locbench/worktrees`
- 删除旧输出：`rm -rf locbench/outputs/*` 与 `locbench/results/loc_output/*`
