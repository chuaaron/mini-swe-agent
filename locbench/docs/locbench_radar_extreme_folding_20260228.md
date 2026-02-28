# LocBench Radar v2.1: Extreme Folding（2026-02-28）

## 1. 改造目标
1. 物理消灭自动骨架的信息过载。
2. 将 `file_radar_search` 的骨架输出改为“锚点优先 + 其余折叠”。
3. 在工具返回末尾注入强约束 SOP，固定后续动作链路：
   `file_radar_search -> (sed | list_symbols) -> sed -> submit`。

## 2. 代码变更
文件：`src/minisweagent/tools/file_radar_search/tool.py`

### 2.1 自动骨架从“平铺摘要”改为“极致折叠”
1. 每个候选文件仅输出：
   - `🎯 Anchors`：仅保留 query 匹配符号（最多 2 个）。
   - `📦 Folded`：仅输出折叠计数（`symbols` / `imports`）。
   - `➡ Next`：下一步动作建议（优先 `sed`，否则 `list_symbols`）。
2. 不再输出：
   - `imports:` 明细
   - `📎 Other Context`
   - `truncated:` 明细
3. `auto_skeleton_truncated` 语义调整为主动折叠模式下默认 `False`。

### 2.2 末尾 Hint 重写为三段式强约束 SOP
1. `STEP 1 — Anchor First`：有锚点先 `sed` 读锚点行区间。
2. `STEP 2 — Expand Only When Needed`：锚点不足时显式调用 `list_symbols`。
3. `STEP 3 — Re-query Instead of Wandering`：Top-N 仍无解时重搜，不允许盲提交流程。

## 3. 测试更新
文件：`tests/run/test_file_radar_search_auto_skeleton.py`

1. 断言更新为新协议字段：
   - `🎯 Anchors`
   - `📦 Folded`
   - `➡ Next`
   - `🚨 STRICT SOP (MANDATORY)`
2. 旧断言移除：
   - `🎯 Matched Symbols`
   - `📎 Other Context`
   - `truncated:`
3. 新增验证：`auto["truncated"] is False`（Extreme Folding 模式）。

## 4. 回归命令
```bash
PYTHONPATH=src pytest -q tests/run/test_file_radar_search_auto_skeleton.py tests/run/test_file_radar_search_index_policy.py
```

当前结果：`7 passed`。

