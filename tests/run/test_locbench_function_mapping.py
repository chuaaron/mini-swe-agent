import json
from pathlib import Path

from minisweagent.locbench.utils import build_loc_output


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_loc_output_maps_qualified_method_name_with_file_hint(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_file(
        repo / "pkg" / "validators.py",
        (
            "class URLValidator:\n"
            "    def __call__(self, value):\n"
            "        return value\n"
        ),
    )
    payload = {
        "functions": [
            {
                "function": "URLValidator.__call__",
                "file_hint": "pkg/validators.py",
            }
        ]
    }

    output = build_loc_output(
        json.dumps(payload),
        "inst-1",
        {"edit_functions": [], "added_functions": []},
        repo_root=str(repo),
    )

    assert output["found_files"] == ["pkg/validators.py"]
    assert output["found_entities"] == ["pkg/validators.py:URLValidator.__call__"]
    assert output["submission_has_functions_key"] is True
    assert output["submitted_function_count"] == 1
    assert output["submitted_file_hint_count"] == 1
    assert output["submitted_qualified_function_count"] == 1
    assert output["submitted_qualified_function_ratio"] == 1.0


def test_build_loc_output_prefers_class_specific_method_over_same_leaf(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_file(
        repo / "pkg" / "mod.py",
        (
            "class A:\n"
            "    def foo(self):\n"
            "        return 1\n\n"
            "class B:\n"
            "    def foo(self):\n"
            "        return 2\n"
        ),
    )
    payload = {
        "functions": [
            {
                "function": "A.foo",
                "file_hint": "pkg/mod.py",
            }
        ]
    }

    output = build_loc_output(
        json.dumps(payload),
        "inst-2",
        {"edit_functions": [], "added_functions": []},
        repo_root=str(repo),
    )

    assert "pkg/mod.py:A.foo" in output["found_entities"]
    assert "pkg/mod.py:B.foo" not in output["found_entities"]
    assert output["submission_has_functions_key"] is True
    assert output["submitted_function_count"] == 1
    assert output["submitted_unique_function_count"] == 1
