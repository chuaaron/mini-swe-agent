# LocBench Agent 索引使用问题总结（截至 2026-02-25）

本文档总结当前 Agent 在 `code_search` / `file_radar_search` 下的索引复用问题，基于以下两次最新实验：

1. `locbench/outputs/Qwen3-Coder-30B-A3B-Instruct/miniswe_tools_radar__search_first/20260225_225408`
2. `locbench/outputs/Qwen3-Coder-30B-A3B-Instruct/miniswe_tools__search_first/20260225_231447`

## 1. 当前索引策略（已生效）

1. `code_search`：
   - 配置：`locbench/config/code_search.yaml`
   - `index_build_policy: read_only`（只复用，不重建）
2. `file_radar_search`：
   - 配置：`locbench/config/file_radar_search.yaml`
   - `index_validation_mode: static`
   - `index_build_policy: read_only`（只复用，不重建）

这意味着：索引缺失或不兼容时，工具会直接失败，而不是在线重建。

## 2. 关键现象（并排统计）

## 2.1 Tools-Radar（`tool_backend=file_radar_search`）

1. 总样本：12
2. `pass_rate`: `0.3333`（correct=4）
3. 模型发起 `@tool file_radar_search`：11 次
4. 成功拿到 `<tool_result>`：1 次
5. 工具失败：10 次，失败原因全部为 `index_missing`
6. 动作格式报错：
   - `found 0 actions`: 218 次
   - `found 2 actions`: 13 次

## 2.2 Tools（`tool_backend=code_search`）

1. 总样本：12
2. `pass_rate`: `0.5833`（correct=7）
3. 模型发起 `@tool code_search`：10 次
4. 成功拿到 `<tool_result>`：0 次
5. 工具失败：10 次，失败原因全部为 `meta_missing`
6. 动作格式报错：
   - `found 0 actions`: 115 次
   - `found 2 actions`: 0 次

## 3. 根因拆解

## 3.1 P0：Radar 索引覆盖不足（`index_missing`）

`file_radar_search` 在 `read_only` 下要求目标目录存在可复用索引；本次多数样本直接报：

1. `reason=index_missing`
2. 无法进入 “Radar -> candidate files -> Sniper” 流程

结论：这不是模型不会用工具，而是多数样本没有命中可复用 Radar 索引。

## 3.2 P0：Code Search 旧索引缺少 `meta.json`（`meta_missing`）

`code_search` 当前兼容性检查先读 `meta.json`，若缺失则返回 `meta_missing` 并在 `read_only` 下失败。

本次 10 次 `code_search` 失败全部是 `meta_missing`，说明：

1. 存在旧格式索引（常见是只有 `embeddings.pt` / `metadata.jsonl`）
2. 但不满足新版复用的 `meta.json` 兼容校验要求

## 3.3 P1：运行统计存在“失败调用盲区”

`run_summary.csv` 中的 `radar_called` / `code_search_called` 目前更接近“成功调用次数”，不能完整反映“尝试调用次数”。

影响：

1. 从 summary 看像是“模型没调用工具”
2. 但轨迹里实际大量调用了工具，只是都失败了（索引问题）

诊断建议：工具有效性判断优先看 `trajectories/*.traj.json` 的真实交互，再看 summary 字段。

## 3.4 P1：动作协议循环放大失败成本

两组实验都出现大量：

1. `Please always provide EXACTLY ONE action in triple backticks, found 0 actions.`

这会把很多样本拖到 `LimitsExceeded`，放大 token/step 消耗，掩盖工具本身效果。

## 4. 结论（是否为索引问题）

结论：**是，当前主问题就是索引可复用性问题**，且分为两类：

1. Radar：`index_missing`（索引覆盖不足）
2. Code Search：`meta_missing`（旧索引缺元数据，不满足 read-only 兼容要求）

动作协议问题是次级问题，会恶化结果，但不是这两组工具失效的主因。

## 5. 修复优先级建议

## 5.1 P0（先做）

1. 做“跑前索引体检”（preflight），按实例检查：
   - Radar：目标 `radar_v1/...` 路径是否存在
   - Code Search：是否存在 `meta.json` 且兼容
2. 对 `meta_missing` 的旧索引做迁移：
   - 补 `meta.json`，或
   - 一次性按新格式重建索引
3. 保持 `read_only` 不变（保证实验可重复性），但在开跑前直接 fail-fast 报覆盖率。

## 5.2 P1（随后）

1. 在 summary 中新增“工具尝试次数”（attempted calls）字段，避免误判“模型没调用”
2. 降低动作协议循环（统一 final submit 模板，减少 `found 0 actions`）

## 6. 验收标准（建议）

完成修复后，建议至少满足：

1. Radar 组：`tool_result / tool_calls >= 0.9`
2. Tools 组：`meta_missing == 0`
3. 两组：`found 0 actions` 次数显著下降
4. `LimitsExceeded` 比例下降，`Submitted` 比例上升

