# LocBench 函数级定位 + AST 映射改进方案（给导师版）

日期：2026-01-27  
适用范围：LocBench（bash/tools 模式为主；IR 模式已有 AST/Graph mapper）

---

## 1. 背景与现状（问题来源）

当前 LocBench 的 bash/tools 模式流程是：
1) LLM 直接输出 `found_files / found_entities / found_modules`
2) 系统仅做**字符串解析**与简单归一化
3) 不做 AST 级别的结构校验

这带来两个核心问题：
- **输出格式漂移**：模型常输出带参数的函数签名（如 `foo(x, y)`），导致 `found_modules` 被污染或不可复用。
- **一致性不足**：同一函数名在不同文件中可能重复，缺少 AST 标准化与消歧。

结论：当前“LLM → 结果 JSON”路径缺乏结构化校验，稳定性不足。

---

## 2. 改进目标

**目标**：让模型只做“函数级定位”，然后由系统用 AST **统一映射到结构化实体**，输出规范的 `found_files / found_entities / found_modules`。

换句话说：
> **LLM 负责“找函数名”，系统负责“映射到代码结构”**。

---

## 3. 方案核心（函数级定位 → AST 映射）

### 3.1 新流程（推荐）

```
LLM 输出函数名/方法名  →  AST 解析与映射  →  标准化输出
```

### 3.2 输出约束（模型侧）
要求模型输出函数级定位，并**提供文件提示（软约束）**：

```json
{
  "functions": [
    {"function": "populate_face_centroids", "file_hint": "coordinates.py"},
    {"function": "construct_face_centroids", "file_hint": "grid/coordinates"},
    {"function": "populate_face_centerpoints", "file_hint": "coordinates.py"}
  ]
}
```

说明：
- `file_hint` 不要求完整路径，可为文件名或部分路径片段
- 仅当函数名在全局唯一时，才允许缺失 `file_hint`

---

## 4. AST 映射设计（系统侧）

### 4.1 AST 索引构建（Python）
对 repo 中 Python 文件解析 AST，建立映射表：

- **function_name → file:qualname**
- **class.method → file:Class.method**
- 可选：建立反向索引（file → functions）

### 4.2 映射规则（含消歧）
1) 使用 `function + file_hint` 做主匹配  
2) file_hint 匹配策略：
   - 路径包含关系优先（hint 是路径子串）
   - 其次使用 Levenshtein 距离（选最小者）
3) 仅在函数名全局唯一时允许忽略 `file_hint`  
4) 若仍多重匹配：
   - 输出 topK（可配置）
   - 或标记为 ambiguous（供后续分析）

### 4.3 结果生成
根据 AST 映射结果生成：

- `found_entities`：`file.py:Class.method`  
- `found_files`：去重后的 file list  
- `found_modules`：按 module 规则生成

---

## 5. 为什么这样做（收益）

### 5.1 稳定性
LLM 的输出只需满足“函数名正确”，系统可确保结构化格式一致。

### 5.2 解释性
最终输出是 AST 解析出的实体，不受模型格式漂移影响。

### 5.3 可扩展
后续可接入：
- 多语言 AST（Python 外）
- Graph Mapper
- 静态分析器

---

## 6. 已知限制

- AST 映射目前仅对 **Python** 准确（非 Python 需规则或扩展解析器）
- 仅函数名时可能多重匹配，需要策略（topK / file hint）

---

## 7. 实施步骤（工程计划）

1) **Prompt 改写**  
   - LocBench 提示词改为只输出 `functions` 列表  
2) **AST 索引构建**  
   - 增加 `function → file:qualname` 的映射  
3) **映射器实现**  
   - 接收 LLM 输出 → AST 映射 → 生成标准输出  
4) **回写输出**  
   - `found_files / found_entities / found_modules`  
5) **评估**  
   - 对比现有流程的 Recall@k 与正确率变化  

---

## 8. 预期效果

- LocBench 输出结构化程度提升  
- `found_modules` 不再被函数签名污染  
- 单题准确率稳定性提升  
- 结果更容易做可重复评估与比较

---

## 9. 下一步需确认事项

1) AST 映射是否仅支持 Python（优先）  
2) 多重匹配处理策略（全部 / topK / file_hint 必填）  
3) 是否需要保留原模型输出作为 debug 字段

---

如果确认该方案，将进入开发实现阶段。
