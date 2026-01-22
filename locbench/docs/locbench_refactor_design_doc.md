# LocBench 重构设计文档（对齐 SWE-QA-Bench）

目标：把 LocBench 的测评体系改造成与 SWE-QA-Bench 同一套“分层配置 + 自包含输出 + 统一入口”的结构，迁移服务器时只改 `local.yaml`。

---

## 1. 背景与问题

现状存在这些痛点：
- 配置分散在 CLI / 代码 / 旧 YAML 中，迁移要改多处
- 运行入口不统一（`mini-extra locbench*` / `python .../locbench.py` 混用）
- 输出位置与评测脚本耦合，路径不够稳定
- 绝对路径过多，不利于项目整体移动

---

## 2. 重构目标（与 SWE-QA-Bench 看齐）

1) **自包含**：除外部数据集与仓库镜像外，逻辑代码/配置模板/脚本全部在 `mini-swe-agent/locbench` 内  
2) **显式配置**：所有实验参数（Model / Slice / Shuffle / Workers / Image / Method）都在 YAML 可见  
3) **单点修改**：迁移服务器只改 `locbench/config/local.yaml`  
4) **环境可复现**：保留 `locbench/Dockerfile` 与依赖说明  
5) **相对路径**：内部路径默认相对项目根目录，可整体移动  
6) **产物隔离**：模型/索引/输出走独立目录并 gitignore  
7) **计费与统计**：与 SWE-QA-Bench 一致，支持双轨计费与 stats 字段

---

## 3. 非目标（本次不做）

- 不改 LocBench 数据集格式
- 不改 LocBench 评测指标逻辑
- 不改 code_search 的核心实现

---

## 4. 目录结构（目标形态）

```
mini-swe-agent/
  locbench/
    config/
      default.yaml
      local.yaml.example
      agent_bash.yaml
      agent_tools.yaml
      code_search.yaml
    results/
      loc_output/        # 定位结果 JSONL
      scores/            # 评测输出（可选）
    outputs/             # 运行日志/轨迹
    indexes/             # code_search 索引
    models/              # code_search 模型
    worktrees/           # IR-only / tools worktree
    docs/ (可选)
```

说明：
- `results/` 是 LocBench 的答案/评测输出根目录，**不再写入数据集目录**。
- `outputs/` 保留运行日志与 traj，方便调试。

---

## 5. 配置分层（与 SWE-QA-Bench 一致）

### 5.1 默认配置（Git 跟踪）
`locbench/config/default.yaml`
- 内置相对路径默认值
- 提供完整参数示例

### 5.2 本地配置（Git 忽略）
`locbench/config/local.yaml`
- 必改：
  - `paths.dataset_root`
  - `env.OPENAI_API_KEY`
- 自动推导：
  - `paths.repos_root` 为空时默认 `dataset_root/locbench_repos`
- 可选覆盖：
  - `paths.worktrees_root`（默认 `locbench/worktrees`）
  - `paths.indexes_root / paths.models_root / paths.output_root`
- 其他路径默认走相对路径（indexes/models/results）

### 5.3 覆盖优先级
`CLI > local.yaml > default.yaml`

---

## 6. 统一入口（替代多种 CLI）

新增统一入口：
```
python -m minisweagent.run_locbench --mode bash|tools|ir
```

行为：
- 加载 default/local/CLI
- 打印最终配置摘要
- 按 mode 选择 runner
- 结果写入 `locbench/results/`

兼容性：
- `mini-extra locbench*` 保留，但仅作为 wrapper（内部调用 `run_locbench`）

---

## 7. 输出与评测解耦

**定位输出（JSONL）**
```
mini-swe-agent/locbench/results/loc_output/<model>/<method>/loc_outputs_<timestamp>.jsonl
```

**日志与轨迹**
```
mini-swe-agent/locbench/outputs/<model>/<method>/<timestamp>/
```

**评测**
- 提供 `score_from_yaml` 包装，内部调用 `evaluation/simple_eval.py`
- 输出结果写入 `locbench/results/scores/`

---

## 8. 方法映射（保持现有三种）

| 方法 | mode | 说明 |
|------|------|------|
| Bash-only | `bash` | 纯 bash 基线 |
| Tools | `tools` | bash + code_search |
| IR-only | `ir` | 纯检索（无 LLM） |

---

## 9. 计费与统计（同步 SWE-QA-Bench）

**配置**
- `locbench/config/default.yaml` 中加入 `pricing`（模型单价）
- `billing.mode` 支持 `auto / usage / estimate`
- `estimate` 使用 `tiktoken + cl100k_base`

**输出**
- JSONL 增加 `stats` 字段（prompt_tokens / completion_tokens / total_tokens / cost_usd / billing_mode / api_calls）

**实现复用**
- 复用 `minisweagent/billing.py`（或等价复制）
- `save_traj` 同步写入 `model_stats`

---

## 10. Worktree 清理（防膨胀）

**配置**
- `paths.worktrees_root`（默认 `locbench/worktrees`）

**行为**
- Runner 在 finally 中清理 worktree
- 支持 debug 开关（例如 `run.keep_worktrees = false`）

---

## 11. 迁移流程（SOP）

1) 拷贝代码仓库  
2) 确保数据集与 repos 在新机器  
3) 复制 `local.yaml.example -> local.yaml`  
4) 只改 `dataset_root / repos_root / OPENAI_API_KEY`  
5) 直接运行 `run_locbench`

---

## 12. 实施计划（分阶段）

**Phase 1：配置统一**
- 新增 `locbench/config/default.yaml` + `local.yaml.example`
- 加入 `locbench/config_loader.py`（可复用 SWE-QA-Bench 逻辑）

**Phase 2：统一入口**
- 新增 `minisweagent.run_locbench`
- 把现有 `locbench.py / locbench_tools.py / locbench_code_search.py` 改为可复用 Runner 类

**Phase 3：输出路径重构**
- 输出统一到 `locbench/results/`
- 评测脚本支持 `output_root`

**Phase 4：计费与清理**
- 加入 pricing/billing 与 stats 输出
- worktree 自动清理机制

**Phase 5：文档与 gitignore**
- 更新 runbook + methods doc
- 产物目录加 `.gitignore + .gitkeep`

---

## 13. 已确认决策

- ✅ 保留 `mini-extra locbench*`，但仅作为 wrapper  
- ✅ 评测结果写入 `locbench/results/scores/`  
- ✅ 不迁移打分算法，只改 IO，必要时由 `run_locbench` 调用 `evaluation/*`  
