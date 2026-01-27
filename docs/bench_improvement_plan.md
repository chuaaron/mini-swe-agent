# LocBench & SWE-QA-Bench 改进文档（草案）

日期：2026-01-27

目标：梳理当前 LocBench / SWE-QA-Bench 的运行逻辑与产出，明确改进需求与落地目标，作为后续改造的共同基线。

---

## 0. 实施进度（截至 2026-01-27）

### 已完成
- 强制提交（Soft Limit）：新增 `final_prompt_template`，在步数上限前注入；`LimitsExceeded` 时兜底提取最终输出。
  - LocBench：JSON 兜底 + 文件名正则抽取
  - SWE-QA：直接兜底为最后一次助手输出
- Token 口径拆分：`trace_tokens` + `billed_tokens`，重试与失败尝试计入 `billed_tokens`。
- 输出增强：
  - `answers/*.jsonl` 与 `loc_outputs_*.jsonl` 增加 `exit_status`、`steps`、`trace_tokens`、`billed_tokens`
  - `run_summary.json`（LocBench/SWE-QA 运行）已生成，包含整体统计与实例级字段
- `run_summary.csv` 已实现（与 JSON 同步）
- Judge 元数据：
  - 评分阶段 `scores/.../run_summary.json` 记录 `judge_config`（model + prompt_hash + temperature）
- 环境清理：每题结束统一 teardown（Always Reset）
- LocBench 单题正确性与 Recall@k 指标已计算并写入输出/汇总
- SWE-QA 评分增加 per-question `pass` 与 `score_avg`，并统计 `pass_rate`
- 模型重试默认次数调整为 3（可由 `MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT` 覆盖）

### 部分完成
- SWE-QA 的“正确/错误”只在评分阶段生成；运行阶段的 run_summary 仍为 `correct=null`

### 未完成
- （暂未定义）如需在运行阶段强制计算 SWE-QA 的正确/错误，需要接入评分或外部判定逻辑

---

## 0.1 超参数调整方式（手动）

### A) 配置文件（推荐）
位置（按测评类型）：
- LocBench：`locbench/config/agent_bash.yaml`、`locbench/config/agent_tools.yaml`
- SWE-QA：`swe_qa_bench/config/agent_bash.yaml`、`swe_qa_bench/config/agent_tools.yaml`

常用可调项：
- 步数限制：`agent.step_limit`
- 费用限制：`agent.cost_limit`
- 强制提交提示：`agent.final_prompt_template`
- 工具超时：`environment.timeout`
- 环境清理：`run.env_teardown_command`

### B) 环境变量 / 命令行
常用可调项：
- 模型重试次数（默认 3）：
  - `MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT`
- SWE-QA 评分通过阈值：
  - `SWE_QA_PASS_THRESHOLD` 或评分命令 `--pass-threshold`

### C) 代码内固定值（如需可改为配置）
目前仍为硬编码或需通过 `model_kwargs` 传递的项：
- 各模型的 HTTP 请求 timeout（如 requests/Provider 默认 60s）
- 部分 provider 的 timeout 需要在 `model_kwargs` 里传（视 provider 支持情况）

如需将这些项统一外置为配置，可在后续改造中补充。

---

## 1. 现有运行逻辑（概要）

### 1.1 LocBench
- 入口：`src/minisweagent/run_locbench.py`
- Runner：
  - bash：`src/minisweagent/locbench/runners/bash_runner.py`
  - tools：`src/minisweagent/locbench/runners/tools_runner.py`
  - ir：`src/minisweagent/locbench/runners/ir_runner.py`
- 核心流程：
  1) 读取数据集 JSONL（`paths.dataset_root`）
  2) 构造实例（instance_id、repo、base_commit、problem_statement）
  3) 逐题创建 model + env + agent，循环 step -> 执行命令
  4) 产出轨迹（`*.traj.json`）+ 预测（`loc_outputs_*.jsonl`）
- 输出：
  - 轨迹：`locbench/outputs/<model>/<method>/<timestamp>/<instance_id>.traj.json`
  - 预测：`locbench/results/loc_output/<model>/<method>/loc_outputs_<ts>.jsonl`
