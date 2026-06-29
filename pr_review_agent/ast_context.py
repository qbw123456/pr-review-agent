"""Extract changed function/class slices from Python files via AST + diff line mapping."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class CodeSlice:
    path: str
    kind: str
    name: str
    start_line: int
    end_line: int
    source: str
    related_symbol: str = ""


def parse_diff_new_line_numbers(diff: str) -> set[int]:
    """Return 1-based line numbers in the NEW (HEAD) file touched by the diff."""
    if not diff:
        return set()

    changed: set[int] = set()
    new_line = 0

    for raw in diff.splitlines():
        if raw.startswith("@@"):
            match = _HUNK_HEADER_RE.match(raw)
            if match:
                new_line = int(match.group(2)) - 1
            continue
        if raw.startswith(("---", "+++", "\\")):
            continue
        if not raw:
            continue

        prefix = raw[0]
        if prefix == " ":
            new_line += 1
        elif prefix == "+":
            new_line += 1
            changed.add(new_line)
        elif prefix == "-":
            if new_line >= 1:
                changed.add(new_line)
            changed.add(new_line + 1)

    return changed


def _is_python_path(path: str) -> bool:
    return path.lower().replace("\\", "/").endswith(".py")


def _node_span(node: ast.AST) -> tuple[int, int] | None:
    if not hasattr(node, "lineno"):
        return None
    start = int(node.lineno)
    end = int(getattr(node, "end_lineno", None) or start)
    return start, end


def _iter_function_and_class_nodes(
    tree: ast.AST,
) -> list[tuple[str, str, ast.AST]]:
    nodes: list[tuple[str, str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            nodes.append(("function", node.name, node))
        elif isinstance(node, ast.AsyncFunctionDef):
            nodes.append(("async_function", node.name, node))
        elif isinstance(node, ast.ClassDef):
            nodes.append(("class", node.name, node))
    return nodes


def _innermost_node_for_line(
    candidates: list[tuple[str, str, ast.AST]],
    line_no: int,
) -> tuple[str, str, ast.AST] | None:
    best: tuple[str, str, ast.AST] | None = None
    best_size = -1
    for kind, name, node in candidates:
        span = _node_span(node)
        if not span:
            continue
        start, end = span
        if start <= line_no <= end:
            size = end - start
            if best is None or size < best_size:
                best = (kind, name, node)
                best_size = size
    return best


def find_slices_for_lines(
    source: str,
    path: str,
    changed_lines: set[int],
) -> list[CodeSlice]:
    if not changed_lines or not source.strip():
        return []

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []

    lines = source.splitlines()
    candidates = _iter_function_and_class_nodes(tree)
    matched: dict[tuple[str, str], CodeSlice] = {}

    for line_no in sorted(changed_lines):
        hit = _innermost_node_for_line(candidates, line_no)
        if hit is None:
            continue
        kind, name, node = hit
        span = _node_span(node)
        if not span:
            continue
        start, end = span
        key = (kind, name)
        if key not in matched:
            matched[key] = CodeSlice(
                path=path,
                kind=kind,
                name=name,
                start_line=start,
                end_line=end,
                source="\n".join(lines[start - 1 : end]),
            )

    if matched:
        return sorted(matched.values(), key=lambda s: (s.start_line, s.name))

    min_l = max(1, min(changed_lines) - 2)
    max_l = min(len(lines), max(changed_lines) + 2)
    return [
        CodeSlice(
            path=path,
            kind="module",
            name="<toplevel>",
            start_line=min_l,
            end_line=max_l,
            source="\n".join(lines[min_l - 1 : max_l]),
        )
    ]


def extract_slices_for_file(
    workdir: Path,
    path: str,
    diff: str,
) -> list[CodeSlice]:
    if not _is_python_path(path):
        return []
    file_path = workdir / path
    if not file_path.is_file():
        return []
    source = file_path.read_text(encoding="utf-8", errors="replace")
    changed = parse_diff_new_line_numbers(diff)
    if not changed:
        return []
    return find_slices_for_lines(source, path, changed)


def _grep_symbol_call_lines(source: str, symbol: str) -> set[int]:
    pattern = re.compile(rf"\b{re.escape(symbol)}\s*\(")
    found: set[int] = set()
    for index, line in enumerate(source.splitlines(), 1):
        if pattern.search(line):
            found.add(index)
    return found


def _grep_class_reference_lines(source: str, class_name: str) -> set[int]:
    escaped = re.escape(class_name)
    patterns = (
        re.compile(rf"\b{escaped}\s*\("),
        re.compile(rf"\bisinstance\s*\([^)]*\b{escaped}\b"),
        re.compile(rf"\bissubclass\s*\([^)]*\b{escaped}\b"),
        re.compile(rf"\bimport\s+{escaped}\b"),
    )
    found: set[int] = set()
    for index, line in enumerate(source.splitlines(), 1):
        if any(p.search(line) for p in patterns):
            found.add(index)
    return found


def _class_reference_lines_from_ast(source: str, path: str, class_name: str) -> set[int]:
    lines: set[int] = set()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return lines

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == class_name:
                lines.add(int(node.lineno))
            elif isinstance(node.func, ast.Attribute) and node.func.attr == class_name:
                lines.add(int(node.lineno))
            elif isinstance(node.func, ast.Name) and node.func.id in {"isinstance", "issubclass"}:
                for arg in node.args[1:]:
                    if isinstance(arg, ast.Name) and arg.id == class_name:
                        lines.add(int(node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                if bound == class_name:
                    lines.add(int(node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name
                if bound == class_name:
                    lines.add(int(node.lineno))
    return lines


def _call_matches_symbol(func: ast.expr, symbol: str) -> bool:
    if isinstance(func, ast.Name) and func.id == symbol:
        return True
    return isinstance(func, ast.Attribute) and func.attr == symbol


def find_symbol_reference_lines(source: str, path: str, symbol: str) -> set[int]:
    """Line numbers in *source* where *symbol* is invoked or referenced (AST, grep fallback)."""
    if not symbol or not source.strip():
        return set()

    lines: set[int] = set()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        lines = _grep_symbol_call_lines(source, symbol)
        if not lines:
            lines = _grep_class_reference_lines(source, symbol)
        return lines

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_matches_symbol(node.func, symbol):
            lines.add(int(node.lineno))

    if not lines:
        lines = _grep_symbol_call_lines(source, symbol)

    class_lines = _class_reference_lines_from_ast(source, path, symbol)
    if not class_lines:
        class_lines = _grep_class_reference_lines(source, symbol)
    lines |= class_lines

    return lines


def class_names_with_init_changed(workdir: Path, path: str, diff: str) -> list[str]:
    """Return class names whose __init__ body/signature overlaps diff changed lines."""
    changed = parse_diff_new_line_numbers(diff)
    if not changed:
        return []

    file_path = workdir / path
    if not file_path.is_file() or not _is_python_path(path):
        return []

    source = file_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []

    names: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name != "__init__":
                continue
            end = int(getattr(item, "end_lineno", None) or item.lineno)
            if any(item.lineno <= line_no <= end for line_no in changed):
                if node.name not in seen:
                    seen.add(node.name)
                    names.append(node.name)
    return names


def extract_caller_slices_for_symbol(
    workdir: Path,
    caller_path: str,
    symbol: str,
) -> list[CodeSlice]:
    if not _is_python_path(caller_path) or not symbol:
        return []
    file_path = workdir / caller_path
    if not file_path.is_file():
        return []
    source = file_path.read_text(encoding="utf-8", errors="replace")
    ref_lines = find_symbol_reference_lines(source, caller_path, symbol)
    if not ref_lines:
        return []
    slices = find_slices_for_lines(source, caller_path, ref_lines)
    return [
        CodeSlice(
            path=sl.path,
            kind=sl.kind,
            name=sl.name,
            start_line=sl.start_line,
            end_line=sl.end_line,
            source=sl.source,
            related_symbol=symbol,
        )
        for sl in slices
    ]


def _format_slice_section(sl: CodeSlice) -> str:
    label = sl.kind.replace("_", " ")
    if sl.related_symbol:
        heading = (
            f"### `{sl.path}` — {label} `{sl.name}` "
            f"(L{sl.start_line}-L{sl.end_line}, calls `{sl.related_symbol}`)"
        )
    else:
        heading = (
            f"### `{sl.path}` — {label} `{sl.name}` "
            f"(L{sl.start_line}-L{sl.end_line})"
        )
    return f"{heading}\n```python\n{sl.source}\n```"


def format_caller_ast_context_block(
    workdir: Path,
    caller_map: dict[str, list[str]],
    *,
    max_paths_per_symbol: int = 5,
) -> str:
    """AST slices for functions/classes that call API symbols in caller files."""
    sections: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    for symbol, paths in sorted(caller_map.items()):
        for caller_path in paths[:max_paths_per_symbol]:
            for sl in extract_caller_slices_for_symbol(workdir, caller_path, symbol):
                key = (sl.path, sl.kind, sl.name)
                if key in seen:
                    continue
                seen.add(key)
                sections.append(_format_slice_section(sl))

    if not sections:
        return ""
    header = (
        "## AST 调用方切片（含 `symbol(...)` 的最小函数/类，优先据此做跨文件审查）\n\n"
        "以下为 grep 找到的调用方文件中、实际调用 API 符号的函数/类；"
        "若不足再对相应文件 `read_file`。\n"
    )
    return header + "\n\n".join(sections)


def format_ast_context_block(
    workdir: Path,
    paths_and_diffs: list[tuple[str, str]],
) -> str:
    """Build Markdown block of AST-resolved changed symbols for subagent extra_context."""
    sections: list[str] = []
    for path, diff in paths_and_diffs:
        slices = extract_slices_for_file(workdir, path, diff)
        for sl in slices:
            sections.append(_format_slice_section(sl))

    if not sections:
        return ""
    header = (
        "## AST 变更区域（按 diff 行号定位函数/类，优先据此审查）\n\n"
        "以下为 HEAD 工作区中覆盖变更行的最小函数/类切片；"
        "若不足再 `read_file` 扩大上下文。\n"
    )
    return header + "\n\n".join(sections)
