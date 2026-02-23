#!/usr/bin/env python3

"""This is the central entry point to the mini-extra script. Use subcommands
to invoke other command line utilities like running on benchmarks, editing config,
inspecting trajectories, etc.
"""

import sys
from importlib import import_module

from rich.console import Console

subcommands = [
    ("minisweagent.run.extra.config", ["config"], "Manage the global config file"),
    ("minisweagent.run.extra.inspector", ["inspect", "i", "inspector"], "Run inspector (browse trajectories)"),
    ("minisweagent.run.extra.github_issue", ["github-issue", "gh"], "Run on a GitHub issue"),
    ("minisweagent.run.extra.locbench", ["locbench"], "Evaluate on LocBench (localization)"),
    ("minisweagent.run.extra.locbench_tools", ["locbench-tools"], "Evaluate on LocBench with tools enabled"),
    ("minisweagent.run.extra.locbench_tools", ["locbench-tools-radar"], "Evaluate on LocBench with radar tools"),
    ("minisweagent.run.extra.locbench_code_search", ["locbench-code-search"], "Evaluate LocBench with code_search only"),
    ("minisweagent.run.extra.swebench", ["swebench"], "Evaluate on SWE-bench (batch mode)"),
    ("minisweagent.run.extra.swebench_single", ["swebench-single"], "Evaluate on SWE-bench (single instance)"),
]


def get_docstring() -> str:
    lines = [
        "This is the [yellow]central entry point for all extra commands[/yellow] from mini-swe-agent.",
        "",
        "Available sub-commands:",
        "",
    ]
    for _, aliases, description in subcommands:
        alias_text = " or ".join(f"[bold green]{alias}[/bold green]" for alias in aliases)
        lines.append(f"  {alias_text}: {description}")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]

    if len(args) == 0 or len(args) == 1 and args[0] in ["-h", "--help"]:
        return Console().print(get_docstring())

    if args[0] in ["locbench", "locbench-tools", "locbench-tools-radar", "locbench-code-search"]:
        from minisweagent.run_locbench import main as run_locbench_main

        mode_map = {
            "locbench": "bash",
            "locbench-tools": "tools",
            "locbench-tools-radar": "tools_radar",
            "locbench-code-search": "ir",
        }
        run_locbench_main(["--mode", mode_map[args[0]], *args[1:]])
        return

    for module_path, aliases, _ in subcommands:
        if args[0] in aliases:
            return import_module(module_path).app(args[1:], prog_name=f"mini-extra {aliases[0]}")

    return Console().print(get_docstring())


if __name__ == "__main__":
    main()
