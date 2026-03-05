# mini-swe-agent Analysis and Documentation

---

## Chapter 1: Architecture Overview

### 1.1 System Structure

mini-swe-agent is organized into four core modules:

```
┌─────────────────────────────────────────────────────┐
│              mini-swe-agent Architecture            │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐     │
│  │ agents/  │    │models/   │    │environ-  │     │
│  │          │    │          │    │ments/    │     │
│  │ • Agent  │    │• Model   │    │• Env     │     │
│  │ • Default│    │• OpenAI  │    │• Terminal│     │
│  │ • Inter- │    │• Local   │    │          │     │
│  │ active   │    │          │    │          │     │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘     │
│       │               │                │            │
│       └───────────────┴────────────────┘            │
│                      │                               │
│              ┌───────▼───────┐                       │
│              │   run/        │                       │
│              │  (entry pts)  │                       │
│              └───────────────┘                       │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**Key Design Principle**: Each module provides interchangeable implementations of a single responsibility.

---

### 1.2 Core Abstraction: The Agent Loop

```
┌─────────────────────────────────────────────────────┐
│                   Agent Main Loop                   │
├─────────────────────────────────────────────────────┤
│                                                     │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐      │
│   │  Think   │───▶│  Act     │───▶│ Observe  │      │
│   │(model.q) │    │(env.exe) │    │(feedback)│      │
│   └──────────┘    └──────────┘    └────┬─────┘      │
│        ▲                             │             │
│        └──────────────────────────────┘             │
│              (feedback loop)                         │
└─────────────────────────────────────────────────────┘
```

**Pseudocode (Actual)**:
```python
while not done:
    response = agent.query()      # Consult LLM
    action = agent.parse(response)
    observation = agent.execute_action(action) # Execute & Capture
    agent.add_message("user", observation)
```

---

### 1.3 Component Interactions

| Component | Responsibility | Example Implementations |
|-----------|----------------|------------------------|
| **Agent** | Orchestrates the loop | `DefaultAgent`, `InteractiveAgent` |
| **Model** | LLM interface | `OpenAIModel`, `LocalModel` |
| **Environment** | Task execution | `TerminalEnv`, `SWEEnv` |

---

## Chapter 2: Protocols & Interfaces

### 2.1 Protocol-First Design

mini-swe-agent uses Python protocols to define clean interfaces:

```python
# src/minisweagent/__init__.py

from typing import Protocol, runtime_checkable

@runtime_checkable
class Model(Protocol):
    def query(self, messages: list[dict], **kwargs) -> dict: ...

@runtime_checkable  
class Environment(Protocol):
    def execute(self, command: str, cwd: str = "") -> dict: ...
    def get_template_vars(self) -> dict: ...

@runtime_checkable
class Agent(Protocol):
    def run(self, task: str, **kwargs) -> tuple[str, str]: ...
```

**Why Protocols?**
- No inheritance required — any class matching the interface works
- Enables duck typing across different implementations
- Easy to extend without modifying core code

---

### 2.2 Model Interface Deep Dive

```python
class Model(Protocol):
    """Actual interface for models."""
    cost: float
    n_calls: int
    
    def query(self, messages: list[dict], **kwargs) -> dict:
        """Return dict with 'content' and 'extra' info."""
        ...
```

**Implementation Example**:
```python
class LitellmModel:
    def query(self, messages: list[dict], **kwargs) -> dict:
        response = litellm.completion(model=self.model, messages=messages, ...)
        return {"content": response.choices[0].message.content, "extra": {...}}
```

---

### 2.3 Environment Interface Deep Dive

```python
class Environment(Protocol):
    """Interface for all task environments."""
    
    def execute(self, command: str, cwd: str = "") -> dict:
        """Run bash command and return {'output', 'returncode'}."""
        ...
    
    def get_template_vars(self) -> dict:
        """Provide context for prompt rendering."""
        ...
```

**Implementation Example**:
```python
class TerminalEnv:
    def execute(self, command: str, cwd: str = "") -> dict:
        result = subprocess.run(
            command, shell=True, text=True, capture_output=True
        )
        return {"output": result.stdout, "returncode": result.returncode}
```

---

### 2.4 Agent Interface Deep Dive

```python
class Agent(Protocol):
    """Interface for all agent implementations."""
    
    def run(self, task: str) -> None:
        """Execute the agent loop for the given task."""
        while True:
            self.step()
        ...
