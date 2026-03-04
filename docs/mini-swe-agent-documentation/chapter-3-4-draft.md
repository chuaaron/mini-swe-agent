# mini-swe-agent Documentation — Chapter 3-4 Draft

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
    def __init__(self, image: str, working_dir: Path):
        self.image = image
        self.working_dir = working_dir
        self.container = None
    
    def setup(self):
        """Start Docker container."""
        self.container = docker.from_env().containers.run(
            self.image,
            detach=True,
            working_dir=str(self.working_dir)
        )
    
    def apply_patch(self, patch: str) -> ToolResult:
        """Apply a diff patch to the working directory."""
        result = self.container.exec_run(
            ["bash", "-c", f"git apply <(echo '{patch}')"]
        )
        return ToolResult(
            success=result.exit_code == 0,
            output=result.output.decode(),
            error=result.stderr.decode() if result.exit_code != 0 else None
        )
    
    def run_tests(self, test_command: str) -> ToolResult:
        """Run the test command in the container."""
        result = self.container.exec_run([test_command])
        return ToolResult(
            success=result.exit_code == 0,
            output=result.output.decode(),
            error=result.stderr.decode() if result.exit_code != 0 else None
        )
    
    def close(self):
        """Stop and remove the container."""
        if self.container:
            self.container.stop()
            self.container.remove()
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

*Draft generated on 2026-03-04*