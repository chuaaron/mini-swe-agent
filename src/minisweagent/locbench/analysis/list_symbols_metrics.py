"""Analyze list_symbols usage/hit/accuracy uplift from LocBench run trajectories."""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minisweagent.locbench.utils import load_jsonl
from minisweagent.tools.registry import parse_tool_command

_ACTION_RE = re.compile(r"```bash\s*\n(.*?)\n```", re.DOTALL)


@dataclass
class InstanceTrajectoryMetrics:
    instance_id: str
    list_symbols_used: bool
    list_symbols_calls: int
    list_symbols_files: list[str]


def _parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return None


def _normalize_path(value: str) -> str:
    cleaned = (value or "").strip()
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return Path(cleaned).as_posix()


def _extract_single_action(content: str) -> str | None:
    actions = _ACTION_RE.findall(content or "")
    normalized = [action.replace("\r\n", "\n").strip() for action in actions]
    unique = list(dict.fromkeys(item for item in normalized if item))
    if len(unique) != 1:
        return None
    return unique[0]


def _next_user_content(messages: list[dict[str, Any]], start_index: int) -> str:
    for msg in messages[start_index + 1 :]:
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


def _extract_executed_tool_calls(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for idx, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        action = _extract_single_action(str(msg.get("content") or ""))
        if not action or not action.startswith("@tool "):
            continue
        next_user = _next_user_content(messages, idx)
        if "<tool_result>" not in next_user:
            continue
        try:
            tool_name, args = parse_tool_command(action)
        except Exception:
            continue
        calls.append((tool_name, args))
    return calls


def _trajectory_metrics(traj_file: Path) -> InstanceTrajectoryMetrics:
    payload = json.loads(traj_file.read_text(encoding="utf-8"))
    instance_id = str(payload.get("instance_id") or traj_file.name.replace(".traj.json", ""))
    messages = payload.get("messages") or []
    tool_calls = _extract_executed_tool_calls(messages)
    list_symbols_files: list[str] = []
    for tool_name, args in tool_calls:
        if tool_name != "list_symbols":
            continue
        raw = args.get("file", args.get("path"))
        if isinstance(raw, str) and raw.strip():
            list_symbols_files.append(_normalize_path(raw))
    return InstanceTrajectoryMetrics(
        instance_id=instance_id,
        list_symbols_used=bool(list_symbols_files),
        list_symbols_calls=len(list_symbols_files),
        list_symbols_files=list_symbols_files,
    )


def _load_run_instances(run_dir: Path) -> list[dict[str, Any]]:
    summary_path = run_dir / "run_summary.json"
    if not summary_path.exists():
        raise ValueError(f"run_summary.json not found: {summary_path}")
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    instances = payload.get("instances")
    if not isinstance(instances, list):
        raise ValueError(f"Invalid run_summary format: {summary_path}")
    return [item for item in instances if isinstance(item, dict) and item.get("instance_id")]


def _load_gt_files(dataset_path: Path) -> dict[str, set[str]]:
    gt: dict[str, set[str]] = {}
    for record in load_jsonl(dataset_path):
        instance_id = record.get("instance_id")
        if not instance_id:
            continue
        files: set[str] = set()
        for key in ("edit_functions", "added_functions"):
            for item in record.get(key, []) or []:
                if not isinstance(item, str) or ":" not in item:
                    continue
                files.add(_normalize_path(item.rsplit(":", 1)[0]))
        gt[str(instance_id)] = files
    return gt


def _path_hits_gt(path: str, gt_files: set[str]) -> bool:
    pred = _normalize_path(path)
    if not pred or not gt_files:
        return False
    if pred in gt_files:
        return True
    if any(gt.endswith(f"/{pred}") for gt in gt_files):
        return True
    if any(pred.endswith(f"/{gt}") for gt in gt_files):
        return True
    basename = Path(pred).name
    basename_matches = [gt for gt in gt_files if Path(gt).name == basename]
    return bool(basename and len(basename_matches) == 1)


def _resolve_default_dataset() -> Path | None:
    candidates = [
        Path("data/Loc-Bench_V1_dataset.jsonl"),
        Path("../data/Loc-Bench_V1_dataset.jsonl"),
    ]
    for path in candidates:
        resolved = path.resolve()
        if resolved.exists():
            return resolved
    return None


def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def _safe_mean(values: list[int]) -> float | None:
    if not values:
        return None
    return float(sum(values)) / len(values)


def _safe_accuracy(rows: list[dict[str, Any]]) -> float | None:
    labels = [row["correct"] for row in rows if row["correct"] is not None]
    if not labels:
        return None
    return float(sum(1 for label in labels if label)) / len(labels)


def _to_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100.0


def compute_list_symbols_metrics(
    *,
    run_dir: Path,
    dataset_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_instances = _load_run_instances(run_dir)
    gt_map = _load_gt_files(dataset_path)
    traj_dir = run_dir / "trajectories"
    traj_metrics_map: dict[str, InstanceTrajectoryMetrics] = {}
    if traj_dir.exists():
        for traj_file in sorted(traj_dir.glob("*.traj.json")):
            traj_metrics = _trajectory_metrics(traj_file)
            traj_metrics_map[traj_metrics.instance_id] = traj_metrics

    per_instance_rows: list[dict[str, Any]] = []
    for row in run_instances:
        instance_id = str(row["instance_id"])
        traj = traj_metrics_map.get(
            instance_id,
            InstanceTrajectoryMetrics(
                instance_id=instance_id,
                list_symbols_used=False,
                list_symbols_calls=0,
                list_symbols_files=[],
            ),
        )
        gt_files = gt_map.get(instance_id, set())
        hit_flags = [_path_hits_gt(item, gt_files) for item in traj.list_symbols_files]
        hit_calls = sum(1 for flag in hit_flags if flag)
        per_instance_rows.append(
            {
                "instance_id": instance_id,
                "correct": _parse_bool(row.get("correct")),
                "radar_called": _parse_bool(row.get("radar_called")),
                "list_symbols_used": traj.list_symbols_used,
                "list_symbols_calls": traj.list_symbols_calls,
                "list_symbols_hit_calls": hit_calls,
                "list_symbols_hit_any": bool(hit_calls),
                "list_symbols_called_files": traj.list_symbols_files,
                "gt_files": sorted(gt_files),
            }
        )

    total_instances = len(per_instance_rows)
    used_rows = [row for row in per_instance_rows if row["list_symbols_used"]]
    non_used_rows = [row for row in per_instance_rows if not row["list_symbols_used"]]
    radar_rows = [row for row in per_instance_rows if row["radar_called"] is True]
    radar_non_used_rows = [row for row in radar_rows if not row["list_symbols_used"]]

    used_call_counts = [int(row["list_symbols_calls"]) for row in used_rows]
    total_calls = sum(used_call_counts)
    hit_call_total = sum(int(row["list_symbols_hit_calls"]) for row in per_instance_rows)
    used_hit_instances = sum(1 for row in used_rows if row["list_symbols_hit_any"])

    acc_overall = _safe_accuracy(per_instance_rows)
    acc_used = _safe_accuracy(used_rows)
    acc_non_used_radar = _safe_accuracy(radar_non_used_rows)
    acc_used_hit = _safe_accuracy([row for row in used_rows if row["list_symbols_hit_any"]])
    acc_used_no_hit = _safe_accuracy([row for row in used_rows if not row["list_symbols_hit_any"]])

    uplift_pp = None
    uplift_relative_pct = None
    if acc_used is not None and acc_non_used_radar is not None:
        uplift_pp = (acc_used - acc_non_used_radar) * 100.0
        if acc_non_used_radar > 0:
            uplift_relative_pct = ((acc_used / acc_non_used_radar) - 1.0) * 100.0

    summary: dict[str, Any] = {
        "run_dir": str(run_dir.resolve()),
        "dataset_path": str(dataset_path.resolve()),
        "total_instances": total_instances,
        "trajectory_coverage_rate": _rate(len(traj_metrics_map), total_instances),
        "radar_called_count": len(radar_rows),
        "list_symbols_used_count": len(used_rows),
        "list_symbols_usage_rate_all": _rate(len(used_rows), total_instances),
        "list_symbols_usage_rate_given_radar": _rate(
            sum(1 for row in used_rows if row["radar_called"] is True), len(radar_rows)
        ),
        "list_symbols_total_calls": total_calls,
        "list_symbols_avg_calls_when_used": _safe_mean(used_call_counts),
        "list_symbols_call_hit_rate": _rate(hit_call_total, total_calls),
        "list_symbols_instance_hit_rate_given_used": _rate(used_hit_instances, len(used_rows)),
        "accuracy_overall": acc_overall,
        "accuracy_when_list_symbols_used": acc_used,
        "accuracy_when_not_used_given_radar": acc_non_used_radar,
        "accuracy_uplift_used_vs_not_used_given_radar_pp": uplift_pp,
        "accuracy_uplift_used_vs_not_used_given_radar_relative_pct": uplift_relative_pct,
        "accuracy_when_list_symbols_hit": acc_used_hit,
        "accuracy_when_list_symbols_used_but_no_hit": acc_used_no_hit,
        "accuracy_overall_pct": _to_percent(acc_overall),
        "accuracy_when_list_symbols_used_pct": _to_percent(acc_used),
        "accuracy_when_not_used_given_radar_pct": _to_percent(acc_non_used_radar),
        "list_symbols_call_hit_rate_pct": _to_percent(_rate(hit_call_total, total_calls)),
        "list_symbols_instance_hit_rate_given_used_pct": _to_percent(_rate(used_hit_instances, len(used_rows))),
    }
    return summary, per_instance_rows


def _write_instance_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "instance_id",
        "correct",
        "radar_called",
        "list_symbols_used",
        "list_symbols_calls",
        "list_symbols_hit_calls",
        "list_symbols_hit_any",
        "list_symbols_called_files",
        "gt_files",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            serialized["list_symbols_called_files"] = json.dumps(row["list_symbols_called_files"], ensure_ascii=True)
            serialized["gt_files"] = json.dumps(row["gt_files"], ensure_ascii=True)
            writer.writerow(serialized)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="LocBench run directory containing run_summary.json")
    parser.add_argument(
        "--dataset",
        default="",
        help="Path to Loc-Bench dataset JSONL (default: auto detect data/Loc-Bench_V1_dataset.jsonl)",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Output JSON path (default: <run-dir>/list_symbols_metrics.json)",
    )
    parser.add_argument(
        "--output-csv",
        default="",
        help="Output CSV path (default: <run-dir>/list_symbols_instance_metrics.csv)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists():
        raise ValueError(f"run directory not found: {run_dir}")

    if args.dataset:
        dataset_path = Path(args.dataset).expanduser().resolve()
    else:
        inferred = _resolve_default_dataset()
        if inferred is None:
            raise ValueError("dataset not found. Please provide --dataset path.")
        dataset_path = inferred
    if not dataset_path.exists():
        raise ValueError(f"dataset not found: {dataset_path}")

    summary, rows = compute_list_symbols_metrics(run_dir=run_dir, dataset_path=dataset_path)

    output_json = Path(args.output_json).expanduser().resolve() if args.output_json else run_dir / "list_symbols_metrics.json"
    output_csv = (
        Path(args.output_csv).expanduser().resolve()
        if args.output_csv
        else run_dir / "list_symbols_instance_metrics.csv"
    )
    output_json.write_text(json.dumps({"summary": summary, "instances": rows}, indent=2, ensure_ascii=True), encoding="utf-8")
    _write_instance_csv(output_csv, rows)

    print(json.dumps(summary, indent=2, ensure_ascii=True))
    print(f"\nWrote: {output_json}")
    print(f"Wrote: {output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
