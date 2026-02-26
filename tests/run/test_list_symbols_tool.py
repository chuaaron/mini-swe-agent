from pathlib import Path

from minisweagent.tools.list_symbols import ListSymbolsTool


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_list_symbols_extracts_python_skeleton(tmp_path: Path):
    repo = tmp_path / "repo"
    source_file = repo / "src" / "auth.py"
    _write_file(
        source_file,
        (
            "import os\n"
            "from typing import Any\n\n"
            "class AuthService:\n"
            "    def login(self, user: str) -> bool:\n"
            "        return bool(user)\n\n"
            "def helper(x: int, y: int = 1) -> int:\n"
            "    return x + y\n"
        ),
    )

    tool = ListSymbolsTool()
    result = tool.run(
        {"file": "src/auth.py", "include-signature": True},
        {"repo_path": str(repo), "allowed_files": ["src/auth.py"]},
    )

    assert result.success is True
    assert result.returncode == 0
    assert result.data["file"] == "src/auth.py"
    assert result.data["language"] == "python"
    assert result.data["import_count"] == 2
    assert {item["text"] for item in result.data["imports"]} == {"import os", "from typing import Any"}

    symbols = {item["name"]: item for item in result.data["symbols"]}
    assert symbols["AuthService"]["kind"] == "class"
    assert symbols["AuthService"]["start"] == 4
    assert symbols["AuthService.login"]["kind"] == "method"
    assert symbols["helper"]["kind"] == "function"
    assert "signature" in symbols["helper"]
    assert "return x + y" not in result.output


def test_list_symbols_respects_allowed_files(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_file(repo / "src" / "a.py", "def a():\n    return 1\n")
    _write_file(repo / "src" / "b.py", "def b():\n    return 2\n")

    tool = ListSymbolsTool()
    result = tool.run(
        {"file": "src/b.py"},
        {"repo_path": str(repo), "allowed_files": ["src/a.py"]},
    )

    assert result.success is False
    assert result.returncode == 1
    assert "not in allowed_files" in (result.error or result.output)


def test_list_symbols_requires_allowed_files(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_file(repo / "src" / "main.py", "def main():\n    return 0\n")

    tool = ListSymbolsTool()
    result = tool.run({"file": "src/main.py"}, {"repo_path": str(repo)})

    assert result.success is False
    assert result.returncode == 1
    assert "allowed_files is empty" in (result.error or result.output)


def test_list_symbols_accepts_unique_basename_from_allowed_files(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_file(repo / "src" / "pkg" / "auth.py", "def login():\n    return True\n")

    tool = ListSymbolsTool()
    result = tool.run(
        {"file": "auth.py"},
        {"repo_path": str(repo), "allowed_files": ["src/pkg/auth.py"]},
    )

    assert result.success is True
    assert result.data["file"] == "src/pkg/auth.py"


def test_list_symbols_rejects_repo_escape_path(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_file(repo / "src" / "main.py", "def main():\n    return 0\n")

    tool = ListSymbolsTool()
    result = tool.run({"file": "../outside.py"}, {"repo_path": str(repo)})

    assert result.success is False
    assert result.returncode == 1
    assert "cannot escape repo root" in (result.error or result.output)
