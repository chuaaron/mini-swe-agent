# SWE-QA-Bench 测评设计文档（mini-swe-agent）

本文档描述如何在 **mini-swe-agent** 中新增 SWE-QA-Bench 测评流程。
目标是与 LocBench 设计平行、完全解耦、互不影响，并严格对齐 SWE-QA-Bench 原生输入输出与评分方式。

---

## 文档索引（建议先看）

- `swe_qa_bench/docs/swe_qa_bench_runbook.md`：运行命令汇总
- `swe_qa_bench/docs/swe_qa_bench_env_setup.md`：环境配置与迁移
- `swe_qa_bench/docs/swe_qa_bench_methods_design_doc.md`：方法设计总览
- `swe_qa_bench/docs/swe_qa_bench_scoring_doc.md`：评分流程与配置
- `swe_qa_bench/docs/swe_qa_bench_index_doc.md`：tools 索引构建

---

## 0. 背景与当前已有能力

**背景**
- SWE-QA-Bench 是基于代码仓库问答能力的评测，输入是自然语言问题，输出是答案文本。
- 官方已经提供了 Direct / RAG / Agent 多种方法与评分流程。
- 我们希望复用 mini-swe-agent 的多步 Bash 交互与 code_search 工具，形成新的测评方法。

**目前已经实现（基于 LocBench 经验）**
- bash-only runner（`locbench.py`）
- tools runner（`locbench_tools.py`，支持 `@tool code_search`）
- IR-only runner（`locbench_code_search.py`）
- ToolAgent + ToolRegistry + code_search 工具
- Docker 环境、worktree 机制、相对路径规范化
- 随机抽样固定种子（`--shuffle --shuffle-seed`）
- 完整的运行/环境/设计文档体系

**迁移原则**
- SWE-QA-Bench 测评应与 LocBench 平行建设，代码与配置放在独立目录，不共享输出路径。
- 输出写入 SWE-QA-Bench 原生 `datasets/answers/...`，评分逻辑迁移到 mini-swe-agent 内部实现。

---

## 1. 目录结构（建议，保持整洁）

### 1.1 mini-swe-agent 内部结构（独立于 locbench）

```
mini-swe-agent/
  swe_qa_bench/
    docs/                # 设计与操作文档
    config/              # SWE-QA-Bench 专用配置
    outputs/             # 轨迹与日志
    models/              # code_search 本地模型（可选）
    indexes/             # code_search 索引（可选）
    worktrees/           # 工具侧 worktree（可选）
```

### 1.2 代码结构（与 LocBench 平行）

```
mini-swe-agent/src/minisweagent/
  swe_qa_bench/
    __init__.py
    runners/
      bash_runner.py     # bash-only
      tools_runner.py    # bash + code_search
    utils.py
```

### 1.3 SWE-QA-Bench 原始数据结构（保持不变）

```
SWE-QA-Bench/SWE-QA-Bench/
  datasets/questions/{repo}.jsonl
  datasets/answers/{MODEL}/{METHOD}/{repo}.jsonl
  datasets/scores/{MODEL}/{METHOD}/{repo}.jsonl
  datasets/repos/{repo}/
```

---

## 2. 目标与范围

**目标**
- 使用 mini-swe-agent 自动回答 SWE-QA-Bench 问题
- 输出严格对齐官方格式
- 能被官方评分模块直接读取

**范围**
- 只实现 **答案生成**（answers）
- 评分仍交给 SWE-QA-Bench 的 `score/`
- 不改数据集结构

**已确认决策（来自需求方）**
- 同时实现 bash-only 与 tools 两种方法
- METHOD 命名固定为 `miniswe_bash` / `miniswe_tools`
- 强制使用 Docker（repos 只读挂载，安全与一致性优先）
- code_search 使用独立配置文件（不复用 LocBench）
- 输出严格按 repo 分文件（官方默认）
- tools 方法需要写入 `relative_code_list`（可解释性与检索分析）
- bash-only 方法也必须写入 `relative_code_list`（基于文件读取记录）
- 候选输出采用“冗余写入”：同时包含 `answer` 与 `final_answer`
- `relative_code_list` 限制为最多 50 条（FIFO），超出则追加 `"<<TRUNCATED>>"`
- 输出路径中的模型名必须由 `--output-model-name` 显式指定（避免斜杠路径问题）

---

## 3. 输入与输出对齐

**输入**
- `datasets/questions/{repo}.jsonl`
- 每行字段：`question`, `ground_truth`, `answer`, `relative_code_list`, `score`

