# LocBench 实验命令操作文档（迁移友好版）

本文档集中维护 **跑实验的命令**，适用于本地与服务器迁移场景。
三种方法共享同一批样本时，建议使用 `--shuffle --shuffle-seed` 固定随机顺序。

---

## 0. 推荐路径变量（服务器迁移时改这里）

```bash
export LOCBENCH_ROOT=/data/locbench
export MINISWE_ROOT=$LOCBENCH_ROOT/mini-swe-agent
export LOCBENCH_DATASET=$LOCBENCH_ROOT/data/Loc-Bench_V1_dataset.jsonl
export LOCBENCH_REPOS=$LOCBENCH_ROOT/locbench_repos
export CODE_SEARCH_MODEL=$MINISWE_ROOT/locbench/models/CodeRankEmbed
export CODE_SEARCH_INDEX=$MINISWE_ROOT/locbench/indexes/llamaindex_code_custom_40_15_800/dense_index_llamaindex_code
```

说明：
- `CODE_SEARCH_MODEL` 与 `CODE_SEARCH_INDEX` 仅对 **IR-only / tools** 需要。
- 若路径不同，请同步更新 `src/minisweagent/config/extra/code_search.yaml`。

---

## 1. 构建 Docker 镜像（bash-only / tools 必需）

```bash
cd $MINISWE_ROOT
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

---

## 2. 三种方法的运行命令

### 2.1 Bash-only（基线）

```bash
mini-extra locbench \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --workers 1 \
  --redo-existing
```

### 2.2 Tools（bash + code_search）

```bash
mini-extra locbench-tools \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --workers 1 \
  --redo-existing
```

### 2.3 IR-only（纯检索）

```bash
mini-extra locbench-code-search \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --redo-existing
```

---

## 3. 同一批随机样本（推荐：三种方法对比）

固定同一批随机 20 条：

```bash
SEED=123
SLICE=0:20

mini-extra locbench \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 1 --redo-existing

mini-extra locbench-tools \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --workers 1 --redo-existing

mini-extra locbench-code-search \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --shuffle --shuffle-seed $SEED --slice $SLICE \
  --redo-existing
```

---

## 4. 常见筛选方式

单条实例：
```bash
mini-extra locbench-tools \
  --dataset $LOCBENCH_DATASET \
  --repos-root $LOCBENCH_REPOS \
  --filter 'UXARRAY__uxarray-1117' \
  --workers 1 --redo-existing
```

切片运行：
```bash
--slice 0:10
```

---

## 5. 输出路径说明

- 轨迹：`$MINISWE_ROOT/locbench/outputs/<model>/<timestamp>/<instance_id>/<instance_id>.traj.json`
- 日志：`$MINISWE_ROOT/locbench/outputs/<model>/<timestamp>/minisweagent.log`
- 结果：`$MINISWE_ROOT/locbench/loc_output/<model>/loc_outputs_<timestamp>.jsonl`
- IR-only 结果：`$MINISWE_ROOT/locbench/loc_output/code_search/<provider>/<model>/loc_outputs_<timestamp>.jsonl`

---

## 6. 评估命令（快速）

```bash
python $LOCBENCH_ROOT/evaluation/simple_eval.py \
  <loc_outputs.jsonl> \
  $LOCBENCH_DATASET
```

---

## 7. 常用参数速查

- `--shuffle` + `--shuffle-seed`：固定随机顺序
- `--slice A:B`：切片运行
- `--filter REGEX`：按 instance_id 过滤
- `--redo-existing`：覆盖已有结果
- `--workers N`：并行数
- `--output / --loc-output`：自定义输出路径

---

## 8. 清理与维护

- 删除 worktree：`rm -rf $MINISWE_ROOT/locbench/tool_worktrees`
- 删除旧输出：`rm -rf $MINISWE_ROOT/locbench/outputs/*` 与 `loc_output/*`
