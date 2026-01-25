# LocBench Tooling 设计文档（一级工具版）

本文档为**独立设计文档**，描述如何在不修改原版 `DefaultAgent` 的前提下，
新增"一级工具"（与 bash 同级）的检索能力，用于后续接入自研 code_search。

方法总览请参考：`locbench/docs/locbench_methods_design_doc.md`。

---

## 1. 背景与目标

**背景**
- 现阶段只使用 bash 进行检索（rg/grep/sed/cat）。
- 计划新增"cursor-like"代码检索工具，采用 sliding 分块，并使用 embedding provider 抽象（首发本地 CodeRankEmbed）。

**目标**
- 新工具为一级工具，agent 可自主调用。
- 新代码与原代码**并行独立**，可独立开发、测试、部署。
- 保持原版 `DefaultAgent` 不变，避免破坏 bash-only baseline。
- 运行与评估流程与现有 LocBench 一致（输出格式不变）。

---

## 2. 约束与原则

- ✅ 不修改 `minisweagent/agents/default.py`
- ✅ 原 `locbench.py`/`locbench.yaml` 继续作为 bash-only baseline
- ✅ **不破坏原版行为**：
  - 原有 agent/runner 不改动
  - 允许复用通用模块（日志、进度条、config 读取）以减少重复
  - 避免复制粘贴导致维护漂移
- ✅ 新工具走独立 agent/runner/config
- ✅ bash 仍可作为 fallback

### 2.1 MVP 设计决策（已定）

- 分块策略：仅支持 sliding window（不做 AST/function 解析）
- Embedding：provider 抽象，V1 仅实现本地 CodeRankEmbed
- 过滤语法：轻量 DSL（如 `lang:python path:src/`）
- Reranker：V1 不引入
- 缓存清理：不做自动清理，手动管理磁盘空间

---

## 3. 现状概览（bash-only baseline）

- Runner：`minisweagent/run/extra/locbench.py`
- Prompt：`minisweagent/config/extra/locbench.yaml`
- Docker 镜像：`mini-swe-agent/locbench/Dockerfile`

---

## 4. 新工具架构总览

新增并行链路（不改原版）：

```
ToolAgent  -> ToolRegistry -> code_search (一级工具)
        \\-> bash (fallback)
```

关键点：
- ToolAgent 与 DefaultAgent 并行存在。
- ToolAgent 扩展解析协议与执行分发。
- ToolRegistry 统一管理工具实现。

---

## 5. 动作协议（单步原则）

ToolAgent 仍保持"单步协议"：
- 一个 THOUGHT
- 一个 code block（统一使用 ```bash）
- 一个动作（bash 或 tool）

**动作格式（采用 bash 包装）**

工具调用：
```bash
@tool code_search --query "find centroid computation" --topk 20
```

普通 bash：
```bash
rg -n "centroid" --type py
```

**设计理由**：
- 统一格式，LM 不易混淆
- 兼容现有 action_regex（```bash）
- 工具调用本质是命令，符合 shell 直觉
- 无需修改 `action_regex`，只需解析命令内容
 - `@tool` 命令会在执行前被 ToolAgent 拦截，不会进入 shell

**约束补充（强约束）**：
- `@tool ...` 必须是 bash block 的**唯一一行**
- 不允许 `cd`/`&&`/管道与 `@tool` 混用
- ToolAgent 必须在执行 shell 前拦截 `@tool`
- 工具所需上下文由 runner 注入（不依赖当前目录）

**解析逻辑**（伪代码）：
```python
def parse_action(self, response: dict) -> dict:
    cmd = extract_bash_command(response["content"])  # 提取 ```bash 内容
    if cmd.startswith("@tool "):
        return {"type": "tool", "raw": cmd}
    return {"type": "bash", "command": cmd}
```

---

## 6. 工具接口规范

**统一接口（Python）**
```python
from typing import Protocol

class Tool(Protocol):
    """工具协议，所有工具需实现此接口"""
    name: str
    description: str

    def run(self, args: dict, context: dict) -> "ToolResult":
        """执行工具，返回结果"""
        ...
```

**context 内容**：
- `repo_path`: 宿主机代码快照路径（必须与 `base_commit` 一致）
- `instance_id`: 当前实例 ID
- `base_commit`: git commit hash
- `repo_slug`: 来自数据集 `repo` 字段，如 "UXARRAY/uxarray"
- `repo_dir`: `repo_slug` 中 `/` 替换为 `_`，如 "UXARRAY_uxarray"
- `repo_mount_path`: 容器内挂载路径，e.g. "/repos/UXARRAY_uxarray"

**ToolResult 规范**：
```python
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool              # 执行是否成功
    data: dict[str, Any]       # 工具返回的实际数据（用于记录）
    output: str                # LM 友好的格式化输出（注入 observation）
    error: str | None = None   # 错误信息（失败时）
    returncode: int = 0        # 返回码（0 表示成功）
