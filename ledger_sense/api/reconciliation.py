"""Continuation sheet against the summary, with the arithmetic shown.

A controller does not want to be told there is a discrepancy. They want to see
the addition that produced it, at the cell where it happened.

Mounted through api.deps.register() in api/app.py, same as every other
router: register() applies require_session again on top of this (free —
FastAPI caches a dependency per request), same belt-and-braces the stub
routers use. It has to be depended on here too, not only via register(),
because owned() needs the tenant off the session, and this router's own unit
tests build a bare FastAPI() + include_router that skips register() entirely
(see tests/api/test_reconciliation.py).

This module also carries the demo-only seed hatch (`seed_demo`, mounted at
RECONCILIATION_SEED_PATH). It is not part of `router` — it mints its own
session the way api/fixtures.py's seed() does, and api/app.py mounts it by
hand, only under LEDGER_SENSE_PROFILE=demo, the same discipline SEED_PATH
follows. /api/fixtures/seed doesn't fit this screen: it always seeds
error="clean" into the real Postgres-backed findings table, not this router's
in-memory reconciliation store.
"""

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from ledger_sense.api.deps import owned, require_session
from ledger_sense.api.fixtures import DocId
from ledger_sense.auth.models import create_tenant, create_user
from ledger_sense.auth.session import SESSION_TTL, Session, issue_session
from ledger_sense.corpus.errors import ErrorClass
from ledger_sense.corpus.generate import generate
from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import Money, money

# Imported for their registration side effect only (F-67).
from ledger_sense.rules.arithmetic import foot, lines, payment  # noqa: F401
from ledger_sense.rules.registry import registered_rules

router = APIRouter(prefix="/api/reconciliation")

CurrentSession = Annotated[Session, Depends(require_session)]

RECONCILIATION_SEED_PATH = "/api/reconciliation/seed"

# A9 is a `convention` rule (F-04) but it IS a reconciliation check - scheduled
# values against the adjusted contract sum - so it is named here rather than
# selected by class. Named, not imported: Phase 4 may run ahead of Phase 3 if the
# controller session slips, in which case A9 is simply not registered yet and this
# screen shows one fewer discrepancy. An import would turn that into a 500.
_ALSO_RECONCILES = frozenset({"A9"})


def _reconciliation_rules() -> list[Callable[..., list[Any]]]:
    """Read the registry per request, never a hand-written list.

    Module-level would be wrong even so: this module can be imported before the
    convention rules are, and the list would freeze A9 out.
    """
    return [r.fn for r in registered_rules().values()
            if r.rule_class == "arithmetic" or r.rule_id in _ALSO_RECONCILES]


# ponytail: in-process store. Swap for the Postgres session in Task 43.
_DOCS: dict[str, tuple[Contract, PayApplication]] = {}
_DOC_TENANTS: dict[str, str] = {}


class Discrepancy(BaseModel):
    field: str
    rule_id: str
    expected: str
    actual: str
    delta: str
    arithmetic: str


def seed_from_corpus(doc_id: str, seed: int, error: ErrorClass, tenant_id: str) -> None:
    contract, app, _pdf = generate(seed=seed, error=error)
    _DOCS[doc_id] = (contract, app)
    _DOC_TENANTS[doc_id] = tenant_id


def _arithmetic_for(rule_id: str, field: str, app: PayApplication,
                     expected: Money, actual: Money) -> str:
    if rule_id == "A1":
        terms = " + ".join(str(ln.total_completed_and_stored) for ln in app.lines)
        return f"{terms} = {expected}, sheet shows {actual}"
    if rule_id == "A2":
        # field is "line[<item_no>].total_completed_and_stored" (rules/arithmetic/
        # lines.py's rule_a2) — find that one line to show previous + this
        # period + stored, not every line's, since A2 is a per-line check.
        item_no = field.removeprefix("line[").split("]", 1)[0]
        line = next((ln for ln in app.lines if ln.item_no == item_no), None)
        if line is None:
            return f"expected {expected}, sheet shows {actual}"
        return (f"{line.previous} + {line.this_period} + {line.stored} = "
                f"{expected}, sheet shows {actual}")
    if rule_id == "A8":
        terms = " + ".join(str(ln.retainage) for ln in app.lines)
        return f"{terms} = {expected}, summary shows {actual}"
    if rule_id == "A9":
        terms = " + ".join(str(ln.scheduled_value) for ln in app.lines)
        return f"{terms} = {actual}, adjusted contract sum is {expected}"
    return f"expected {expected}, sheet shows {actual}"


@router.get("/{doc_id}")
def reconcile(doc_id: str, session: CurrentSession) -> dict[str, object]:
    # owned() alone: a doc_id absent from _DOC_TENANTS gives .get() None, which
    # owned() treats identically to "belongs to someone else" — 404 either
    # way, no separate "document not found" branch to keep in sync.
    owned(session, _DOC_TENANTS.get(doc_id))
    contract, app = _DOCS[doc_id]

    discrepancies = [
        Discrepancy(
            field=finding.field, rule_id=finding.rule_id,
            expected=str(finding.expected), actual=str(finding.actual),
            delta=str(money(abs(finding.actual - finding.expected))),
            arithmetic=_arithmetic_for(finding.rule_id, finding.field, app,
                                       finding.expected, finding.actual))
        for rule in _reconciliation_rules()
        for finding in rule(app, contract)
    ]

    return {
        "lines": [ln.model_dump(mode="json") for ln in app.lines],
        "summary": {
            "total_completed_and_stored": str(app.total_completed_and_stored),
            "retainage_total": str(app.retainage_total),
            "previous_payments": str(app.previous_payments),
            "change_orders_net": str(app.change_orders_net),
        },
        "discrepancies": [d.model_dump() for d in discrepancies],
    }


class ReconciliationSeedRequest(BaseModel):
    doc_id: DocId
    seed: int = 1
    error: ErrorClass = "clean"


def seed_demo(body: ReconciliationSeedRequest, response: Response) -> dict[str, object]:
    """Mint one synthetic reconciliation document plus a fresh session.

    Unauthenticated and unrate-limited, mounted only under the demo profile —
    same four fences as api/fixtures.py's seed(): see that module's docstring.
    No document row is written to the real `documents` table; this router's
    store is in-memory, so seed_from_corpus is the whole seam.
    """
    suffix = uuid.uuid4().hex[:8]
    tenant_id = create_tenant(f"demo-recon-{suffix}")
    user_id = create_user(tenant_id, f"demo-recon-{suffix}@example.invalid", uuid.uuid4().hex)
    seed_from_corpus(body.doc_id, seed=body.seed, error=body.error, tenant_id=tenant_id)

    response.set_cookie(
        "session",
        issue_session(user_id, tenant_id),
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="strict",
        # Deliberately not Secure — see api/fixtures.py's module docstring for
        # why (Playwright's dev server runs over http://, and a Secure cookie
        # arriving over a non-secure connection is silently dropped).
        secure=False,
        path="/",
    )
    return {"doc_id": body.doc_id, "tenant_id": tenant_id}
