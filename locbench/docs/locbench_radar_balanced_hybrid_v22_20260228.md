# LocBench Radar v2.2: Balanced Hybrid（2026-02-28）

## 1. 目标
1. 保留 v2.1 的低噪声折叠思路，避免回退到“平铺文字墙”。
2. 修复 v2.1 的信息过折叠问题，给模型恢复最小但有效的语义气味。
3. 软化验证护栏，减少无效拦截和动作死循环。

## 2. 代码改动

### 2.1 file_radar_search：极端折叠 -> 平衡折叠
文件：`src/minisweagent/tools/file_radar_search/tool.py`

主要变化：
1. 自动骨架标题改为 `balanced folded`。
2. 每个候选文件新增 `🧭 Context Glimpse`：
   - 保留 query 命中的 `🎯 Anchors`（最多 2 个）。
   - 额外透传少量非锚点符号（通常 3 个；Top-1 且无锚点时最多 5 个）。
3. `📦 Folded` 统计改为：`total_symbols - anchors - glimpses`。
4. 末尾提示从强硬 `STRICT SOP` 改为 `💡 Next-Step Playbook`，明确：
   - 先看锚点；
   - 不够再 `list_symbols`；
   - 仍不确定再重搜。
5. 符号预览字符串加长度上限，抑制单条过长导致的输出膨胀。
6. `🎯 Anchors` 与 `🧭 Context Glimpse` 改为多行 bullet 形态，提升可扫读性。
7. `🧭 Context Glimpse` 预览优先带 `doc_first_sentence`（来自 `list_symbols`），形成“结构 + 语义”混合线索。

### 2.2 tools_runner：验证路径加入 list_symbols
文件：`src/minisweagent/locbench/runners/tools_runner.py`

主要变化：
1. 新增 `list_symbols` 验证状态记录：
   - `list_symbols_called_count`
   - `list_symbols_inspected_files`
2. 当调用 `@tool list_symbols` 时，标记对应文件为“已观察”并参与验证满足判断。
3. 提交拦截提示改为明确指导语，强调：
   - 这不是 JSON 格式错误；
   - 可通过 bash 读取或 `@tool list_symbols` 满足验证。
4. `file_hint` 提交校验改为接受“bash 已读 + list_symbols 已展开”的并集历史。

### 2.3 list_symbols：Python Docstring 首句抽取（P0）
文件：`src/minisweagent/tools/list_symbols/tool.py`

主要变化：
1. 在 Python AST 分支中，为 class/function/method 符号增加 `doc_first_sentence` 字段。
2. 清洗规则（防脏数据）：
   - 按首个空段落（`\n\n`）截断；
   - 再按第一个句号 `.` 截断首句；
   - 将换行折叠为空格，保证单行输出；
   - 硬截断到 100 字符，超长追加 `...`。
3. 当前为 Python-only（不引入 Tree-sitter 依赖），优先验证定位收益与 token 成本。

## 3. 回归测试
```bash
PYTHONPATH=src pytest -q \
  tests/run/test_list_symbols_tool.py \
  tests/run/test_file_radar_search_auto_skeleton.py \
  tests/run/test_locbench_tools_radar_guard.py
```

当前结果：`12 passed`。

## 4. 32 题回归集运行命令

回归集来源：`/tmp/radar_v21_analysis/regressions_v20_to_v21.csv`

### 4.1 直接运行（tools_radar + search_first）
```bash
cd /Users/chz/code/locbench/mini-swe-agent

FILTER_REGEX="$(
python - <<'PY'
import csv
from pathlib import Path

csv_path = Path("/tmp/radar_v21_analysis/regressions_v20_to_v21.csv")
ids = []
with csv_path.open("r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        iid = (row.get("instance_id") or "").strip()
        if iid:
            ids.append(iid)

if not ids:
    raise SystemExit("No instance_id found in regression csv.")

import re
escaped = [re.escape(x) for x in ids]
print(rf"^({'|'.join(escaped)})$")
PY
)"

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt search_first \
  --method miniswe_tools_radar__v22_balanced_hybrid \
  --filter "$FILTER_REGEX" \
  --workers 4 \
  --skip-missing \
  --redo-existing
```

### 4.2 可选：运行前确认过滤后恰好 32 题
```bash
python - <<'PY'
import csv
import json
from pathlib import Path

csv_path = Path("/tmp/radar_v21_analysis/regressions_v20_to_v21.csv")
dataset_path = Path("data/Loc-Bench_V1_dataset.jsonl")
ids = set()
with csv_path.open("r", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        iid = (row.get("instance_id") or "").strip()
        if iid:
            ids.add(iid)

present = set()
with dataset_path.open("r", encoding="utf-8") as f:
    for line in f:
        if not line.strip():
            continue
        rec = json.loads(line)
        iid = rec.get("instance_id")
        if iid in ids:
            present.add(iid)

print(f"regression_ids={len(ids)}")
print(f"present_in_dataset={len(present)}")
missing = sorted(ids - present)
print(f"missing={len(missing)}")
if missing:
    for x in missing[:10]:
        print("  -", x)
PY
```

建议：实际跑前，用 `--workers 2` 先做一次 smoke，再切回 `--workers 4` 全量跑。
