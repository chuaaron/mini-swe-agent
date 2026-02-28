from pathlib import Path

from minisweagent.tools.file_radar_search.tool import FileRadarSearchTool


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_tool(tmp_path: Path, **overrides) -> FileRadarSearchTool:
    config = {
        "embedding_provider": "local",
        "embedding_model": "dummy-embedder",
        "embedding_device": "cpu",
        "index_root": str(tmp_path / "indexes"),
        "chunker": "sliding",
        "chunk_size": 800,
        "overlap": 200,
        "aggregation": "hybrid",
        "index_validation_mode": "static",
        "index_build_policy": "read_only",
    }
    config.update(overrides)
    return FileRadarSearchTool(config)


def test_auto_skeleton_top3_compact_output(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_file(
        repo / "src" / "auth.py",
        (
            "import os\n"
            "from typing import Any\n\n"
            "class AuthService:\n"
            "    def login(self, user: str) -> bool:\n"
            "        return bool(user)\n\n"
            "def helper(token: str) -> bool:\n"
            "    return token.startswith('x')\n"
        ),
    )
    _write_file(
        repo / "src" / "token_cache.py",
        (
            "import time\n\n"
            "class TokenCache:\n"
            "    def load(self) -> dict:\n"
            "        return {}\n"
        ),
    )
    _write_file(
        repo / "src" / "middleware.py",
        (
            "from .auth import AuthService\n\n"
            "def require_auth() -> None:\n"
            "    pass\n"
        ),
    )

    tool = _build_tool(
        tmp_path,
        auto_skeleton_enabled=True,
        auto_skeleton_topn=3,
        auto_skeleton_budget_chars=3500,
        auto_skeleton_max_imports_per_file=0,
    )
    candidates = [
        {"path": "src/auth.py", "score": 0.93, "evidence_count": 8},
        {"path": "src/token_cache.py", "score": 0.82, "evidence_count": 5},
        {"path": "src/middleware.py", "score": 0.75, "evidence_count": 4},
    ]

    auto = tool._build_auto_skeleton(query="auth token login", repo_root=repo, results=candidates)
    assert auto["enabled"] is True
    assert auto["topn"] == 3
    assert len(auto["files"]) == 3
    assert auto["files"][0]["path"] == "src/auth.py"
    assert "AuthService" in auto["files"][0]["anchors_preview"]
    assert auto["files"][0]["folded_symbols_count"] >= 0

    output = tool._format_results("auth token login", candidates, auto_skeleton=auto)
    assert "Auto skeleton (Top-3, extreme folded, no code body):" in output
    assert "🎯 Anchors:" in output
    assert "📦 Folded:" in output
    assert "➡ Next:" in output
    assert "🚨 STRICT SOP (MANDATORY)" in output
    assert "STEP 1 — Anchor First:" in output
    assert "STEP 2 — Expand Only When Needed:" in output
    assert "STEP 3 — Re-query Instead of Wandering:" in output
    assert "return bool(user)" not in output


def test_auto_skeleton_extreme_folding_avoids_truncation_flag(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_file(
        repo / "src" / "dense.py",
        (
            "import alpha_module\n"
            "import beta_module\n"
            "import gamma_module\n"
            "import delta_module\n"
            "import epsilon_module\n\n"
            "class VeryLongAuthenticationService:\n"
            "    def build_authentication_payload(self):\n"
            "        return 1\n\n"
            "def compute_authorization_context_for_user_session():\n"
            "    return 2\n"
        ),
    )

    tool = _build_tool(
        tmp_path,
        auto_skeleton_enabled=True,
        auto_skeleton_topn=1,
        auto_skeleton_budget_chars=120,
        auto_skeleton_max_imports_per_file=0,
        auto_skeleton_max_symbols_per_file=20,
    )
    candidates = [{"path": "src/dense.py", "score": 0.99, "evidence_count": 12}]

    auto = tool._build_auto_skeleton(query="auth payload context", repo_root=repo, results=candidates)
    assert auto["enabled"] is True
    assert len(auto["files"]) == 1
    assert auto["truncated"] is False
    file_item = auto["files"][0]
    assert file_item["folded_imports_count"] >= 0
    assert file_item["folded_symbols_count"] >= 0

    output = tool._format_results("auth payload context", candidates, auto_skeleton=auto)
    assert "truncated:" not in output
