# Oracle-Sniper 测试文件（操作版）

这个文件是放在 `locbench/docs` 的测试操作清单，用于你每次改完代码后的快速回归。

## 1. 代码级单测（必跑）

```bash
cd /Users/chz/code/locbench/mini-swe-agent
PYTHONPATH=src pytest -q \
  tests/run/test_locbench_tools_radar_guard.py \
  tests/run/test_locbench_oracle_sniper.py
```

期望：
1. 全部通过。
2. 允许出现 pytest 配置 warning（不影响功能）。

## 2. Oracle 模式冒烟（1 条）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt oracle_sniper \
  --method miniswe_tools_oracle_sniper__smoke \
  --slice 0:1 \
  --workers 1 \
  --skip-missing \
  --redo-existing
```

## 3. Radar 模式冒烟（1 条）

```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --method miniswe_tools_radar__smoke \
  --slice 0:1 \
  --workers 1 \
  --skip-missing \
  --redo-existing
```

## 4. 运行后检查项

对每个 run 的 `run_summary.json` 检查：
1. `meta.oracle_sniper_mode` 是否符合预期。
2. `stats_overall.oracle_file_provided_rate` 是否存在（Oracle 模式）。
3. `stats_overall.oracle_verification_compliance_rate` 是否存在（Oracle 模式）。
4. `stats_overall.verification_compliance_rate` 是否存在（Radar 模式）。

## 5. 轨迹健康检查（Oracle）

抽查轨迹是否出现以下特征：
1. 若模型尝试 `@tool`，应出现明确拦截提示（Tools Disabled）。
2. 提交前应有 bash 读取行为（`rg/cat/sed/nl/head/tail`）。
3. 若未读取直接提交，应出现 Verification Required 拦截。

## 6. 快速脚本：打印 Oracle 核心指标

```bash
python - <<'PY'
import json
from pathlib import Path

summary = Path("locbench/outputs/<model>/<method>/<timestamp>/run_summary.json")
data = json.loads(summary.read_text())
s = data.get("stats_overall", {})
for k in [
    "total_instances",
    "pass_rate",
    "oracle_file_provided_rate",
    "oracle_verification_compliance_rate",
    "entity_hit_rate_given_oracle_file",
    "steps_to_success_in_oracle_mean",
]:
    print(f"{k}: {s.get(k)}")
PY
```

## 7. 判定标准（建议）

1. 功能正确：
   - 单测通过。
   - Oracle 模式不注册工具且可正常提交。
2. 行为正确：
   - 提交前验证门禁生效。
   - 拦截反馈是指导性报错，不是模糊格式报错。
3. 指标可用：
   - `run_summary.json` 中 Oracle 指标完整可读。