- 终止机制：
  - `DefaultAgent.query()` 超过 `step_limit` 或 `cost_limit` 时抛出 `LimitsExceeded`
  - `Submitted` 仅在命令输出首行包含 `MINI_SWE_AGENT_FINAL_OUTPUT` 时触发
- 当前 step 限制：`locbench/config/agent_bash.yaml` / `agent_tools.yaml` 内 `step_limit=120`

### 1.2 SWE-QA-Bench
- 入口：`src/minisweagent/run_swe_qa.py`
- Runner：
  - bash：`src/minisweagent/swe_qa_bench/runners/bash_runner.py`
  - tools：`src/minisweagent/swe_qa_bench/runners/tools_runner.py`
- 核心流程：
  1) 读取 questions/<repo>.jsonl
  2) 逐题创建 model + env + agent
  3) 产出 answers + traj
- 输出：
  - 轨迹：`swe_qa_bench/outputs/<model>/<method>/<timestamp>/<instance_id>.traj.json`
  - 答案：`swe_qa_bench/results/answers/<model>/<method>/<repo>.jsonl`
- 评分：
  - LLM-as-judge：`src/minisweagent/swe_qa_bench/score.py`
  - 输出：`swe_qa_bench/results/scores/<model>/<method>/<repo>.jsonl`（多维评分）

---

## 2. 现有统计/日志现状

### 2.1 运行时进度与退出状态
- `RunBatchProgressManager` 会在 UI 中展示：
  - 总 token（`minisweagent.models.GLOBAL_TOKEN_STATS`）
  - Exit Status 统计表（Submitted / LimitsExceeded / TimeoutExpired 等）
- 同时写出一个 YAML：
  - `.../outputs/.../exit_statuses_<ts>.yaml`
  - 内容：`instances_by_exit_status`

### 2.2 单题统计
- `build_answer_stats()` 会写入：
  - `api_calls`（等价于步数/模型调用次数）
  - `cost_usd`
  - 如果 model 支持 token 统计（`TokenTracker`），还会包含 `prompt_tokens`/`completion_tokens`/`total_tokens`
- `save_traj()` 会在 `*.traj.json` 写入 `api_calls`、`instance_cost`、`exit_status`

### 2.3 目前不足点（总结）
- 运行结束后没有统一的“总览报告”（总 token、总步数、平均步数、错误分布）
- Exit Status 的区分在 UI 和 YAML 中存在，但不够直观、不可直接关联到“正确/错误”
- SWE-QA 的评分为多维分数，缺少“单题正确/错误”与“单题准确率”

---

## 3. 需要解决的问题与目标

### 3.1 超步数时必须输出最终结果
现状：
- `DefaultAgent.query()` 在超出 `step_limit` 时直接抛出 `LimitsExceeded`，导致：
  - 该题 `exit_status=LimitsExceeded`
  - `result` 为空
  - 答案/loc_output 中无有效最终输出

目标：
- 即使达到 step 上限，也必须产生一个“最终答案”
- 需要调整 prompt 或 agent 控制流程，使超限时进行“最后一次强制提交”

待定方向：
- 在临近步数上限时增加“finalize now”指令
- 或在 `LimitsExceeded` 发生时触发一次“强制总结并提交”

---

### 3.2 记录消耗的总 token（运行级）
现状：
- UI 中展示全局 token
- 单题 stats 可能包含 total_tokens，但缺少运行级汇总输出

目标：
- 输出 run-level 统计（总 token、均值、p50/p90）
- 可用于不同模型/配置对比

---

### 3.3 统计每道题的 steps
现状：
- 单题 stats 已含 `api_calls`（等价于 step 计数）
- 但未在 run 汇总中展示，也未明确“steps=api_calls”

目标：
- 在结果文件和 run summary 中明确展示 steps
- 如果需要区分“模型步数 vs 工具动作数”，需新增字段

---

