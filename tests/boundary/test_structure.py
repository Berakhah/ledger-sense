"""§5, structural: the boundary holds because the codebase has one shape.

Behavioural tests prove admit() refuses; these prove there is no path that
does not go through it. Static, over the AST, so an `import ... as` or an
attribute call cannot dodge them (F-32). The guards prove: `Labelled` is
constructed only at the door and the store; `documents.insert` is called only
by the store; no provider client (vendor SDK, cloud SDK, or stdlib HTTP) is
imported outside the gateway; and no module relabels a frozen `Labelled` via
`dataclasses.replace`, which would dodge the `Labelled(` constructor guard.
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
# stdlib HTTP (urllib.request, http.client) and the cloud SDKs are included
# for the same reason as the named vendor clients: a 2am `urllib.request
# .urlopen(...)` from a script dropped in extract/ is exactly the route this
# guard exists to close, not just a name from a pip-installed SDK.
PROVIDER_CLIENTS = (
    "httpx", "requests", "openai", "anthropic", "google.generativeai", "groq",
    "mistralai", "cohere", "urllib.request", "http.client", "aiohttp",
    "urllib3", "google", "boto3", "vertexai",
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
        # ledger_sense/writeback/ (Task 37's HttpQBOClient) is exempt. This
        # guard exists to enforce Never-rule #1 — no CLIENT_CONFIDENTIAL
        # document byte reaches an inference/OCR provider outside the door
        # gateway.py controls. Writeback is a different boundary entirely:
        # it never touches a raw document, only already-extracted,
        # already-validated billing fields a human has approved, sent to
        # the CLIENT'S OWN explicitly-connected QuickBooks account — not a
        # model/inference provider, free-tier or otherwise. Routing it
        # through the document gateway would not add a safety check; it
        # would just be the wrong door.
        if PACKAGE / "writeback" in path.parents:
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


# Modules the writeback exemption above must never import. The exemption's
# justification (writeback never touches a raw document, only
# already-validated billing fields going to the client's own connected
# accounting system) is a comment until something enforces it — this is
# that enforcement. writeback is currently unwired into the pipeline
# (nothing constructs a WriteRequest yet), so a stray `from
# ledger_sense.extract import ...` dropped in six months would slip past
# every other guard silently; this one exists specifically to catch it.
WRITEBACK_FORBIDDEN_IMPORTS = ("ledger_sense.extract", "ledger_sense.ingest",
                               "ledger_sense.gateway")


def test_writeback_never_imports_the_document_pipeline_or_the_gateway() -> None:
    """Task 37 review (recommended hardening), extended for Phase 6:
    ledger_sense/writeback/'s provider-client exemption rests on writeback
    never seeing a raw document. ledger_sense/api/writeback.py inherits that
    same assumption — it reconstructs a WriteRequest from persisted claims
    (writeback/assemble.py), never from the pipeline directly — so it is
    checked by the same guard rather than a second, easily-forgotten copy."""
    offenders = []
    for path, tree in _sources():
        if not (PACKAGE / "writeback" in path.parents
                or path == PACKAGE / "api" / "writeback.py"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            if any(name == forbidden or name.startswith(forbidden + ".")
                   for name in names for forbidden in WRITEBACK_FORBIDDEN_IMPORTS):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], (
        f"writeback (or its API router) imported the document pipeline or "
        f"the gateway, invalidating its provider-client exemption: {offenders}")


def _dataclass_replace_calls(tree: ast.Module) -> list[int]:
    """Line numbers of calls to `dataclasses.replace` (or an alias of it).

    Matches `replace(...)` where `replace` was bound by `from dataclasses
    import replace [as x]`, and `<name>.replace(...)` where `<name>` was bound
    by `import dataclasses [as d]`. A bare `replace(x)` with no such import,
    or `s.replace("a", "b")` on some other object, is not flagged — those are
    `str.replace` or unrelated user code, not a `Labelled` relabel.
    """
    name_aliases: set[str] = set()
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "dataclasses":
            name_aliases |= {alias.asname or alias.name
                              for alias in node.names if alias.name == "replace"}
        elif isinstance(node, ast.Import):
            module_aliases |= {alias.asname or alias.name
                                for alias in node.names if alias.name == "dataclasses"}

    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_name_hit = isinstance(func, ast.Name) and func.id in name_aliases
        is_attr_hit = (isinstance(func, ast.Attribute) and func.attr == "replace"
                       and isinstance(func.value, ast.Name) and func.value.id in module_aliases)
        if is_name_hit or is_attr_hit:
            lines.append(node.lineno)
    return lines


def test_no_module_relabels_a_dataclass_with_replace() -> None:
    offenders = [f"{path}:{line}" for path, tree in _sources()
                 for line in _dataclass_replace_calls(tree)]
    assert offenders == [], f"dataclasses.replace used to relabel a Labelled: {offenders}"


def test_dataclass_replace_calls_direct_name() -> None:
    tree = ast.parse("from dataclasses import replace\nreplace(x, a=1)\n")
    assert _dataclass_replace_calls(tree) == [2]


def test_dataclass_replace_calls_aliased_name() -> None:
    tree = ast.parse("import dataclasses as d\nd.replace(x, a=1)\n")
    assert _dataclass_replace_calls(tree) == [2]


def test_dataclass_replace_calls_module_attribute() -> None:
    tree = ast.parse("import dataclasses\ndataclasses.replace(x)\n")
    assert _dataclass_replace_calls(tree) == [2]


def test_dataclass_replace_ignores_str_replace() -> None:
    tree = ast.parse("s.replace('a', 'b')")
    assert _dataclass_replace_calls(tree) == []


def test_dataclass_replace_ignores_unrelated_bare_call() -> None:
    tree = ast.parse("replace(x)")
    assert _dataclass_replace_calls(tree) == []


# The runner is the first code to touch a CLIENT_CONFIDENTIAL document after
# the door. It must be deterministic all the way down: no gateway, no OCR
# escalation (which is where a model lane would hang), no provider client.
_RUNNER_FORBIDDEN = ("ledger_sense.gateway", "ledger_sense.extract.ocr", *PROVIDER_CLIENTS)


def test_the_runner_imports_nothing_that_could_reach_a_provider() -> None:
    offenders = []
    for path, tree in _sources():
        if "run" not in path.parts:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                # The module-only string (catches `from ledger_sense.extract.ocr
                # import parse`) plus one full dotted name per imported alias
                # (catches `from ledger_sense.extract import ocr` and `from
                # ledger_sense import gateway`, which a module-only check misses
                # because the forbidden leaf is the imported *name*, not the module).
                names = [node.module]
                names += [f"{node.module}.{alias.name}" for alias in node.names]
            for name in names:
                if any(name == f or name.startswith(f + ".") for f in _RUNNER_FORBIDDEN):
                    offenders.append(f"{path}:{node.lineno} imports {name}")
    assert offenders == [], offenders