```

**工具输出示例**（成功）：
```json
{
  "success": true,
  "data": {
    "query": "centroid computation",
    "results": [
      {
        "path": "uxarray/grid/coordinates.py",
        "score": 0.73,
        "line_span": {"start": 1200, "end": 1320},
        "snippet": "def _construct_face_centroids(...):"
      }
    ],
    "metadata": {
      "chunker": "sliding",
      "chunk_size": 800,
      "overlap": 200,
      "embedding_provider": "local",
      "embedding_model": "CodeRankEmbed"
    }
  },
  "output": "Found 5 relevant files:\n1. uxarray/grid/coordinates.py (score: 0.73)\n   def _construct_face_centroids(...)\n...",
  "returncode": 0
}
```

**工具输出示例**（失败）：
```json
{
  "success": false,
  "data": {},
  "output": "Tool execution failed",
  "error": "Index not found for repo: numpy_numpy@abc123@local_CodeRankEmbed",
  "returncode": 1
}
```

**要求**：
- `path` 必须相对 repo 根目录
- `output` 字段必须是 LM 友好的纯文本，用于注入 observation
- `data` 字段包含结构化数据，用于轨迹记录和调试
- 错误时 `success=False`，`error` 包含详细错误信息
- 输出稳定、可复现

---

### 6.1 code_search 工具设计

### 工具概述

`code_search` 是一个语义代码检索工具，通过 embedding 相似度查找相关代码片段。

**调用方式**：
```bash
@tool code_search --query "<自然语言描述>" [--topk <数量>] [--filters <过滤条件>]
```

**与 bash 检索的对比**：

| 方面 | bash (rg/grep) | code_search |
|------|----------------|-------------|
| 检索方式 | 精确文本匹配 | 语义相似度 |
| 适用场景 | 知道函数/变量名 | 知道功能描述 |
| 返回结果 | 原始代码行 | 相关度排序的代码块 |
| 示例 | `rg "centroid"` | `@tool code_search --query "计算几何中心"` |

---

### 输入参数

```python
@dataclass
class CodeSearchArgs:
    """code_search 工具参数"""
    query: str           # 必需：自然语言查询
    topk: int = 20       # 可选：返回结果数量（默认 20）
    filters: str | None = None  # 可选：文件过滤（轻量 DSL，如 "lang:python path:src/"）

    def __post_init__(self):
        if not self.query or not self.query.strip():
            raise ValueError("query cannot be empty")
        if self.topk < 1 or self.topk > 100:
            raise ValueError("topk must be between 1 and 100")
```

**参数暴露范围**：
- Agent 仅可设置 `query` / `topk` / `filters`
- 其他参数（chunk_size、overlap、embedding_model、index_root 等）通过 YAML 配置给工具，不允许由 Agent 覆盖

**参数说明**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | str | 必需 | 自然语言描述，如 "centroid computation in grid" |
| `topk` | int | 20 | 返回结果数量，建议 10-50 |
| `filters` | str | None | 文件过滤，轻量 DSL，如 `lang:python path:src/` |

**调用示例**：
```bash
# 基础用法
@tool code_search --query "centroid computation"

# 指定返回数量
@tool code_search --query "handle user authentication" --topk 30

# 带过滤条件
@tool code_search --query "database connection" --filters "lang:python path:src/db/"
```

---

### 输出格式

**LM 友好的文本输出**（`output` 字段）：
```
Found 15 relevant files for "centroid computation":

1. uxarray/grid/coordinates.py (score: 0.89)
   Lines 1200-1320 | Grid class
   ┌────────────────────────────────────────┐
   │ def _construct_face_centroids(self):   │
   │     """Compute centroids for each...""" │
   │     weights = self.face_areas...       │
   └────────────────────────────────────────┘

2. uxarray/geometry/centroids.py (score: 0.76)
   Lines 45-120 | centroid_function
   ┌────────────────────────────────────────┐
   │ def compute_centroid(coords):          │
   │     return np.mean(coords, axis=0)     │
   └────────────────────────────────────────┘

