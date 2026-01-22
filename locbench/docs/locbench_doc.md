# LocBench（Bash-Only）实验文档

本文档描述当前阶段 **仅使用 Bash 工具** 的 LocBench 定位实验方案。
当前系统不引入任何额外搜索/检索工具，所有定位均通过 shell 命令完成。
本文件仅覆盖 **bash-only 基线**。

相关总览文档：
- `locbench/docs/locbench_runbook.md`（运行命令汇总）
- `locbench/docs/locbench_methods_design_doc.md`（方法设计）
- `locbench/docs/locbench_env_setup.md`（环境配置与迁移）

---

## 1. 目标与范围

**目标**
- 使用 mini-swe-agent 在 Docker 容器内对 LocBench 进行定位评测。
- 仅允许 Bash 命令进行检索和阅读文件，禁止任何修改操作。
- 输出统一落在 `mini-swe-agent/locbench/` 下，便于管理与评估。

**范围**
- ✅ Bash-only（rg/grep/sed/cat 等）
- ✅ 本地仓库镜像只读挂载
- ✅ 每实例独立工作目录
- ❌ 不使用任何额外工具（如检索 API、embedding、IDE 工具）
- ❌ 不进行代码修改/提交

---

## 2. 当前实现文件

**Runner（批量执行）**
- `mini-swe-agent/src/minisweagent/run_locbench.py`
- `mini-swe-agent/src/minisweagent/locbench/runners/bash_runner.py`

**Prompt 配置**
- `mini-swe-agent/locbench/config/agent_bash.yaml`
  - 强化单步协议（一个 THOUGHT + 一个 bash block + 一个命令）
  - 明确“只定位、不修改”

**Docker 镜像**
- `mini-swe-agent/locbench/Dockerfile`
  - 内置 git + ripgrep
  - 使用清华源（国内可稳定 apt-get）

**输出目录**
- `mini-swe-agent/locbench/outputs/`
- `mini-swe-agent/locbench/results/loc_output/`

---

## 3. 设计流程（Bash-only）

每个实例执行流程：
1. 将 `locbench_repos/` 只读挂载到容器 `/repos`
2. 容器内创建 `/work/<instance_id>`
3. `git clone --no-hardlinks /repos/<org_repo> /work/<instance_id>`
4. `git checkout <base_commit>`
5. 仅通过 Bash 做搜索与阅读
6. 输出 JSON（found_files/found_entities/found_modules）

关键点：
- **只读镜像**：不污染本地仓库
- **单实例隔离**：每个实例独立容器与工作目录
- **单步协议**：避免多命令导致 FormatError

---

## 4. Docker 镜像构建（国内源）

```bash
cd /Users/chz/code/locbench/mini-swe-agent
docker build -t locbench-minisweagent:latest -f locbench/Dockerfile .
```

可选：如果不想 build，可以直接使用 `python:3.11`（有 git，但无 rg）：
```bash
--image python:3.11
```

---

## 5. 运行方式

### 单条测试（推荐）
```bash
cd /Users/chz/code/locbench/mini-swe-agent
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --slice 0:1 \
  --workers 1 \
  --redo-existing
```

### 批量运行
```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --workers 4
```

### 指定模型
```bash
PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --model openai/deepseek-v3.2 \
  --workers 2
```

### CLI 入口
```bash
mini-extra locbench --help
```

---

## 6. 输出与评估

**输出路径**
- 轨迹：`mini-swe-agent/locbench/outputs/<model>/<method>/<timestamp>/<instance_id>/<instance_id>.traj.json`
- 日志：`mini-swe-agent/locbench/outputs/<model>/<method>/<timestamp>/minisweagent.log`
- 结果：`mini-swe-agent/locbench/results/loc_output/<model>/<method>/loc_outputs_<timestamp>.jsonl`

**简单评估**
```bash
python /Users/chz/code/locbench/evaluation/simple_eval.py \
  /Users/chz/code/locbench/mini-swe-agent/locbench/results/loc_output/<model>/<method>/loc_outputs_<timestamp>.jsonl \
  /Users/chz/code/locbench/data/Loc-Bench_V1_dataset.jsonl
```

**完整评估**
```bash
python /Users/chz/code/locbench/evaluation/compute_full_metrics.py \
  /Users/chz/code/locbench/mini-swe-agent/locbench/results/loc_output/<model>/<method>/loc_outputs_<timestamp>.jsonl \
  /Users/chz/code/locbench/data/Loc-Bench_V1_dataset.jsonl
```

---

## 7. Bash-only 约束（重点）

目前 **只允许 Bash 工具**：
- 允许：`rg`, `grep`, `sed`, `awk`, `cat`, `find`, `head`, `tail`
- 不允许：任何额外检索工具 / embedding 服务 / IDE 工具

提示词里已设置 **单步协议**：
- 一个 THOUGHT
- 一个 bash code block
- 一个命令（多步用 `&&` 串联）

此规则用于避免 `FormatError`，确保真正执行检索命令。

---

## 8. 常见参数

- `--slice 0:10`：跑部分实例
- `--filter REGEX`：按 instance_id 过滤
- `--shuffle`：打乱实例顺序
- `--redo-existing`：覆盖已有结果
- `--image`：指定 Docker 镜像

---

## 9. 常见问题

**1) Docker build 失败 / 502**
- 已切换清华源，若仍失败可改阿里云/中科大源。

**2) FormatError（found N actions）**
- 说明一个回复中输出了多个 bash block。
- 已在 prompt 中加入硬性“单步协议”，仍可再加强。

**3) 实例被跳过**
- `loc_outputs_*.jsonl` 中已存在该 instance_id。
- 用 `--redo-existing` 或删除输出文件。

---

## 10. 备注

- 本阶段所有定位结果完全依赖 Bash 检索，速度与质量受限于 shell 工具。
- 作为 bash-only baseline，这份结果可用于不同模型/配置之间的对比。