```

**Implementation Example**:
```python
class DefaultAgent:
    def run(self, task: str) -> None:
        while True:
            self.step()

    def step(self):
        response = self.query()
        action = self.parse_action(response)
        output = self.execute_action(action)
        self.add_message("user", self.render(..., output=output))
```

---

## Chapter Summary

| 章节 | 内容 | 字数预估 |
|------|------|---------|
| **第 1 章** | 架构概览、核心抽象、组件交互 | ~800 字 |
| **第 2 章** | Protocol 设计、Model/Env/Agent 接口详解 | ~1000 字 |

---

## Chapter 3: Tool System

### 3.1 Tool Protocol Design

The tool system in mini-swe-agent follows a protocol-first approach, enabling extensibility and modularity:

```python
# src/minisweagent/tools/base.py

from dataclasses import dataclass
from typing import Any, Protocol

@dataclass
class ToolResult:
    """Tool execution result."""
    success: bool
    data: dict[str, Any]
    output: str
    error: str | None = None
    returncode: int = 0

class Tool(Protocol):
    """Tool protocol definition."""
    name: str
    description: str

    def run(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult: ...
```

**Key Design Elements**:
- **ToolResult dataclass**: Structured return type with success flag, output data, and error handling
- **Tool Protocol**: Minimal interface requiring only `name`, `description`, and `run()` method
- **Context parameter**: Allows tools to access shared state and environment information

---

### 3.2 Tool Registry Pattern

Tools are registered and managed through a central registry:

```python
# src/minisweagent/tools/registry.py

class ToolRegistry:
    """Central registry for all available tools."""
    
    def __init__(self):
        self._tools: dict[str, Tool] = {}
    
    def register(self, name: str, tool: Tool) -> None:
        """Register a tool with the given name."""
        self._tools[name] = tool
    
    def get(self, name: str) -> Tool:
        """Retrieve a tool by name."""
        return self._tools[name]
    
    def list_tools(self) -> list[dict]:
        """Return metadata for all registered tools."""
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
        ]
```

---

### 3.3 Core Tool Categories

mini-swe-agent provides several categories of built-in tools:

| Category | Purpose | Example Tools |
|----------|---------|---------------|
| **File Operations** | Read/write/search files | `read_file`, `write_file`, `search_files` |
| **Code Analysis** | Parse and understand code | `list_symbols`, `parse_ast` |
| **Execution** | Run commands and processes | `execute_command`, `run_tests` |
| **Navigation** | Explore project structure | `list_files`, `find_file` |

---

### 3.4 Tool Calling Mechanism

Tools are invoked through the model's function calling interface:

```python
# Pseudocode for tool invocation flow

def execute_tool_call(model_response, tool_registry):
    if model_response.get('tool_calls'):
        for tool_call in model_response['tool_calls']:
            tool_name = tool_call['function']['name']
            arguments = json.loads(tool_call['function']['arguments'])
            
            tool = tool_registry.get(tool_name)
            result = tool.run(arguments, context)
            
            # Add tool result to conversation history
            add_tool_response(tool_call['id'], result.output)
```

---

## Chapter 4: Environment Implementations

### 4.1 Environment Protocol

The environment protocol defines the interface for task execution:

```python
# src/minisweagent/__init__.py (simplified)

class Environment(Protocol):
    """Interface for all environment implementations."""
    
    def apply_patch(self, patch: str) -> ToolResult: ...
    def get_file_list(self) -> list[str]: ...
    def run_tests(self, test_command: str) -> ToolResult: ...
```

---

### 4.2 Environment Types

mini-swe-agent supports multiple environment backends:

```python
# src/minisweagent/environments/__init__.py

