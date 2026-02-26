import json
from pathlib import Path

from minisweagent.locbench.analysis.list_symbols_metrics import compute_list_symbols_metrics


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def test_compute_list_symbols_metrics_from_trajectories(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_json(
        run_dir / "run_summary.json",
        {
            "meta": {},
            "stats_overall": {},
            "instances": [
                {"instance_id": "repo__a-1", "correct": True, "radar_called": True},
                {"instance_id": "repo__b-2", "correct": False, "radar_called": True},
                {"instance_id": "repo__c-3", "correct": False, "radar_called": True},
            ],
        },
    )

    _write_jsonl(
        tmp_path / "dataset.jsonl",
        [
            {"instance_id": "repo__a-1", "edit_functions": ["src/a.py:fix_a"], "added_functions": []},
            {"instance_id": "repo__b-2", "edit_functions": ["src/b.py:fix_b"], "added_functions": []},
            {"instance_id": "repo__c-3", "edit_functions": ["src/c.py:fix_c"], "added_functions": []},
        ],
    )

    _write_json(
        run_dir / "trajectories" / "repo__a-1.traj.json",
        {
            "instance_id": "repo__a-1",
            "messages": [
                {"role": "assistant", "content": "THOUGHT\n```bash\n@tool list_symbols --file src/a.py\n```"},
                {"role": "user", "content": "<tool_result>\nFile skeleton...\n</tool_result>"},
            ],
        },
    )
    _write_json(
        run_dir / "trajectories" / "repo__b-2.traj.json",
        {
            "instance_id": "repo__b-2",
            "messages": [
                {"role": "assistant", "content": "THOUGHT\n```bash\n@tool list_symbols --file src/other.py\n```"},
                {"role": "user", "content": "<tool_result>\nFile skeleton...\n</tool_result>"},
            ],
        },
    )
    _write_json(
        run_dir / "trajectories" / "repo__c-3.traj.json",
        {
            "instance_id": "repo__c-3",
            "messages": [
                {"role": "assistant", "content": "THOUGHT\n```bash\nrg -n token src/c.py\n```"},
                {"role": "user", "content": "<returncode>0</returncode>"},
            ],
        },
    )

    summary, rows = compute_list_symbols_metrics(run_dir=run_dir, dataset_path=tmp_path / "dataset.jsonl")

    assert summary["total_instances"] == 3
    assert summary["list_symbols_used_count"] == 2
    assert summary["list_symbols_total_calls"] == 2
    assert summary["list_symbols_usage_rate_all"] == 2 / 3
    assert summary["list_symbols_call_hit_rate"] == 0.5
    assert summary["list_symbols_instance_hit_rate_given_used"] == 0.5
    assert summary["accuracy_when_list_symbols_used"] == 0.5
    assert summary["accuracy_when_not_used_given_radar"] == 0.0
    assert summary["accuracy_uplift_used_vs_not_used_given_radar_pp"] == 50.0
    assert len(rows) == 3


def test_ignores_non_executed_list_symbols_calls(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_json(
        run_dir / "run_summary.json",
        {
            "meta": {},
            "stats_overall": {},
            "instances": [{"instance_id": "repo__x-1", "correct": False, "radar_called": True}],
        },
    )
    _write_jsonl(
        tmp_path / "dataset.jsonl",
        [{"instance_id": "repo__x-1", "edit_functions": ["src/x.py:fix"], "added_functions": []}],
    )
    _write_json(
        run_dir / "trajectories" / "repo__x-1.traj.json",
        {
            "instance_id": "repo__x-1",
            "messages": [
                {"role": "assistant", "content": "THOUGHT\n```bash\n@tool list_symbols --file src/x.py\n```"},
                {"role": "user", "content": "Tool execution failed"},
            ],
        },
    )

    summary, rows = compute_list_symbols_metrics(run_dir=run_dir, dataset_path=tmp_path / "dataset.jsonl")

    assert summary["list_symbols_used_count"] == 0
    assert summary["list_symbols_total_calls"] == 0
    assert rows[0]["list_symbols_used"] is False
