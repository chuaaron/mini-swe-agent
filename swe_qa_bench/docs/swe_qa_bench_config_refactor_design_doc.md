# SWE-QA-Bench 配置重构设计文档

本文档描述 SWE-QA-Bench 配置系统的重构方案，目标是让迁移到新服务器时只需改一处本地配置，避免硬编码和长命令行。

---

## 1. 背景与问题

当前配置方式主要依赖命令行或分散的配置文件，导致以下问题：
- 迁移困难：路径写死，换机器要到处改。
- Git 污染：为某台机器改了配置后容易误提交。
- 命令行膨胀：运行时参数堆积，不利于维护。
- 环境与逻辑混在一起：路径与模型设置掺杂，配置不可读。

---

## 2. 目标

- 迁移时只修改一处本地配置。
- 默认配置可提交，机器配置不可提交。
- CLI 仍然保留，并作为最高优先级覆盖。
- 配置结构清晰、可扩展、低风险。

---

## 3. 非目标

- 不改 LocBench（本次仅重构 SWE-QA-Bench）。
- 不引入复杂变量展开（如 ${VAR}）。
- 不引入多套配置切换系统（profile）。

---

## 4. 核心决策（已确认）

### 4.1 配置层级结构

决策：各自独立一套（Parallel & Decoupled）。

路径：
- `mini-swe-agent/swe_qa_bench/config/default.yaml`
- `mini-swe-agent/swe_qa_bench/config/local.yaml`
- `mini-swe-agent/locbench/config/...`（未来改造）

理由：
- SWE-QA-Bench 与 LocBench 数据结构不同，强行合并会互相干扰。
- 独立配置便于未来拆分 SWE-QA-Bench 模块。

### 4.2 Local 层承载密钥

决策：API key / API base 放 `local.yaml`。

理由：
- 本地配置越显式越好。
- `local.yaml` 进入 `.gitignore`，避免泄漏。

实施注意：
- Runner 加载配置时必须把 `env` 显式注入到 `os.environ`，否则 SDK 不会识别。

### 4.3 运行入口策略

决策：保留 CLI 入口，但 CLI 默认行为改为“加载 YAML”，并新增统一入口脚本。

方案：
- 新增统一入口：`src/minisweagent/run_swe_qa.py`。
- 默认加载 `config/default.yaml` + 可选 `config/local.yaml`。
- CLI 参数只用于覆盖当前运行。
- `runners/*.py` 退回为纯类库，不保留复杂的 `__main__` 逻辑。
- `run_from_yaml` 标记为 Deprecated，但不删除（打印 Warning）。

LocBench 现阶段不动，避免 Scope Creep。

### 4.4 YAML 结构

决策：分组字段（Grouped）。

结构：
- `run`: workers/slice/shuffle 等
- `model`: provider/model_name 等
- `paths`: dataset_root/repos_root/indexes_root 等
- `env`: API key/base 等

理由：
- 可读性更强，语义清晰。
- 避免字段命名冲突。

### 4.5 覆盖规则与变量展开

决策：
- 覆盖优先级固定为：`CLI > local.yaml > default.yaml`。
- 深度合并（Deep Merge），list 采用 **替换**（Replace）。
- 只支持 `~` 展开（`os.path.expanduser`），不做 `${VAR}`。

理由：
- 解析器保持简单（KISS）。
- `~` 覆盖多数路径需求。

### 4.6 相对路径解析

决策：相对“项目根目录”解析，或使用绝对路径与 `~`。

方案：
- 在 config loader 中定义 `PROJECT_ROOT`。
- 若路径为相对路径（例如 `./data`），则转换为 `PROJECT_ROOT/data`。
- 在 `local.yaml` 注释中引导用户使用绝对路径或 `~`。

实现提示：
- `PROJECT_ROOT` 由 `config_loader.py` 位置向上查找 `pyproject.toml` 得到，避免依赖 `os.getcwd()`。

### 4.7 local.yaml 缺失行为

决策：加载阶段静默忽略，验证阶段报错提示。

方案：
- 如果 `local.yaml` 不存在，打印 Info 提示并继续使用 default。
- 在配置校验阶段检查关键路径，若缺失则给出明确错误信息。

