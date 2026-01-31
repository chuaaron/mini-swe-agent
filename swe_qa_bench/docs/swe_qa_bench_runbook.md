# SWE-QA-Bench 实验命令操作文档（迁移友好版）

本文档集中维护 **跑实验的命令**，适用于本地与服务器迁移场景。
配置采用 default/local 分层，迁移时只改 `local.yaml`。

---

## 0. 推荐路径变量（服务器迁移时改这里）

```bash
export SWEQA_ROOT=/data/locbench/SWE-QA-Bench/SWE-QA-Bench
export SWEQA_DATASET=$SWEQA_ROOT/datasets
export SWEQA_REPOS=$SWEQA_DATASET/repos
export MINISWE_ROOT=/data/locbench/mini-swe-agent
export SWEQA_MODEL=$MINISWE_ROOT/swe_qa_bench/models/CodeRankEmbed
export SWEQA_INDEX=$MINISWE_ROOT/swe_qa_bench/indexes
```

说明：
- `SWEQA_MODEL` 与 `SWEQA_INDEX` 仅在 tools 模式需要。
- 路径优先在 `swe_qa_bench/config/local.yaml` 中配置。

---

## 1. 构建 Docker 镜像（bash-only / tools 必需）

```bash
cd $MINISWE_ROOT
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

---

## 2. 运行命令（统一入口）

配置文件：
- `swe_qa_bench/config/default.yaml`（提交到 Git，内置相对路径默认值）
- `swe_qa_bench/config/local.yaml`（本机配置，已加入 `.gitignore`）

说明：`run_from_yaml` 已标记为 Deprecated，仅保留兼容。

建议先复制模板：
```bash
cp $MINISWE_ROOT/swe_qa_bench/config/local.yaml.example \\
  $MINISWE_ROOT/swe_qa_bench/config/local.yaml
```

注意：
- `--repos` 后面的参数不要被换行拆开（否则 shell 会把 repo 名当作命令）。
- 迁移到新机器时，通常只需改 `local.yaml` 里的 `dataset_root` 和 `OPENAI_API_KEY`。

默认仓库隔离为单仓库挂载（`repo_mount_mode: single`）。如需回退到全量挂载，
请在 `swe_qa_bench/config/agent_bash.yaml` / `agent_tools.yaml` 中设置：
```
environment:
  repo_mount_mode: all
```

### 2.1 Bash-only（基线）

```bash
cd $MINISWE_ROOT
PYTHONPATH=src python -m minisweagent.run_swe_qa --mode bash
```

最小化单条 sanity run（与当前实验一致）：
```bash
cd $MINISWE_ROOT
PYTHONPATH=src python -m minisweagent.run_swe_qa \\
  --mode bash --repos requests --slice 0:1 --workers 1 --redo-existing
```

说明：
- `--redo-existing` 会把同一问题追加到 `answers/*.jsonl`，需要干净评分时先清理该文件。
- 运行完成会打印 `Answer appended to: .../answers/<model>/<method>/<repo>.jsonl`。

### 2.2 Tools（bash + code_search）

```bash
cd $MINISWE_ROOT
PYTHONPATH=src python -m minisweagent.run_swe_qa --mode tools
```

最小化单条 sanity run（tools）：
```bash
cd $MINISWE_ROOT
PYTHONPATH=src python -m minisweagent.run_swe_qa \\
  --mode tools --repos requests --slice 0:1 --workers 1 --redo-existing
```

说明：
- tools 模式需要已构建索引与模型路径（见 `local.yaml` 的 `paths.indexes_root` 与 `paths.model_root`）。

---

## 3. 同一批随机样本（建议三种方法对比）

在 `default.yaml` / `local.yaml` 中设置：

```yaml
run:
  shuffle: true
  shuffle_seed: 123
  slice: "0:20"
```

支持指定仓库（两种写法等价）：

```yaml
run:
  repos: ["requests", "flask"]
  # 或
  # repos: "requests,flask"
```

---

## 4. 输出路径说明（run_id 隔离）

- 轨迹与日志：
  - `$MINISWE_ROOT/swe_qa_bench/results/<run_id>/outputs/<model>/<method>/<timestamp>/`
- 候选答案（不再写入数据集目录）：
  - `$MINISWE_ROOT/swe_qa_bench/results/<run_id>/answers/<model>/<method>/<repo>.jsonl`
- 评分输出（不再写入数据集目录）：
  - `$MINISWE_ROOT/swe_qa_bench/results/<run_id>/scores/<model>/<method>/<repo>.jsonl`

说明：
- answers 里的每条记录包含 `stats` 字段（token/cost/api_calls 等）。
- `Overall Progress ($X.XX)` 来自计费统计，若为 0.00 请确认 `tiktoken` 已安装。
- 输出根目录可在 `swe_qa_bench/config/local.yaml` 中通过 `paths.output_root` 自定义。

---

## 5. 评分命令

```bash
cd $MINISWE_ROOT
PYTHONPATH=src python -m minisweagent.swe_qa_bench.score_from_yaml \
  --config $MINISWE_ROOT/swe_qa_bench/config/score_bash.yaml
```

详细配置与字段说明见：
- `swe_qa_bench/docs/swe_qa_bench_scoring_doc.md`

---

## 6. 清理与维护

```bash
rm -rf $MINISWE_ROOT/swe_qa_bench/results/<run_id>/outputs/*
rm -rf $MINISWE_ROOT/swe_qa_bench/worktrees/*
```

注意：
- 不建议删除 `swe_qa_bench/results/<run_id>/answers` 与 `swe_qa_bench/results/<run_id>/scores`，除非你明确要重跑该 run。
- tools 模式的索引通常较大，清理请谨慎：`$MINISWE_ROOT/swe_qa_bench/indexes/`。
