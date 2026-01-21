# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

mini-SWE-agent is a minimal AI software engineering agent (~100 lines of core logic) that solves GitHub issues and programming challenges. It emphasizes simplicity, performance, and deployability over complex features.

## Architecture

The project follows a modular, polymorphic design with three core components (defined as Protocols in `src/minisweagent/__init__.py`):

- **Agent** (`src/minisweagent/agents/`): Control flow and loop. The `DefaultAgent` implements a simple step loop: query model -> parse action -> execute -> observe.
- **Environment** (`src/minisweagent/environments/`): Executes agent actions via bash. Supports local, docker, singularity, and other sandboxes.
- **Model** (`src/minisweagent/models/`): Language model interfaces. Defaults to `LitellmModel` for broad model compatibility.

**Key design principle**: Every action is executed independently with `subprocess.run` (no persistent shell session). This makes sandboxing trivial—you can literally swap `subprocess.run` with `docker exec`.

### Run Scripts

Entry points are in `src/minisweagent/run/`:
- `mini.py` - Main CLI (`mini` command), interactive/visual modes
- `hello_world.py` - Minimal example showing the core pattern
- `extra/` - Additional utilities (swebench, inspector, github_issue, etc.)

**Every use case should start with a run script** that picks one agent, environment, and model class.

### Configuration

Config files use YAML with Jinja2 templates stored in `src/minisweagent/config/`:
- `default.yaml` - Base configuration
- `mini.yaml` - For `mini` CLI (adds confirm mode, $3 cost limit)
- `extra/` - Task-specific configs (swebench, github issues, etc.)

Templates receive variables from agent config, environment, and model via `get_template_vars()` methods. The agent parses bash commands from LM responses using a regex (default: ```bash blocks).

## Development Commands

```bash
# Install from source (developer setup)
pip install -e .

# Run the main CLI
mini -v  # Visual (textual) mode
mini     # Simple REPL mode

# Linting and formatting (via ruff)
ruff check .                    # Lint
ruff check --fix .              # Fix linting issues
ruff format .                   # Format code

# Run pre-commit hooks
pre-commit run --all-files

# Run tests
pytest                          # Run all tests
pytest tests/path/to/test.py    # Run specific test file
pytest -k "test_name"           # Run tests matching pattern
pytest -k "not slow"            # Skip slow tests
pytest -x                       # Stop on first failure
```

## Code Style (from .cursor/rules and .github/copilot-instructions.md)

- Target Python 3.10+
- Use type annotations (`list` not `List`)
- Use `pathlib` instead of `os.path`; prefer `Path.read_text()` over `with ...open()`
- Use `typer` for CLI interfaces
- Use `jinja2` for templates
- Use `dataclass` (pydantic `BaseModel`) for config
- Keep code minimal and concise—this repository rewards brevity
- Don't catch exceptions unless explicitly told to
- Avoid initializing variables just to pass them—pass expressions directly

### Test Style

- Use `pytest`, not `unittest`
- **Do not mock/patch anything unless explicitly asked**
- Every test should test multiple points of failure
- Avoid splitting assertions: `assert func() == b` not `result = func(); assert result == b`
- `pytest.mark.parametrize`: first arg is tuple, second is list
- Print statements in tests are OK

## Key Implementation Details

### Agent Control Flow (src/minisweagent/agents/default.py)

The agent uses exception-based control flow:
- `NonTerminatingException` (FormatError, ExecutionTimeoutError) - fed back to LM as user message
- `TerminatingException` (Submitted, LimitsExceeded) - ends the run

The agent finishes when the LM outputs `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` (checked in `has_finished()`).

### Template Variables

Templates receive merged vars from:
1. `AgentConfig.model_dump()` - agent settings
2. `env.get_template_vars()` - platform info, environment variables
3. `model.get_template_vars()` - model name/cost info
4. `extra_template_vars` - task and kwargs passed to `run()`

### Model Selection

Models are selected via `get_model()` in `src/minisweagent/models/__init__.py`:
- Shortcut names: "anthropic", "litellm", "openrouter", "portkey", etc.
- Full import path: `"minisweagent.models.litellm_model.LitellmModel"`
- Default: `LitellmModel` (supports most providers via litellm)

Anthropic models get `set_cache_control="default_end"` by default for prompt caching.

### Adding New Components

To add a new agent, environment, or model:
1. Create class implementing the Protocol (no need to explicitly inherit)
2. Use pydantic `BaseModel` for config
3. Implement `get_template_vars()` to provide template variables
4. Create a run script or register in mapping dicts

## Configuration Paths

- Global config: `~/.config/mini-swe-agent/.env` (env vars, API keys)
- Built-in configs: `src/minisweagent/config/`
- Config discovery: current dir → `MSWEA_CONFIG_DIR` → built-in → built-in/extra

Set `MSWEA_MODEL_NAME` env var or use config to specify default model.
