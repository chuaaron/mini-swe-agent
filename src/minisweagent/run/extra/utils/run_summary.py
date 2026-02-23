"""Utilities for writing run summary artifacts."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    rank = int(math.ceil((percentile / 100.0) * len(sorted_values))) - 1
    rank = max(0, min(rank, len(sorted_values) - 1))
    return int(sorted_values[rank])


def _avg(values: list[int]) -> float:
    return float(sum(values)) / len(values) if values else 0.0


def _build_overall_stats(instance_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [int(item.get("steps", 0) or 0) for item in instance_summaries]
    trace_tokens = [int(item.get("trace_tokens", 0) or 0) for item in instance_summaries]
    billed_tokens = [int(item.get("billed_tokens", 0) or 0) for item in instance_summaries]
    costs = [float(item.get("cost_usd", 0.0) or 0.0) for item in instance_summaries]

    correct_values = [item.get("correct") for item in instance_summaries if item.get("correct") is not None]
    pass_rate = None
    if correct_values:
        pass_rate = sum(1 for value in correct_values if value) / len(correct_values)

    success_count = sum(1 for item in instance_summaries if item.get("exit_status") == "Submitted")

    stats = {
        "total_instances": len(instance_summaries),
        "success_count": success_count,
        "pass_rate": pass_rate,
        "avg_steps": _avg(steps),
        "p50_steps": _percentile(steps, 50),
        "p90_steps": _percentile(steps, 90),
        "avg_trace_tokens": _avg(trace_tokens),
        "p50_trace_tokens": _percentile(trace_tokens, 50),
        "p90_trace_tokens": _percentile(trace_tokens, 90),
        "avg_billed_tokens": _avg(billed_tokens),
        "p50_billed_tokens": _percentile(billed_tokens, 50),
        "p90_billed_tokens": _percentile(billed_tokens, 90),
        "total_cost": sum(costs),
    }

    has_radar_fields = any("radar_called" in item for item in instance_summaries)
    if has_radar_fields:
        radar_called_instances = [item for item in instance_summaries if bool(item.get("radar_called"))]
        radar_called_count = len(radar_called_instances)
        radar_verified_count = sum(
            1 for item in radar_called_instances if bool(item.get("radar_verification_satisfied"))
        )
        blocked_submission_count = sum(int(item.get("blocked_submission_count", 0) or 0) for item in instance_summaries)

        total_tool_calls = sum(int(item.get("radar_tool_calls", 0) or 0) for item in instance_summaries)
        total_tool_output_chars = sum(int(item.get("radar_tool_output_chars", 0) or 0) for item in instance_summaries)
        avg_tool_output_chars = (float(total_tool_output_chars) / total_tool_calls) if total_tool_calls else 0.0

        premature_submit_instances = sum(
            1 for item in radar_called_instances if int(item.get("blocked_submission_count", 0) or 0) > 0
        )
        premature_submit_rate = (
            premature_submit_instances / radar_called_count if radar_called_count else None
        )

        stats.update(
            {
                "radar_called_count": radar_called_count,
                "verification_compliance_rate": (
                    radar_verified_count / radar_called_count if radar_called_count else None
                ),
                "blocked_submission_count": blocked_submission_count,
                "avg_tool_output_chars": avg_tool_output_chars,
                "premature_submit_rate": premature_submit_rate,
            }
        )

    return stats


def _build_exit_status_counts(instance_summaries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in instance_summaries:
        status = str(item.get("exit_status") or "Unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def write_run_summary(
    path: Path,
    *,
    meta: dict[str, Any],
    instance_summaries: list[dict[str, Any]],
    csv_path: Path | None = None,
) -> None:
    payload = {
        "meta": meta,
        "stats_overall": _build_overall_stats(instance_summaries),
        "stats_by_exit_status": _build_exit_status_counts(instance_summaries),
        "instances": instance_summaries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    if csv_path is not None:
        _write_run_summary_csv(csv_path, instance_summaries)


def _write_run_summary_csv(path: Path, instance_summaries: list[dict[str, Any]]) -> None:
    if not instance_summaries:
        return
    base_fields = [
        "instance_id",
        "exit_status",
        "steps",
        "trace_tokens",
        "billed_tokens",
        "cost_usd",
        "correct",
    ]
    extra_fields: list[str] = []
    seen = set(base_fields)
    for record in instance_summaries:
        for key in record.keys():
            if key not in seen:
                seen.add(key)
                extra_fields.append(key)
    fieldnames = base_fields + sorted(extra_fields)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in instance_summaries:
            writer.writerow(record)