**输出（必须对齐）**
- `datasets/answers/{MODEL}/{METHOD}/{repo}.jsonl`
- 每行必须包含：
  ```json
  {"question": "...", "answer": "...", "final_answer": "...", "relative_code_list": ["..."]}
  ```

说明：
- `answer` 与 `final_answer` 值必须一致（冗余写入）。
- `relative_code_list` 为相对路径列表（去重、保序）。
  - tools：来自 code_search 结果 + 实际读过的文件
  - bash-only：来自实际读过的文件
- `relative_code_list` 仅保留前 50 条（FIFO），若被截断，末尾追加 `"<<TRUNCATED>>"`（必须为最后一个元素）。
- 不必写入 `ground_truth` 或 `score`。
- `question` 原文作为评分时唯一键，必须原样保留。

**评分脚本字段差异（已识别）**
- `score/main.py` 读取参考答案字段为 `aggregated_answer`，但 reference 文件只有 `answer`。
- `score/main.py` 读取候选答案字段为 `final_answer`。

处理策略（已确认）：
- 候选答案采用冗余写入（`answer` + `final_answer`）。
- 参考答案不修改原始 reference 文件，评分脚本增加 fallback（见第 10 节）。

---

## 4. 方法设计

### 4.1 方法 A：bash-only（miniswe_bash）

**特点**
- 仅使用 bash（rg/sed/cat）读取代码
- 无语义检索
- 作为 baseline

**方法名（固定）**
- `METHOD=miniswe_bash`

---

### 4.2 方法 B：tools（miniswe_tools）

**特点**
- LLM 可调用 `@tool code_search` 做语义检索
- 适合描述性问题
- 依赖本地模型与索引

**方法名（固定）**
- `METHOD=miniswe_tools`

---

## 5. Runner 设计（建议实现）

### 5.1 入口文件

```
src/minisweagent/swe_qa_bench/runners/bash_runner.py
src/minisweagent/swe_qa_bench/runners/tools_runner.py
```

如需 CLI：
```
src/minisweagent/run/extra/swe_qa_bench.py
src/minisweagent/run/extra/swe_qa_bench_tools.py
```

### 5.2 输入参数（建议）

```
--dataset-root <path>     # SWE-QA-Bench/SWE-QA-Bench/datasets
--repos-root <path>       # SWE-QA-Bench/SWE-QA-Bench/datasets/repos
--repos <csv>             # requests,flask,...
--slice 0:20              # 切片
--shuffle --shuffle-seed  # 随机抽样
--workers N               # 并发
--model <provider/model>  # LLM
--method <string>         # 固定：miniswe_bash / miniswe_tools
    --output-model-name <str> # 输出目录名（必填，用于 answers/{MODEL}/...）
```

### 5.5 单文件运行配置（推荐）

为减少命令行复杂度，提供一个轻量脚本读取 YAML 并调用 runner：

```
PYTHONPATH=src python -m minisweagent.swe_qa_bench.run_from_yaml --config <run.yaml>
```

run.yaml 示例见：
`mini-swe-agent/swe_qa_bench/config/run_example.yaml`

该文件可同时包含：
- 数据集路径、切片、并发
- 输出路径与方法名
- Docker 镜像
- agent/tool 配置文件
- API key（通过 `env:` 字段注入到当前进程）

### 5.3 核心流程

```
for repo in repos:
  read questions/{repo}.jsonl
  for question in questions:
    run agent
    parse final JSON {answer: "..."}
    collect relative_code_list
    append to answers/{MODEL}/{METHOD}/{repo}.jsonl
```

### 5.4 relative_code_list 采集规则（必须实现）

**目标**：为每条回答提供可解释的“检索/阅读依据”。

**tools 模式**
- 维护 `tool_candidates`：所有 `@tool code_search` 返回的文件路径（相对路径）。
- 维护 `files_read`：实际被 agent 阅读过的文件（cat/sed/head/tail/rg 等）。
- `relative_code_list` = Unique(`tool_candidates` + `files_read`)（并集，保序去重）。

**bash-only 模式**
- `relative_code_list` = `files_read`（去重、保序）。

**files_read 提取规则（建议）**
- 明确读取文件的命令：`cat`, `sed -n`, `head`, `tail` 等，直接解析命令参数。
- `rg/grep`：
  - 若命令包含文件参数，则直接记录这些文件；
  - 若命令输出包含 `path:line`，可从输出解析路径（可选）。
- 所有路径统一转换为相对 repo 根目录的路径。
- 限制最多 50 条，超出则追加 `"<<TRUNCATED>>"`（必须为最后一个元素）。