### 3.4 更完整的日志与退出原因
现状：
- UI + `exit_statuses_*.yaml` 记录 exit_status
- 但不直观、不易关联到单题输出与最终结果

目标：
- 输出结构化汇总（CSV/JSON）：
  - instance_id -> exit_status -> steps -> tokens -> 是否成功/是否超限
- 明确区分：
  - 正常退出（Submitted）
  - 超步数（LimitsExceeded）
  - 超时（TimeoutExpired / Timeout）
  - 其他异常（Uncaught *）

---

### 3.5 明确每题正确/错误与单题准确率
现状：
- SWE-QA 评分输出多维分数，不提供“正确/错误”
- LocBench 输出 recall 指标，但未标注“是否正确”

目标：
- 定义“正确”判定逻辑（阈值或规则）
  - SWE-QA：如 correctness >= 8 视为正确
  - LocBench：file_recall > 0 或 entity_recall > 0（需确认）
- 产出 per-question accuracy（0/1）及汇总准确率

---

### 3.6 单步卡住/超慢：双层重试策略（模型 vs 工具）
目标：对“模型推理卡住”与“工具执行卡住”采取不同策略，避免无效重试和单步长时间挂死。

#### 3.6.1 模型生成卡住（Model Inference Stuck）→ 必须重试
现象：Agent 发出 prompt 后，长时间收不到模型 token 返回（API 无响应 / 连接挂起）。

策略：原样自动重试（Auto-Retry）。

逻辑：
- 设定单次 LLM 请求 `request_timeout`（如 60s）
- 超时后捕获异常
- 不改动上下文，重新发起一次完全相同的请求
- 建议 `max_retries = 3`

原因：输入固定，网络或后端抖动导致无响应，重试成功概率高。

#### 3.6.2 工具执行卡住（Tool Execution Stuck）→ 不重试，直接报错
现象：Agent 执行 bash 命令长时间不结束（如死循环 / 扫描超大目录）。

策略：超时终止 + 明确反馈（Timeout & Feedback）。

逻辑：
- 设定工具执行 `timeout`（如 120s）
- 超时后强制 kill 进程
- 不自动重试
- 将错误作为 Observation 反馈给模型：
  - `Error: Execution timed out after 120s`
- 超时后执行清理与环境复位（尤其是可复用环境）：
  - 清理残留进程 / 锁文件
  - 需要时执行最小化回滚（如 git reset --hard 或重建 workdir）
- 环境重置应作为每题结束的统一 teardown（Always Reset），而非仅在超时时触发

原因：死循环类命令重试无意义，应让模型自行改命令策略（缩小范围、分段读取等）。

#### 3.6.3 设计原则（结论）
- 对模型宽容：允许重试
- 对工具严厉：不重试，直接报错反馈
- 既保障稳定性，又防止 Agent 陷入无效循环

---

## 4. 产出物与验收口径（草案）

### 4.1 新增输出（建议）
- `run_summary.json` / `run_summary.csv`
  - total_instances
  - exit_status_counts
  - total_tokens / avg_tokens / p90_tokens
  - total_steps / avg_steps / p90_steps
  - per_instance: instance_id, exit_status, steps, tokens, correctness
  - token 口径拆分：
    - trace_tokens：最终有效轨迹中的 token（分析模型推理/上下文）
    - billed_tokens：包含所有重试/失败尝试的 token（成本核算）
  - Judge 元数据（SWE-QA 必需）：
    - judge_config.model / judge_config.prompt_hash / judge_config.temperature

### 4.2 现有输出需扩展字段
- `answers/*.jsonl`（SWE-QA）：
  - 增加 `exit_status`、`steps`、`trace_tokens`、`billed_tokens`
- `loc_outputs_*.jsonl`（LocBench）：
  - 增加 `exit_status`、`steps`、`trace_tokens`、`billed_tokens`
  - 注：`total_tokens` 仍保留在 `stats`（由 billing stats 提供）

### 4.3 准确率定义需确认
- SWE-QA：使用 correctness 分数阈值
- LocBench：基于 recall 命中规则

---

