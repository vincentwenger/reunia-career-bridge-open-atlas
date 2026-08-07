"""Helpers for structural tests against split source modules."""

from __future__ import annotations

import ast
from pathlib import Path


def function_source(path: Path, function_name: str) -> str:
    """Return one top-level function, including decorators, from *path*."""

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == function_name
    )
    start_line = min(
        [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
    )
    end_line = node.end_lineno or node.lineno
    return "\n".join(source.splitlines()[start_line - 1 : end_line])


def package_source(package_root: Path) -> str:
    """Return a deterministic source bundle for one split implementation package."""

    return "\n\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package_root.glob("*.py"))
        if path.name != "__init__.py"
    )
