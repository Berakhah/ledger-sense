"""F-32: the AST guards must see through attribute calls and import aliases.
A guard that only matches `Labelled(` is evaded by `boundary.Labelled(` or
`from ledger_sense.boundary import Labelled as L`."""

import ast

from tests._ast_guard import calls_to


def _tree(source: str) -> ast.Module:
    return ast.parse(source)


def test_a_bare_name_call_is_found() -> None:
    tree = _tree("x = Labelled(payload=b'', sensitivity=s, origin=o, provenance='p')\n")
    assert calls_to(tree, "Labelled") == [1]


def test_an_attribute_call_is_found() -> None:
    tree = _tree("import ledger_sense.boundary as b\nx = b.Labelled(1, 2, 3, 4)\n")
    assert calls_to(tree, "Labelled") == [2]


def test_an_aliased_import_is_found() -> None:
    tree = _tree("from ledger_sense.boundary import Labelled as L\n\nx = L(1, 2, 3, 4)\n")
    assert calls_to(tree, "Labelled") == [3]


def test_a_different_name_is_not_found() -> None:
    tree = _tree("from ledger_sense.boundary import Origin\nx = Origin('UPLOAD')\n")
    assert calls_to(tree, "Labelled") == []


def test_a_class_definition_is_not_a_call() -> None:
    tree = _tree("class Labelled:\n    pass\n")
    assert calls_to(tree, "Labelled") == []