... (13 more results, use --topk to see more)
```

**结构化数据**（`data` 字段）：
```json
{
  "query": "centroid computation",
  "topk": 20,
  "returned": 15,
  "results": [
    {
      "path": "uxarray/grid/coordinates.py",
      "score": 0.89,
      "line_span": {"start": 1200, "end": 1320},
      "symbol": "Grid._construct_face_centroids",
      "snippet": "def _construct_face_centroids(self):\n    \"\"\"Compute...\"\"\"\n    weights = self.face_areas...",
      "language": "python"
    },
    {
      "path": "uxarray/geometry/centroids.py",
      "score": 0.76,
      "line_span": {"start": 45, "end": 120},
      "symbol": "centroid_function",
      "snippet": "def compute_centroid(coords):\n    return np.mean...",
      "language": "python"
    }
  ],
  "metadata": {
    "index_version": "v1.2",
    "embedding_provider": "local",
    "embedding_model": "CodeRankEmbed",
    "chunker": "sliding",
    "chunk_size": 800,
    "overlap": 200
  }
}
```

**说明**
- `line_span` 使用 1-based 行号（来自 chunker 的 `start_line`/`end_line`）
- `symbol` 为可选字段，无法识别时 `data` 使用 `null`，文本输出使用 `-`
- 对外输出保持 1-based 行号，对内做 -1 转换即可

---

### 实现接口

```python
# minisweagent/tools/code_search/tool.py
import re
from dataclasses import dataclass
from typing import Any

@dataclass
class CodeSearchArgs:
    query: str
    topk: int = 20
    filters: str | None = None

class CodeSearchTool:
    name = "code_search"
    description = "Semantic code search using embeddings"

    def __init__(self, index_root: str, embedder_provider: str, embedder_model: str):
        self.index_root = index_root
        self.embedder_provider = embedder_provider
        self.embedder_model = embedder_model
        # 初始化 embedding 客户端、加载索引等

    def _sanitize_id(self, value: str) -> str:
        """将路径不安全字符替换为下划线"""
        return re.sub(r"[^A-Za-z0-9._-]", "_", value)

    def run(self, args: CodeSearchArgs, context: dict) -> ToolResult:
        """执行语义检索"""
        try:
            # 1. 获取 repo 路径
            repo_path = context["repo_path"]
            repo_dir = context["repo_dir"]

            # 2. 加载或创建索引
            index = self._get_or_build_index(repo_path, repo_dir, context["base_commit"])

            # 3. 执行检索
            results = index.search(args.query, topk=args.topk, filters=args.filters)

            # 4. 格式化输出
            output = self._format_output(results, args.query)

            return ToolResult(
                success=True,
                data={
                    "query": args.query,
                    "topk": args.topk,
                    "returned": len(results),
                    "results": [r.to_dict() for r in results],
                    "metadata": index.metadata,
                },
                output=output,
                returncode=0,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                output=f"Search failed: {str(e)}",
                error=str(e),
                returncode=1,
            )

    def _format_output(self, results: list, query: str) -> str:
        """格式化为 LM 友好的输出"""
        lines = [f"Found {len(results)} relevant files for \"{query}\":\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.path} (score: {r.score:.2f})")
            symbol = r.symbol or "-"
            lines.append(f"   Lines {r.line_span['start']}-{r.line_span['end']} | {symbol}")
            lines.append("   +" + "-" * 40 + "+")
            for line in r.snippet.split("\n")[:3]:  # 最多显示 3 行
                lines.append(f"   | {line[:38]} |")
            lines.append("   +" + "-" * 40 + "+\n")
        return "\n".join(lines)

    def _get_or_build_index(self, repo_path: str, repo_dir: str, commit: str):
        """获取或构建索引（可缓存）"""
        embedder_id = self._sanitize_id(f"{self.embedder_provider}_{self.embedder_model}")
        index_key = f"{repo_dir}@{commit[:8]}@{embedder_id}"
        index_path = f"{self.index_root}/{repo_dir}/{commit[:8]}/{embedder_id}/embeddings.pt"

        if Path(index_path).exists():
            return Index.load(index_path)

        # 构建新索引
        return Index.build(repo_path, commit, save_to=index_path)
