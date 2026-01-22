# LocBench 测评方法设计文档（mini-swe-agent）

本文档说明 mini-swe-agent 在 LocBench 上的三种测评方法及其实现原理。
目标是便于迁移到服务器后，快速理解各方法的代码入口与运行机制。

---

## 1. 方法总览

| 方法 | 入口命令 | 是否用 LLM | 是否用 Docker | 是否用 code_search |
|------|----------|------------|---------------|--------------------|
| Bash-only | `mini-extra locbench` | ✅ | ✅ | ❌ |
| Tools | `mini-extra locbench-tools` | ✅ | ✅ | ✅ |
| IR-only | `mini-extra locbench-code-search` | ❌ | ❌ | ✅ |

---

## 2. 共同输入与输出

**共同输入**
- 数据集 JSONL：`data/Loc-Bench_V1_dataset.jsonl`
- 仓库镜像根目录：`locbench_repos/`
- 每条样本的 `repo` + `base_commit`

**共同输出（JSONL）**
- `found_files` / `found_entities` / `found_modules`
- 输出格式一致，可直接用 `evaluation/simple_eval.py`

---

## 3. 方法一：Bash-only（基线）

**入口**
- Runner：`src/minisweagent/run_locbench.py`（mode=bash）
- Prompt：`locbench/config/agent_bash.yaml`

**执行流程**
1. 容器启动后挂载 `/repos`（只读）
2. 复制仓库到 `/work/<instance_id>`
3. `git checkout <base_commit>`
4. 仅通过 bash（rg/cat/sed）探索
5. 输出 `MINI_SWE_AGENT_FINAL_OUTPUT` JSON

**特点**
- 纯 bash 操作，检索靠字符串匹配
- 无语义检索，步数可能较多

---

## 4. 方法二：Tools（bash + code_search）

**入口**
- Runner：`src/minisweagent/run_locbench.py`（mode=tools）
- Prompt：`locbench/config/agent_tools.yaml`
- 工具配置：`locbench/config/code_search.yaml`

**核心组件**
- `ToolAgent`：支持 `@tool` 命令拦截
- `ToolRegistry`：管理工具分发
- `code_search`：语义检索工具（宿主机执行）

**执行流程**
1. LLM 仍在 Docker 内执行 bash
2. 如输出 `@tool code_search ...`，由 ToolAgent 拦截
3. `code_search` 在宿主机执行，基于 worktree 检索
4. 工具结果注入到 LLM 上下文
5. LLM 再用 bash 精确读取

**关键点**
- 工具输出路径必须是相对路径（已在工具内做归一化）
- 宿主机与容器版本一致：通过 `tool_worktrees` checkout `base_commit`

---

## 5. 方法三：IR-only（纯检索）

**入口**
- Runner：`src/minisweagent/run_locbench.py`（mode=ir）

**执行流程**
1. 对每条实例构造 worktree（checkout 到 `base_commit`）
2. 用 `problem_statement` 直接做 code_search
3. 将块级得分聚合到文件（同文件分数求和）
4. 用 AST/Graph Mapper 生成 `found_entities` / `found_modules`
5. 输出 JSONL

**特点**
- 无 LLM、无 Docker，速度快
- 结果质量依赖索引与 mapper

---

## 6. Worktree 与版本一致性

**目录**：`$MINISWE_ROOT/locbench/tool_worktrees/`

- 每个 `repo_dir@commit` 生成一个 worktree
- code_search 和 mapper 都基于此路径读取
- 目的：确保宿主机检索与 `base_commit` 一致

---

## 7. code_search 关键实现点

**入口**：`src/minisweagent/tools/code_search/tool.py`

- `LocalEmbedder`：本地 `torch + transformers`
- `chunker`：滑动窗口，`chunk_size/overlap` 由 YAML 配置
- 索引布局：
  - 新版：`index_root/<repo_dir>/<commit[:8]>/<provider_model>/embeddings.pt`
  - 兼容旧版：`index_root/<repo_dir>/embeddings.pt`

**输出**
- `path` 强制相对 repo 根目录
- `line_span` 1-based 行号

---

## 8. 输出与评估

**输出路径**
- bash/tools：`locbench/outputs/...` + `locbench/results/loc_output/...`
- IR-only：`locbench/results/loc_output/...`

**评估脚本**
- `evaluation/simple_eval.py`

---

## 9. 方法对比建议

- 先跑 **bash-only** 作为 baseline
- 再跑 **tools** 看语义检索提升
- 用 **IR-only** 作为检索上限

随机对比建议：
- 使用 `--shuffle --shuffle-seed` 固定样本集
