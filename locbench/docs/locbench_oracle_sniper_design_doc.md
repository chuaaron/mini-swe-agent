# LocBench Oracle-Sniper 设计文档（GT 文件注入实验）

本文档定义一个“极简工程”实验：  
**直接把 Ground Truth 文件路径告诉 Agent，只评估它在文件内定位函数的能力**。

---

## 1. 实验目标

你当前的 `tools_radar` 结果混合了两类能力：
1. 找文件（retrieval）
2. 文件内找函数（in-file localization）

Oracle-Sniper 的目的是把第 1 类能力“封顶”：
- 文件由系统直接提供（Oracle）
- Agent 只做文件内狙击（Sniper）

这样可以回答一个关键问题：  
**当前误差主要来自“找错文件”，还是“找到文件后仍找不准函数”。**

---

## 2. 结论先行：这个实验合理吗？

合理，但要明确它的性质：
- 它是 **ablation / upper-bound 实验**，不是标准 benchmark 主赛道结果。
- 不能与普通 `tools` / `tools_radar` 直接做“谁更强”的结论。
- 它回答的是“在文件已知条件下，Agent 的函数定位上限”。

论文里建议命名为：
- `Oracle-File Sniper`
- 或 `GT-File Assisted Localization`

---

## 3. 极简落地策略（推荐）

不新起复杂 runner，直接复用现有 `tools_radar` 主体能力：
- 复用 worktree、docker、日志、run_summary、并发执行框架
- 复用“提交前必须验证阅读”的状态机
- 关闭 `file_radar_search` 工具调用能力

建议作为一个新的 prompt 变体：
- `tools_prompt = oracle_sniper`
- 方法名：`miniswe_tools_oracle_sniper`

---

## 4. 数据注入方案（Oracle 文件来源）

### 4.1 GT 文件抽取规则

对每条样本，构建 `oracle_files`（有序去重）：

1. 主路径：从 `patch` 提取
   - 规则：匹配 `diff --git a/<path> b/<path>`
   - 使用 `b/<path>` 作为目标路径（兼容重命名场景）
2. 兜底路径：从 `edit_functions` 提取文件前缀
   - 规则：`path:function` 取 `path`

### 4.1.1 测试文件过滤（强制）

必须过滤测试文件，避免模型“借答案写测试”偏离定位目标。

建议规则（V1）：
- 丢弃 `tests/` 目录文件
- 丢弃文件名包含 `test_` 或以 `_test.py` 结尾的文件

伪代码示例：
```python
def is_test_file(path: str) -> bool:
    p = path.lower()
    name = p.split("/")[-1]
    return (
        p.startswith("tests/")
        or "/tests/" in p
        or name.startswith("test_")
        or name.endswith("_test.py")
    )
```

执行顺序建议：
1. 先从 `patch` + `edit_functions` 提取候选文件
2. 再应用 `is_test_file` 过滤
3. 若过滤后为空，再回退到未过滤集合（记录标志位，便于分析）

建议保存两个字段：
- `oracle_files`: 全量 GT 文件列表
- `oracle_primary_file`: 第一优先文件（`oracle_files[0]`）

### 4.2 注入到实例模板

在每题 prompt 中显式注入：
- `oracle_primary_file`
- `oracle_files`（最多前 5 个，避免提示词过长）

示例提示语：
> The bug is officially confirmed to be in: `{{oracle_primary_file}}`.  
> You must inspect this file with bash and localize the exact function/class to edit.

---

## 5. Agent 行为约束

## 5.1 关闭雷达（禁用工具）

Oracle-Sniper 模式下不允许 `@tool`：
- 若模型输出 `@tool ...`，直接 `FormatError`，返回明确指导：
  - “oracle_sniper mode forbids tools; use bash on the provided file.”

此外要做“物理级禁用”，不仅靠提示词：
- Prompt 层：删除所有工具说明
- Runtime 层：
  - `ToolRegistry` 不注册任何工具（或注册空集）
  - `parse_action`/`execute_tool` 对 `@tool` 直接拒绝
- 若底层模型 SDK 支持原生 `tools` 参数（OpenAI/Anthropic tool-calling）：
  - 显式传 `tools=[]`，彻底切断工具幻觉

## 5.2 强制狙击（阅读门禁）

启动时直接把 `candidate_files = oracle_files`，并设置：
- `needs_verification = true`

