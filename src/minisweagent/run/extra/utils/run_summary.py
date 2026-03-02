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


def _collect_float_values(instance_summaries: list[dict[str, Any]], keys: list[str]) -> list[float]:
    values: list[float] = []
    for item in instance_summaries:
        for key in keys:
            raw = item.get(key)
            if raw is None:
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                pass
            break
    return values


def _mean_or_none(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _acc_or_none(values: list[float]) -> float | None:
    return (sum(1 for value in values if value >= 1.0) / len(values)) if values else None


def _rate_or_none(numerator: int, denominator: int) -> float | None:
    return (numerator / denominator) if denominator else None


def _build_subset_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    function_recall_at_1 = _collect_float_values(rows, ["function_recall_at_1", "entity_recall_at_1"])
    function_recall_all = _collect_float_values(rows, ["function_recall_all", "entity_recall_all"])
    edit_function_recall_at_1 = _collect_float_values(rows, ["edit_function_recall_at_1"])
    edit_function_recall_all = _collect_float_values(rows, ["edit_function_recall_all"])
    added_function_recall_all = _collect_float_values(rows, ["added_function_recall_all"])
    submitted_counts = [int(value) for value in _collect_float_values(rows, ["submitted_function_count"])]
    steps = [int(item.get("steps", 0) or 0) for item in rows]
    correct_values = [item.get("correct") for item in rows if item.get("correct") is not None]
    return {
        "count": len(rows),
        "pass_rate": (sum(1 for value in correct_values if value) / len(correct_values)) if correct_values else None,
        "function_acc_at_1": _acc_or_none(function_recall_at_1),
        "function_recall_at_1": _mean_or_none(function_recall_at_1),
        "function_acc_all": _acc_or_none(function_recall_all),
        "function_recall_all": _mean_or_none(function_recall_all),
        "edit_function_acc_at_1": _acc_or_none(edit_function_recall_at_1),
        "edit_function_recall_at_1": _mean_or_none(edit_function_recall_at_1),
        "edit_function_acc_all": _acc_or_none(edit_function_recall_all),
        "edit_function_recall_all": _mean_or_none(edit_function_recall_all),
        "added_function_acc_all": _acc_or_none(added_function_recall_all),
        "added_function_recall_all": _mean_or_none(added_function_recall_all),
        "avg_submitted_function_count": _mean_or_none([float(value) for value in submitted_counts]),
        "under_two_functions_submission_rate": (
            sum(1 for value in submitted_counts if value <= 2) / len(submitted_counts) if submitted_counts else None
        ),
        "avg_steps": _avg(steps),
    }


def _build_overall_stats(instance_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [int(item.get("steps", 0) or 0) for item in instance_summaries]
    trace_tokens = [int(item.get("trace_tokens", 0) or 0) for item in instance_summaries]
    billed_tokens = [int(item.get("billed_tokens", 0) or 0) for item in instance_summaries]
    costs = [float(item.get("cost_usd", 0.0) or 0.0) for item in instance_summaries]
    file_recall_by_k = {
        k: _collect_float_values(instance_summaries, [f"file_recall_at_{k}"]) for k in (1, 5, 10)
    }
    function_recall_by_k = {
        k: _collect_float_values(instance_summaries, [f"function_recall_at_{k}", f"entity_recall_at_{k}"])
        for k in (1, 5, 10)
    }
    edit_function_recall_by_k = {
        k: _collect_float_values(instance_summaries, [f"edit_function_recall_at_{k}"])
        for k in (1, 5, 10)
    }
    added_function_recall_by_k = {
        k: _collect_float_values(instance_summaries, [f"added_function_recall_at_{k}"])
        for k in (1, 5, 10)
    }
    file_recall_all_values = _collect_float_values(instance_summaries, ["file_recall_all"])
    function_recall_all_values = _collect_float_values(
        instance_summaries,
        ["function_recall_all", "entity_recall_all"],
    )
    edit_function_recall_all_values = _collect_float_values(instance_summaries, ["edit_function_recall_all"])
    added_function_recall_all_values = _collect_float_values(instance_summaries, ["added_function_recall_all"])
    added_eval_count = sum(
        1 for item in instance_summaries if int(item.get("gt_added_function_count", 0) or 0) > 0
    )
    submitted_function_count_values = _collect_float_values(instance_summaries, ["submitted_function_count"])
    submitted_function_counts = [int(value) for value in submitted_function_count_values]
    submitted_file_hint_count_values = _collect_float_values(instance_summaries, ["submitted_file_hint_count"])
    submitted_qualified_ratio_values = _collect_float_values(instance_summaries, ["submitted_qualified_function_ratio"])

    correct_values = [item.get("correct") for item in instance_summaries if item.get("correct") is not None]
    function_hit_values = [
        bool(item.get("function_hit_any"))
        for item in instance_summaries
        if item.get("function_hit_any") is not None
    ]
    if not function_hit_values:
        function_hit_values = [
            bool(item.get("entity_hit_any"))
            for item in instance_summaries
            if item.get("entity_hit_any") is not None
        ]
    pass_rate = None
    if correct_values:
        pass_rate = sum(1 for value in correct_values if value) / len(correct_values)

    success_count = sum(1 for item in instance_summaries if item.get("exit_status") == "Submitted")

    stats = {
        "total_instances": len(instance_summaries),
        "success_count": success_count,
        "pass_rate": pass_rate,
        # Keep legacy naming for compatibility.
        "acc_at_1": _acc_or_none(file_recall_by_k[1]),
        "file_acc_at_1": _acc_or_none(file_recall_by_k[1]),
        "file_recall_at_1": _mean_or_none(file_recall_by_k[1]),
        "file_acc_at_5": _acc_or_none(file_recall_by_k[5]),
        "file_recall_at_5": _mean_or_none(file_recall_by_k[5]),
        "file_acc_at_10": _acc_or_none(file_recall_by_k[10]),
        "file_recall_at_10": _mean_or_none(file_recall_by_k[10]),
        # New main metrics for full-coverage evaluation.
        "acc_all": _acc_or_none(file_recall_all_values),
        "file_acc_all": _acc_or_none(file_recall_all_values),
        "file_recall_all": _mean_or_none(file_recall_all_values),
        "function_hit_rate": (
            sum(1 for value in function_hit_values if value) / len(function_hit_values)
            if function_hit_values
            else None
        ),
        "function_acc_at_1": _acc_or_none(function_recall_by_k[1]),
        "function_recall_at_1": _mean_or_none(function_recall_by_k[1]),
        "function_acc_at_5": _acc_or_none(function_recall_by_k[5]),
        "function_recall_at_5": _mean_or_none(function_recall_by_k[5]),
        "function_acc_at_10": _acc_or_none(function_recall_by_k[10]),
        "function_recall_at_10": _mean_or_none(function_recall_by_k[10]),
        "function_acc_all": _acc_or_none(function_recall_all_values),
        "function_recall_all": _mean_or_none(function_recall_all_values),
        "edit_function_acc_at_1": _acc_or_none(edit_function_recall_by_k[1]),
        "edit_function_recall_at_1": _mean_or_none(edit_function_recall_by_k[1]),
        "edit_function_acc_at_5": _acc_or_none(edit_function_recall_by_k[5]),
        "edit_function_recall_at_5": _mean_or_none(edit_function_recall_by_k[5]),
        "edit_function_acc_at_10": _acc_or_none(edit_function_recall_by_k[10]),
        "edit_function_recall_at_10": _mean_or_none(edit_function_recall_by_k[10]),
        "edit_function_acc_all": _acc_or_none(edit_function_recall_all_values),
        "edit_function_recall_all": _mean_or_none(edit_function_recall_all_values),
        "added_function_eval_count": added_eval_count,
        "added_function_acc_at_1": _acc_or_none(added_function_recall_by_k[1]),
        "added_function_recall_at_1": _mean_or_none(added_function_recall_by_k[1]),
        "added_function_acc_at_5": _acc_or_none(added_function_recall_by_k[5]),
        "added_function_recall_at_5": _mean_or_none(added_function_recall_by_k[5]),
        "added_function_acc_at_10": _acc_or_none(added_function_recall_by_k[10]),
        "added_function_recall_at_10": _mean_or_none(added_function_recall_by_k[10]),
        "added_function_acc_all": _acc_or_none(added_function_recall_all_values),
        "added_function_recall_all": _mean_or_none(added_function_recall_all_values),
        "added_coverage_rate": _mean_or_none(added_function_recall_all_values),
        "added_coverage_acc_all": _acc_or_none(added_function_recall_all_values),
        "submission_functions_payload_rate": (
            len(submitted_function_counts) / len(instance_summaries) if instance_summaries else None
        ),
        "avg_submitted_function_count": _mean_or_none(submitted_function_count_values),
        "p50_submitted_function_count": (
            _percentile(submitted_function_counts, 50) if submitted_function_counts else None
        ),
        "p90_submitted_function_count": (
            _percentile(submitted_function_counts, 90) if submitted_function_counts else None
        ),
        "single_function_submission_rate": (
            sum(1 for value in submitted_function_counts if value == 1) / len(submitted_function_counts)
            if submitted_function_counts
            else None
        ),
        "under_two_functions_submission_rate": (
            sum(1 for value in submitted_function_counts if value <= 2) / len(submitted_function_counts)
            if submitted_function_counts
            else None
        ),
        "avg_submitted_file_hint_count": _mean_or_none(submitted_file_hint_count_values),
        "avg_submitted_qualified_function_ratio": _mean_or_none(submitted_qualified_ratio_values),
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
        anti_laziness_applicable_instances = [
            item for item in radar_called_instances if bool(item.get("radar_anti_laziness_applicable"))
        ]
        anti_laziness_applicable_count = len(anti_laziness_applicable_instances)
        anti_laziness_compliant_count = sum(
            1 for item in anti_laziness_applicable_instances if bool(item.get("radar_anti_laziness_satisfied"))
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

        single_call_instances = [
            item for item in radar_called_instances if int(item.get("radar_tool_calls", 0) or 0) == 1
        ]
        multi_call_instances = [
            item for item in radar_called_instances if int(item.get("radar_tool_calls", 0) or 0) >= 2
        ]
        single_radar_call_count = len(single_call_instances)
        multi_radar_call_count = len(multi_call_instances)
        cross_dir_inspected_count = sum(
            1 for item in radar_called_instances if bool(item.get("radar_cross_dir_inspected"))
        )
        first_candidate_fixation_count = sum(
            1 for item in radar_called_instances if bool(item.get("radar_first_candidate_fixated"))
        )
        fixation_applicable_instances = [
            item for item in radar_called_instances if bool(item.get("radar_anti_laziness_applicable"))
        ]

        def _is_success(entry: dict[str, Any]) -> bool:
            if entry.get("correct") is not None:
                return bool(entry.get("correct"))
            return str(entry.get("exit_status") or "") == "Submitted"

        single_call_success_rate = (
            sum(1 for item in single_call_instances if _is_success(item)) / single_radar_call_count
            if single_radar_call_count
            else None
        )
        second_call_success_rate = (
            sum(1 for item in multi_call_instances if _is_success(item)) / multi_radar_call_count
            if multi_radar_call_count
            else None
        )
        second_radar_call_success_delta = (
            second_call_success_rate - single_call_success_rate
            if second_call_success_rate is not None and single_call_success_rate is not None
            else None
        )

        stats.update(
            {
                "radar_called_count": radar_called_count,
                "verification_compliance_rate": (
                    radar_verified_count / radar_called_count if radar_called_count else None
                ),
                "anti_laziness_applicable_count": anti_laziness_applicable_count,
                "anti_laziness_compliant_count": anti_laziness_compliant_count,
                "anti_laziness_violation_count": anti_laziness_applicable_count - anti_laziness_compliant_count,
                "anti_laziness_compliance_rate": (
                    anti_laziness_compliant_count / anti_laziness_applicable_count
                    if anti_laziness_applicable_count
                    else None
                ),
                "blocked_submission_count": blocked_submission_count,
                "avg_tool_output_chars": avg_tool_output_chars,
                "premature_submit_rate": premature_submit_rate,
                "single_radar_call_count": single_radar_call_count,
                "single_radar_call_rate": (
                    single_radar_call_count / radar_called_count if radar_called_count else None
                ),
                "multi_radar_call_count": multi_radar_call_count,
                "cross_dir_inspected_count": cross_dir_inspected_count,
                "cross_dir_inspection_rate": (
                    cross_dir_inspected_count / radar_called_count if radar_called_count else None
                ),
                "first_candidate_fixation_count": first_candidate_fixation_count,
                "first_candidate_fixation_rate": (
                    first_candidate_fixation_count / radar_called_count if radar_called_count else None
                ),
                "first_candidate_fixation_applicable_count": len(fixation_applicable_instances),
                "first_candidate_fixation_applicable_rate": (
                    sum(1 for item in fixation_applicable_instances if bool(item.get("radar_first_candidate_fixated")))
                    / len(fixation_applicable_instances)
                    if fixation_applicable_instances
                    else None
                ),
                "single_radar_call_success_rate": single_call_success_rate,
                "second_radar_call_success_rate": second_call_success_rate,
                "second_radar_call_success_delta": second_radar_call_success_delta,
            }
        )

    has_oracle_fields = any(bool(item.get("oracle_sniper_mode")) for item in instance_summaries)
    if has_oracle_fields:
        oracle_instances = [item for item in instance_summaries if bool(item.get("oracle_sniper_mode"))]
        oracle_instance_count = len(oracle_instances)
        oracle_provided_instances = [item for item in oracle_instances if bool(item.get("oracle_file_provided"))]
        oracle_file_provided_count = len(oracle_provided_instances)

        oracle_verified_count = 0
        for item in oracle_provided_instances:
            verified = item.get("oracle_verification_satisfied")
            if verified is None:
                verified = item.get("radar_verification_satisfied")
            if bool(verified):
                oracle_verified_count += 1

        oracle_blocked_submission_count = sum(
            int(item.get("blocked_submission_count", 0) or 0) for item in oracle_instances
        )
        oracle_entity_hit_count = sum(1 for item in oracle_provided_instances if bool(item.get("entity_hit_any")))

        oracle_success_instances = [item for item in oracle_instances if item.get("correct") is True]
        if not oracle_success_instances and oracle_instances and all(
            "correct" not in item for item in oracle_instances
        ):
            oracle_success_instances = [item for item in oracle_instances if item.get("exit_status") == "Submitted"]
        oracle_success_steps = [int(item.get("steps", 0) or 0) for item in oracle_success_instances]

        stats.update(
            {
                "oracle_instance_count": oracle_instance_count,
                "oracle_file_provided_count": oracle_file_provided_count,
                "oracle_file_provided_rate": (
                    oracle_file_provided_count / oracle_instance_count if oracle_instance_count else None
                ),
                "oracle_verification_compliance_rate": (
                    oracle_verified_count / oracle_file_provided_count if oracle_file_provided_count else None
                ),
                "entity_hit_rate_given_oracle_file": (
                    oracle_entity_hit_count / oracle_file_provided_count if oracle_file_provided_count else None
                ),
                "oracle_blocked_submission_count": oracle_blocked_submission_count,
                "steps_to_success_in_oracle_count": len(oracle_success_steps),
                "steps_to_success_in_oracle_mean": _avg(oracle_success_steps),
                "steps_to_success_in_oracle_p50": _percentile(oracle_success_steps, 50),
                "steps_to_success_in_oracle_p90": _percentile(oracle_success_steps, 90),
            }
        )

    has_gt_profile_fields = any("gt_file_count_all" in item for item in instance_summaries)
    if has_gt_profile_fields:
        single_file_instances = [
            item for item in instance_summaries if int(item.get("gt_file_count_all", 0) or 0) == 1
        ]
        multi_file_instances = [
            item for item in instance_summaries if int(item.get("gt_file_count_all", 0) or 0) >= 2
        ]
        function_count_1_instances = [
            item for item in instance_summaries if int(item.get("gt_function_count_all", 0) or 0) == 1
        ]
        function_count_2_instances = [
            item for item in instance_summaries if int(item.get("gt_function_count_all", 0) or 0) == 2
        ]
        function_count_3_instances = [
            item for item in instance_summaries if int(item.get("gt_function_count_all", 0) or 0) == 3
        ]
        function_count_4_plus_instances = [
            item for item in instance_summaries if int(item.get("gt_function_count_all", 0) or 0) >= 4
        ]
        has_added_instances = [
            item for item in instance_summaries if int(item.get("gt_added_function_count", 0) or 0) > 0
        ]
        no_added_instances = [
            item for item in instance_summaries if int(item.get("gt_added_function_count", 0) or 0) == 0
        ]
        stats.update(
            {
                "single_file_instance_count": len(single_file_instances),
                "single_file_instance_rate": _rate_or_none(len(single_file_instances), len(instance_summaries)),
                "multi_file_instance_count": len(multi_file_instances),
                "multi_file_instance_rate": _rate_or_none(len(multi_file_instances), len(instance_summaries)),
                "stats_by_gt_bucket": {
                    "single_file": _build_subset_stats(single_file_instances),
                    "multi_file": _build_subset_stats(multi_file_instances),
                    "func_count_1": _build_subset_stats(function_count_1_instances),
                    "func_count_2": _build_subset_stats(function_count_2_instances),
                    "func_count_3": _build_subset_stats(function_count_3_instances),
                    "func_count_4_plus": _build_subset_stats(function_count_4_plus_instances),
                    "has_added_functions": _build_subset_stats(has_added_instances),
                    "no_added_functions": _build_subset_stats(no_added_instances),
                },
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
