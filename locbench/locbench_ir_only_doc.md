# LocBench IR-only 测评操作文档（mini-swe-agent）

本文档仅覆盖 **IR-only / code_search** 测评流程，不涉及 Agent + bash。

相关总览文档：
- `locbench/locbench_runbook.md`（运行命令汇总）
- `locbench/locbench_methods_design_doc.md`（方法设计）
- `locbench/locbench_env_setup.md`（环境配置与迁移）

---

## 1. 目标

使用本地 CodeRankEmbed + 预建索引，对 LocBench 做纯检索定位评测。

---

## 2. 资源路径（你的现有资源）

- 数据集：`/Users/chz/code/locbench/data/Loc-Bench_V1_dataset.jsonl`
- 仓库镜像：`/Users/chz/code/locbench/locbench_repos`
- 模型：`/Users/chz/code/locbench/mini-swe-agent/locbench/models/CodeRankEmbed`
- 索引：`/Users/chz/code/locbench/mini-swe-agent/locbench/indexes/llamaindex_code_custom_40_15_800/dense_index_llamaindex_code`
- 输出目录（默认）：`/Users/chz/code/locbench/mini-swe-agent/locbench/loc_output/code_search/...`
- 日志目录（默认）：`/Users/chz/code/locbench/mini-swe-agent/locbench/outputs/code_search/`

---

## 3. 环境准备

确保 `miniswe` 环境可用，并安装必要依赖：

```bash
pip install torch transformers einops
```

检查依赖：

```bash
python -c "import torch, transformers, einops; print(torch.__version__)"
```

---

## 4. 配置 code_search

配置文件：`/Users/chz/code/locbench/mini-swe-agent/src/minisweagent/config/extra/code_search.yaml`

应确保关键字段如下（已设置完成）：

```yaml
embedding_model: /Users/chz/code/locbench/mini-swe-agent/locbench/models/CodeRankEmbed
index_root: /Users/chz/code/locbench/mini-swe-agent/locbench/indexes/llamaindex_code_custom_40_15_800/dense_index_llamaindex_code
trust_remote_code: true
```

说明：
- `embedding_model` 必须是本地模型路径（避免联网失败）
- `index_root` 指向你现有的预建索引根目录
- `trust_remote_code` 为 CodeRankEmbed 的必需项

---

## 5. 运行 IR-only 测评

### 5.1 单条测试

```bash
mini-extra locbench-code-search \
  --dataset /Users/chz/code/locbench/data/Loc-Bench_V1_dataset.jsonl \
  --repos-root /Users/chz/code/locbench/locbench_repos \
  --slice 0:1
```

### 5.2 全量测评

```bash
mini-extra locbench-code-search \
  --dataset /Users/chz/code/locbench/data/Loc-Bench_V1_dataset.jsonl \
  --repos-root /Users/chz/code/locbench/locbench_repos
```

### 5.3 可选参数

- `--filter <regex>`：按 instance_id 过滤
- `--slice A:B`：切片运行
- `--redo-existing`：不跳过已存在结果
- `--loc-output <path>`：指定输出路径（推荐用于分批评测）

---

## 6. 输出说明

默认输出路径示例：

```
/Users/chz/code/locbench/mini-swe-agent/locbench/loc_output/code_search/local/<MODEL>/loc_outputs_YYYYMMDD_HHMMSS.jsonl
```

JSONL 字段包含：
- `instance_id`
- `found_files`
- `found_modules`
- `found_entities`
- `meta_data`

---

## 7. 评估指标

可以直接使用 LocBench 自带评估脚本：

```bash
python /Users/chz/code/locbench/evaluation/simple_eval.py \
  <loc_outputs.jsonl> \
  /Users/chz/code/locbench/data/Loc-Bench_V1_dataset.jsonl
```

---

## 8. 常见问题

1) **模型加载提示 trust_remote_code**
- 设置 `trust_remote_code: true`

2) **缺少 torch / transformers / einops**
- 重新安装：
  ```bash
  pip install torch transformers einops
  ```

3) **索引不存在**
- 确保 `index_root` 指向已有索引目录
- 或使用 `--force-rebuild` 重新构建（耗时）
