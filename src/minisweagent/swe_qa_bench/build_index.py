#!/usr/bin/env python3

"""Prebuild code_search indexes for SWE-QA-Bench repos."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import typer
import yaml

from minisweagent.config import get_config_path
from minisweagent.swe_qa_bench.config_loader import load_config
from minisweagent.tools.code_search import CodeSearchTool

_HELP_TEXT = "Prebuild code_search indexes for SWE-QA-Bench repos."

app = typer.Typer(rich_markup_mode="rich", add_completion=False)


def _list_repos(repos_root: Path) -> list[str]:
    return sorted(
        path.name
        for path in repos_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def _resolve_commit(repo_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.SubprocessError:
        return "HEAD"


def _build_one(repo_path: Path, repo_dir: str, tool: CodeSearchTool) -> None:
    commit = _resolve_commit(repo_path)
    tool._get_or_build_index(repo_path, repo_dir, commit)


# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    repos_root: Path = typer.Option(..., "--repos-root", help="Root dir containing repo checkouts"),
    repos: str = typer.Option("", "--repos", help="Comma-separated repo list (default: all)"),
    tool_config_path: Path = typer.Option(
        Path("swe_qa_bench/config/code_search.yaml"),
        "--tool-config",
        help="Path to code_search config",
    ),
    config_dir: Path | None = typer.Option(None, "--config-dir", help="Optional config dir for default/local.yaml"),
    indexes_root: str | None = typer.Option(None, "--indexes-root", help="Override indexes root"),
    model_root: str | None = typer.Option(None, "--model-root", help="Override embedding model root"),
) -> None:
    # fmt: on
    repos_root = repos_root.resolve()
    if not repos_root.exists():
        raise typer.BadParameter(f"Repos root not found: {repos_root}")

    repo_list = [item.strip() for item in repos.split(",") if item.strip()] if repos else _list_repos(repos_root)
    if not repo_list:
        raise typer.BadParameter("No repos to index")

    config_path = get_config_path(tool_config_path)
    tool_config: dict[str, Any] = yaml.safe_load(config_path.read_text())
    if not indexes_root or not model_root:
        if config_dir is not None:
            config_dir = config_dir.expanduser().resolve()
        base_config = load_config(config_dir=config_dir)
        paths = base_config.get("paths", {})
        indexes_root = indexes_root or paths.get("indexes_root")
        model_root = model_root or paths.get("model_root")
    if not indexes_root or not model_root:
        raise typer.BadParameter("indexes_root and model_root must be set (via local.yaml or CLI)")
    tool_config["index_root"] = str(indexes_root)
    tool_config["embedding_model"] = str(model_root)
    tool = CodeSearchTool(tool_config)

    start_time = time.time()
    for repo in repo_list:
        repo_path = (repos_root / repo).resolve()
        if not repo_path.exists():
            print(f"Skipping {repo}: repo not found")
            continue
        print(f"Building index for {repo}...")
        _build_one(repo_path, repo, tool)
        print(f"Done: {repo}")
    elapsed = time.time() - start_time
    print(f"Index build finished in {elapsed:.1f}s")


if __name__ == "__main__":
    app()
