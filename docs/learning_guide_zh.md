# mini-swe-agent 学习文档

本文档用于快速理解 mini-swe-agent 的整体逻辑与核心模块关系，适合作为源码阅读路线图。

## 1. 整体运行流程（从启动到结束）
1. CLI 入口启动：`src/minisweagent/__main__.py` 调用 `src/minisweagent/run/mini.py` 的 `app()`。
2. 读取全局配置：`src/minisweagent/__init__.py` 通过 `platformdirs` 定位 `~/.config/mini-swe-agent/.env` 并加载环境变量。
3. 解析 YAML 配置：`src/minisweagent/run/mini.py` 加载 `src/minisweagent/config/mini.yaml`（可通过 `-c` 指定）。
4. 构建核心对象：
   - Model：`src/minisweagent/models/__init__.py` 根据 model 名称/类选择实现（默认 `LitellmModel`）。
   - Environment：默认本地 `LocalEnvironment`（`src/minisweagent/environments/local.py`）。
   - Agent：默认交互式 `InteractiveAgent` 或 Textual UI `TextualAgent`（由 `-v` 和 `MSWEA_VISUAL_MODE_DEFAULT` 决定）。
5. 进入 Agent 主循环：`DefaultAgent.run()` → `step()` → `query()` → `parse_action()` → `execute_action()` → `get_observation()`。
6. 结束时保存轨迹：`src/minisweagent/run/utils/save.py` 输出 `.traj.json` 记录消息、配置与成本。

## 2. Agent 核心逻辑（最重要）
核心代码：`src/minisweagent/agents/default.py`
- `run(task)`：初始化 system/user 消息后进入无限循环。
- `query()`：调用 `model.query()`，并更新消息列表；同时检查步数/成本限制。
- `parse_action()`：用正则从模型输出中提取唯一的 bash 命令块，不符合格式会触发 `FormatError`。
- `execute_action()`：调用环境执行命令，超时触发 `ExecutionTimeoutError`。
- `has_finished()`：如果命令输出首行包含 `MINI_SWE_AGENT_FINAL_OUTPUT` 或 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`，则抛出 `Submitted` 结束。

交互增强：
- `InteractiveAgent`（`src/minisweagent/agents/interactive.py`）增加人类确认/手动模式（`human/confirm/yolo`）。
- `TextualAgent`（`src/minisweagent/agents/interactive_textual.py`）提供 TUI 交互界面。

## 3. Model 层：如何调用大模型
核心逻辑在 `src/minisweagent/models/litellm_model.py`：
- `query()` 调用 `litellm.completion` 获取模型响应，并统计成本。
- `GLOBAL_MODEL_STATS`（`src/minisweagent/models/__init__.py`）用于全局成本/调用限制。

模型选择入口：`src/minisweagent/models/__init__.py`
- `get_model_name()`：优先使用 CLI 传参，其次是 config，最后是环境变量 `MSWEA_MODEL_NAME`。
- `get_model_class()`：根据 model 名称或显式类路径选择实现（默认 Litellm）。

## 4. Environment 层：如何执行命令
默认是 `LocalEnvironment`（`src/minisweagent/environments/local.py`）：
- 使用 `subprocess.run(shell=True)` 执行命令。
- 支持设置 `cwd`、`env`、`timeout`。

其他环境实现：
- Docker：`src/minisweagent/environments/docker.py`
- Singularity：`src/minisweagent/environments/singularity.py`
- 额外环境（swerex/bubblewrap）：`src/minisweagent/environments/extra/`

## 5. 配置系统与模板
配置入口：`src/minisweagent/config/`
- `mini.yaml`：`mini` CLI 默认配置，含 agent 模板、格式规则、成本/步数限制等。
- `default.yaml`：基础 Agent 配置（不含交互 UI）。

模板机制（重要）：
- `system_template` / `instance_template` 定义提示词结构。
- `action_regex` 决定如何从模型输出解析命令（默认匹配 ```bash 块）。
- `action_observation_template` 定义“执行结果”如何回填给模型。

## 6. 终止与异常机制
`src/minisweagent/agents/default.py` 中的关键异常：
- `FormatError`：模型输出格式不符合规范。
- `ExecutionTimeoutError`：命令执行超时。
- `LimitsExceeded`：达到步数或成本限制。
- `Submitted`：模型显式输出“提交完成”标记，Agent 结束。

## 7. 轨迹记录（调试与复现）
`src/minisweagent/run/utils/save.py` 保存：
- 全部消息（system/user/assistant）
- 模型与环境配置快照
- 成本统计与退出原因
输出格式为 `*.traj.json`，便于离线分析或回放。

## 8. mini-extra：扩展工具入口
入口：`src/minisweagent/run/mini_extra.py`，常用子命令：
- `config`：管理全局 `.env`（`src/minisweagent/run/extra/config.py`）。
- `inspect`：轨迹浏览器。
- `swebench` / `swebench-single`：批量/单例 SWE-bench 评测（`src/minisweagent/run/extra/swebench.py`）。

## 9. 推荐阅读顺序（学习路径）
1. `src/minisweagent/run/mini.py`（入口与初始化）
2. `src/minisweagent/agents/default.py`（核心控制流）
3. `src/minisweagent/models/litellm_model.py`（模型调用）
4. `src/minisweagent/environments/local.py`（命令执行）
5. `src/minisweagent/config/mini.yaml`（模板与规则）
6. `src/minisweagent/run/utils/save.py`（轨迹保存）

如需更深入某个模块，可从对应目录的 `README.md` 开始，例如：
- `src/minisweagent/agents/README.md`
- `src/minisweagent/models/README.md`
- `src/minisweagent/environments/README.md`
