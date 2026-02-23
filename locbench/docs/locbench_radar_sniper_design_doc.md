# LocBench Radar-Sniper 解耦式检索设计文档

本文档定义一个全新的代码定位工具方案：**Radar-Sniper Decoupling**。  
目标是在保留语义检索召回能力的同时，抑制上下文污染与“工具权威偏见”。

---

## 1. 背景与痛点

当前 `@tool code_search` 会返回带 snippet 的代码块。对 Agent 来说，这会产生两个问题：

1. **Context Pollution（上下文污染）**  
   过多代码片段挤占上下文预算，降低后续推理效率。
2. **Authority Bias（权威偏见）**  
   模型看到“像答案”的片段后，容易跳过真实阅读流程，直接盲猜提交。

---

## 2. 核心思想：雷达与狙击枪分离

### 2.1 雷达（Radar）阶段
- 工具只做“语义召回 + 候选排序”。
- **绝不返回任何代码内容**（不返回 snippet、不返回具体行内容）。
- 输出仅包含候选文件列表与分数。

### 2.2 狙击（Sniper）阶段
- Agent 必须使用 Bash（`rg` / `sed` / `cat` / `nl`）在候选文件中做字面验证。
- 最终定位结论必须来自“二次验证”而非工具直接输出。

---

## 3. 设计目标与非目标

### 3.1 目标
- 新工具与旧 `code_search` **并行独立**，不破坏现有链路。
- 新工具定位粒度为 **file-level**。
- 强制形成“先召回，再验证”的两段式流程。
- 可在 LocBench tools 模式中稳定对比实验。

### 3.2 非目标
- 不替代现有 `code_search`（两者长期并存）。
- 不在 V1 解决 entity-level 直接映射。
- 不在 V1 引入 reranker 或复杂多跳检索。

---

## 4. 新工具定义（独立于原 code_search）

## 4.1 工具命名
- 建议工具名：`file_radar_search`
- 明确语义：该工具只负责“文件嫌疑名单”，不是代码片段搜索器。

## 4.2 调用格式
```bash
@tool file_radar_search --query "<problem_statement>" [--topk-files N] [--topk-blocks M] [--filters F]
```

参数建议：
- `query`：必填，自然语言问题描述
- `topk-files`：返回文件数（默认 15，范围 1-100）
- `topk-blocks`：内部召回块数（默认 80，范围 10-500）
- `filters`：可选，如 `lang:python path:src/`

## 4.3 输出契约（强约束）
工具返回中禁止出现以下字段：
- `snippet`
- `line_content`
- 任意代码原文

允许字段：
- `path`
- `score`
- `evidence_count`（命中块数）
- `language`（可选）

### output（给模型看的文本）示例
```
Found 5 candidate files for "auth token refresh":
1. src/auth/session.py (score: 0.91, evidence: 4)
2. src/auth/jwt.py (score: 0.88, evidence: 3)
3. tests/auth/test_refresh.py (score: 0.82, evidence: 2)
...
```

### data（结构化）示例
```json
{
  "query": "auth token refresh",
  "topk_files": 5,
  "topk_blocks": 80,
  "returned": 5,
  "results": [
    {"path": "src/auth/session.py", "score": 0.91, "evidence_count": 4, "language": "python"}
  ],
  "metadata": {
    "index_version": "radar_v1",
    "embedding_model": "...",
    "aggregation": "hybrid"
  }
}
```

---

## 5. 与现有 code_search 的隔离策略

必须做到“新旧并行，不互相污染”：

1. **独立包路径**
   - 新增：`src/minisweagent/tools/file_radar_search/`
   - 保留：`src/minisweagent/tools/code_search/`
2. **独立配置**
   - 新增：`locbench/config/file_radar_search.yaml`
3. **独立索引命名空间**
   - 建议 `index_root/.../radar/...`
   - 避免和旧工具共享同一目录结构，防止元数据混用
4. **独立 tool name**
   - `file_radar_search` 与 `code_search` 不可重名
5. **独立 prompt 变体**
   - 新增 Radar 专用 agent 配置（见第 8 节）

---

## 6. 检索与排序算法（V1）

## 6.1 检索流程
1. 按 `query` 做向量检索，召回 `topk_blocks` chunk。
2. 将 chunk 分数聚合到文件级。
3. 输出 `topk_files` 文件名单。

## 6.2 文件分数聚合（建议默认：hybrid）
- 原始 `sum` 容易偏置大文件，建议混合策略：
  - `file_score = 0.7 * max(chunk_scores) + 0.3 * mean(top3_chunk_scores)`
- 次级排序键：`evidence_count`（命中块数）。

