"""§5, structural: the boundary holds because the codebase has one shape.

Behavioural tests prove admit() refuses; these prove there is no path that
does not go through it. Static, over the AST, so an `import ... as` or an
attribute call cannot dodge them (F-32).
"""

import ast
import pathlib

from tests._ast_guard import calls_to

PACKAGE = pathlib.Path("ledger_sense")

# The two places a Labelled may be built: where the file is opened, and where
# a stored label is rebuilt. Adding a third is a design change, not an edit here.
LABELLED_CONSTRUCTION_SITES = {
    PACKAGE / "ingest" / "intake.py",
    PACKAGE / "store" / "documents.py",
}

# Anything that could carry bytes to a provider. gateway.py is the only module
# allowed to know these exist — and today it does not import them either.
PROVIDER_CLIENTS = (
    "httpx", "requests", "openai", "anthropic", "google.generativeai", "groq",
    "mistralai", "cohere",
)


def _sources() -> list[tuple[pathlib.Path, ast.Module]]:
    # ledger_sense/web is the Next.js app; its .next build cache holds no Python.
    return [(path, ast.parse(path.read_text(encoding="utf-8")))
            for path in sorted(PACKAGE.rglob("*.py")) if "web" not in path.parts]


def test_labelled_is_constructed_only_at_the_door_and_the_store() -> None:
    hits = [f"{path}:{line}" for path, tree in _sources()
            if path not in LABELLED_CONSTRUCTION_SITES
            for line in calls_to(tree, "Labelled")]
    assert hits == [], f"Labelled constructed outside intake.py/documents.py: {hits}"


def test_the_door_and_the_store_do_construct_it() -> None:
    """The guard above would pass vacuously if the names moved. Pin them."""
    for site in LABELLED_CONSTRUCTION_SITES:
        tree = ast.parse(site.read_text(encoding="utf-8"))
        assert calls_to(tree, "Labelled"), f"{site} no longer builds a Labelled"


def _documents_insert_calls(tree: ast.Module) -> list[int]:
    """Line numbers of every call that inserts into the `documents` table.

    Matches `documents.insert(...)`, `<alias>.insert(...)` where `<alias>` is
    a local name bound by `from ledger_sense.store.retention import documents
    as <alias>`, and `<anything>.documents.insert(...)` (e.g.
    `retention.documents.insert(...)` or
    `ledger_sense.store.retention.documents.insert(...)`).
    """
    aliases = {"documents"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ledger_sense.store.retention":
            aliases |= {alias.asname or alias.name
                        for alias in node.names if alias.name == "documents"}

    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "insert"):
            continue
        target = func.value
        is_alias = isinstance(target, ast.Name) and target.id in aliases
        is_documents_attr = isinstance(target, ast.Attribute) and target.attr == "documents"
        if is_alias or is_documents_attr:
            lines.append(node.lineno)
    return lines


def test_documents_insert_is_called_only_by_store_document() -> None:
    offenders = [f"{path}:{line}" for path, tree in _sources()
                 if path != PACKAGE / "store" / "documents.py"
                 for line in _documents_insert_calls(tree)]
    assert offenders == [], f"a second writer to documents: {offenders}"


def test_the_store_does_call_documents_insert() -> None:
    """The guard above would pass vacuously if the writer moved. Pin it."""
    site = PACKAGE / "store" / "documents.py"
    tree = ast.parse(site.read_text(encoding="utf-8"))
    assert _documents_insert_calls(tree), f"{site} no longer calls documents.insert"


def test_documents_insert_calls_direct_name() -> None:
    tree = ast.parse("documents.insert()")
    assert _documents_insert_calls(tree) == [1]


def test_documents_insert_calls_aliased_import() -> None:
    tree = ast.parse(
        "from ledger_sense.store.retention import documents as d\n"
        "d.insert()\n"
    )
    assert _documents_insert_calls(tree) == [2]


def test_documents_insert_calls_attribute_chain() -> None:
    tree = ast.parse("retention.documents.insert()")
    assert _documents_insert_calls(tree) == [1]


def test_documents_insert_ignores_an_unrelated_insert() -> None:
    tree = ast.parse("items.insert(0, x)")
    assert _documents_insert_calls(tree) == []


def test_no_module_outside_the_gateway_imports_a_provider_client() -> None:
    offenders = []
    for path, tree in _sources():
        if path == PACKAGE / "gateway.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            if any(name == client or name.startswith(client + ".")
                   for name in names for client in PROVIDER_CLIENTS):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"a provider client outside gateway.py: {offenders}"
