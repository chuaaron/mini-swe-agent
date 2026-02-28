# LocBench 变更留痕（2026-02-27）

本文档记录 `tools_radar` 新增的 `Top-N 自动骨架` 实现，供复现实验与论文附录使用。

---

## 1. 改动目标

1. 解决 Radar 返回“只有路径与分数、缺少结构气味”的问题。
2. 提高骨架信息使用率：从“模型主动调 list_symbols”改为“Radar 返回内置骨架”。
3. 严格控制 token：对自动骨架做字符预算和截断标记。

---

## 2. 代码改动

### 2.1 file_radar_search 增加 Top-N 自动骨架

改动文件：
1. `src/minisweagent/tools/file_radar_search/tool.py`

新增配置字段（`FileRadarSearchConfig`）：
1. `auto_skeleton_enabled`
2. `auto_skeleton_topn`
3. `auto_skeleton_budget_chars`
4. `auto_skeleton_max_imports_per_file`
5. `auto_skeleton_max_symbols_per_file`
6. `auto_skeleton_include_signature`
7. `auto_skeleton_query_aware`

行为变化：
1. `file_radar_search` 返回仍保留候选文件列表（path/score/evidence）。
2. 若开启自动骨架，则额外附加：
   - `Auto skeleton (Top-N, compact, no code body)` 段
   - 每个文件的 `imports` / `symbols` / `query_hits` 预览
   - 预算截断时的 `truncated` 标记
3. 结构化返回新增字段：
   - `auto_skeleton_enabled`
   - `auto_skeleton_topn`
   - `auto_skeleton_budget_chars`
   - `auto_skeleton_truncated`
   - `auto_skeleton_files`

实现细节：
1. 自动骨架复用 `ListSymbolsTool` 解析文件结构，不返回代码正文。
2. 预算分配默认偏向 Top-3：`50% / 30% / 20%`。
3. 在 `query_aware=true` 时，符号按 query token overlap 排序后压缩输出，并拆分为：
   - `🎯 Matched Symbols`
   - `📎 Other Context`
4. 返回末尾追加强提示：
   - `💡 SYSTEM HINTS FOR NEXT STEP`
   - 指导 `sed -n 'Lstart,Lend p' <path>` 精读与重搜策略

---

### 2.2 默认配置更新

改动文件：
1. `locbench/config/file_radar_search.yaml`

新增默认参数：
1. `auto_skeleton_enabled: true`
2. `auto_skeleton_topn: 3`
3. `auto_skeleton_budget_chars: 4000`
4. `auto_skeleton_max_imports_per_file: 0`
5. `auto_skeleton_max_symbols_per_file: 14`
6. `auto_skeleton_include_signature: false`
7. `auto_skeleton_query_aware: true`

---

### 2.3 文档更新

改动文件：
1. `locbench/docs/locbench_tools_radar_runbook.md`
2. `locbench/docs/locbench_radar_auto_skeleton_changes_20260227.md`（本文件）

---

## 3. 新增测试

新增文件：
1. `tests/run/test_file_radar_search_auto_skeleton.py`

覆盖点：
1. 自动骨架 Top-3 输出存在且不泄露正文。
2. 小预算下自动触发截断标记。

---

## 4. 运行建议

1. 若追求更强可解释性：保持 `budget_chars=4000`。
2. 若关注 token 成本：降到 `2500~3000`。
3. 对比实验建议固定：
   - 同一模型
   - 同一 slice / seed / workers
   - 仅切换 auto skeleton 开关

---

## 5. A/B 建议指令（示例）

先准备两份 tool config（仅差一个参数）：
1. `file_radar_search_auto.yaml`：`auto_skeleton_enabled: true`
2. `file_radar_search_no_auto.yaml`：`auto_skeleton_enabled: false`

关闭自动骨架（对照）：
```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --tool-config locbench/config/file_radar_search_no_auto.yaml \
  --method miniswe_tools_radar__no_auto_skeleton
```

开启自动骨架（实验组）：
```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --tool-config locbench/config/file_radar_search_auto.yaml \
  --method miniswe_tools_radar__auto_skeleton_top3
```

---

## 6. 可直接运行的 A/B 命令（560 题示例）

```bash
cd /Users/chz/code/locbench/mini-swe-agent

cp locbench/config/file_radar_search.yaml locbench/config/file_radar_search_auto.yaml
cp locbench/config/file_radar_search.yaml locbench/config/file_radar_search_no_auto.yaml

python - <<'PY'
from pathlib import Path
p = Path("locbench/config/file_radar_search_no_auto.yaml")
s = p.read_text(encoding="utf-8")
s = s.replace("auto_skeleton_enabled: true", "auto_skeleton_enabled: false")
p.write_text(s, encoding="utf-8")
print("updated", p)
PY

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --tool-config locbench/config/file_radar_search_no_auto.yaml \
  --method miniswe_tools_radar__neutral__no_auto_skeleton \
  --slice 0:560 \
  --shuffle --shuffle-seed 123 \
  --workers 4 \
  --skip-missing \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --tool-config locbench/config/file_radar_search_auto.yaml \
  --method miniswe_tools_radar__neutral__auto_skeleton_top3 \
  --slice 0:560 \
  --shuffle --shuffle-seed 123 \
  --workers 4 \
  --skip-missing \
  --redo-existing
```
