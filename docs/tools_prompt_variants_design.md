# Tools Prompt Variants (Design)

## Overview
We want three tools-mode prompt variants to control the relative priority of `code_search` vs `bash`.
The current tools prompt is neutral and leaves the choice to the agent. The goal is to make prompt
variants selectable via config and CLI for controlled experiments with high contrast between variants.

## Goals
- Provide three prompt variants: `neutral`, `search_first`, `search_fallback`.
- Make the variant selectable from config and CLI without changing tool behavior.
- Keep backward compatibility: default behavior stays unchanged.
- Keep the implementation minimal and transparent (file-based configs, no dynamic prompt rewrites).
- Keep the experiment surface area small (batch runners only).

## Non-Goals
- No changes to the code_search tool implementation or indexing.
- No changes to scoring/evaluation logic.
- No changes to the execution environment (docker/local/etc.).
- No support for interactive flows (`mini`, `mini-extra`, etc.) in Phase 1.

## Current Behavior
- Tools mode uses `agent_tools.yaml` for system/instance templates.
- The prompt describes `code_search` as optional and suggests using it when paths are unknown.
- CLI can override `agent_config`, but there is no higher-level prompt variant selector.

## Proposed Solution

### 1) Prompt Variants
We add three tool prompt variants with clear guidance and high contrast:
- `neutral`: Keep the current instructions (baseline).
- `search_first`:
  - MUST start with `code_search` unless the task explicitly provides exact paths.
  - Do NOT start with bash exploration.
  - After results, use `rg/cat/sed` to validate.
- `search_fallback`:
  - MUST start with bash/rg for exact localization.
  - Only use `code_search` if bash returns 0 results or too many results (> 20) after 2 attempts.
  - After `code_search`, return to bash for validation.

These differences live only in the templates (system + rules) and do not affect tool availability.

### 2) Configuration and CLI Selection
Add a new run-level setting:
- `run.tools_prompt`: one of `neutral|search_first|search_fallback`.

Add a CLI flag:
- `--tools-prompt {neutral,search_first,search_fallback}`

Selection precedence:
1. `--agent-config` (explicit file always wins)
2. `--tools-prompt` (CLI)
3. `run.agent_config` (config)
4. `run.tools_prompt` (config)
5. default `neutral`

### 3) Mapping Variant to Config Files
For each benchmark, create three agent config files:

LocBench:
- `locbench/config/agent_tools_neutral.yaml`
- `locbench/config/agent_tools_search_first.yaml`
- `locbench/config/agent_tools_search_fallback.yaml`

SWE-QA-Bench:
- `swe_qa_bench/config/agent_tools_neutral.yaml`
- `swe_qa_bench/config/agent_tools_search_first.yaml`
- `swe_qa_bench/config/agent_tools_search_fallback.yaml`

`agent_tools.yaml` remains as-is (neutral) to avoid breaking existing runs.

### 4) Run Entrypoints
Add selection logic in:
- `src/minisweagent/run_locbench.py`
- `src/minisweagent/run_swe_qa.py`

Behavior:
- If `agent_config` is explicitly provided, do nothing.
- Else if in tools mode, pick the matching agent config file based on tools_prompt.

### 5) Defaults
Add `run.tools_prompt: neutral` to:
- `locbench/config/default.yaml`
- `swe_qa_bench/config/default.yaml`

### 6) Result Separation (Saving Logic)
We must ensure outputs from different prompt variants do not mix.

**Primary mechanism: method namespacing**
- Introduce `effective_method` in tools mode:
- If `tools_prompt` is `search_first` or `search_fallback`,
  set `effective_method = "{method}__{tools_prompt}"`.
- If `tools_prompt` is `neutral`, keep `effective_method = method`
  to preserve backwards compatibility.
- Use `effective_method` everywhere outputs are written:
  - LocBench: `output_root/loc_output/<model>/<effective_method>/...`
  - SWE-QA: `output_root/answers/<model>/<effective_method>/...`
- This guarantees separation even when `output_dir` is the same.

**Secondary mechanism: metadata tagging**
- Add `tools_prompt`, `agent_config`, and `effective_method` to `run_summary.json` meta.
- Also add `tools_prompt` to each per-instance output record (e.g., loc_output jsonl, SWE-QA answers).

**Why this works**
- SWE-QA answers are keyed by `method`; without namespacing, different prompt variants
  would overwrite or skip each other's answers.
- LocBench outputs are grouped by `method` even though filenames have timestamps.

**Compatibility**
- Default neutral runs remain at `miniswe_tools` (no suffix). No `__neutral` suffix.
- Non-neutral variants always append suffix even if the user passes `--method`.

This is explicit but behavior remains unchanged because `agent_tools.yaml` stays neutral.

## Prompt Content Changes (High-Level)
Only the "Available Tools" guidance and "Rules" lines change.

Neutral (baseline):
- Use code_search when paths are unknown; then bash to read files.

Search-first (high contrast):
- MUST start with `code_search` unless the task includes exact file paths.
- Do NOT use bash exploration first.
- After results, use rg/cat/sed to inspect.

Search-fallback (high contrast):
- MUST start with bash/rg for exact matches and file reads.
- Only use `code_search` if 0 results or > 20 results after 2 bash attempts.
- Then return to bash for validation.

## Compatibility and Risks
- Backward compatible: default remains neutral.
- Experiments can be reproduced by fixing `run.tools_prompt` or `--tools-prompt`.
- Risk: users may pass invalid values; validate and error early.

## Testing/Validation (Manual)
- Run tools mode with each tools_prompt value and verify the config summary logs:
  - `agent_config` path matches the chosen variant file.
- Run with `--agent-config` and ensure tools_prompt is ignored.

## Documentation Update (Phase 1)
- Add a short note in `docs/usage/` for the new `--tools-prompt` flag.

## Optional Extensions (Not in Phase 1)
- Add tools_prompt selection to `mini-extra locbench-tools` (extra config path).

## Open Questions
- Should we also add variants under `src/minisweagent/config/extra/` for batch runs?
- Do we want a short name alias (`sf`, `sb`) for CLI convenience?