_ENVIRONMENT_MAPPING = {
    "docker": "minisweagent.environments.docker.DockerEnvironment",
    "singularity": "minisweagent.environments.singularity.SingularityEnvironment",
    "local": "minisweagent.environments.local.LocalEnvironment",
    "swerex_docker": "minisweagent.environments.extra.swerex_docker.SwerexDockerEnvironment",
    "swerex_modal": "minisweagent.environments.extra.swerex_modal.SwerexModalEnvironment",
    "bubblewrap": "minisweagent.environments.extra.bubblewrap.BubblewrapEnvironment",
}
```

**Environment Selection**:
- **Docker**: Isolated containers for reproducible execution
- **Singularity**: HPC-compatible container runtime
- **Local**: Direct filesystem access for development
- **SwrEx**: SWE-bench specific environments

---

### 4.3 Environment Lifecycle

```
┌─────────────────────────────────────────────────────┐
│              Environment Lifecycle                   │
├─────────────────────────────────────────────────────┤
│                                                     │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐     │
│   │  Setup   │───▶│ Execute  │───▶│ Cleanup  │     │
│   │(init)    │    │(run)     │    │(close)   │     │
│   └──────────┘    └──────────┘    └──────────┘     │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**Lifecycle Methods**:
1. **Setup**: Clone repository, install dependencies, configure environment
2. **Execute**: Run agent loop, apply patches, execute tests
3. **Cleanup**: Close containers, remove temporary files

---

### 4.4 Docker Environment Deep Dive

```python
# src/minisweagent/environments/docker.py (simplified)

class DockerEnvironment:
    """Uses subprocess to call docker CLI directly."""

    def _start_container(self):
        cmd = [self.config.executable, "run", "-d", ...]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.container_id = result.stdout.strip()

    def execute(self, command, cwd="", timeout=None):
        # Wraps command in docker exec
        cmd = [self.config.executable, "exec", "-w", cwd, 
               self.container_id, "bash", "-lc", command]
        result = subprocess.run(cmd, text=True, capture_output=True)
        return {"output": result.stdout, "returncode": result.returncode}
```

---

### 4.5 Environment Factory Pattern

The environment factory provides a clean interface for creating environments:

```python
# src/minisweagent/environments/__init__.py

def get_environment(config: dict, *, default_type: str = "") -> Environment:
    """Factory function to create environment from config."""
    config = copy.deepcopy(config)
    environment_class = config.pop("environment_class", default_type)
    return get_environment_class(environment_class)(**config)

def get_environment_class(spec: str) -> type[Environment]:
    """Get environment class by specification string."""
    full_path = _ENVIRONMENT_MAPPING.get(spec, spec)
    try:
        module_name, class_name = full_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        return getattr(module, class_name)
    except (ValueError, ImportError, AttributeError):
        msg = f"Unknown environment type: {spec}"
        raise ValueError(msg)
```

---

## Chapter Summary

| 章节 | 内容 | 字数预估 |
|------|------|---------|
| **第 3 章** | Tool Protocol、Registry 模式、核心工具分类、Tool calling 机制 | ~1000 字 |
| **第 4 章** | Environment Protocol、环境类型对比、生命周期管理、Docker 实现详解 | ~1200 字 |

---


## Chapter 5: Agent Implementations

### 5.1 DefaultAgent Architecture

The `DefaultAgent` is the core agent implementation that orchestrates the main loop:

```python
# src/minisweagent/agents/default.py

class DefaultAgent:
    def __init__(self, model: Model, env: Environment, *, config_class: type = AgentConfig, **kwargs):
        self.config = config_class(**kwargs)
        self.messages: list[dict] = []
        self.model = model
        self.env = env
```

**Key Components**:
- **Model**: Language model interface for generating responses
- **Environment**: Execution environment for running actions
- **Config**: Configuration object with templates and limits

---

### 5.2 The Agent Loop

The main agent loop follows a simple pattern:

```python
def run(self, task: str, **kwargs) -> tuple[str, str]:
    """Run step() until agent is finished. Return exit status & message"""
    self.extra_template_vars |= {"task": task, **kwargs}
    self.messages = []
    self.add_message("system", self.render_template(self.config.system_template))
    self.add_message("user", self.render_template(self.config.instance_template))
    
    while True:
        try:
            self.step()
        except NonTerminatingException as e:
            self.add_message("user", str(e))
        except TerminatingException as e:
            self.add_message("user", str(e))
            return type(e).__name__, str(e)
```

**Flow**:
1. Initialize conversation with system prompt and task description
2. Loop: query model → parse action → execute → observe
3. Handle exceptions (recoverable vs terminating)
4. Return final status when done

---

### 5.3 Step Function

Each step consists of three phases:

