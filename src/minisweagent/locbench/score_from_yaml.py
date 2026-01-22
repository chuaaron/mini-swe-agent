#!/usr/bin/env python3

"""Run LocBench scoring from a YAML config."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import yaml

from minisweagent.locbench.config_loader import load_config
from minisweagent.locbench.score import score_locbench, write_scores


def _load_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("score config must be a YAML mapping")
    return data


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def main() -> None:
    parser = argparse.ArgumentParser(description="Score LocBench outputs from YAML config")
    parser.add_argument("--config", required=True, help="Path to score config YAML")
    args = parser.parse_args()

    local_config = load_config()
    config_path = Path(args.config).expanduser().resolve()
    config = _load_config(config_path)

    dataset_path = _normalize_optional(config.get("dataset_path"))
    if not dataset_path:
        dataset_path = _normalize_optional((local_config.get("paths", {}) if isinstance(local_config, dict) else {}).get("dataset_root"))
    if not dataset_path:
        raise ValueError("dataset_path must be set (score config or locbench config)")
    dataset_path = Path(dataset_path).expanduser().resolve()

    pred_path = _normalize_optional(config.get("loc_output"))
    if not pred_path:
        raise ValueError("loc_output must be set in score config")
    pred_path = Path(pred_path).expanduser().resolve()

    output_root_value = _normalize_optional(config.get("output_root"))
    if not output_root_value:
        output_root_value = _normalize_optional(
            (local_config.get("paths", {}) if isinstance(local_config, dict) else {}).get("output_root")
        )
    output_root = Path(output_root_value).expanduser().resolve() if output_root_value else pred_path.parent

    output_model = _normalize_optional(config.get("output_model_name")) or "unknown_model"
    method = _normalize_optional(config.get("method")) or "unknown_method"
    output_path_value = _normalize_optional(config.get("output_path"))
    if output_path_value:
        output_path = Path(output_path_value).expanduser().resolve()
    else:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_path = output_root / "scores" / output_model / method / f"loc_scores_{timestamp}.json"

    results, summary = score_locbench(pred_path, dataset_path)
    write_scores(output_path, results, summary)
    print(f"Scored {summary['instances']} records -> {output_path}")


if __name__ == "__main__":
    main()
