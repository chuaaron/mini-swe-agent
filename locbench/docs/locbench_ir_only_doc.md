# LocBench IR-only 测评操作文档（mini-swe-agent）

本文档仅覆盖 **IR-only / code_search** 测评流程，不涉及 Agent + bash。

相关总览文档：
- `locbench/docs/locbench_runbook.md`（运行命令汇总）
- `locbench/docs/locbench_methods_design_doc.md`（方法设计）
- `locbench/docs/locbench_env_setup.md`（环境配置与迁移）

---

## 1. 目标

使用本地 CodeRankEmbed + 预建索引，对 LocBench 做纯检索定位评测。

---

## 2. 资源路径（本地配置）

通过 `locbench/config/local.yaml` 统一配置：
- `paths.dataset_root`
- `paths.repos_root`
- `paths.model_root`
- `paths.indexes_root`

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

配置文件：`locbench/config/local.yaml`

示例：
```yaml
paths:
  model_root: /Users/chz/code/locbench/mini-swe-agent/locbench/models/CodeRankEmbed
  indexes_root: /Users/chz/code/locbench/mini-swe-agent/locbench/indexes/llamaindex_code_custom_40_15_800/dense_index_llamaindex_code
```

---

## 5. 运行 IR-only 测评

### 5.1 单条测试

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode ir \
  --slice 0:1
```

### 5.2 全量测评

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode ir
```

### 5.3 可选参数

- `--filter <regex>`：按 instance_id 过滤
- `--slice A:B`：切片运行
- `--redo-existing`：不跳过已存在结果
- `--filter <regex>`：按 instance_id 过滤
- `--slice A:B`：切片运行
- `--redo-existing`：不跳过已存在结果

---

## 6. 输出说明

默认输出路径示例：

```
/Users/chz/code/locbench/mini-swe-agent/locbench/results/loc_output/<MODEL>/<METHOD>/loc_outputs_YYYYMMDD_HHMMSS.jsonl
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
