"""Shared AST guard helper (F-32).

The original Task 13 guard matched only `node.func.id == "Labelled"` — an
ast.Name — and missed `bok_core.boundary.Labelled(...)` (an ast.Attribute)
and every aliased import. This resolves the module's import aliases first,
then matches both call shapes. One implementation, imported by every guard.
"""

from __future__ import annotations

import ast


def calls_to(tree: ast.Module, name: str) -> list[int]:
    """Line numbers of every call to `name` in `tree`.

    Matches `name(...)`, `anything.name(...)`, and `alias(...)` where the
    module did `from x import name as alias`.
    """
    aliases = {name}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases |= {alias.asname or alias.name for alias in node.names if alias.name == name}

    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called = func.id
        elif isinstance(func, ast.Attribute):
            called = func.attr
        else:
            continue
        if called in aliases:
            lines.append(node.lineno)
    return lines
