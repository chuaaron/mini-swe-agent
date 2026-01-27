# SWE-QA-Bench 输出/结果统一保存逻辑设计

本文档描述将 SWE-QA-Bench 的 `results` 写入逻辑与 `outputs` 统一为“按运行时间戳分目录”的设计方案，并覆盖评分流程的改造。

---

## 0. 背景

当前设计中：
- `outputs/` 用时间戳区分每次运行，保存轨迹与日志。
- `results/answers` 与 `results/scores` 使用固定路径追加写入，跨运行混合。

用户诉求是“保持和 outputs 一样的保存逻辑”，让答案/评分产物与日志轨迹在同一运行目录内一一对应，便于排错、复现和管理。

---

## 1. 目标与非目标

**目标**
- 运行产物在同一 run 目录内：日志、轨迹、answers、scores 对应同一运行。
- 同一 run 可续跑；不同 run 不互相污染。
- 评分流程可直接基于 run 目录执行，避免手动拼路径。

**非目标**
- 不改变答案格式、评分逻辑、question 对齐规则。
- 不调整 agent/tool 行为与模型配置结构。

---

## 2. 新目录结构（以 run 为单位）

### 2.1 Run 根目录

```
swe_qa_bench/outputs/<model>/<method>/<run_id>/
  run_meta.json
  minisweagent.log
  exit_statuses_<ts>.yaml
  instances/
    <repo>-<idx>/
      <repo>-<idx>.traj.json
  answers/
    <repo>.jsonl
  scores/
    <repo>.jsonl
```

说明：
- `run_id` 默认时间戳（`YYYYMMDD_HHMMSS`），也支持用户显式指定。
- `answers/` 与 `scores/` 直接位于 run 根目录，避免重复嵌套 `<model>/<method>`。
- `instances/` 继续保留每个 instance 的轨迹文件，结构与现状一致。

### 2.2 示例

```
swe_qa_bench/outputs/openai_deepseek-v3.2/miniswe_tools/20250218_153012/
  answers/requests.jsonl
  scores/requests.jsonl
  instances/requests-12/requests-12.traj.json
```

---

## 3. 配置与参数变更

### 3.1 新增/调整字段

**配置新增**
```
run:
  run_id: ""          # 可选，缺省用时间戳
  run_dir: ""         # 可选，显式运行目录（绝对/显式路径）
  write_legacy_results: false
paths:
  output_root: swe_qa_bench/outputs
```

**CLI 新增/调整**
- `--run-id`：显式指定 run_id，用于续跑或对齐日志。
- `--run-dir`：显式运行目录（绝对/显式路径），不再追加 `<model>/<method>`。
- `--output-dir`：软废弃，语义等同于 `--run-dir`（作为叶子目录使用）。

### 3.2 计算规则

```
run_dir =
  if run_dir is set: <run_dir>
  else: <output_root>/<output_model_name>/<method>/<run_id>
```

说明：
- `run_id` 仅在未显式给 `run_dir`/`output_dir` 时生效。
- `output_dir` 语义等同 `run_dir`，不再拼接 `<model>/<method>`。

---

## 4. 写入逻辑

### 4.1 答案写入

```
answer_path = <run_dir>/answers/<repo>.jsonl
```

保持每行输出格式不变：
```
{"question": "...", "answer": "...", "final_answer": "...", "relative_code_list": ["..."], "stats": {...}}
```

### 4.2 评分写入

```
score_path = <run_dir>/scores/<repo>.jsonl
```

### 4.3 轨迹与日志

```
traj_path = <run_dir>/instances/<instance_id>/<instance_id>.traj.json
log_path  = <run_dir>/minisweagent.log
```

### 4.4 Run 元数据

`run_meta.json` 记录完整运行信息，建议字段：
```json
{
  "run_id": "20250218_153012",
  "timestamp_start": "...",
  "duration": "...",
  "git_commit": "sha123...",
  "command": "python -m minisweagent.run_swe_qa ...",
  "args": {},
  "config": {},
  "environment": {
    "python": "3.10.x",
    "host": "hostname"
  },
  "dataset_path": "/path/to/data"
}
```

说明：
- `duration` 在运行结束时回写，异常退出可为空。
- `config` 为最终生效的完整配置快照。

---

## 5. 评分流程改造

### 5.1 新参数支持

评分 CLI/score_from_yaml 必须显式指定运行目录：
1) `--run-dir`（或 `run.run_dir`）直接指向某次运行目录。
2) `--output-root + --output-model-name + --method + --run-id` 推导 run_dir。

默认不再自动推断“最新一次 run”。如需此能力，提供显式 `--latest` 作为可选快捷入口。

### 5.2 评分读取路径

当 run_dir 被解析后：
```
candidate_base = <run_dir>/answers
output_base    = <run_dir>/scores
```

评分结果默认写入同一 run_dir，保证一一对应。

---

## 6. 兼容与迁移策略

### 6.1 旧目录兼容

默认关闭旧路径读写，仅在显式开启时使用：
- 写入旧路径：`run.write_legacy_results: true`
- 评分旧路径：需要显式 `--legacy-results`（或同等配置开关）

### 6.2 可选兼容写入

若开启 `run.write_legacy_results: true`，则在写入 run_dir 时同步写入旧路径，便于已有脚本不改动。

---

## 7. 影响与风险

- 现有脚本若硬编码 `swe_qa_bench/results` 路径需要更新。
- 新的 run_id 机制要求用户在续跑时明确复用 run_id，避免生成新目录。
- 文档与 runbook 需要同步更新路径说明。
- `redo-existing` 仅在当前 run_dir 内生效，跨 run 不再去重。

---

## 8. 验证建议

1) 运行一次，确认 `answers/` 与 `instances/` 在同一 run_dir 下生成。
2) 使用 `--run-id` 复跑，确保复用同一 run_dir 并尊重 `--redo-existing`。
3) 基于 run_dir 执行 score，输出落在 `scores/`。
4) 检查 `run_meta.json` 字段完整性。
