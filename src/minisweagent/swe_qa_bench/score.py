#!/usr/bin/env python3

"""LLM-as-judge scoring for SWE-QA-Bench (independent of SWE-QA-Bench repo)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

import requests
import yaml


_JUDGE_SYSTEM_PROMPT = "You are a helpful assistant"

_PROMPT_TEMPLATE = """You are a professional evaluator. Please rate the candidate answer against the reference answer based on five criteria.
Evaluation Criteria and Scoring Guidelines (each scored 1 to 10):
    1. Correctness:
        10 — Completely correct; core points and details are accurate with no ambiguity.
        8-9 — Mostly correct; only minor details are slightly inaccurate or loosely expressed.
        6-7 — Partially correct; some errors or omissions, but main points are generally accurate.
        4-5 — Several errors or ambiguities that affect understanding of the core information.
        2-3 — Many errors; misleading or fails to convey key information.
        1 — Serious errors; completely wrong or misleading.
    2. Completeness:
        10 — Covers all key points from the reference answer without omission.
        8-9 — Covers most key points; only minor non-critical information missing.
        6-7 — Missing several key points; content is somewhat incomplete.
        4-5 — Important information largely missing; content is one-sided.
        2-3 — Covers very little relevant information; seriously incomplete.
        1 — Covers almost no relevant information; completely incomplete.
    3. Relevance:
        10 — Content fully focused on the question topic; no irrelevant information.
        8-9 — Mostly focused; only minor irrelevant or peripheral information.
        6-7 — Generally on topic; some off-topic content but still relevant overall.
        4-5 — Topic not sufficiently focused; contains considerable off-topic content.
        2-3 — Content deviates from topic; includes excessive irrelevant information.
        1 — Majority of content irrelevant to the question.
    4. Clarity:
        10 — Fluent language; clear and precise expression; very easy to understand.
        8-9 — Mostly fluent; clear expression with minor unclear points.
        6-7 — Generally clear; some expressions slightly unclear or not concise.
        4-5 — Expression somewhat awkward; some ambiguity or lack of fluency.
        2-3 — Language obscure; sentences are not smooth; hinders understanding.
        1 — Expression confusing; very difficult to understand.
    5. Reasoning:
        10 — Reasoning is clear, logical, and well-structured; argumentation is excellent.
        8-9 — Reasoning is clear and logical; well-structured with solid argumentation.
        6-7 — Reasoning generally reasonable; mostly clear logic; minor jumps.
        4-5 — Reasoning is average; some logical jumps or organization issues.
        2-3 — Reasoning unclear; lacks logical order; difficult to follow.
        1 — No clear reasoning; logic is chaotic.

INPUT:
    Question:{question}
    Reference Answer:{reference}
    Candidate Answer:{candidate}

OUTPUT:
    Please output ONLY a JSON object with 5 integer fields in the range [1,10], corresponding
    to the evaluation scores:
        {{
        "correctness": <1-10>,
        "completeness": <1-10>,
        "relevance": <1-10>,
        "clarity": <1-10>,
        "reasoning": <1-10>
        }}

