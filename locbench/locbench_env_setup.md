# LocBench 环境配置与迁移指南

本文档用于本地与服务器迁移场景，说明依赖、路径、模型与索引的配置方式。

---

## 1. 系统要求

- Python 3.10/3.11
- Git
- Docker（bash-only / tools 必需）
- 足够磁盘空间（repo 镜像 + 索引）
- 可选：GPU（code_search 可用 CUDA）

---

## 2. 推荐目录布局（服务器示例）

```
/data/locbench/
  data/Loc-Bench_V1_dataset.jsonl
  locbench_repos/
  mini-swe-agent/
    locbench/models/CodeRankEmbed
    locbench/indexes/...
```

可通过环境变量统一配置：

```bash
export LOCBENCH_ROOT=/data/locbench
export MINISWE_ROOT=$LOCBENCH_ROOT/mini-swe-agent
export LOCBENCH_DATASET=$LOCBENCH_ROOT/data/Loc-Bench_V1_dataset.jsonl
export LOCBENCH_REPOS=$LOCBENCH_ROOT/locbench_repos
```

---

## 3. Python 环境

```bash
cd $MINISWE_ROOT
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

code_search 依赖（IR-only / tools）：
```bash
pip install torch transformers einops
```

可选依赖（Graph mapper）：
```bash
pip install networkx
```

---

## 4. 全局配置与 API Key

mini-swe-agent 使用全局 `.env`，位置可由 `MSWEA_GLOBAL_CONFIG_DIR` 指定。

```bash
export MSWEA_GLOBAL_CONFIG_DIR=$MINISWE_ROOT/.config
mini-extra config setup
```

或直接设置环境变量：
```bash
export MSWEA_MODEL_NAME=openai/deepseek-v3.2
export OPENAI_API_KEY=sk-...
```

---

## 5. Docker 镜像（bash-only / tools）

```bash
cd $MINISWE_ROOT
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

若在中国网络环境，Dockerfile 已切换为镜像源；
如需替换镜像源，可改 `locbench/Dockerfile`。

---

## 6. 模型与索引迁移（code_search）

必须准备：
- 本地模型目录（如 `locbench/models/CodeRankEmbed`）
- 预建索引目录（如 `locbench/indexes/...`）

迁移后更新配置文件：
`src/minisweagent/config/extra/code_search.yaml`

关键字段示例：
```yaml
embedding_model: /data/locbench/mini-swe-agent/locbench/models/CodeRankEmbed
index_root: /data/locbench/mini-swe-agent/locbench/indexes/llamaindex_code_custom_40_15_800/dense_index_llamaindex_code
trust_remote_code: true
```

如需离线运行，可设置：
```bash
export TRANSFORMERS_OFFLINE=1
export HF_HOME=$MINISWE_ROOT/locbench/models
```

---

## 7. 迁移清单（建议）

需要复制到服务器的资产：
- `data/Loc-Bench_V1_dataset.jsonl`
- `locbench_repos/`（完整 git 镜像）
- `mini-swe-agent/` 代码目录
- `locbench/models/CodeRankEmbed`（本地模型）
- `locbench/indexes/...`（预建索引）

---

## 8. 运行前检查

- `locbench_repos/<repo_dir>/.git` 存在
- `base_commit` 能在镜像中 `git checkout`
- `code_search.yaml` 的路径已更新
- Docker 能拉起容器（bash-only / tools）

---

## 9. 运行与清理

- 输出目录：`$MINISWE_ROOT/locbench/outputs/` 与 `loc_output/`
- worktree 目录：`$MINISWE_ROOT/locbench/tool_worktrees/`

需要清理时：
```bash
rm -rf $MINISWE_ROOT/locbench/tool_worktrees
rm -rf $MINISWE_ROOT/locbench/outputs/*
rm -rf $MINISWE_ROOT/locbench/loc_output/*
```

