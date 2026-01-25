# SWE-QA-Bench 环境配置与迁移指南

本文档用于本地与服务器迁移场景，说明依赖、路径、模型与索引的配置方式。

---

## 1. 系统要求

- Python 3.10/3.11
- Git
- Docker（bash-only / tools 必需）
- 足够磁盘空间（repos + outputs + 可选索引）
- 可选：GPU（code_search 可用 CUDA）

---

## 2. 推荐目录布局（服务器示例）

```
/data/locbench/
  mini-swe-agent/
    swe_qa_bench/
      models/CodeRankEmbed
      indexes/
  SWE-QA-Bench/SWE-QA-Bench/
    datasets/
      questions/
      reference/
      repos/
```

推荐统一路径变量：

```bash
export SWEQA_ROOT=/data/locbench/SWE-QA-Bench/SWE-QA-Bench
export SWEQA_DATASET=$SWEQA_ROOT/datasets
export SWEQA_REPOS=$SWEQA_DATASET/repos
export MINISWE_ROOT=/data/locbench/mini-swe-agent
```

---

## 3. Python 环境

```bash
cd $MINISWE_ROOT
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

tools 模式依赖（code_search）：

```bash
pip install torch transformers einops
```

---

## 4. API 与模型配置

在 `swe_qa_bench/config/local.yaml` 中配置 API 与路径（文件已忽略提交）：

```yaml
env:
  OPENAI_API_KEY: "sk-xxx"
  OPENAI_API_BASE: "https://api.example.com/v1/chat/completions"
```

建议先复制模板：
```bash
cp $MINISWE_ROOT/swe_qa_bench/config/local.yaml.example \\
  $MINISWE_ROOT/swe_qa_bench/config/local.yaml
```

说明：
- `OPENAI_API_BASE` 需为完整的 `/v1/chat/completions` URL。
- `output_model_name` 用于输出目录名，避免包含 `/`。

默认仓库隔离为单仓库挂载（`repo_mount_mode: single`）。如需回退到全量挂载，
请在 `swe_qa_bench/config/agent_bash.yaml` / `agent_tools.yaml` 中设置：
```
environment:
  repo_mount_mode: all
```

---

## 5. Docker 镜像（bash-only / tools）

```bash
cd $MINISWE_ROOT
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

---

## 6. code_search 模型与索引（tools 模式）

确保本地模型与索引已准备：
- 模型路径：`$MINISWE_ROOT/swe_qa_bench/models/CodeRankEmbed`
- 索引路径：`$MINISWE_ROOT/swe_qa_bench/indexes/`

配置文件：`$MINISWE_ROOT/swe_qa_bench/config/code_search.yaml`

说明：
- `embedding_model` 与 `index_root` 由 `local.yaml` 中的 `paths.model_root` / `paths.indexes_root` 动态注入。
- `code_search.yaml` 仅保留通用参数（chunker、overlap、trust_remote_code 等）。

可选：离线模式

```bash
export TRANSFORMERS_OFFLINE=1
export HF_HOME=$MINISWE_ROOT/swe_qa_bench/models
```

---

## 7. 运行前检查

- `datasets/questions/` 与 `datasets/reference/` 存在
- `datasets/repos/<repo>` 可读（默认单仓库只读挂载）
- `default.yaml` 与 `local.yaml` 已配置路径与 API
- tools 模式已完成索引构建（见索引文档）
