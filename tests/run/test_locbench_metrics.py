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
    assert metrics["function_recall_at_10"] == metrics["entity_recall_at_10"]
    assert metrics["function_recall_all"] == metrics["entity_recall_all"]
    assert metrics["function_acc_all"] == metrics["entity_acc_all"]
    assert metrics["file_recall_all"] == 1.0
    assert metrics["file_acc_all"] is True
    assert metrics["gt_file_count_all"] == 1
    assert metrics["gt_function_count_all"] == 2
    assert metrics["gt_edit_function_count"] == 2
    assert metrics["gt_added_function_count"] == 0
    assert metrics["gt_has_added_functions"] is False
    assert metrics["gt_single_file"] is True
    assert metrics["edit_function_hit_any"] is True
    assert metrics["edit_function_recall_at_1"] == 0.5
    assert metrics["edit_function_recall_all"] == 0.5
    assert metrics["edit_function_acc_all"] is False
    assert metrics["added_function_hit_any"] is None
    assert metrics["added_function_recall_at_1"] is None
    assert metrics["added_function_recall_all"] is None
    assert metrics["added_function_acc_all"] is None


def test_compute_locbench_metrics_tracks_added_function_scope():
    record = {
        "edit_functions": ["pkg/a.py:foo"],
        "added_functions": ["pkg/a.py:bar"],
    }
    found_files = ["pkg/a.py"]
    found_entities = ["pkg/a.py:foo"]

    metrics = compute_locbench_metrics(record, found_files, found_entities)

    assert metrics["function_recall_all"] == 0.5
    assert metrics["edit_function_recall_all"] == 1.0
    assert metrics["edit_function_acc_all"] is True
    assert metrics["added_function_recall_all"] == 0.0
    assert metrics["added_function_acc_all"] is False
    assert metrics["gt_has_added_functions"] is True
    assert metrics["gt_added_function_count"] == 1
