#!/usr/bin/env python3

"""LLM-as-judge scoring for SWE-QA-Bench (independent of SWE-QA-Bench repo)."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

import requests


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
            {"role": "system", "content": "You are a helpful assistant"},
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
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def _process(record: dict[str, Any]) -> dict[str, Any] | None:
        question = (record.get("question") or "").strip()
        if not question:
            return None
        candidate_answer = record.get("final_answer") or record.get("answer") or ""
        candidate_answer = _normalize_answer(candidate_answer)
        reference = reference_dict.get(question, "")
        if not reference or not candidate_answer:
            return None
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
            return None
        return {
            "question": question,
            "candidate_answer": candidate_answer,
            "reference": reference,
            "correctness": scores["correctness"],
            "completeness": scores["completeness"],
            "clarity": scores["clarity"],
            "relevance": scores["relevance"],
            "reasoning": scores["reasoning"],
            "total_score": sum(scores.values()),
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
    output_root: Path | None = None,
) -> None:
    reference_dir = dataset_root / "reference"
    if output_root is None:
        candidate_base = dataset_root / "answers" / candidate_model / method
        output_base = dataset_root / "scores" / candidate_model / method
    else:
        candidate_base = output_root / "answers" / candidate_model / method
        output_base = output_root / "scores" / candidate_model / method

    for repo in _iter_repos(reference_dir, repos):
        candidate_path = candidate_base / f"{repo}.jsonl"
        reference_path = reference_dir / f"{repo}.jsonl"
        output_path = output_base / f"{repo}.jsonl"

        if not candidate_path.exists() or not reference_path.exists():
            print(f"Skipping {repo}: missing candidate or reference file")
            continue

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
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for record in results:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(f"Scored {repo}: {len(results)} records -> {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score SWE-QA-Bench answers with LLM-as-judge.")
    parser.add_argument("--dataset-root", required=True, help="Path to SWE-QA-Bench datasets root")
    parser.add_argument("--candidate-model", default=os.getenv("MODEL"), help="Candidate model (answers dir)")
    parser.add_argument("--method", default=os.getenv("METHOD"), help="Method name (answers dir)")
    parser.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL"), help="Judge model name")
    parser.add_argument("--judge-api-base", default=os.getenv("JUDGE_API_BASE") or os.getenv("OPENAI_API_BASE"))
    parser.add_argument("--judge-api-key", default=os.getenv("JUDGE_API_KEY") or os.getenv("OPENAI_API_KEY"))
    parser.add_argument("--max-workers", type=int, default=int(os.getenv("JUDGE_MAX_WORKERS", "8")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("JUDGE_TIMEOUT", "60")))
    parser.add_argument("--repos", default="", help="Comma-separated repo list (optional)")
    args = parser.parse_args()

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
    )


if __name__ == "__main__":
    main()