提交前必须满足：
1. 至少读取过一个 `oracle_files` 中文件（`rg/cat/sed/nl/head/tail`）
2. 最终 JSON 里的 `file_hint` 必须出现在历史读取记录中（已在 P0 机制中定义）

## 5.3 拦截反馈质量

沿用你现有明确拦截文案风格：
- 必须说明“不是 JSON 格式问题”
- 必须给出下一步唯一可执行建议
- 连续拦截进入 strict recovery（避免死循环）

---

## 6. 配置与代码改动清单（最小集）

## 6.1 配置文件

新增：
- `locbench/config/agent_tools_radar_oracle_sniper.yaml`

核心差异：
- system prompt 里删除/禁用工具说明
- instance prompt 增加 `oracle_primary_file` / `oracle_files`
- 强化“必须先读该文件再提交”

## 6.2 CLI 与模式路由

`src/minisweagent/run_locbench.py`：
- 扩展 `tools_prompt` 允许值：`oracle_sniper`
- `mode=tools_radar` + `tools_prompt=oracle_sniper` 时加载对应 agent config

## 6.3 Runner 注入与约束

`src/minisweagent/locbench/runners/tools_runner.py`：
- 在 `_build_instances` 或 `_process_instance` 里注入 `oracle_files`
- 初始化 `ProgressTrackingAgent` 时设置候选文件来源为 `oracle_files`
- Oracle 模式下禁用 `execute_tool`（或 parse 阶段拒绝 `@tool`）

---

## 7. 评测指标设计

除了标准 `pass_rate`，建议新增：

1. `oracle_file_provided_rate`
   - 成功注入 Oracle 文件的样本比例
2. `oracle_verification_compliance_rate`
   - 注入后完成 bash 阅读验证比例
3. `entity_hit_rate_given_oracle_file`
   - Oracle 文件条件下实体命中率（核心指标）
4. `oracle_blocked_submission_count`
   - 因未阅读 Oracle 文件被拦截次数
5. `steps_to_success_in_oracle`
   - 仅在成功样本上统计步数（建议同时报告 `mean/p50/p90`）
   - 用于衡量“已知正确文件时的文件内理解效率”

解释口径建议：
- 若 `entity_hit_rate_given_oracle_file` 高，但 `steps_to_success_in_oracle` 仍高：
  - 说明模型最终能找对，但在单文件结构理解上仍低效
- 这能为后续引入 AST/结构化阅读工具提供直接动机

关键对比：
- `tools_radar` vs `oracle_sniper`
- 若 `oracle_sniper` 明显高于 `tools_radar`，瓶颈主要在检索阶段
- 若二者接近，瓶颈主要在文件内推理阶段

---

## 8. 实验分组建议

建议至少两组：

1. `Oracle-Primary`
   - 仅注入 `oracle_primary_file`
   - 最贴近“单文件先验”

2. `Oracle-AllFiles`
   - 注入全部 `oracle_files`
   - 更宽松上限，观察多文件任务收益

可选第三组：
3. `Oracle-Primary + NoHintText`
   - 屏蔽 `hints_text`，减少额外先验污染

---

## 9. 命令示例（目标形态）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt oracle_sniper \
  --method miniswe_tools_oracle_sniper \
  --slice 0:50 \
  --shuffle --shuffle-seed 123 \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

---

## 10. 风险与防偏差说明

1. **风险：结论被误读为主赛道能力**
   - 对策：结果表格单独分区，明确标注 Oracle 条件

2. **风险：多文件 patch 被简化为单文件导致低估**
   - 对策：同时报告 `Oracle-Primary` 与 `Oracle-AllFiles`

3. **风险：Agent 不按要求仍尝试工具**
   - 对策：运行时硬禁工具，不只靠 prompt

---

## 11. 里程碑（1-2 天可完成）

1. M1：实现 `oracle_files` 抽取与注入  
2. M2：新增 `agent_tools_radar_oracle_sniper.yaml`  
3. M3：Runner 中禁用工具 + 启动即进入验证态  
4. M4：跑 50 条 smoke，对比 `tools_radar`  
5. M5：再跑 500 条主实验并出 paired 分析

---

## 12. 一句话总结

Oracle-Sniper 是一个低成本、高信息量的诊断实验：  
**用“已知正确文件”把检索误差剥离掉，直接测你系统的文件内定位上限。**
