from minisweagent.locbench.utils import compute_locbench_metrics


def test_compute_locbench_metrics_includes_function_alias_fields():
    record = {
        "edit_functions": [
            "pkg/a.py:foo",
            "pkg/a.py:bar",
        ],
        "added_functions": [],
    }
    found_files = ["pkg/a.py"]
    found_entities = ["pkg/a.py:foo"]

    metrics = compute_locbench_metrics(record, found_files, found_entities)

    assert metrics["file_hit_any"] is True
    assert metrics["entity_hit_any"] is True
    assert metrics["function_hit_any"] is True
    assert metrics["function_recall_at_1"] == metrics["entity_recall_at_1"]
    assert metrics["function_recall_at_3"] == metrics["entity_recall_at_3"]
    assert metrics["function_recall_at_5"] == metrics["entity_recall_at_5"]
