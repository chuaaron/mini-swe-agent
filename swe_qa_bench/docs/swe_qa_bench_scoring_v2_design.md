# SWE-QA-Bench Scoring v2 Design

## Overview
This document proposes a scoring and result-management upgrade for SWE-QA-Bench.
Goals: remove cross-run contamination, improve diagnostics, and support category-level analysis.

## Goals
- Run isolation: prevent answers/scores from mixing across runs.
- Better diagnostics: per-dimension averages and weighted scores.
- Category-aware evaluation: per-category stats (e.g., debugging vs feature).
- Robustness: support judge variance control and resume.
- Human-friendly artifacts: auto-generated Markdown report.

## Non-Goals
- Change the judge rubric or prompt content.
- Modify original dataset in-place in v1 (use external maps first).
- Rework the SWE-QA-Bench data format in this phase.

## Current State (v1)
- Answers: appended to
  `results/answers/<model>/<method>/<repo>.jsonl`
- Scores: overwritten at
  `results/scores/<model>/<method>/<repo>.jsonl`
- Summary: `run_summary.json` with overall avg_score/pass_rate only.
- No category breakdown or weighted scores.
- No run_id or resume mechanism.

## Problems
1) Answer contamination across runs (append-only answers).
2) Scores overwrite previous runs, losing history.
3) Overall avg score hides dimension-level weaknesses.
4) No category/difficulty breakdown.
5) Judge variance not controlled.
6) No resume; partial runs create orphaned artifacts.

## Proposed Changes

### 1) Run Isolation with run_id
Introduce a run-scoped directory structure:

```
results/<run_id>/
  answers/<model>/<method>/<repo>.jsonl
  scores/<model>/<method>/<repo>.jsonl
  outputs/<model>/<method>/...
```

- `run_id` default: `YYYYMMDD_HHMMSS`
- Deterministic and time-sortable.
- CLI: `--run-id <id>`; config: `run.run_id`.
- `--resume <run_id>` uses existing answers/scores and only processes missing items.

Backward compatibility:
- If `run_id` is omitted, generate default and write to new structure.
- Provide optional `--legacy-paths` flag if needed for old tooling.

### 2) Weighted Score (Default)
Add weighted scoring on top of the 5 dimensions:

```
weighted_score = 0.4*correctness + 0.3*reasoning + 0.1*completeness
               + 0.1*relevance   + 0.1*clarity
```

Config:
```
score.weights:
  correctness: 0.4
  reasoning: 0.2
  completeness: 0.2
  relevance: 0.1
  clarity: 0.1
```

- Default weights as above (SWE-oriented).
- `pass` threshold defaults to `weighted_score`.
- Configurable: `pass_metric: avg|weighted` (default: weighted).

### 3) Dimension-Level Aggregates
Run summary should include:
- `avg_correctness`, `avg_completeness`, `avg_relevance`,
  `avg_clarity`, `avg_reasoning`
- `avg_score` (mean of score_avg)
- `avg_weighted_score` (if enabled)
- `pass_rate`

### 4) Category / Difficulty Breakdown
Short-term (recommended): external mapping (YAML).

Example `category_map.yaml`:
```
question_hash_to_category:
  "9f2a3b1c": debugging
  "0aa1bc22": feature

question_hash_to_difficulty:
  "9f2a3b1c": easy
```

Usage:
- Config: `score.category_map: /path/to/category_map.yaml`
- Group stats by category and difficulty.

Long-term:
- Write back categories into dataset reference files.

### 5) Judge Variance Control
Add `judge_rounds`:
- Default: 1
- If > 1, score the same question multiple times and aggregate.

Aggregation modes:
- `median` (default when rounds > 1)
- `mean`

Config:
```
score.judge_rounds: 3
score.judge_agg: median
```

### 6) Resume / Partial Runs
Add `--resume <run_id>`:
- Read existing answers/scores in that run_id.
- Skip questions already scored.
- Continue from the remaining set.

### 7) Markdown Report
Generate `README.md` in each run directory:
- Summary Dashboard (1 row): run_id, model, pass_rate, weighted_score
- Category Analysis: pass_rate + score by category
- Dimension Breakdown: 5-dimension averages
- Judge Config: model, prompt hash, rounds, weights, pass_threshold

## CLI / Config Additions (Proposed)

Run stage:
- `--run-id`
- `--resume`
- `run.run_id`

Score stage:
- `score.weights`
- `score.pass_metric` (default: weighted)
- `score.judge_rounds` (default: 1)
- `score.judge_agg` (default: median when rounds > 1)
- `score.category_map` (hash-based)
- `--run-id` / `--resume` for scoring

## Output Schema (Proposed Additions)

Per-record score (repo jsonl):
- `score_avg`
- `weighted_score`
- `judge_rounds`
- `judge_agg`
- `category` / `difficulty` (if mapped)
- `question_hash` (sha256(question_text)[:8])

run_summary.json:
- `avg_*` per dimension
- `avg_weighted_score`
- `pass_rate`
- `grouped_stats`: category/difficulty if enabled
- `run_id`
- `judge_config`: model, prompt_hash, rounds, agg, weights, pass_threshold, pass_metric

## Rollout Plan
1) Implement run_id directories + resume (core isolation).
2) Add dimension averages + weighted score.
3) Add category_map support and grouped stats.
4) Add judge_rounds aggregation.
5) Add Markdown report.

## Open Questions
- Should we keep legacy path outputs for a transition period?
- How to set default weights for the SWE task domain?
- Should pass_threshold apply to avg or weighted by default?
