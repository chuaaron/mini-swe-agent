# SWE-QA-Bench 实验命令操作文档（迁移友好版）

本文档集中维护 **跑实验的命令**，适用于本地与服务器迁移场景。
推荐使用 YAML 运行入口，避免复杂命令行。

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
- YAML 里的路径支持环境变量展开。

---

## 1. 构建 Docker 镜像（bash-only / tools 必需）

```bash
cd $MINISWE_ROOT
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

---

## 2. 运行命令（推荐：YAML）

说明：`run_*.yaml` 通常包含 API key，已加入 `.gitignore`，迁移时请手动拷贝。

### 2.1 Bash-only（基线）

```bash
cd $MINISWE_ROOT
PYTHONPATH=src python -m minisweagent.swe_qa_bench.run_from_yaml \
  --config $MINISWE_ROOT/swe_qa_bench/config/run_bash.yaml
```

### 2.2 Tools（bash + code_search）

```bash
cd $MINISWE_ROOT
PYTHONPATH=src python -m minisweagent.swe_qa_bench.run_from_yaml \
  --config $MINISWE_ROOT/swe_qa_bench/config/run_tools.yaml
```

---

## 3. 同一批随机样本（建议三种方法对比）

在 `run_bash.yaml` / `run_tools.yaml` 中设置：

```yaml
shuffle: true
shuffle_seed: 123
slice: "0:20"
```

支持指定仓库（两种写法等价）：

```yaml
repos: ["requests", "flask"]
# 或
repos: "requests,flask"
```

---

## 4. 输出路径说明

- 轨迹与日志：
  - `$MINISWE_ROOT/swe_qa_bench/outputs/<model>/<method>/<timestamp>/`
- 候选答案：
  - `$SWEQA_DATASET/answers/<model>/<method>/<repo>.jsonl`
- 评分输出：
  - `$SWEQA_DATASET/scores/<model>/<method>/<repo>.jsonl`

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
rm -rf $MINISWE_ROOT/swe_qa_bench/outputs/*
rm -rf $MINISWE_ROOT/swe_qa_bench/worktrees/*
```

注意：
- 不建议删除 `datasets/answers` 与 `datasets/scores`，除非你明确要重跑全量。
- tools 模式的索引通常较大，清理请谨慎：`$MINISWE_ROOT/swe_qa_bench/indexes/`。
