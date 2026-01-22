# SWE-QA-Bench 评分流程文档（mini-swe-agent）

SWE-QA-Bench 的评分逻辑已迁移到 mini-swe-agent 内部实现，
不再依赖 `SWE-QA-Bench/score` 目录。

---

## 1. 评分入口

推荐使用 YAML 入口：

```bash
cd /Users/chz/code/locbench/mini-swe-agent
PYTHONPATH=src python -m minisweagent.swe_qa_bench.score_from_yaml \
  --config /Users/chz/code/locbench/mini-swe-agent/swe_qa_bench/config/score_bash.yaml
```

---

## 2. YAML 配置字段说明

`score_bash.yaml` / `score_tools.yaml` 关键字段：

```yaml
dataset_root: /path/to/SWE-QA-Bench/SWE-QA-Bench/datasets
candidate_model: openai_deepseek-v3.2
method: miniswe_bash
judge_model: deepseek-v3.2
judge_api_base: https://api.example.com/v1/chat/completions
judge_api_key: sk-xxx
max_workers: 8
timeout: 60
repos: ["requests"]
```

说明：
- `candidate_model` + `method` 必须与 answers 输出路径一致。
- `judge_api_base` 必须是完整的 `/v1/chat/completions` URL。
- `repos` 为空时默认评分全部仓库。

---

## 3. 输出路径

评分输出写入：

```
SWE-QA-Bench/SWE-QA-Bench/datasets/scores/{MODEL}/{METHOD}/{repo}.jsonl
```

---

## 4. 兼容性处理（已内置）

评分逻辑内部已做字段 fallback：
- reference：`aggregated_answer` 不存在时回退到 `answer`
- candidate：`final_answer` 不存在时回退到 `answer`

无需修改原始 reference 数据。

---

## 5. 常见问题

1) **API 返回 404**
- 检查 `judge_api_base` 是否包含 `/v1/chat/completions`。

2) **评分为空或跳过**
- 检查 answers 路径是否存在：
  `datasets/answers/<model>/<method>/<repo>.jsonl`

3) **单条快速评分**
- 在 YAML 里设置 `repos: ["requests"]` 或 `repos: "requests"`。

