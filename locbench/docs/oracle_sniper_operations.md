# Oracle-Sniper 操作说明（入口版）

## 1. 模式定义

`oracle_sniper` 是 `tools_radar` 的特殊提示词分支：
1. 禁用所有 `@tool` 调用（运行时硬禁用）。
2. 系统注入 GT 文件作为候选文件。
3. 强制先用 bash 读取候选文件，再允许提交。

## 2. 运行命令

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

## 3. 关键配置

1. Agent 配置文件：
`locbench/config/agent_tools_radar_oracle_sniper.yaml`
2. 主设计文档：
`locbench/docs/locbench_oracle_sniper_design_doc.md`
3. 测试操作文件：
`locbench/docs/oracle_sniper_test_file.md`

## 4. 指标关注点

1. `oracle_file_provided_rate`
2. `oracle_verification_compliance_rate`
3. `entity_hit_rate_given_oracle_file`
4. `steps_to_success_in_oracle_mean`

## 5. 推荐阅读顺序

1. `locbench/docs/README.md`
2. `locbench/docs/locbench_operations_all_in_one.md`
3. `locbench/docs/oracle_sniper_test_file.md`