### 4.8 环境变量注入

决策：配置合并后立即注入到 `os.environ`，适用于 run 与 score。

理由：
- SDK 通常只读取环境变量，不会读取 YAML。

### 4.9 code_search 路径覆盖

决策：以 `local.yaml` 为唯一真理源（Source of Truth）。

方案：
- `code_search.yaml` 仅保留模板或相对路径。
- 初始化 code_search 时从 `paths.indexes_root`/`paths.model_root` 动态拼接。

### 4.10 CLI 覆盖范围

决策：白名单（Whitelist）覆盖。

方案：
- 允许：workers/slice/shuffle/repos/model/dataset_root 等高频参数。
- 不允许：任意深层结构覆盖（例如直接覆盖 env 或工具参数）。

### 4.11 非 Docker 支持

决策：暂不预留（YAGNI）。

---

## 5. 配置文件结构（建议）

### 5.1 default.yaml（提交到 Git）

用途：通用逻辑参数与默认路径占位。

建议字段：
- `run`: 并发、shuffle、slice 等默认参数
- `model`: 模型与 provider
- `paths`: 默认路径占位（可写成相对或 `~/`）

示例（仅示意）：

```yaml
run:
  workers: 1
  shuffle: false
  shuffle_seed: 42
  slice: ""

model:
  model_class: chatanywhere
  model_name: deepseek-v3.2

paths:
  dataset_root: ~/data/SWE-QA-Bench/SWE-QA-Bench/datasets
  repos_root: ~/data/SWE-QA-Bench/SWE-QA-Bench/datasets/repos
  indexes_root: ~/data/mini-swe-agent/swe_qa_bench/indexes
  model_root: ~/data/mini-swe-agent/swe_qa_bench/models/CodeRankEmbed
  output_model_name: openai_deepseek-v3.2
```

### 5.2 local.yaml（不提交）

用途：本机路径与密钥。

示例（仅示意）：

```yaml
paths:
  dataset_root: /data/locbench/SWE-QA-Bench/SWE-QA-Bench/datasets
  repos_root: /data/locbench/SWE-QA-Bench/SWE-QA-Bench/datasets/repos
  indexes_root: /data/locbench/mini-swe-agent/swe_qa_bench/indexes
  model_root: /data/locbench/mini-swe-agent/swe_qa_bench/models/CodeRankEmbed
  output_model_name: openai_deepseek-v3.2

env:
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.example.com/v1/chat/completions
```

---

## 6. CLI 解析与合并规则

加载顺序：
1. 读取 `default.yaml`。
2. 若 `local.yaml` 存在则覆盖合并。
3. 解析 CLI 参数并覆盖。

合并规则：
- 递归深度合并（Deep Merge），只覆盖叶子节点。
- list 采用 **替换**，不做追加。
- 仅 `~` 展开，不做复杂变量替换。
- 相对路径按项目根目录解析。

环境变量注入：
- 配置合并完成后，读取 `env` 字段并注入到 `os.environ`（run/score 都执行）。

---

## 7. 迁移流程（目标体验）

迁移到新服务器时：
1. `git pull`
2. 新建或更新 `config/local.yaml`（仅改路径与密钥）
3. 直接运行 CLI，无需改代码或长参数

---

## 8. 实施计划（SWE-QA-Bench）

- 新增 `config/default.yaml` 与 `config/local.yaml` 模板
- 新增统一入口 `src/minisweagent/run_swe_qa.py`
- runners 改为类库形式，移除复杂 `__main__` 逻辑
- CLI 默认加载并合并两层配置（Deep Merge + list 替换）
- CLI 参数白名单覆盖（保留已有常用参数接口）
- 合并后注入 `env` 到 `os.environ`
- 关键路径校验（dataset_root/repos_root 等）
- code_search 路径由 `paths.*` 动态注入
- `run_from_yaml` 标记 Deprecated（打印 Warning）
- `.gitignore` 忽略 `local.yaml`
- 更新文档：运行与环境说明

---

## 9. 风险与边界

- 深度合并需保证实现正确（避免 list/非 dict 类型误合并）。
- 暂不支持复杂变量替换，路径需写完整或使用 `~`。
- LocBench 暂不改，避免影响已稳定流程。