```python
def step(self) -> dict:
    """Query the LM, execute the action, return the observation."""
    return self.get_observation(self.query())

def query(self) -> dict:
    """Query the model and return the response."""
    # Check limits
    if 0 < self.config.step_limit <= self.model.n_calls:
        raise LimitsExceeded()
    
    response = self.model.query(self.messages)
    self.add_message("assistant", **response)
    return response

def get_observation(self, response: dict) -> dict:
    """Execute the action and return the observation."""
    output = self.execute_action(self.parse_action(response))
    observation = self.render_template(self.config.action_observation_template, output=output)
    self.add_message("user", observation)
    return output
```

---

### 5.4 Action Parsing

Actions are extracted using regex patterns:

```python
def parse_action(self, response: dict) -> dict:
    """Parse the action from the message. Returns the action."""
    actions = re.findall(self.config.action_regex, response["content"], re.DOTALL)
    normalized_actions = [_normalize_action_block(action) for action in actions]
    unique_actions = list(dict.fromkeys(normalized_actions))
    
    if len(unique_actions) == 1:
        return {"action": unique_actions[0], **response}
    raise FormatError(self.render_template(self.config.format_error_template, actions=actions))
```

**Action format**: Actions must be wrapped in ````bash\n...\n``` blocks.

---

### 5.5 Exception Hierarchy

mini-swe-agent uses a two-level exception hierarchy:

```python
class NonTerminatingException(Exception):
    """Raised for conditions that can be handled by the agent."""

class FormatError(NonTerminatingException):
    """Raised when the LM's output is not in the expected format."""

class ExecutionTimeoutError(NonTerminatingException):
    """Raised when the action execution timed out."""

class TerminatingException(Exception):
    """Raised for conditions that terminate the agent."""

class Submitted(TerminatingException):
    """Raised when the LM declares that the agent has finished its task."""

class LimitsExceeded(TerminatingException):
    """Raised when the agent has reached its cost or step limit."""