## 5. 待确认问题（已给出建议答案）

### 5.1 “正确/错误”的具体判定阈值是否统一？
建议：不强求统一，按任务类型定义 “Pass（通过）” 标准。

- SWE-QA-Bench（LLM-as-judge）
  - Pass 标准：score >= 7（或 8，待最终确认）
  - 汇总字段：保留原始 score（avg），新增 pass_rate
- LocBench（定位/检索）
  - Pass 标准：只要 Ground Truth 文件/实体出现在预测列表中，即视为命中
  - 汇总字段：Recall@k（如 Recall@1/3/5），单题正确：Recall@Top > 0

### 5.2 “step” 是否等价于 api_calls？
建议：直接使用 `api_calls` 作为 steps。
理由：每个 step 对应一次 LLM 推理（API Call），便于跨模型横向对比；工具动作数无需单独统计。

### 5.3 总 token 是否包含 prompt + completion？
建议：必须包含总 token（prompt + completion），并分开记录。
字段：`total_tokens`（主要排序）、`prompt_tokens`、`completion_tokens`。
补充：区分统计口径，避免重试/失败被“吞掉”导致成本对账偏差。
- `trace_tokens`：最终有效轨迹中的 token
- `billed_tokens`：包含所有重试与失败尝试的 token

### 5.4 重试策略是否区分错误类型？
建议：必须区分，避免无效重试与资源浪费。

- RateLimit / APITimeout / ServerError（5xx）
  - 指数退避重试（Exponential Backoff）
  - 上限：最多重试 5–10 次，总耗时不超过 2 分钟
- ContextWindowExceeded（上下文超限）
  - 立即停止重试
  - 标记 Exit Status：ContextLimitExceeded
- FormatError（输出格式解析失败）
  - 重试 1–3 次，并附带格式修正提示
  - 超过次数则标记为 Failed

---

## 6. 关键改造方案（补充细化）

### 6.1 超步数强制提交（Soft/Hard Limit）
问题：`LimitsExceeded` 直接抛出导致无最终答案。

建议机制（Soft + Hard）：
- step_limit = N（如 20）
- 当 step == N-1：
  - 系统注入强制提交提示（Task-Aware）：
    - SWE-QA：要求“直接输出最终答案文本”
    - LocBench：要求“严格输出 JSON 格式的文件/实体列表”，并给出格式示例
- 若仍未提交：
  - Runner 尝试从最后一次模型输出兜底提取
  - LocBench 建议增加正则兜底解析（从自然语言中抽取可能的文件路径）
  - 若无法提取，标记 SubmissionFailed

### 6.2 Run Summary（结构化汇总文件）
建议新增 `run_summary.json`（或 JSON/CSV 双输出）：

```json
{
  "meta": {
    "model": "gpt-4o",
    "judge_config": {
      "model": "gpt-4o-2024-05-13",
      "prompt_hash": "a1b2c3d4",
      "temperature": 0.0
    },
    "timestamp": "2026-01-27T...",
    "config": {}
  },
  "stats_overall": {
    "total_instances": 100,
    "success_count": 85,
    "pass_rate": 0.85,
    "avg_trace_tokens": 15000,
    "avg_billed_tokens": 16800,
    "total_cost": 12.50
  },
  "stats_by_exit_status": {
    "Submitted": 90,
    "LimitsExceeded": 5,
    "Timeout": 3,
    "ContextLimitExceeded": 2
  },
  "instances": [
    {
      "instance_id": "swe-bench-dev-1",
      "status": "Submitted",
      "correct": true,
      "steps": 12,
      "trace_tokens": 12400,
      "billed_tokens": 13150,
      "final_answer_path": "..."
    }
  ]
}
```

---

## 7. 下一步（建议）

- 确认 SWE-QA 的 pass 阈值（score >= 7 or 8）
- 确认 LocBench 的 “Recall@Top 命中” 判定逻辑
- 设计 Soft/Hard step_limit 的具体注入点与 fallback 行为
- 落地 run_summary.json 结构 + per-instance 扩展字段
