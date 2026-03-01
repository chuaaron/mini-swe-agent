"""Radar navigation: call graph, cross-file deps, directory context."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


def extract_call_graph(source: str) -> dict[str, list[str]]:
    """Extract function-to-function call relations within a single file via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    all_funcs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            all_funcs.add(node.name)

    graph: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if isinstance(child.func, ast.Name) and child.func.id in all_funcs:
                calls.add(child.func.id)
            elif isinstance(child.func, ast.Attribute) and child.func.attr in all_funcs:
                calls.add(child.func.attr)
        calls.discard(node.name)
        if calls:
            graph[node.name] = sorted(calls)
    return graph


def build_reverse_graph(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    """Build reverse call graph (callers for each function)."""
    reverse: dict[str, list[str]] = defaultdict(list)
    for caller, callees in graph.items():
        for callee in callees:
            reverse[callee].append(caller)
    return {k: sorted(v) for k, v in reverse.items()}


def extract_imports_from_source(source: str) -> list[str]:
    """Extract import module paths."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def find_cross_file_deps(repo_root: Path, relevant_files: list[str]) -> dict[str, list[str]]:
    """Find import relationships between relevant files."""
    file_module_names: dict[str, set[str]] = {}
    for f in relevant_files:
        module = f.replace("/", ".").removesuffix(".py")
        variants = {module}
        parts = module.split(".")
        if parts:
            variants.add(parts[-1])
        if len(parts) >= 2:
            variants.add(".".join(parts[-2:]))
        file_module_names[f] = variants

    deps: dict[str, list[str]] = {}
    for f in relevant_files:
        full_path = repo_root / f
        if not full_path.exists() or full_path.suffix != ".py":
            continue
        try:
            source = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        imported = extract_imports_from_source(source)
        dep_files: list[str] = []
        for imp in imported:
            for other_file, variants in file_module_names.items():
                if other_file == f:
                    continue
                if any(imp == v or imp.endswith("." + v) for v in variants):
                    if other_file not in dep_files:
                        dep_files.append(other_file)
                    break
        if dep_files:
            deps[f] = dep_files
    return deps


def build_focused_tree(relevant_files: list[str]) -> str:
    """Build a minimal directory tree from relevant file paths."""
    dirs: dict[str, list[str]] = defaultdict(list)
    for f in relevant_files:
        dirs[str(Path(f).parent)].append(Path(f).name)
    lines = []
    for d in sorted(dirs):
        lines.append(f"  {d}/")
        for name in sorted(dirs[d]):
            lines.append(f"    {name} ◀")
    return "\n".join(lines)


def format_call_relations(
    symbol_name: str,
    call_graph: dict[str, list[str]],
    reverse_graph: dict[str, list[str]],
) -> str:
    """Format call relations for a symbol as a compact string."""
    lookup = symbol_name.rsplit(".", 1)[-1] if "." in symbol_name else symbol_name
    parts = []
    callees = call_graph.get(lookup, [])
    if callees:
        parts.append(f"calls → {', '.join(callees)}")
    callers = reverse_graph.get(lookup, [])
    if callers:
        parts.append(f"called by ← {', '.join(callers)}")
    return " | ".join(parts)