```

---

### 6.2 code_search 独立评测接口（IR-only baseline）

目标：提供一个**对外开放的测评入口**，只使用 `code_search` 做 LocBench 的定位评测（不走 Agent）。
该入口应与现有 LocBench 输出格式一致，便于直接复用 `evaluation/*` 的评测脚本。

**新增 runner（建议）**
- `minisweagent/run/extra/locbench_code_search.py`
- CLI 子命令：`mini-extra locbench-code-search`

**输入参数（建议）**
```
--dataset <path>        # LocBench JSONL
--repos-root <path>     # locbench_repos
--slice 0:100           # 可选
--topk-blocks 50        # 可选，block 检索数量（传给 code_search --topk）
--topk-files 10         # 可选，输出文件数量
--topk-modules 10       # 可选，输出模块数量
--topk-entities 50      # 可选，输出实体数量
--filters "lang:python" # 可选
--mapper-type ast       # 可选：ast / graph
--graph-index-dir <path> # 可选：Graph 索引目录（mapper=graph 时必需）
--index-root <path>     # 可选，默认 ~/.cache/mini-swe-agent/indexes
--force-rebuild         # 可选，强制重建索引
```

**评测逻辑（高层）**
1. 逐条读取 LocBench 实例
2. 读取 `repo` 字段得到 `repo_slug`，再生成 `repo_dir = repo_slug.replace("/", "_")`
3. 为该实例准备 `repo_path`（host worktree, checkout 到 `base_commit`）
4. 调用 `code_search`（query **只使用** `problem_statement`）
5. 将 block 结果按文件聚合分数（同一文件分数求和），按分数排序得到 `found_files`
6. 生成 mapper 输入 blocks（`file_path`、`start_line`、`end_line`）
   - `line_span` 为 1-based，传入 mapper 前做 -1 转换
7. 使用 AST/Graph mapper 生成 `found_modules` 与 `found_entities`
8. 输出 JSONL 与 bash-only runner 相同结构：
   - `instance_id` / `repo` / `base_commit`
   - `found_files` / `found_entities` / `found_modules`

**输出路径（建议）**
```
mini-swe-agent/locbench/loc_output/code_search/<embedding_provider>/<embedding_model>/loc_outputs_<timestamp>.jsonl
```

**约束**
- 评测入口仅使用 `code_search`，不依赖 Agent
- 输出格式必须与 bash-only baseline 完全一致
- 允许独立于 agent 调用（可用于回归测试/对比实验）
- 使用独立配置文件 `minisweagent/config/extra/code_search.yaml`
- `filters` 默认 `None`，如需过滤由 runner 显式传入（可依据数据集语言字段决定）
- Loc-Bench V1 JSONL 无 `language` 字段（默认不过滤更安全）
- `mapper_type` 默认 `ast`
- `mapper_type=graph` 需要 block 内包含 `span_ids`（滑动分块默认不提供）
- 映射器与工具逻辑已内置于 mini-swe-agent（不依赖外部工程）

**found_modules / found_entities 生成策略（MVP）**
- 使用 mini-swe-agent 内置 `ASTBasedMapper` / `GraphBasedMapper`（从 locbench-ir-based 复制）
- `ASTBasedMapper` 依赖 `file_path` + 0-based `start_line/end_line`
- `GraphBasedMapper` 依赖 `span_ids`（滑动分块不提供，需后续扩展）
- Graph mapper 依赖 `networkx` 与 graph 索引文件（可选能力）

**路径规范化（IR-only）**
- 复用同等逻辑的 `clean_file_path`（已内置）
- 目标：保证输出与 GT 路径格式一致（相对路径）

---

### 6.3 分块模块解耦设计

**结论**：建议分块逻辑独立为子模块，避免与索引/embedding 强耦合。

**理由**
- 分块策略可能扩展（滑动/函数/AST），需要独立演进
- 分块是纯文本处理，适合与 embedding/index 解耦
- 便于单测与可替换性（同一输入可对比不同 chunker）

**推荐结构**
```
minisweagent/tools/code_search/
  chunkers/
    __init__.py
    base.py          # Chunker Protocol
    sliding.py       # SlidingChunker
```

**设计模式**
- Strategy Pattern：不同 chunker 实现统一接口
- Factory/Registry：根据 YAML 配置选择 chunker 实例

**接口（示意）**
```python
from dataclasses import dataclass
from typing import Protocol

@dataclass
class Chunk:
    path: str
    language: str | None
    start_line: int
    end_line: int
    text: str

class Chunker(Protocol):
    name: str

    def chunk_file(self, path: str, text: str, language: str | None) -> list[Chunk]:
        ...
```

**使用方式（示意）**
```python
chunker = ChunkerFactory.from_config(cfg.chunker, cfg.chunk_size, cfg.overlap)
chunks = chunker.chunk_file(path, text, language)
```

**当前范围**
- 仅实现 `SlidingChunker`
- 其他 chunker 作为未来扩展点，不进入 MVP

---

### Embedding Provider 设计（V1: local only）

V1 采用 provider 抽象，但首发只实现本地模型（CodeRankEmbed）。
OpenAI API 等远端 provider 留作后续扩展。

**统一接口**
```python
from typing import Protocol

class BaseEmbedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...
```

**本地实现（示意）**
```python
class LocalEmbedder:
    def __init__(self, model_name: str, device: str = "cpu"):
        self.model_name = model_name
        # 这里用 sentence-transformers 或等价库加载 CodeRankEmbed

    def embed(self, texts: list[str]) -> list[list[float]]:
        # 返回 List[List[float]] 形式的向量
        ...
```

**约束**
- embedding provider 与模型名要写入索引 metadata
- index key 必须包含 provider + model（避免误用旧索引）

---

### Prompt 中的工具描述

在 `locbench_tools.yaml` 的 `system_template` 中：

```yaml
system_template: |
  ## Available Tools

  ### @tool code_search --query "<desc>" [--topk N] [--filters F]
  Semantic code search using embeddings.

  **When to use**:
  - You know what functionality you want, but not the exact function name
  - You want to find related code across multiple files
  - Text search (rg) returns too many or too few results

  **Examples**:
  - @tool code_search --query "centroid computation"
  - @tool code_search --query "handle user authentication" --topk 30
  - @tool code_search --query "database connection pool" --filters "lang:python"

  **Output format**: Ranked list of files with relevance scores and code snippets.

  ### Bash commands
  You can also use standard bash commands directly:
  - rg: exact text search
  - cat/nl/sed: read files
  - find: locate files

  **Note**: Use code_search for exploration, then use rg/cat to read the actual code.
```

---

### 索引管理策略

**索引位置（建议）**：
```
~/.cache/mini-swe-agent/indexes/
  └── numpy_numpy/
      └── abc123/
          └── local_CodeRankEmbed/
              ├── embeddings.pt
              ├── metadata.jsonl
              └── meta.json
```

**路径规范**
- `embedding_provider`/`embedding_model` 需参与路径命名
- 模型名中的特殊字符（如 `/`）统一替换为 `_`，避免路径异常

**缓存策略**：
- 索引按 `(repo_dir, commit)` 强隔离
- 不跨 commit 复用（保证行号/片段一致）
- MVP 阶段不做自动清理，手动管理磁盘空间

**构建参数**（可配置）：
```yaml
code_search:
  chunker: sliding          # 仅支持 sliding
  chunk_size: 800           # 块大小
  overlap: 200              # 重叠大小
  embedding_provider: local
  embedding_model: CodeRankEmbed
  embedding_batch_size: 64
  embedding_max_length: 4096
  embedding_device: cpu
  trust_remote_code: false
  index_root: ~/.cache/mini-swe-agent/indexes
  max_file_size: 524288
```

建议将该配置独立存放在 `minisweagent/config/extra/code_search.yaml`，
由 ToolAgent 与 IR-only runner 共同读取，避免参数漂移。

---

### 错误处理

| 错误场景 | 返回内容 | LM 建议操作 |
|---------|---------|-----------|
| 索引不存在 | `Index not found, building...` | 等待或重试 |
| 查询为空 | `query cannot be empty` | 检查参数格式 |
| 无结果 | `No results found` | 改用 rg 或调整查询 |
| 索引损坏 | `Index corrupted, rebuilding...` | 自动重建 |

---

### 与 bash 检索的协作

**推荐工作流**（在 Prompt 中告诉 LM）：
```
1. 先用 code_search 做语义探索，找到相关区域
2. 再用 rg 精确查找具体函数/变量
3. 最后用 cat/nl 阅读完整代码
```

**示例对话**：
```
User: 找到处理用户登录的代码

Agent:
```bash
@tool code_search --query "user login authentication" --topk 20
```

System: Found 12 results... 1. src/auth/login.py ...

Agent:
```bash
rg -n "def login" src/auth/
```

System: src/auth/login.py:45: def login_user(...)

Agent:
```bash
nl -ba src/auth/login.py | sed -n '45,80p'
```
```

---

## 7. 代码改动规划（并行扩展）

**新增文件**
- `minisweagent/agents/tool_agent.py`
- `minisweagent/tools/registry.py`
- `minisweagent/tools/code_search/`（工具包）
  - `tool.py`（code_search 实现）
  - `chunkers/`（分块策略）
- `minisweagent/run/extra/locbench_tools.py`
- `minisweagent/config/extra/locbench_tools.yaml`
- `minisweagent/run/extra/locbench_code_search.py`（IR-only 评测入口）
- `minisweagent/config/extra/code_search.yaml`（工具配置）

**不改动**
- `minisweagent/agents/default.py`
- 原 `locbench.py`/`locbench.yaml`

---

## 8. ToolAgent 设计要点

**实现方式（建议）**
- 不修改 `DefaultAgent`，可选择继承或复制其逻辑
- 若继承：减少重复但要确保不影响原版行为
- 若复制：完全隔离但维护成本更高
- 推荐：**轻度复用**通用组件（日志、进度条、config 读取）

**核心实现**（伪代码）：
```python
import time
import re
from pydantic import BaseModel
from jinja2 import Template

class ToolAgentConfig(BaseModel):
    system_template: str
    instance_template: str
    action_observation_template: str
    format_error_template: str
    tool_format_error_template: str
    tool_error_template: str
    step_limit: int = 120
    cost_limit: float = 3.0

class ToolAgent:
    def __init__(self, model, env, tool_registry, **kwargs):
        self.model = model
        self.env = env
        self.tool_registry = tool_registry
        self.config = ToolAgentConfig(**kwargs)
        self.messages = []
        self.extra_template_vars = {}

    def run(self, task: str, **kwargs):
        """运行 agent 直到完成"""
        self.extra_template_vars |= {"task": task, **kwargs}
        self.messages = []
        self._add_message("system", self._render(self.config.system_template))
        self._add_message("user", self._render(self.config.instance_template))

        while True:
            try:
                self._step()
            except NonTerminatingException as e:
                self._add_message("user", str(e))
            except TerminatingException as e:
                self._add_message("user", str(e))
                return type(e).__name__, str(e)

    def _step(self):
        """执行一步：query -> parse -> execute -> observe"""
        # 应在这里或 query 前检查 step_limit/cost_limit
        response = self.model.query(self.messages)
        action = self._parse_action(response)
        observation = self._get_observation(action)
        self._add_message("user", observation)

    def _parse_action(self, response: dict) -> dict:
        """解析动作，支持 bash 和 @tool"""
        actions = re.findall(r"```bash\s*\n(.*?)\n```", response["content"], re.DOTALL)
        if len(actions) != 1:
            raise FormatError(self._render(self.config.format_error_template, actions=actions))

        cmd = actions[0].strip()
        if cmd.startswith("@tool "):
            return {"type": "tool", "raw": cmd}
        return {"type": "bash", "command": cmd}

    def _get_observation(self, action: dict) -> str:
        """执行动作并返回 observation"""
        if action["type"] == "tool":
            result = self.tool_registry.execute(action["raw"], context=self.extra_template_vars)
            if not result.success:
                raise ToolError(self._render(self.config.tool_error_template,
                                            tool_name=action["raw"].split()[1],
                                            error=result.error))
            output = {"type": "tool", "output": result.output, "returncode": result.returncode}
            return self._render(self.config.action_observation_template, output=output)
        else:
            result = self.env.execute(action["command"])
            output = {"type": "bash", "output": result["output"], "returncode": result["returncode"]}
            return self._render(self.config.action_observation_template, output=output)

    def _render(self, template: str, **kwargs) -> str:
        """渲染模板"""
        template_vars = (self.config.model_dump() |
                        self.extra_template_vars |
                        self.model.get_template_vars() |
                        kwargs)
        return Template(template).render(**template_vars)

    def _add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content, "timestamp": time.time()})
```

**action_regex**（与 DefaultAgent 相同）：
```python
# 只识别 ```bash block
action_regex = r"```bash\s*\n(.*?)\n```"
```

---

## 9. ToolRegistry 设计要点

- 维护 `{tool_name -> tool_instance}` 映射
- 支持注册多个工具
- 统一日志与异常处理
- 对外提供 `execute(raw_command, context)`：
  - 解析 `@tool` 命令（使用 `parse_tool_command`）
  - 查找工具并调用 `tool.run(args, context)`
  - 返回 `ToolResult`

---

## 10. 环境一致性策略

**利用 Docker 挂载（默认单仓库）**

LocBench 默认按实例挂载单仓库（`repo_mount_mode: single`），示例：
```python
mount_arg = f"{repo_source_path}:/repos/{repo_dir}:ro"
```

宿主机 `repo_source_path` 与容器内 `/repos/<repo_dir>` 指向同一镜像仓库，但不保证 checkout 到同一 commit。

**容器启动流程**（`locbench.yaml:139-145`）：
```bash
mkdir -p /work &&
rm -rf /work/{instance_id} &&
git -c safe.directory=* clone --no-hardlinks /repos/{repo_dir} /work/{instance_id} &&
cd /work/{instance_id} &&
git checkout -q {base_commit}
```

容器内的代码已经 checkout 到指定 commit，宿主机 `repo_root` 可能仍停留在其它分支/commit。

**工具运行位置（首选）**：宿主机 + worktree

**工具获取代码路径**：
```python
# 在 locbench_tools.py 中传递 context
repo_slug = instance["repo"]
repo_dir = repo_slug.replace("/", "_")
context = {
    "repo_path": f"{worktree_root}/{repo_dir}@{instance['base_commit'][:8]}",  # 宿主机快照路径
    "repo_mount_path": f"/repos/{repo_dir}",  # 容器内路径，如 /repos/numpy_numpy
    "instance_id": instance["instance_id"],
    "base_commit": instance["base_commit"],
    "repo_slug": repo_slug,
    "repo_dir": repo_dir,
}

# 传递给 ToolRegistry
tool_registry.execute("@tool code_search ...", context=context)
```

**一致性要求（必须保证）**：
- 工具看到的代码必须与 `base_commit` 对齐
- 不能直接读取 `repo_root` 的工作区（commit 可能不一致）
- 工具输出的 `path` 必须是**相对路径**（供容器内 bash 读取）

**推荐做法（首选）**：
- 方案 A：在宿主机为每个实例创建 `git worktree` 到 `base_commit`
- 方案 B：工具在容器内运行，直接读取 `/work/<instance_id>`（后续再做）

示例 worktree 目录结构：
```
/Users/chz/code/locbench/mini-swe-agent/locbench/tool_worktrees/
  └── numpy_numpy@abc123/  # repo_dir@commit
```

**未来扩展（可选）**：
- 如需在容器内运行工具，可用 RPC
- 当前阶段不需要

---

## 11. Prompt 设计

在 `locbench_tools.yaml` 中新增工具说明，保持单步协议。

**system_template（新增工具说明）**：
```yaml
system_template: |
  You are a helpful assistant that can interact with a computer to localize code.

  ## Available Tools

  ### Code Search (@tool code_search)
  Semantic search for code by functionality.

  Usage: @tool code_search --query "<description>" [--topk <number>]

  Example: @tool code_search --query "centroid computation in grid" --topk 20

  Returns: Ranked list of files with code snippets and relevance scores.

  ### Standard Bash Commands
  You can also use standard bash commands:
  - rg: exact text search
  - cat/nl/sed: read files
  - find: locate files

  ## Workflow Recommendation
  1. If the task description does not specify exact file paths, use @tool code_search first
  2. Use rg/grep for exact text search
  3. Use cat/nl to read specific files
  4. Output final JSON when done

  ## Action Format
  Your response must contain exactly ONE bash code block.
  Include a THOUGHT section before your command.
  If you call a tool, the bash block must contain only a single `@tool ...` line.

  <format_example>
  THOUGHT: Your reasoning here

  ```bash
  @tool code_search --query "..."
  ```
  </format_example>
```

**action_observation_template（支持工具输出）**：
```yaml
action_observation_template: |
  {% if output.type == "tool" -%}
  <tool_result>
  {{ output.output }}
  </tool_result>
  {%- else -%}
  <returncode>{{output.returncode}}</returncode>
  {% if output.output | length < 10000 -%}
  <output>
  {{ output.output -}}
  </output>
  {%- else -%}
  <warning>
  The output of your last command was too long.
  Please try a different command that produces less output.
  </warning>
  {%- set elided_chars = output.output | length - 10000 -%}
  <output_head>
  {{ output.output[:5000] }}
  </output_head>
  <elided_chars>
  {{ elided_chars }} characters elided
  </elided_chars>
  <output_tail>
  {{ output.output[-5000:] }}
  </output_tail>
  {%- endif -%}
  {%- endif %}
```

**tool_format_error_template（新增）**：
```yaml
tool_format_error_template: |
  Invalid tool command format: {{ command }}

  Usage: @tool <tool_name> [--arg1 value1] [--arg2 value2]

  Available tools:
  {% for tool in available_tools -%}
  - {{ tool.name }}: {{ tool.description }}
  {% endfor %}

  Please try again with the correct format.
```

**tool_error_template（新增）**：
```yaml
tool_error_template: |
  Tool execution failed: {{ tool_name }}

  Error: {{ error }}

  Please try:
  1. Fix your command arguments
  2. Use bash commands instead (rg, grep, cat, etc.)
```

---

## 12. 输出与评估

输出格式保持一致：
- `found_files`
- `found_entities`
- `found_modules`

评估脚本不变：
`evaluation/simple_eval.py` / `evaluation/compute_full_metrics.py`

补充说明：
- code_search IR-only 输出保持相同 schema，可直接用同一评测脚本对比

---

## 13. 计划与里程碑

**M1：工具框架**
- ToolAgent + ToolRegistry + locbench_tools.py

**M2：工具接入**
- 接入 code_search，输出 JSON

**M2.5：IR-only 评测入口**
- locbench_code_search.py（单独评测 code_search）

**M3：实验与对比**
- 与 bash-only baseline 对比
- 汇总准确率与成本

---

## 14. 风险与对策

- **协议失配** → 强化单步协议与格式校验
- **索引不一致** → 绑定 `commit` 作为索引 key
- **性能瓶颈** → 缓存/复用索引
- **输出不稳定** → tool 输出结构固定化

---

## 15. CLI 入口建议

新增子命令：
- `mini-extra locbench-tools`
- `mini-extra locbench-code-search`

并保留：
- `mini-extra locbench`（bash-only baseline）

---

## 附录 A：目录结构

```
minisweagent/
├── agents/
│   ├── default.py              # 不动（bash-only baseline）
│   ├── interactive.py           # 不动
│   └── tool_agent.py           # 新增（不改原版）
├── tools/                       # 新目录
│   ├── __init__.py
│   ├── base.py                 # Tool Protocol
│   ├── registry.py             # ToolRegistry
│   └── code_search/             # code_search 工具包
│       ├── __init__.py
│       ├── tool.py              # 具体工具实现
│       └── chunkers/            # 分块策略
│           ├── __init__.py
│           ├── base.py
│           └── sliding.py
├── run/extra/
│   ├── locbench.py             # 不动（bash-only baseline）
│   ├── locbench_tools.py       # 新增（不改原版）
│   └── locbench_code_search.py # IR-only 评测入口
└── config/extra/
    ├── locbench.yaml           # 不动
    ├── locbench_tools.yaml     # 新增
    └── code_search.yaml        # code_search 配置（可复用）

locbench/
└── outputs/
    ├── bash_baseline/          # baseline 输出
    │   └── {model}/{timestamp}/
    └── tools/                  # tools 版本输出
        └── {model}/{timestamp}/
```

**无依赖关系**：
- `tool_agent.py` 不 import `default.py`
- `locbench_tools.py` 不 import `locbench.py`
- 两套代码可以独立删除，互不影响
- 可以复制辅助函数（如 `extract_json_payload`），但不建立 import 依赖

---

## 附录 B：工具调用流程

```
                    ┌─────────────┐
                    │   Agent     │
                    └──────┬──────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  Bash Cmd   │ │ @tool cmd   │ │ @tool cmd   │
    │ rg "foo"    │ │ search ...  │ │ find ...    │
    └─────────────┘ └──────┬──────┘ └──────┬──────┘
                           │                │
                    ┌──────▼──────┐        │
                    │ ToolRegistry│        │
                    └──────┬──────┘        │
                           │                │
           ┌───────────────┼───────────────┤
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  Docker Env │ │  code_search │ │  Other Tool │
    │ (subprocess)│ │  (宿主机)    │ │  (宿主机)   │
    └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
           │                │                │
           └────────────────┴────────────────┘
                           │
                    ┌──────▼──────┐
                    │ Observation  │
                    │ (格式化输出) │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Messages   │
                    │  (历史记录)  │
                    └─────────────┘
```

**关键点**：
- Bash 命令走容器内的 subprocess（`env.execute()`）
- 工具命令走宿主机的 ToolRegistry
- 两种输出统一格式化为 observation
- Observation 加入 messages 历史供 LM 参考

---

## 附录 C：异常处理体系

**异常分类**：
```python
# 非终止异常（可恢复，反馈给 LM）
class NonTerminatingException(Exception):
    """Agent 可处理的异常"""

class FormatError(NonTerminatingException):
    """动作格式错误"""

class ToolFormatError(NonTerminatingException):
    """工具命令格式错误"""

class ToolExecutionError(NonTerminatingException):
    """工具执行失败（可重试）"""

# 终止异常（结束运行）
class TerminatingException(Exception):
    """终止 agent 运行"""

class Submitted(TerminatingException):
    """LM 声称任务完成"""

class LimitsExceeded(TerminatingException):
    """超出步数/成本限制"""
```

**处理流程**：
```python
def run(self, task: str, **kwargs):
    while True:
        try:
            self._step()
        except NonTerminatingException as e:
            # 将错误信息反馈给 LM，让 LM 自我修正
            self._add_message("user", str(e))
        except TerminatingException as e:
            # 结束运行
            return type(e).__name__, str(e)
```

**错误恢复策略**：
| 错误类型 | 处理方式 |
|---------|---------|
| 格式错误（无 bash block） | 提示正确格式，等待 LM 重试 |
| 工具格式错误 | 提示工具用法，等待 LM 重试 |
| 工具执行失败 | 提示错误信息，建议改用 bash |
| 超时 | 提示命令超时，建议简化命令 |

---

## 附录 D：工具参数解析

`@tool` 命令使用 argparse 风格的参数解析：

```python
import shlex
from typing import Any

def parse_tool_command(command: str) -> tuple[str, dict[str, Any]]:
    """解析 @tool 命令

    Args:
        command: 原始命令，如 "@tool code_search --query foo --topk 20"

    Returns:
        (tool_name, args_dict)
    """
    parts = shlex.split(command[len("@tool "):])
    tool_name = parts[0]

    # 简单的 key=value 解析
    args = {}
    i = 1
    while i < len(parts):
        if parts[i].startswith("--"):
            key = parts[i][2:]
            if i + 1 < len(parts) and not parts[i + 1].startswith("--"):
                args[key] = parts[i + 1]
                i += 2
            else:
                args[key] = True
                i += 1
        else:
            i += 1

    return tool_name, args

# 示例
parse_tool_command("@tool code_search --query centroid --topk 20")
# 返回: ("code_search", {"query": "centroid", "topk": "20"})
# 注意：需要在 ToolRegistry 或 Tool.run 内做类型转换（如 topk -> int）
```