REQUIREMENT:
    No explanation, no extra text, no formatting other than valid JSON"""


def _resolve_api_url(value: str) -> str:
    base = value.strip()
    if not base:
        raise ValueError("judge API base URL must be set")
    if base.endswith("/chat/completions"):
        return base
    return base.rstrip("/") + "/chat/completions"


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[len("```json") :]
    if text.startswith("```"):
        text = text[len("```") :]
    if text.endswith("```"):
        text = text[: -len("```")]
    return text.strip()


def _parse_scores(text: str) -> dict[str, int] | None:
    text = _strip_code_fence(text)
    try:
        scores = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(scores, dict):
        return None
    keys = ["correctness", "completeness", "clarity", "relevance", "reasoning"]
    for key in keys:
        if key not in scores or not (0 <= scores[key] <= 10):
            return None
    return {key: int(scores[key]) for key in keys}


def _question_hash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:8]


def _load_category_map(path: Path | None) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("category_map must be a YAML mapping")
    return {
        "category": data.get("question_hash_to_category", {}) or {},
        "difficulty": data.get("question_hash_to_difficulty", {}) or {},
    }


def _normalize_weights(weights: dict[str, float] | None) -> dict[str, float]:
    keys = ["correctness", "completeness", "relevance", "clarity", "reasoning"]
    if not weights:
        return {key: 0.2 for key in keys}
    normalized: dict[str, float] = {}
    total = 0.0
    for key in keys:
        value = float(weights.get(key, 0.0))
        normalized[key] = value
        total += value
    if total <= 0:
        return {key: 0.2 for key in keys}
    return {key: value / total for key, value in normalized.items()}


def _aggregate(values: list[int], mode: str) -> float:
    if not values:
        return 0.0
    if mode == "median":
        return float(statistics.median(values))
    return float(sum(values)) / len(values)


def _resolve_answers_scope(path: Path) -> tuple[Path, Path, list[str]]:
    path = path.expanduser().resolve()
    if path.is_file():
        raise ValueError(f"answers_root must be a directory, got file: {path}")
    if path.name == "answers":
        return path.parent, path, []
    if (path / "answers").exists():
        return path, path / "answers", []
    parts = list(path.parts)
    if "answers" in parts:
        idx = len(parts) - 1 - parts[::-1].index("answers")
        base_root = Path(*parts[:idx]) if idx > 0 else Path(path.anchor or "/")
        answers_root = base_root / "answers"
        if not answers_root.exists():
            raise ValueError(f"answers root not found under: {base_root}")
        scope_parts = parts[idx + 1 :]
        return base_root, answers_root, scope_parts
    raise ValueError(f"answers_root must be a results root or answers dir: {path}")


def _has_jsonl(dir_path: Path) -> bool:
    return any(dir_path.glob("*.jsonl"))


def _iter_answer_sets(answers_root: Path, scope_parts: list[str]) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []

    def add_runs(model_dir: Path, method_dir: Path, run_dir: Path) -> None:
        if _has_jsonl(run_dir):
            pairs.append((model_dir.name, method_dir.name, run_dir.name))

    if not scope_parts:
        for model_dir in sorted(answers_root.iterdir()):
            if not model_dir.is_dir():
                continue
            for method_dir in sorted(model_dir.iterdir()):
                if not method_dir.is_dir():
                    continue
                for run_dir in sorted(method_dir.iterdir()):
                    if run_dir.is_dir():
                        add_runs(model_dir, method_dir, run_dir)
        return pairs

    if len(scope_parts) > 3:
        raise ValueError(f"answers_root too deep (expected answers/<model>/<method>/<run_id>): {answers_root}")

    model = scope_parts[0]
    model_dir = answers_root / model
    if not model_dir.exists():
        return []
    if len(scope_parts) == 1:
        for method_dir in sorted(model_dir.iterdir()):
            if not method_dir.is_dir():
                continue
            for run_dir in sorted(method_dir.iterdir()):
                if run_dir.is_dir():
                    add_runs(model_dir, method_dir, run_dir)
        return pairs

    method = scope_parts[1]
    method_dir = model_dir / method
    if not method_dir.exists():
        return []
    if len(scope_parts) == 2:
        for run_dir in sorted(method_dir.iterdir()):
            if run_dir.is_dir():
                add_runs(model_dir, method_dir, run_dir)
        return pairs

    run_id = scope_parts[2]
    run_dir = method_dir / run_id
    if run_dir.is_dir():
        add_runs(model_dir, method_dir, run_dir)
    return pairs


def score_multiple_answer_roots(
    *,
    answers_roots: list[Path],
    dataset_root: Path,
    judge_model: str,
    api_url: str,
    api_key: str,
    repos: list[str] | None,
    max_workers: int,
    timeout: int,
    pass_threshold: float,
    pass_metric: str,
    weights: dict[str, float] | None,
    judge_rounds: int,
    judge_agg: str,
    category_map_path: Path | None,
    resume: bool,
    candidate_model_filter: str | None = None,
    method_filter: str | None = None,
) -> None:
    seen: set[tuple[Path, str, str, str]] = set()
    for root in answers_roots:
        base_root, answers_root, scope_parts = _resolve_answers_scope(root)
        pairs = _iter_answer_sets(answers_root, scope_parts)
        if not pairs:
            print(f"Skipping {root}: no answers found")
            continue
        for candidate_model, method, run_id in pairs:
            if candidate_model_filter and candidate_model != candidate_model_filter:
                continue
            if method_filter and method != method_filter:
                continue
            key = (base_root, candidate_model, method, run_id)
            if key in seen:
                continue
            seen.add(key)
            score_dataset(
                dataset_root=dataset_root,
                candidate_model=candidate_model,
                method=method,
                judge_model=judge_model,
                api_url=api_url,
                api_key=api_key,
                repos=repos,
                max_workers=max_workers,
                timeout=timeout,
                pass_threshold=pass_threshold,
                output_root=base_root,
                run_id=run_id,
                pass_metric=pass_metric,
                weights=weights,
                judge_rounds=judge_rounds,
                judge_agg=judge_agg,
                category_map_path=category_map_path,
                resume=resume,
            )


def _call_judge(
    *,
    api_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    response = requests.post(api_url, headers=headers, data=json.dumps(payload), timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""


def score_answer(
    *,
    question: str,
    reference: str,
    candidate: str,
    api_url: str,
    api_key: str,
    model: str,
    timeout: int,
) -> dict[str, int] | None:
    prompt = _PROMPT_TEMPLATE.format(question=question, reference=reference, candidate=candidate)
    try:
        result_text = _call_judge(api_url=api_url, api_key=api_key, model=model, prompt=prompt, timeout=timeout)
    except Exception:
        return None
    return _parse_scores(result_text)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _normalize_answer(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item)
    return str(value)


def _build_reference_dict(reference_path: Path) -> dict[str, str]:
    reference_dict: dict[str, str] = {}
    for record in _read_jsonl(reference_path):
        question = (record.get("question") or "").strip()
        if not question:
            continue
        answer = record.get("aggregated_answer")
        if not answer:
            answer = record.get("answer")
        normalized = _normalize_answer(answer)
        if normalized:
            reference_dict[question] = normalized
    return reference_dict


def _format_float(value: float) -> str:
    return f"{value:.4f}"


def _write_markdown_report(output_dir: Path, summary: dict[str, Any]) -> None:
    meta = summary.get("meta", {})
    stats = summary.get("stats_overall", {})
    dims = summary.get("stats_dimensions", {})
    grouped = summary.get("grouped_stats", {})
    judge = meta.get("judge_config", {})

    lines: list[str] = []
    lines.append("# SWE-QA-Bench Run Report")
    lines.append("")
    lines.append("## Summary Dashboard")
    lines.append("")
    lines.append("| run_id | model | pass_rate | weighted_score | avg_score |")
    lines.append("| --- | --- | --- | --- | --- |")
    lines.append(
        f"| {summary.get('run_id','')} | {meta.get('candidate_model','')} | "
        f"{_format_float(stats.get('pass_rate',0.0))} | "
        f"{_format_float(stats.get('avg_weighted_score',0.0))} | "
        f"{_format_float(stats.get('avg_score',0.0))} |"
    )
    lines.append("")

    lines.append("## Category Analysis")
    lines.append("")
    lines.append("| category | count | pass_rate | avg_score | avg_weighted_score |")
    lines.append("| --- | --- | --- | --- | --- |")
    category_stats = (grouped or {}).get("category", {})
    if category_stats:
        for label, item in sorted(category_stats.items()):
            lines.append(
                f"| {label} | {item.get('count',0)} | "
                f"{_format_float(item.get('pass_rate',0.0))} | "
                f"{_format_float(item.get('avg_score',0.0))} | "
                f"{_format_float(item.get('avg_weighted_score',0.0))} |"
            )
    else:
        lines.append("| (none) | 0 | 0.0000 | 0.0000 | 0.0000 |")
    lines.append("")

    lines.append("## Dimension Breakdown")
    lines.append("")
    lines.append("| correctness | completeness | relevance | clarity | reasoning |")
    lines.append("| --- | --- | --- | --- | --- |")
    lines.append(
        f"| {_format_float(dims.get('correctness',0.0))} | "
        f"{_format_float(dims.get('completeness',0.0))} | "
        f"{_format_float(dims.get('relevance',0.0))} | "
        f"{_format_float(dims.get('clarity',0.0))} | "
        f"{_format_float(dims.get('reasoning',0.0))} |"
    )
    lines.append("")

    lines.append("## Judge Config")
    lines.append("")
    lines.append("| model | prompt_hash | rounds | agg | pass_metric | pass_threshold |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    lines.append(
        f"| {judge.get('model','')} | {judge.get('prompt_hash','')} | "
        f"{judge.get('rounds',1)} | {judge.get('agg','')} | "
        f"{judge.get('pass_metric','')} | {stats.get('pass_threshold','')} |"
    )
    weights = judge.get("weights")
    if weights:
        lines.append("")
        lines.append(f"weights: `{json.dumps(weights, ensure_ascii=False)}`")
    lines.append("")

    report_path = output_dir / "README.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _iter_repos(reference_dir: Path, repos: list[str] | None) -> list[str]:
    if repos:
        return repos
    return sorted(path.stem for path in reference_dir.glob("*.jsonl"))


def _score_records(
    candidate_records: list[dict[str, Any]],
    reference_dict: dict[str, str],
    api_url: str,
    api_key: str,
    judge_model: str,
    max_workers: int,
    timeout: int,
    pass_threshold: float,
    pass_metric: str,
    weights: dict[str, float],
    judge_rounds: int,
    judge_agg: str,
    category_map: dict[str, dict[str, str]],
    existing_hashes: set[str] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def _process(record: dict[str, Any]) -> dict[str, Any] | None:
        question = (record.get("question") or "").strip()
        if not question:
            return None
        question_id = _question_hash(question)
        if existing_hashes and question_id in existing_hashes:
            return None
        candidate_answer = record.get("final_answer") or record.get("answer") or ""
        candidate_answer = _normalize_answer(candidate_answer)
        reference = reference_dict.get(question, "")
        if not reference or not candidate_answer:
            return None

        rounds = max(1, int(judge_rounds))
        per_round: list[dict[str, int]] = []
        for _ in range(rounds):
            scores = score_answer(
                question=question,
                reference=reference,
                candidate=candidate_answer,
                api_url=api_url,
                api_key=api_key,
                model=judge_model,
                timeout=timeout,
            )
            if scores is None:
                continue
            per_round.append(scores)

        if not per_round:
            return None

        aggregated: dict[str, float] = {}
        for key in ["correctness", "completeness", "clarity", "relevance", "reasoning"]:
            aggregated[key] = _aggregate([item[key] for item in per_round], judge_agg)

        score_avg = sum(aggregated.values()) / len(aggregated)
        weighted_score = sum(aggregated[key] * weights[key] for key in weights)
        pass_score = weighted_score if pass_metric == "weighted" else score_avg

        category = category_map.get("category", {}).get(question_id)
        difficulty = category_map.get("difficulty", {}).get(question_id)

        return {
            "question": question,
            "question_hash": question_id,
            "candidate_answer": candidate_answer,
            "reference": reference,
            "correctness": aggregated["correctness"],
            "completeness": aggregated["completeness"],
            "clarity": aggregated["clarity"],
            "relevance": aggregated["relevance"],
            "reasoning": aggregated["reasoning"],
            "total_score": sum(aggregated.values()),
            "score_avg": score_avg,
            "weighted_score": weighted_score,
            "pass": pass_score >= pass_threshold,
            "judge_rounds": rounds,
            "judge_agg": judge_agg,
            "category": category,
            "difficulty": difficulty,
        }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_record = {executor.submit(_process, record): record for record in candidate_records}
        for future in as_completed(future_to_record):
            result = future.result()
            if result is not None:
                results.append(result)
    return results


def score_dataset(
    *,
    dataset_root: Path,
    candidate_model: str,
    method: str,
    judge_model: str,
    api_url: str,
    api_key: str,
    repos: list[str] | None,
    max_workers: int,
    timeout: int,
    pass_threshold: float,
    output_root: Path | None = None,
    run_id: str | None = None,
    pass_metric: str = "weighted",
    weights: dict[str, float] | None = None,
    judge_rounds: int = 1,
    judge_agg: str = "median",
    category_map_path: Path | None = None,
    resume: bool = False,
    answers_path: Path | None = None,
) -> None:
    reference_dir = dataset_root / "reference"
    base_root = output_root or dataset_root
    if resume and not run_id:
        raise ValueError("resume requires run_id")
    run_id = run_id or time.strftime("%Y%m%d_%H%M%S")
    candidate_base = base_root / "answers" / candidate_model / method / run_id
    output_base = base_root / "scores" / candidate_model / method / run_id

    weights = _normalize_weights(weights)
    if pass_metric == "weighted_score":
        pass_metric = "weighted"
    if pass_metric == "score_avg":
        pass_metric = "avg"
    pass_metric = pass_metric if pass_metric in {"avg", "weighted"} else "weighted"
    judge_agg = judge_agg if judge_agg in {"mean", "median"} else "median"
    category_map = _load_category_map(category_map_path)

    if answers_path:
        answers_path = answers_path.expanduser().resolve()
        if not answers_path.exists():
            raise ValueError(f"answers_path not found: {answers_path}")
        inferred_repo = answers_path.stem
        if repos:
            if len(repos) != 1:
                raise ValueError("answers_path supports a single repo")
            if repos[0] != inferred_repo:
                raise ValueError(
                    f"answers_path repo mismatch: {repos[0]} vs {inferred_repo}"
                )
        repos = [inferred_repo]

    scored_repos: list[str] = []
    total_scored = 0
    total_passed = 0
    total_score_avg = 0.0
    total_weighted_score = 0.0
    dim_totals = {key: 0.0 for key in ["correctness", "completeness", "relevance", "clarity", "reasoning"]}
    dim_counts = 0
    grouped: dict[str, dict[str, dict[str, Any]]] = {"category": {}, "difficulty": {}}

    def _accumulate(records: Iterable[dict[str, Any]]) -> None:
        nonlocal total_scored, total_passed, total_score_avg, total_weighted_score, dim_counts
        for record in records:
            total_scored += 1
            total_passed += 1 if record.get("pass") else 0
            total_score_avg += float(record.get("score_avg", 0.0))
            total_weighted_score += float(record.get("weighted_score", 0.0))
            for key in dim_totals:
                dim_totals[key] += float(record.get(key, 0.0))
            dim_counts += 1
            for group_key in ["category", "difficulty"]:
                group_value = record.get(group_key)
                if not group_value:
                    continue
                bucket = grouped[group_key].setdefault(
                    group_value,
                    {"count": 0, "passed": 0, "score_sum": 0.0, "weighted_sum": 0.0},
                )
                bucket["count"] += 1
                bucket["passed"] += 1 if record.get("pass") else 0
                bucket["score_sum"] += float(record.get("score_avg", 0.0))
                bucket["weighted_sum"] += float(record.get("weighted_score", 0.0))
    for repo in _iter_repos(reference_dir, repos):
        candidate_path = answers_path if answers_path else candidate_base / f"{repo}.jsonl"
        reference_path = reference_dir / f"{repo}.jsonl"
        output_path = output_base / f"{repo}.jsonl"

        if not candidate_path.exists() or not reference_path.exists():
            print(f"Skipping {repo}: missing candidate or reference file")
            continue

        existing_hashes: set[str] | None = None
        existing_records: list[dict[str, Any]] = []
        if resume and output_path.exists():
            existing_records = _read_jsonl(output_path)
            existing_hashes = {
                (record.get("question_hash") or "").strip()
                for record in existing_records
                if record.get("question_hash")
            }

        reference_dict = _build_reference_dict(reference_path)
        candidate_records = _read_jsonl(candidate_path)
        results = _score_records(
            candidate_records,
            reference_dict,
            api_url,
            api_key,
            judge_model,
            max_workers,
            timeout,
            pass_threshold,
            pass_metric,
            weights,
            judge_rounds,
            judge_agg,
            category_map,
            existing_hashes,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "a" if resume and output_path.exists() else "w"
        with output_path.open(file_mode, encoding="utf-8") as handle:
            for record in results:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(f"Scored {repo}: {len(results)} records -> {output_path}")
        scored_repos.append(repo)
        if existing_records:
            _accumulate(existing_records)
        _accumulate(results)

    prompt_hash = hashlib.sha256(f"{_JUDGE_SYSTEM_PROMPT}{_PROMPT_TEMPLATE}".encode("utf-8")).hexdigest()[:8]
    summary_path = output_base / "run_summary.json"
    avg_score = total_score_avg / total_scored if total_scored else 0.0
    avg_weighted = total_weighted_score / total_scored if total_scored else 0.0
    pass_rate = total_passed / total_scored if total_scored else 0.0
    avg_dims = {key: (dim_totals[key] / dim_counts if dim_counts else 0.0) for key in dim_totals}
    grouped_stats: dict[str, dict[str, Any]] = {"category": {}, "difficulty": {}}
    for group_key, items in grouped.items():
        for label, bucket in items.items():
            count = bucket["count"]
            grouped_stats[group_key][label] = {
                "count": count,
                "pass_rate": (bucket["passed"] / count) if count else 0.0,
                "avg_score": (bucket["score_sum"] / count) if count else 0.0,
                "avg_weighted_score": (bucket["weighted_sum"] / count) if count else 0.0,
            }
    summary_payload = {
        "meta": {
            "benchmark": "swe_qa_bench",
            "candidate_model": candidate_model,
            "method": method,
            "answers_path": str(answers_path) if answers_path else "",
            "judge_config": {
                "model": judge_model,
                "prompt_hash": prompt_hash,
                "temperature": 0.0,
                "rounds": judge_rounds,
                "agg": judge_agg,
                "weights": weights,
                "pass_metric": pass_metric,
            },
        },
        "stats_overall": {
            "repos_scored": len(scored_repos),
            "total_records": total_scored,
            "pass_rate": pass_rate,
            "avg_score": avg_score,
            "avg_weighted_score": avg_weighted,
            "pass_threshold": pass_threshold,
        },
        "stats_dimensions": avg_dims,
        "grouped_stats": grouped_stats,
        "repos": scored_repos,
        "run_id": run_id,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False))
    _write_markdown_report(output_base, summary_payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score SWE-QA-Bench answers with LLM-as-judge.")
    parser.add_argument("--dataset-root", required=True, help="Path to SWE-QA-Bench datasets root")
    parser.add_argument("--candidate-model", default=os.getenv("MODEL"), help="Candidate model (answers dir)")
    parser.add_argument("--method", default=os.getenv("METHOD"), help="Method name (answers dir)")
    parser.add_argument("--run-id", default=os.getenv("RUN_ID"), help="Run identifier (answers/scores dir)")
    parser.add_argument("--resume", action="store_true", help="Resume scoring (skip existing)")
    parser.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL"), help="Judge model name")
    parser.add_argument("--judge-api-base", default=os.getenv("JUDGE_API_BASE") or os.getenv("OPENAI_API_BASE"))
    parser.add_argument("--judge-api-key", default=os.getenv("JUDGE_API_KEY") or os.getenv("OPENAI_API_KEY"))
    parser.add_argument("--max-workers", type=int, default=int(os.getenv("JUDGE_MAX_WORKERS", "8")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("JUDGE_TIMEOUT", "60")))
    parser.add_argument("--repos", default="", help="Comma-separated repo list (optional)")
    parser.add_argument("--output-root", default=os.getenv("OUTPUT_ROOT"), help="Results root (default: dataset root)")
    parser.add_argument("--answers-path", default=os.getenv("ANSWERS_PATH"), help="Direct answers file path")
    parser.add_argument(
        "--answers-roots",
        default=os.getenv("ANSWERS_ROOTS", ""),
        help="Comma-separated list of results roots, answers dirs, or answers/<model>/<method>/<run_id> dirs",
    )
    parser.add_argument("--pass-metric", default=os.getenv("PASS_METRIC", "weighted"), help="avg or weighted")
    parser.add_argument("--weights", default=os.getenv("SCORE_WEIGHTS", ""), help="JSON weights mapping")
    parser.add_argument("--judge-rounds", type=int, default=int(os.getenv("JUDGE_ROUNDS", "1")))
    parser.add_argument("--judge-agg", default=os.getenv("JUDGE_AGG", "median"), help="median or mean")
    parser.add_argument("--category-map", default=os.getenv("CATEGORY_MAP", ""), help="Path to category_map.yaml")
    parser.add_argument(
        "--pass-threshold",
        type=float,
        default=float(os.getenv("SWE_QA_PASS_THRESHOLD", "7")),
        help="Score average threshold for pass (default: 7).",
    )
    args = parser.parse_args()

    answers_roots = (
        [Path(item).expanduser().resolve() for item in args.answers_roots.split(",") if item.strip()]
        if args.answers_roots
        else []
    )
    using_batch_roots = bool(answers_roots)

    if not using_batch_roots:
        if not args.candidate_model:
            raise SystemExit("candidate model must be set via --candidate-model or MODEL env var")
        if not args.method:
            raise SystemExit("method must be set via --method or METHOD env var")

    judge_model = args.judge_model or args.candidate_model
    if not judge_model:
        raise SystemExit("judge model must be set via --judge-model or JUDGE_MODEL env var")
    if not args.judge_api_base:
        raise SystemExit("judge API base must be set via --judge-api-base or env var")
    if not args.judge_api_key:
        raise SystemExit("judge API key must be set via --judge-api-key or env var")

    api_url = _resolve_api_url(args.judge_api_base)
    repos = [item.strip() for item in args.repos.split(",") if item.strip()] if args.repos else None

    weights = None
    if args.weights:
        try:
            weights = json.loads(args.weights)
        except json.JSONDecodeError:
            raise SystemExit("weights must be a JSON mapping")
    output_root = Path(args.output_root).expanduser().resolve() if args.output_root else None
    category_map_path = Path(args.category_map).expanduser().resolve() if args.category_map else None
    answers_path = Path(args.answers_path).expanduser().resolve() if args.answers_path else None

    if answers_path and answers_roots:
        raise SystemExit("Use either --answers-path or --answers-roots, not both.")

    if answers_roots:
        score_multiple_answer_roots(
            answers_roots=answers_roots,
            dataset_root=Path(args.dataset_root).resolve(),
            judge_model=judge_model,
            api_url=api_url,
            api_key=args.judge_api_key,
            repos=repos,
            max_workers=args.max_workers,
            timeout=args.timeout,
            pass_threshold=args.pass_threshold,
            pass_metric=args.pass_metric,
            weights=weights,
            judge_rounds=args.judge_rounds,
            judge_agg=args.judge_agg,
            category_map_path=category_map_path,
            resume=args.resume,
            candidate_model_filter=args.candidate_model or None,
            method_filter=args.method or None,
        )
        return

    score_dataset(
        dataset_root=Path(args.dataset_root).resolve(),
        candidate_model=args.candidate_model,
        method=args.method,
        judge_model=judge_model,
        api_url=api_url,
        api_key=args.judge_api_key,
        repos=repos,
        max_workers=args.max_workers,
        timeout=args.timeout,
        pass_threshold=args.pass_threshold,
        output_root=output_root,
        run_id=args.run_id,
        pass_metric=args.pass_metric,
        weights=weights,
        judge_rounds=args.judge_rounds,
        judge_agg=args.judge_agg,
        category_map_path=category_map_path,
        resume=args.resume,
        answers_path=answers_path,
    )


if __name__ == "__main__":
    main()
