# mini-swe-agent Documentation — Chapter 1-2 Draft

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
│                   Agent Main Loop                    │
├─────────────────────────────────────────────────────┤
│                                                     │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐     │
│   │  Observe │───▶│  Think   │───▶│  Act     │     │
│   │(env.obs) │    │model.gen │    │tool call │     │
│   └──────────┘    └──────────┘    └────┬─────┘     │
│        ▲                             │             │
│        └──────────────────────────────┘             │
│              (feedback loop)                         │
└─────────────────────────────────────────────────────┘
```

**Pseudocode**:
```python
while not done:
    observation = environment.observe()
    response = model.generate(prompt + observation)
    action = parse_action(response)
    environment.execute(action)
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
class ModelProtocol(Protocol):
    def generate(self, prompt: str) -> str: ...

@runtime_checkable  
class EnvironmentProtocol(Protocol):
    def observe(self) -> str: ...
    def execute(self, action: str) -> None: ...

@runtime_checkable
class AgentProtocol(Protocol):
    def run(self, task: str) -> None: ...
```

**Why Protocols?**
- No inheritance required — any class matching the interface works
- Enables duck typing across different implementations
- Easy to extend without modifying core code

---

### 2.2 Model Interface Deep Dive

```python
class ModelProtocol(Protocol):
    """Interface for all language model backends."""
    
    def generate(self, prompt: str) -> str:
        """Generate completion for the given prompt."""
        ...
    
    def generate_with_tools(self, 
                           prompt: str, 
                           tools: list[dict]) -> dict:
        """Generate response with tool-calling capability."""
        ...
```

**Implementation Example**:
```python
class OpenAIModel:
    def generate(self, prompt: str) -> str:
        response = openai.ChatCompletion.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
```

---

### 2.3 Environment Interface Deep Dive

```python
class EnvironmentProtocol(Protocol):
    """Interface for all task environments."""
    
    def observe(self) -> str:
        """Return current state observation."""
        ...
    
    def execute(self, action: str) -> None:
        """Execute an action in the environment."""
        ...
    
    def reset(self) -> None:
        """Reset environment to initial state."""
        ...
```

**Implementation Example**:
```python
class TerminalEnv:
    def __init__(self, working_dir: Path):
        self.process = subprocess.Popen(
            ['/bin/bash'],
            stdout=PIPE, stderr=PIPE
        )
        self.working_dir = working_dir
    
    def observe(self) -> str:
        return self.process.stdout.read().decode()
    
    def execute(self, action: str) -> None:
        self.process.stdin.write(action.encode())
```

---

### 2.4 Agent Interface Deep Dive

```python
class AgentProtocol(Protocol):
    """Interface for all agent implementations."""
    
    def __init__(self, 
                 model: ModelProtocol, 
                 environment: EnvironmentProtocol):
        """Initialize with model and environment."""
        ...
    
    def run(self, task: str) -> None:
        """Execute the agent loop for the given task."""
        ...
```

**Implementation Example**:
```python
class DefaultAgent:
    def __init__(self, model: ModelProtocol, environment: EnvironmentProtocol):
        self.model = model
        self.env = environment
    
    def run(self, task: str) -> None:
        prompt = f"Task: {task}\n\nPlease solve this task."
        
        while True:
            observation = self.env.observe()
            response = self.model.generate(prompt + observation)
            
            if self._is_done(response):
                break
            
            action = self._parse_action(response)
            self.env.execute(action)
```

---

## Chapter Summary

| 章节 | 内容 | 字数预估 |
|------|------|---------|
| **第 1 章** | 架构概览、核心抽象、组件交互 | ~800 字 |
| **第 2 章** | Protocol 设计、Model/Env/Agent 接口详解 | ~1000 字 |

---

*Draft generated on 2026-03-04*