```

---

### 5.6 Task Completion Detection

The agent detects task completion via special markers:

```python
def has_finished(self, output: dict[str, str]):
    """Raises Submitted exception with final output if the agent has finished its task."""
    lines = output.get("output", "").lstrip().splitlines(keepends=True)
    if lines and lines[0].strip() in ["MINI_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]:
        raise Submitted("".join(lines[1:]))
```

---

## Chapter 6: Model Interfaces

### 6.1 Model Protocol

The model protocol defines the interface for all language model backends:

```python
# src/minisweagent/__init__.py (simplified)

class Model(Protocol):
    """Interface for all model implementations."""
    
    def query(self, messages: list[dict]) -> dict: ...
    def get_template_vars(self) -> dict: ...
    
    @property
    def cost(self) -> float: ...
    @property
    def n_calls(self) -> int: ...
```

---

### 6.2 Model Factory Pattern

Models are created through a factory pattern:

```python
# src/minisweagent/models/__init__.py

def get_model(input_model_name: str | None = None, config: dict | None = None) -> Model:
    """Get an initialized model object from any kind of user input or settings."""
    resolved_model_name = get_model_name(input_model_name, config)
    if config is None:
        config = {}
    config = copy.deepcopy(config)
    config["model_name"] = resolved_model_name

    model_class = get_model_class(resolved_model_name, config.pop("model_class", ""))

    if (from_env := os.getenv("MSWEA_MODEL_API_KEY")) and not str(type(model_class)).endswith("DeterministicModel"):
        config.setdefault("model_kwargs", {})["api_key"] = from_env

    return model_class(**config)
```

---

### 6.3 Model Class Mapping

The system supports multiple model providers:

```python
_MODEL_CLASS_MAPPING = {
    "anthropic": "minisweagent.models.anthropic.AnthropicModel",
    "litellm": "minisweagent.models.litellm_model.LitellmModel",
    "litellm_response": "minisweagent.models.litellm_response_api_model.LitellmResponseAPIModel",
    "openrouter": "minisweagent.models.openrouter_model.OpenRouterModel",
    "portkey": "minisweagent.models.portkey_model.PortkeyModel",
    "requesty": "minisweagent.models.requesty_model.RequestyModel",
    "chatanywhere": "minisweagent.models.chatanywhere_model.ChatAnywhereModel",
    "deterministic": "minisweagent.models.test_models.DeterministicModel",
}
```

---

### 6.4 Global Model Statistics

The system tracks global model usage:

```python
class GlobalModelStats:
    """Global model statistics tracker with optional limits."""

    def __init__(self):
        self._cost = 0.0
        self._n_calls = 0
        self._lock = threading.Lock()
        self.cost_limit = float(os.getenv("MSWEA_GLOBAL_COST_LIMIT", "0"))
        self.call_limit = int(os.getenv("MSWEA_GLOBAL_CALL_LIMIT", "0"))

    def add(self, cost: float) -> None:
        """Add a model call with its cost, checking limits."""
        with self._lock:
            self._cost += cost
            self._n_calls += 1
        if 0 < self.cost_limit < self._cost or 0 < self.call_limit < self._n_calls + 1:
            raise RuntimeError(f"Global cost/call limit exceeded")

    @property
    def cost(self) -> float:
        return self._cost

    @property
    def n_calls(self) -> int:
        return self._n_calls
```

---

### 6.5 Model Selection Priority

Model name resolution follows this priority:
1. Explicit `input_model_name` parameter
2. `config["model_name"]` from configuration
3. `MSWEA_MODEL_NAME` environment variable

---

## Chapter Summary

| 章节 | 内容 | 字数预估 |
|------|------|---------|
| **第 5 章** | DefaultAgent 架构、Agent Loop、Step 函数、Action Parsing、Exception Hierarchy | ~1200 字 |
| **第 6 章** | Model Protocol、Factory Pattern、Model Mapping、Global Stats、Selection Priority | ~1000 字 |

---

## Chapter 7: Run Scripts & Entry Points

### 7.1 Overview

Run scripts serve as the entry points for different use cases, combining agents, models, and environments into complete workflows.

```
┌─────────────────────────────────────────────────────┐
│              Run Scripts Architecture               │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐     │
│  │hello_    │    │   mini   │    │mini_     │     │
│  │world.py  │    │   .py    │    │extra.py  │     │
│  │          │    │          │    │          │     │
│  │Simple    │    │CLI      │    │Sub-      │     │
│  │demo      │    │tool     │    │commands  │     │
│  └──────────┘    └──────────┘    └──────────┘     │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

### 7.2 Hello World — Simplest Entry Point

The `hello_world.py` script demonstrates the minimal setup:

```python
# src/minisweagent/run/hello_world.py

import os
from pathlib import Path

import typer
import yaml

from minisweagent import package_dir
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel

app = typer.Typer()

@app.command()
def main(
    task: str = typer.Option(..., "-t", "--task", help="Task/problem statement"),
    model_name: str = typer.Option(
        os.getenv("MSWEA_MODEL_NAME"),
        "-m", "--model",
        help="Model name (defaults to MSWEA_MODEL_NAME env var)"
    ),
) -> DefaultAgent:
    agent = DefaultAgent(
        LitellmModel(model_name=model_name),
        LocalEnvironment(),
        **yaml.safe_load(Path(package_dir / "config" / "default.yaml").read_text())["agent"],
    )
    agent.run(task)
    return agent

if __name__ == "__main__":
    app()
```

**Key Components**:
1. **Typer CLI**: Defines command-line interface with options
2. **Model Selection**: Uses `LitellmModel` with configurable model name
3. **Environment**: Uses `LocalEnvironment` for local execution
4. **Configuration**: Loads agent config from YAML file

---

### 7.3 Mini — Main CLI Tool

The `mini.py` script provides the main user-facing CLI:

```python
# src/minisweagent/run/mini.py (simplified)

@app.command()
def main(
    visual: bool = typer.Option(False, "-v", "--visual"),  # Pager-style UI
    model_name: str | None = typer.Option(None, "-m", "--model"),
    task: str | None = typer.Option(None, "-t", "--task"),
    yolo: bool = typer.Option(False, "-y", "--yolo"),  # Run without confirmation
    cost_limit: float | None = typer.Option(None, "-l", "--cost-limit"),
    config_spec: Path = typer.Option(DEFAULT_CONFIG, "-c", "--config"),
    output: Path | None = typer.Option(DEFAULT_OUTPUT, "-o", "--output"),
) -> Any:
    # Load configuration
    config = yaml.safe_load(config_path.read_text())
    
    # Get task from CLI or interactive prompt
    if not task:
        task = prompt_session.prompt(...)
    
    # Apply overrides
    if yolo:
        config.setdefault("agent", {})["mode"] = "yolo"
    if cost_limit is not None:
        config.setdefault("agent", {})["cost_limit"] = cost_limit
    
    # Create model and environment
    model = get_model(model_name, config.get("model", {}))
    env = LocalEnvironment(**config.get("environment", {}))
    
    # Select agent type based on visual flag
    agent_class = InteractiveAgent if not visual else TextualAgent
    agent = agent_class(model, env, **config.get("agent", {}))
    
    # Run agent
    exit_status, result = agent.run(task)
```

**Features**:
- **Dual UI modes**: Simple REPL (`mini`) vs pager-style (`mini -v`)
- **Interactive task input**: Uses `prompt_toolkit` for rich terminal input
- **Configuration merging**: CLI args override YAML config
- **Trajectory saving**: Saves execution trace to JSON file

---

### 7.4 Mini-Extra — Subcommand Router

The `mini_extra.py` script routes to various subcommands:

```python
# src/minisweagent/run/mini_extra.py (simplified)

subcommands = [
    ("minisweagent.run.extra.config", ["config"], "Manage the global config file"),
    ("minisweagent.run.extra.inspector", ["inspect", "i"], "Run inspector"),
    ("minisweagent.run.extra.github_issue", ["github-issue", "gh"], "Run on GitHub issue"),
    ("minisweagent.run.extra.locbench", ["locbench"], "Evaluate on LocBench"),
    ("minisweagent.run.extra.swebench", ["swebench"], "Evaluate on SWE-bench"),
]

def main():
    args = sys.argv[1:]
    
    if len(args) == 0:
        return Console().print(get_docstring())
    
    for module_path, aliases, _ in subcommands:
        if args[0] in aliases:
            return import_module(module_path).app(args[1:])
```

**Subcommand Categories**:
| Category | Subcommands | Purpose |
|----------|-------------|---------|
| **Configuration** | `config` | Manage global settings |
| **Inspection** | `inspect`, `i` | Browse execution trajectories |
| **GitHub** | `github-issue`, `gh` | Solve GitHub issues |
| **Benchmarks** | `locbench`, `swebench` | Run evaluations |

---

### 7.5 Extra Subcommands

The `extra/` directory contains specialized run scripts:

```
src/minisweagent/run/extra/
├── config.py           # Configuration management
├── inspector.py        # Trajectory browser
├── github_issue.py     # GitHub issue solver
├── locbench.py         # LocBench evaluation (bash mode)
├── locbench_tools.py   # LocBench with tools
├── locbench_code_search.py  # LocBench code search only
├── swebench.py         # SWE-bench batch evaluation
└── swebench_single.py  # Single instance evaluation
```

**Example: GitHub Issue Solver**
```python
# src/minisweagent/run/extra/github_issue.py (simplified)

@app.command()
def main(
    issue_url: str = typer.Argument(..., help="GitHub issue URL"),
    model_name: str | None = typer.Option(None, "-m", "--model"),
):
    # Parse issue URL to extract repo and issue number
    # Clone repository
    # Create agent with appropriate tools
    # Run agent to solve issue
```

---

### 7.6 Configuration Loading Pattern

All run scripts follow a consistent configuration pattern:

```python
# Typical configuration loading sequence

import yaml
from pathlib import Path

# 1. Load base config from YAML
config = yaml.safe_load(config_path.read_text())

# 2. Apply CLI overrides
if cli_option:
    config.setdefault("section", {})["key"] = value

# 3. Create components with merged config
model = get_model(model_name, config.get("model", {}))
env = Environment(**config.get("environment", {}))
agent = Agent(model, env, **config.get("agent", {}))
```

---

### 7.7 CLI Design Patterns

mini-swe-agent uses several CLI design patterns:

**Pattern 1: Typer-based Commands**
```python
app = typer.Typer()

@app.command()
def main(option: str = typer.Option(...)):
    ...
```

**Pattern 2: Subcommand Routing**
```python
def main():
    for module_path, aliases, _ in subcommands:
        if sys.argv[1] in aliases:
            return import_module(module_path).app(sys.argv[2:])
```

**Pattern 3: Environment Variable Defaults**
```python
model_name: str = typer.Option(
    os.getenv("MSWEA_MODEL_NAME"),  # Default from env var
    "-m", "--model"
)
```

---

## Chapter Summary

| 章节 | 内容 | 字数预估 |
|------|------|---------|
| **第 7 章** | Run Scripts 架构、Hello World、Mini CLI、Subcommand Router、配置加载模式 | ~1200 字 |

---