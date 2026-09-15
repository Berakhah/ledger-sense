"""Which contract fields each registered rule reads — pinned by AST.

The extraction run supplies a tenant-default Contract whose only real field
is `rounding` (spec §2 step 5). Any rule that reads more than that must be
listed in CONTRACT_DEPENDENT_RULES so the run excludes it; otherwise it runs
against placeholder values and reports a confident, wrong finding.
"""

import ast
import inspect
import textwrap

from ledger_sense.rules.arithmetic import foot, lines, payment  # noqa: F401 — registers
from ledger_sense.rules.engine import CONTRACT_DEPENDENT_RULES
from ledger_sense.rules.registry import registered_rules

# Methods on PayApplication that themselves read the contract beyond `rounding`.
_CONTRACT_READING_METHODS = {"adjusted_contract_sum"}


def _contract_reads(fn: object) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))  # type: ignore[arg-type]
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    param = func.args.args[1].arg  # (app, contract)
    reads: set[str] = set()
    for node in ast.walk(func):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == param):
            reads.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _CONTRACT_READING_METHODS):
            reads.add(f"via {node.func.attr}")
    return reads


def test_every_rule_the_run_executes_reads_only_rounding() -> None:
    offenders = {}
    for rule_id, rule in registered_rules().items():
        if rule_id.startswith(("T_", "TEST")):
            continue  # test-registered rules from other modules
        reads = _contract_reads(rule.fn) - {"rounding"}
        if reads and rule_id not in CONTRACT_DEPENDENT_RULES:
            offenders[rule_id] = reads
    assert offenders == {}, f"rules reading contract fields the run cannot supply: {offenders}"


def test_a7_is_the_contract_dependent_rule() -> None:
    assert "A7" in CONTRACT_DEPENDENT_RULES
    assert "via adjusted_contract_sum" in _contract_reads(registered_rules()["A7"].fn)
