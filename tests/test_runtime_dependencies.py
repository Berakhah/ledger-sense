"""Everything the application imports must be installed by `uv sync --no-dev`.

The Dockerfile installs with --no-dev, while pytest always runs with the dev
group present — so a runtime import of a dev-only package passes every test
and then kills the container at startup. That happened: writeback/qbo.py
imported httpx, which was only in the dev group (for TestClient), and
`docker compose --profile demo up` stopped starting at all.
"""

import ast
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    match = _NAME.match(requirement)
    assert match, requirement
    return _normalise(match.group(1))


def _runtime_closure() -> set[str]:
    """[project.dependencies] and everything they pull in, extras aside.
    Environment markers are kept, not evaluated: over-including a
    platform-only dependency can only hide a problem, never invent one."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pending = [_requirement_name(r) for r in project["dependencies"]]
    closure: set[str] = set()
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        closure.add(name)
        try:
            requires = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            continue  # marker-excluded here (e.g. a backport); provides no import
        for requirement in requires:
            if "extra ==" not in requirement:
                pending.append(_requirement_name(requirement))
    return closure


def _imported_top_level_modules() -> set[str]:
    modules: set[str] = set()
    for path in (ROOT / "ledger_sense").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return {m for m in modules if m != "ledger_sense" and m not in sys.stdlib_module_names}


def test_every_third_party_import_is_a_runtime_dependency() -> None:
    providers = metadata.packages_distributions()
    runtime = _runtime_closure()
    imported = _imported_top_level_modules()
    assert imported, "the walk found no third-party imports at all"
    missing = {module: providers.get(module, ["<not installed>"]) for module in sorted(imported)
               if not any(_normalise(d) in runtime for d in providers.get(module, []))}
    assert missing == {}, (
        f"imported by ledger_sense/ but not installed by `uv sync --no-dev`: {missing}")
