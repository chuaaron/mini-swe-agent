# mini-swe-agent Documentation — Chapter 5-6 Draft

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

*Draft generated on 2026-03-04*