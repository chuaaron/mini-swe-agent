# SWE-QA-Bench code_search 索引构建文档

本文件仅针对 tools 模式（code_search）使用，bash-only 不需要索引。

---

## 1. 前置条件

- 已安装依赖：`torch`, `transformers`, `einops`
- 本地模型已就绪：`swe_qa_bench/models/CodeRankEmbed`
- 索引输出目录已准备：`swe_qa_bench/indexes/`

---

## 2. 配置文件

配置文件路径：

```
mini-swe-agent/swe_qa_bench/config/code_search.yaml
```

关键字段示例：

```yaml
embedding_model: /data/locbench/mini-swe-agent/swe_qa_bench/models/CodeRankEmbed
index_root: /data/locbench/mini-swe-agent/swe_qa_bench/indexes
chunk_size: 800
chunk_overlap: 200
trust_remote_code: true
```

---

## 3. 手动构建索引（推荐）

构建单个 repo：

```bash
cd /Users/chz/code/locbench/mini-swe-agent
PYTHONPATH=src python -m minisweagent.swe_qa_bench.build_index \
  --repos-root /Users/chz/code/locbench/SWE-QA-Bench/SWE-QA-Bench/datasets/repos \
  --repos requests \
  --tool-config /Users/chz/code/locbench/mini-swe-agent/swe_qa_bench/config/code_search.yaml
```

构建多个 repo：

```bash
--repos requests,flask,pytest
```

不指定 `--repos` 时默认构建全部 repo。

---

## 4. 离线模式（可选）

```bash
export TRANSFORMERS_OFFLINE=1
export HF_HOME=/data/locbench/mini-swe-agent/swe_qa_bench/models
```

---

## 5. 索引输出位置

索引默认写入：

```
mini-swe-agent/swe_qa_bench/indexes/<repo>/...
```

tools 运行时会读取该目录，确保路径与 `code_search.yaml` 一致。

