"""list_symbols tool implementation (file skeleton only, no source body)."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minisweagent.tools.base import ToolResult

_IMPORT_PATTERNS = (
    re.compile(r"^\s*#include\s+[<\"].+[>\"]\s*$"),
    re.compile(r"^\s*import\s+.+$"),
    re.compile(r"^\s*from\s+\S+\s+import\s+.+$"),
    re.compile(r"^\s*(?:const|let|var)\s+\w+\s*=\s*require\(.+\)\s*;?\s*$"),
)

_CLASS_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:class|struct|interface|enum)\s+([A-Za-z_]\w*)"),
)

_FUNCTION_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\("),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\("),
    re.compile(r"^\s*func\s+([A-Za-z_]\w*)\s*\("),
    re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)\s*\("),
    re.compile(
        r"^\s*(?:public|private|protected|static|final|virtual|inline|\s)+"
        r"[A-Za-z_][\w:<>,\s*&\[\]]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:\{|$)"
    ),
)


def _parse_int(value: Any, *, name: str, default: int, min_value: int, max_value: int) -> int:
    parsed = value if value is not None else default
    if isinstance(parsed, str):
        try:
            parsed = int(parsed)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    if not isinstance(parsed, int):
        raise ValueError(f"{name} must be an integer")
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"{name} must be between {min_value} and {max_value}")
    return parsed


def _parse_bool(value: Any, *, name: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError(f"{name} must be a boolean")


@dataclass
class ListSymbolsArgs:
    file: str
    include_signature: bool = False
    max_symbols: int = 500
    max_imports: int = 200

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "ListSymbolsArgs":
        file_path = raw.get("file", raw.get("path"))
        if not file_path or not str(file_path).strip():
            raise ValueError("file cannot be empty")
        include_signature = _parse_bool(
            raw.get("include-signature", raw.get("include_signature")),
            name="include-signature",
            default=False,
        )
        max_symbols = _parse_int(
            raw.get("max-symbols", raw.get("max_symbols")),
            name="max-symbols",
            default=500,
            min_value=1,
            max_value=2000,
        )
        max_imports = _parse_int(
            raw.get("max-imports", raw.get("max_imports")),
            name="max-imports",
            default=200,
            min_value=0,
            max_value=1000,
        )
        return cls(
            file=str(file_path).strip(),
            include_signature=include_signature,
            max_symbols=max_symbols,
            max_imports=max_imports,
        )


class ListSymbolsTool:
    name = "list_symbols"
    description = "List file-level imports/includes and symbol skeleton without returning code bodies"

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.max_file_size = int(cfg.get("max_file_size", 512 * 1024))

    def run(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        try:
            parsed = ListSymbolsArgs.from_raw(args)
            repo_root = Path(context["repo_path"]).resolve()
            requested_path = self._normalize_rel_path(parsed.file)
            allowed_files = self._normalize_allowed_files(context.get("allowed_files"))
            if not allowed_files:
                raise RuntimeError("list_symbols requires prior file_radar_search candidates (allowed_files is empty)")
            matched_allowed = self._match_allowed_path(requested_path, allowed_files)
            if matched_allowed is None:
                raise RuntimeError(
                    "requested file is not in allowed_files; "
                    f"requested={requested_path} allowed_count={len(allowed_files)}"
                )
            requested_path = matched_allowed

            target = (repo_root / requested_path).resolve()
            self._ensure_in_repo(target, repo_root)
            if not target.is_file():
                raise RuntimeError(f"file not found: {requested_path}")

            if target.stat().st_size > self.max_file_size:
                raise RuntimeError(
                    f"file too large for skeleton extraction: {requested_path} (max_file_size={self.max_file_size})"
                )

            source = target.read_text(encoding="utf-8", errors="replace")
            language = self._detect_language(requested_path)
            imports, symbols = self._extract_skeleton(
                source=source,
                language=language,
                include_signature=parsed.include_signature,
            )
            imports = imports[: parsed.max_imports]
            symbols = symbols[: parsed.max_symbols]
            data = {
                "file": requested_path,
                "language": language,
                "imports": imports,
                "symbols": symbols,
                "import_count": len(imports),
                "symbol_count": len(symbols),
                "include_signature": parsed.include_signature,
                "allowed_files_enforced": bool(allowed_files),
            }
            return ToolResult(
                success=True,
                data=data,
                output=self._format_output(data),
                returncode=0,
            )
        except ValueError:
            raise
        except Exception as exc:
            return ToolResult(success=False, data={}, output=str(exc), error=str(exc), returncode=1)

    def _normalize_rel_path(self, file_path: str) -> str:
        cleaned = file_path.strip()
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        normalized = Path(cleaned).as_posix()
        if normalized in {"", ".", ".."}:
            raise RuntimeError("invalid file path")
        if normalized.startswith("/"):
            raise RuntimeError("file path must be relative to repo root")
        if ".." in Path(normalized).parts:
            raise RuntimeError("file path cannot escape repo root")
        return normalized

    def _ensure_in_repo(self, target: Path, repo_root: Path) -> None:
        try:
            target.relative_to(repo_root)
        except ValueError as exc:
            raise RuntimeError("file path resolved outside repo root") from exc

    def _normalize_allowed_files(self, raw: Any) -> set[str]:
        if not isinstance(raw, (list, tuple, set)):
            return set()
        allowed: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            try:
                allowed.add(self._normalize_rel_path(item))
            except RuntimeError:
                continue
        return allowed

    def _match_allowed_path(self, requested_path: str, allowed_files: set[str]) -> str | None:
        if requested_path in allowed_files:
            return requested_path
        basename = Path(requested_path).name
        basename_matches = [item for item in allowed_files if Path(item).name == basename]
        if basename and len(basename_matches) == 1:
            return basename_matches[0]
        if "/" in requested_path:
            suffix_matches = [item for item in allowed_files if item.endswith(requested_path)]
            if len(suffix_matches) == 1:
                return suffix_matches[0]
        return None

    def _detect_language(self, file_path: str) -> str:
        suffix = Path(file_path).suffix.lower()
        if suffix == ".py":
            return "python"
        if suffix in {".js", ".jsx"}:
            return "javascript"
        if suffix in {".ts", ".tsx"}:
            return "typescript"
        if suffix in {".c", ".h"}:
            return "c"
        if suffix in {".cc", ".cpp", ".cxx", ".hpp"}:
            return "cpp"
        if suffix == ".java":
            return "java"
        if suffix == ".go":
            return "go"
        if suffix == ".rs":
            return "rust"
        return "unknown"

    def _extract_skeleton(
        self,
        *,
        source: str,
        language: str,
        include_signature: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if language == "python":
            try:
                return self._extract_python_skeleton(source, include_signature=include_signature)
            except SyntaxError:
                return self._extract_regex_skeleton(source, include_signature=include_signature)
        return self._extract_regex_skeleton(source, include_signature=include_signature)

    def _extract_python_skeleton(
        self,
        source: str,
        *,
        include_signature: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        tree = ast.parse(source)
        imports: list[dict[str, Any]] = []

        for node in tree.body:
            if isinstance(node, ast.Import):
                names = ", ".join(alias.name for alias in node.names)
                imports.append({"line": int(node.lineno), "text": f"import {names}"})
            elif isinstance(node, ast.ImportFrom):
                module_name = "." * int(node.level) + (node.module or "")
                names = ", ".join(alias.name for alias in node.names)
                imports.append({"line": int(node.lineno), "text": f"from {module_name} import {names}"})

        symbols: list[dict[str, Any]] = []

        def add_symbol(
            *,
            name: str,
            kind: str,
            node: ast.AST,
            signature: str | None = None,
        ) -> None:
            start = int(getattr(node, "lineno", 0) or 0)
            end = int(getattr(node, "end_lineno", start) or start)
            symbol: dict[str, Any] = {"name": name, "kind": kind, "start": start, "end": end}
            if include_signature and signature:
                symbol["signature"] = signature
            symbols.append(symbol)

        def class_signature(node: ast.ClassDef) -> str:
            if not include_signature:
                return ""
            bases: list[str] = []
            for base in node.bases:
                try:
                    bases.append(ast.unparse(base))
                except Exception:
                    bases.append("?")
            if bases:
                return f"class {node.name}({', '.join(bases)})"
            return f"class {node.name}"

        def func_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
            if not include_signature:
                return ""
            try:
                args_text = ast.unparse(node.args)
            except Exception:
                args_text = "(...)"
            prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
            return f"{prefix}{node.name}{args_text}"

        def visit_class(node: ast.ClassDef, class_prefix: str = "") -> None:
            full_class_name = f"{class_prefix}.{node.name}" if class_prefix else node.name
            add_symbol(name=full_class_name, kind="class", node=node, signature=class_signature(node))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_name = f"{full_class_name}.{child.name}"
                    add_symbol(name=method_name, kind="method", node=child, signature=func_signature(child))
                elif isinstance(child, ast.ClassDef):
                    visit_class(child, class_prefix=full_class_name)

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                visit_class(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add_symbol(name=node.name, kind="function", node=node, signature=func_signature(node))

        symbols.sort(key=lambda item: (item["start"], item["name"]))
        imports.sort(key=lambda item: item["line"])
        return imports, symbols

    def _extract_regex_skeleton(
        self,
        source: str,
        *,
        include_signature: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        lines = source.splitlines()
        imports: list[dict[str, Any]] = []
        symbols: list[dict[str, Any]] = []
        for idx, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            for pattern in _IMPORT_PATTERNS:
                if pattern.match(raw_line):
                    imports.append({"line": idx, "text": stripped})
                    break

            class_name = self._extract_first_group(_CLASS_PATTERNS, raw_line)
            if class_name:
                end_line = self._find_brace_block_end(lines, idx)
                symbol: dict[str, Any] = {"name": class_name, "kind": "class", "start": idx, "end": end_line}
                if include_signature:
                    symbol["signature"] = stripped.rstrip("{").strip()
                symbols.append(symbol)
                continue

            function_name = self._extract_first_group(_FUNCTION_PATTERNS, raw_line)
            if function_name:
                end_line = self._find_brace_block_end(lines, idx)
                symbol = {"name": function_name, "kind": "function", "start": idx, "end": end_line}
                if include_signature:
                    symbol["signature"] = stripped.rstrip("{").strip()
                symbols.append(symbol)

        return imports, symbols

    def _extract_first_group(self, patterns: tuple[re.Pattern[str], ...], line: str) -> str | None:
        for pattern in patterns:
            matched = pattern.match(line)
            if matched:
                return matched.group(1)
        return None

    def _find_brace_block_end(self, lines: list[str], start_line: int) -> int:
        depth = 0
        seen_open = False
        for idx in range(start_line - 1, len(lines)):
            line = lines[idx]
            for char in line:
                if char == "{":
                    depth += 1
                    seen_open = True
                elif char == "}" and seen_open:
                    depth -= 1
                    if depth <= 0:
                        return idx + 1
        return start_line

    def _format_output(self, data: dict[str, Any]) -> str:
        file_path = data.get("file", "")
        language = data.get("language", "unknown")
        imports = data.get("imports", [])
        symbols = data.get("symbols", [])
        lines = [f"File skeleton for {file_path} (language: {language})", ""]
        lines.append(f"imports/includes ({len(imports)}):")
        if imports:
            for item in imports:
                lines.append(f"- L{item['line']}: {item['text']}")
        else:
            lines.append("- <none>")
        lines.append("")
        lines.append(f"symbols ({len(symbols)}):")
        if symbols:
            for idx, symbol in enumerate(symbols, start=1):
                entry = f"{idx}. {symbol['kind']} {symbol['name']} [{symbol['start']}-{symbol['end']}]"
                signature = symbol.get("signature")
                if signature:
                    entry += f" sig: {signature}"
                lines.append(entry)
        else:
            lines.append("- <none>")
        return "\n".join(lines).strip()
