"""AST scan for floats in the money path.

Layer 1 is `mypy --strict` on the Money type. Layer 3 is the Pydantic model
walk in tests/test_no_floats.py. This is layer 2.
"""

import ast
import pathlib


class _FloatFinder(ast.NodeVisitor):
    def __init__(self, path: pathlib.Path, source: str) -> None:
        self.path = path
        self.lines = source.splitlines()
        self.hits: list[str] = []

    def _record(self, node: ast.AST) -> None:
        lineno = getattr(node, "lineno", 0)
        line = self.lines[lineno - 1].strip() if 0 < lineno <= len(self.lines) else ""
        self.hits.append(f"{self.path}:{lineno}: {line}")

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, float):
            self._record(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "float":
            self._record(node)
        self.generic_visit(node)


def scan(paths: list[str]) -> list[str]:
    hits: list[str] = []
    for root in paths:
        for path in sorted(pathlib.Path(root).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            finder = _FloatFinder(path, source)
            finder.visit(ast.parse(source))
            hits.extend(finder.hits)
    return hits


if __name__ == "__main__":
    import sys

    found = scan(["ledger_sense/domain", "ledger_sense/rules", "ledger_sense/writeback"])
    for hit in found:
        print(hit, file=sys.stderr)
    sys.exit(1 if found else 0)
