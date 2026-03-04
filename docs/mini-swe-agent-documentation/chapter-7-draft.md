# mini-swe-agent Documentation — Chapter 7 Draft

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

*Draft generated on 2026-03-05*