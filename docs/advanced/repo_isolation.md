# 评测仓库隔离设计文档

## 背景
LocBench 与 SWE-QA-Bench 的每个问题实例都明确指定“所属仓库”，LocBench 还给出
`base_commit` 等版本信息。评测的核心假设是“每题只访问自己的仓库版本”，否则会出现
跨仓库信息泄露与指标偏高的问题。当前实现为了简化容器启动，把 `repos_root` 整体只读
挂载到容器 `/repos`，再进入目标仓库工作目录执行任务。这样虽然**工作目录**正确，但
容器内仍可读取其他仓库，与评测隔离假设不一致。

从架构评审视角，本改动是保障 Benchmark 公平性（Fairness）与防作弊/泄露
（Anti-cheating/Leakage）的必要步骤。

## 问题陈述
- 题目只应访问单一仓库（LocBench 还要求固定到 `base_commit`）。
- 现有实现把所有仓库挂载到 `/repos`，导致其他仓库可读。
- 这可能带来跨仓库泄露，影响评测公平性与可复现性。

## 目标
- 容器内仅可见当前题目的仓库。
- 保持 LocBench `base_commit` checkout 逻辑不变。
- 尽量不改 prompt/输出格式，改动局部化。
- 提供回退到旧行为的开关。

## 非目标
- 加强 `local` 环境隔离（无容器时不提供隔离保证）。
- 实现更复杂的安全沙箱（网络、系统调用限制等）。

## 现状概览
- LocBench（bash/tools）：
  - `repos_root -> /repos:ro` 全量挂载
  - 容器内 `git clone /repos/<repo_dir> /work/<instance_id>`
  - `git checkout <base_commit>`
- SWE-QA-Bench（bash/tools）：
  - `repos_root -> /repos:ro` 全量挂载
  - 直接在 `/repos/<repo>` 工作
- 结果：容器内所有仓库可读

## 方案设计
引入“按实例挂载”的 repo 挂载策略，并默认开启单仓库模式。

### 配置新增（environment）
在 agent config 的 `environment` 下新增：
```
environment:
  repo_mount_mode: single  # 默认 single，可选 all
```

语义：
- `single`：仅挂载当前实例仓库到容器。
- `all`：保留旧行为，挂载整个 `repos_root`。

### 挂载策略
仅针对 docker 环境：
- 按实例生成 `run_args`（避免全局静态 mount）。
- `single` 模式下，用单仓库挂载替换全量挂载：
  - LocBench：`-v <repo_source_path>:/repos/<repo_dir>:ro`
  - SWE-QA-Bench：`-v <repo_path>:/repos/<repo>:ro`
- 保留 `--rm` 与用户额外 `run_args`。
- `all` 模式保持 `repos_root -> /repos:ro`。
- 路径结构保持 `/repos/<repo_name>`，不引入 `/repo` 变体。

### LocBench tools 模式注意事项
tools 模式会把 `instance["repo_path"]` 替换为 worktree 路径，但 worktree 的 `.git` 文件
指向外部路径，容器内 clone 该路径可能失败。为避免该问题：
- 新增 `repo_source_path`（原始仓库路径）。
- 容器挂载使用 `repo_source_path`，即使 `repo_path` 被替换为 worktree。

### 失败策略（single 模式）
当 `repo_source_path`/`repo_path` 不存在时直接 fail（host 侧报错），避免容器内
出现不清晰的 git clone 错误，节省排错成本。

## 兼容性
- 默认 `environment.repo_mount_mode: single`。
- 支持 `environment.repo_mount_mode: all` 回退旧行为。
- `local` 环境不提供隔离保证（文档说明）。

## 具体修改点

### LocBench
更新以下 `_get_locbench_environment`：
- `src/minisweagent/locbench/runners/bash_runner.py`
- `src/minisweagent/locbench/runners/tools_runner.py`
- `src/minisweagent/run/extra/locbench.py`
- `src/minisweagent/run/extra/locbench_tools.py`

新增实例字段：
- `repo_source_path`（原始仓库路径）

挂载逻辑：
- `single`：挂载 `repo_source_path` 到 `/repos/<repo_dir>`
- `all`：保留 `repos_root -> /repos:ro`

### SWE-QA-Bench
更新以下 `_get_environment`：
- `src/minisweagent/swe_qa_bench/runners/bash_runner.py`
- `src/minisweagent/swe_qa_bench/runners/tools_runner.py`

挂载逻辑：
- `single`：挂载 `instance["repo_path"]` 到 `/repos/<repo>`
- `all`：保留 `repos_root -> /repos:ro`

### 配置与文档
新增配置项（environment）：
- `locbench/config/agent_bash.yaml`
- `locbench/config/agent_tools.yaml`
- `swe_qa_bench/config/agent_bash.yaml`
- `swe_qa_bench/config/agent_tools.yaml`

更新相关运行/环境文档以说明默认隔离行为与回退方式。

## 测试计划
- 单元测试：验证 `single` / `all` 下 `run_args` 组装结果。
- 逻辑测试：构造实例，检查：
  - `single`：`/repos` 仅包含当前 repo
  - `all`：`/repos` 包含所有 repo
- 手动跑一条实例，确认：
  - LocBench `base_commit` checkout 正常
  - SWE-QA-Bench 工作目录正确

## 风险与应对
- **worktree clone 失败**：挂载 worktree 路径导致 `.git` 指向外部。
  - 通过 `repo_source_path` 解决。
- **用户自定义 run_args 冲突**：可能已手动挂载 `/repos`。
  - `single` 模式下移除 `repos_root:/repos:ro`，文档说明优先级。
- **脚本依赖 `/repos` 列目录**：旧脚本可能假设 `/repos` 里有所有 repo。
  - 通过 `repo_mount_mode: all` 兼容。

## 评审结论（已定）
- `repo_mount_mode` 放在 `environment`，语义上属于环境构建参数。
- 挂载路径保持 `/repos/<repo_name>`，不引入 `/repo` 变体。
- `single` 模式下 repo 路径不存在直接 fail，避免容器内隐式错误。