---

## 6. Prompt 设计

**输出格式（统一 JSON）**
```
MINI_SWE_AGENT_FINAL_OUTPUT
{"answer": "..."}
```

实现层要求：输出文件中同时写入 `answer` 与 `final_answer` 两个字段（值一致）。

**bash-only 提示词**
- 单步协议：一个 THOUGHT + 一个 bash block + 一个命令
- 只读，不允许修改文件
- 目标：回答问题而非定位 patch

**tools 提示词**
- 在 system_template 中加入 `@tool code_search` 说明
- 允许语义检索 + bash 精读

---

## 7. 代码仓库访问策略

- SWE-QA-Bench 仓库路径：`datasets/repos/{repo}`
- 数据集没有 base_commit，默认使用 `HEAD`
- Docker 容器中只读挂载 `datasets/repos` 至 `/repos`（强制）
- tools 模式在宿主机对 `datasets/repos/{repo}` 建索引

---

## 8. code_search 索引与配置

必须单独配置文件：
```
mini-swe-agent/swe_qa_bench/config/code_search.yaml
```

建议索引目录：
```
mini-swe-agent/swe_qa_bench/indexes/
```

说明：
- 不与 LocBench 共用索引目录，避免混用
- 可复用 LocBench 的 code_search 实现

---

## 9. 输出与日志

**答案输出**
```
SWE-QA-Bench/SWE-QA-Bench/datasets/answers/{MODEL}/{METHOD}/{repo}.jsonl
```

**并发写入要求**
- 同一 repo 文件可能被多线程同时写入，必须加锁。
- 建议使用 `filelock` 或每 repo 独立线程队列，避免 JSONL 行交错。

**运行日志与轨迹**
```
mini-swe-agent/swe_qa_bench/outputs/{MODEL}/{timestamp}/
```

---

## 10. 评分流程（完全独立）

评分逻辑迁移到 `mini-swe-agent/src/minisweagent/swe_qa_bench/score.py`，
不再依赖 `SWE-QA-Bench/score` 目录。

推荐用单文件 YAML 运行：

```
PYTHONPATH=src python -m minisweagent.swe_qa_bench.score_from_yaml \
  --config /Users/chz/code/locbench/mini-swe-agent/swe_qa_bench/config/score_bash.yaml
```

或直接 CLI：

```
PYTHONPATH=src python -m minisweagent.swe_qa_bench.score \
  --dataset-root /Users/chz/code/locbench/SWE-QA-Bench/SWE-QA-Bench/datasets \
  --candidate-model openai_deepseek-v3.2 \
  --method miniswe_bash \
  --judge-model deepseek-v3.2 \
  --judge-api-base https://api.chatanywhere.tech/v1/chat/completions
```

**重要**
- `candidate_model` 与 `method` 必须与 answers 输出路径一致。
- 评分脚本内部已做 reference 字段 fallback（`aggregated_answer` -> `answer`）。
- 不修改原始 reference 文件。

---

## 11. 实现状态（已完成）

已落地的模块与入口：
- Runner：`src/minisweagent/swe_qa_bench/runners/bash_runner.py` / `tools_runner.py`
- YAML 运行入口：`src/minisweagent/swe_qa_bench/run_from_yaml.py`
- 评分模块：`src/minisweagent/swe_qa_bench/score.py`
- 评分入口：`src/minisweagent/swe_qa_bench/score_from_yaml.py`
- 索引构建：`src/minisweagent/swe_qa_bench/build_index.py`
- 工具配置：`swe_qa_bench/config/code_search.yaml`
- 输出规范：`answer` + `final_answer` 冗余写入，`relative_code_list` 保序去重且最多 50 条

---

## 12. 迁移注意事项

迁移到服务器时，确保：
- `datasets/questions/` 与 `datasets/reference/` 完整
- `datasets/repos/` 已 clone
- mini-swe-agent 代码与 swe_qa_bench 目录齐全
- code_search 模型与索引（如启用 tools）

---

## 13. 关键规则（当前实现）

- `score/main.py` 加入 `aggregated_answer` fallback 到 `answer`（列表化）
- `score/main.py` 加入 `final_answer` fallback 到 `answer`
- `relative_code_list` FIFO 保序去重，最多 50 条，截断后追加 `"<<TRUNCATED>>"`
- bash-only 路径解析覆盖 `rg/grep/sed/cat/head/tail`
- 输出目录模型名由 `--output-model-name` 显式指定
