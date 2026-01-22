#!/usr/bin/env python3

"""Run SWE-QA-Bench scoring from a single YAML config."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import yaml

from minisweagent.swe_qa_bench.config_loader import load_config
from minisweagent.swe_qa_bench.score import _resolve_api_url, score_dataset


def _load_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("score config must be a YAML mapping")
    return data


def _apply_env(env: dict[str, Any] | None) -> None:
    if not env:
        return
    for key, value in env.items():
        if value is None:
            continue
        os.environ[str(key)] = str(value)


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_repos(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or None
    text = str(value).strip()
    if not text:
        return None
    return [item.strip() for item in text.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Score SWE-QA-Bench answers from YAML config")
    parser.add_argument("--config", required=True, help="Path to score config YAML")
    args = parser.parse_args()

    # Ensure local.yaml env is applied (API keys, base URLs).
    local_config = load_config()

    config_path = Path(args.config).expanduser().resolve()
    config = _load_config(config_path)

    _apply_env(config.get("env"))

    dataset_root = Path(config.get("dataset_root")).expanduser().resolve()
    output_root_value = config.get("output_root")
    if output_root_value is None:
        output_root_value = (local_config.get("paths", {}) if isinstance(local_config, dict) else {}).get("output_root")
    output_root = Path(str(output_root_value)).expanduser().resolve() if output_root_value else None
    candidate_model = _normalize_optional(config.get("candidate_model"))
    method = _normalize_optional(config.get("method"))
    judge_model = _normalize_optional(config.get("judge_model"))
    judge_api_base = _normalize_optional(config.get("judge_api_base"))
    judge_api_key = _normalize_optional(config.get("judge_api_key"))
    max_workers = int(config.get("max_workers", 8))
    timeout = int(config.get("timeout", 60))
    repos = _as_repos(config.get("repos"))

    if not candidate_model:
        raise ValueError("candidate_model must be set")
    if not method:
        raise ValueError("method must be set")
    if not judge_model:
        judge_model = candidate_model
    if not judge_api_base:
        judge_api_base = os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL") or ""
    if not judge_api_key:
        judge_api_key = os.getenv("OPENAI_API_KEY") or ""
    if not judge_api_base:
        raise ValueError("judge_api_base must be set")
    if not judge_api_key:
        raise ValueError("judge_api_key must be set")

    api_url = _resolve_api_url(judge_api_base)

    score_dataset(
        dataset_root=dataset_root,
        candidate_model=candidate_model,
        method=method,
        judge_model=judge_model,
        api_url=api_url,
        api_key=judge_api_key,
        repos=repos,
        max_workers=max_workers,
        timeout=timeout,
        output_root=output_root,
    )


if __name__ == "__main__":
    main()