可配置：
- `aggregation: hybrid | max | sum`

---

## 7. “强制 Bash 验证”机制

仅靠 prompt 不够，需要运行时约束。

## 7.1 约束目标
在一次 `file_radar_search` 后，Agent 必须至少执行一次针对候选文件的 Bash 读取，再允许提交最终答案。

## 7.2 状态机（建议）
- 状态 `needs_verification = false`
- 调用 `file_radar_search` 后：
  - 记录候选文件集合 `candidate_files`
  - 置 `needs_verification = true`
- 每次 Bash action：
  - 若命令包含候选路径且属于读取类命令（`rg/sed/cat/nl/head/tail`），记为已验证
  - 至少 1 个候选被验证后，`needs_verification = false`
- 若模型尝试 `Submitted` 且 `needs_verification = true`：
  - 拒绝提交，注入格式化提醒，要求先验证文件

## 7.3 失败回退
- 如果工具返回空候选，允许 Agent 直接回到纯 Bash 流程。
- 若候选过多，可先限制到 top-N 后再验证。

---

## 8. Prompt 与运行模式设计

沿用现有三种策略，但针对 Radar 重写规则：

1. `agent_tools_radar_neutral.yaml`
   - 可用 Radar，也可先 Bash
2. `agent_tools_radar_search_first.yaml`
   - 必须先调用 `file_radar_search`
3. `agent_tools_radar_search_fallback.yaml`
   - 先 Bash，失败后再 Radar

核心规则统一增加：
- Radar 结果不能直接作为最终依据
- 最终输出前必须有“候选文件验证动作”

---

## 9. LocBench 集成方案

建议新增独立模式，避免污染原 `tools` 实验：

- 新模式：`--mode tools_radar`
- 对应 method：`miniswe_tools_radar`（可叠加 `__search_first` / `__search_fallback`）

Runner 复用现有 `ToolsRunner` 主体，但替换：
- tool 注册为 `file_radar_search`
- 启用验证状态机
- run_summary 增加 Radar 专用统计

---

## 10. 评测与观测指标

除了现有 LocBench 指标，新增过程指标：

1. `verification_compliance_rate`  
   有 Radar 调用且完成 Bash 验证的比例。
2. `blocked_submission_count`  
   因未验证被系统拦截的次数。
3. `avg_tool_output_chars`  
   工具输出长度（用于量化上下文节省）。
4. `premature_submit_rate`  
   使用 Radar 后、未充分探索就提交的比例（通过规则近似）。

A/B 对比维度：
- baseline: `tools + code_search`
- new: `tools_radar + file_radar_search`

---

## 11. 文件级改造清单（实施阶段）

仅定义目标，不在本文实现代码。

计划新增：
- `src/minisweagent/tools/file_radar_search/__init__.py`
- `src/minisweagent/tools/file_radar_search/tool.py`
- `src/minisweagent/tools/file_radar_search/chunkers/...`（可复用）
- `locbench/config/file_radar_search.yaml`
- `locbench/config/agent_tools_radar_neutral.yaml`
- `locbench/config/agent_tools_radar_search_first.yaml`
- `locbench/config/agent_tools_radar_search_fallback.yaml`

计划改动：
- `src/minisweagent/run_locbench.py`（新增 `tools_radar` 模式路由）
- `src/minisweagent/locbench/runners/tools_runner.py`（注册新工具 + 验证状态机）
- `src/minisweagent/run/extra/utils/run_summary.py`（扩展 radar 指标字段）

---

## 12. 风险与对策

1. **风险：召回不到关键文件**
   - 对策：允许 `topk_blocks/topk_files` 提升；保留 Bash fallback。
2. **风险：验证约束过严，拖慢收敛**
   - 对策：仅要求最小验证（>=1 个候选读取）即可提交。
3. **风险：命令解析误判**
   - 对策：先做保守命中规则（路径子串 + 读取命令白名单），逐步收紧。

---

## 13. 里程碑建议

1. M1：完成工具骨架与只返回文件列表的输出契约。  
2. M2：接入 `tools_radar` 模式并跑通 10 条样本。  
3. M3：补齐验证状态机与过程指标。  
4. M4：做 `tools` vs `tools_radar` 小规模对比（同 seed / 同 slice）。  
5. M5：全量评测并决定是否推广为默认 tools 方案。

---

## 14. 结论

`Radar-Sniper` 的本质不是“换一个检索器”，而是把“召回”和“证据阅读”拆成两个受约束阶段。  
这能在不丢失语义召回能力的前提下，显著减少上下文污染与盲信提交，适合做为 LocBench tools 体系的下一条独立实验线。
