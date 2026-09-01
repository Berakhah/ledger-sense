"""One mutation per error class, applied to a clean application.

Each mutation must trip exactly one rule and leave the others clean, so a
fixture proves the rule rather than proving the pipeline is noisy.

`inject` takes and returns `(Contract, PayApplication)` uniformly. C1 needs a
contract term (`contract.retainage_rate`, a flat Decimal field per Task 4's
real model — there is no nested `.retainage.base_rate`) to disagree with, so
every branch carries the contract through rather than adding a second
injection mechanism just for that one case.
"""

from typing import Literal

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money

ErrorClass = Literal[
    "clean", "A1_footing", "A2_line_sum", "A8_retainage",
    "I1_duplicate", "C4_stored_double_count", "C1_retainage_rate",
]


def inject(contract: Contract, app: PayApplication,
           error: ErrorClass) -> tuple[Contract, PayApplication]:
    if error == "clean":
        return contract, app
    if error == "A1_footing":
        return contract, app.model_copy(update={
            "total_completed_and_stored": money(
                app.total_completed_and_stored + money("0.01"))})
    if error == "A2_line_sum":
        first = app.lines[0].model_copy(update={
            "total_completed_and_stored": money(
                app.lines[0].total_completed_and_stored + money("5.00"))})
        return contract, app.model_copy(update={"lines": [first, *app.lines[1:]]})
    if error == "A8_retainage":
        return contract, app.model_copy(update={
            "retainage_total": money(app.retainage_total + money("15.00"))})
    if error == "C4_stored_double_count":
        first = app.lines[0]
        doubled = first.model_copy(update={
            "stored": first.this_period,
            "total_completed_and_stored": money(
                first.previous + first.this_period + first.this_period)})
        return contract, app.model_copy(update={"lines": [doubled, *app.lines[1:]]})
    if error == "C1_retainage_rate":
        # The contract term the billed figure must disagree with: the rate the
        # contract actually calls for, applied to the full completed-and-stored
        # total, then billed at half of that (an arbitrary but deterministic
        # miscalculation — any value that disagrees with `held` proves the rule).
        held = money(contract.retainage_rate * app.total_completed_and_stored)
        return contract, app.model_copy(update={
            "retainage_total": money(held / 2)})
    if error == "I1_duplicate":
        # A duplicate is a HISTORY-level error: it needs a second document, not
        # a mutated field, so it cannot be expressed by `inject`. Use
        # `generate_history` instead.
        raise ValueError(
            "I1_duplicate is produced by generate_history, not inject")
    raise ValueError(f"unknown error class: {error}")


def inject_duplicate(app: PayApplication) -> PayApplication:
    """I1_duplicate: a re-submission carrying the same invoice number in the
    same period. I1 must catch the second."""
    return app.model_copy(update={
        "application_no": (app.application_no or 1) + 1,
        "period_to": app.period_to,          # same period — that is the tell
    })


def generate_history(seed: int) -> list[tuple[Contract, PayApplication, bytes]]:
    """Two documents: the original and a re-submission carrying the same invoice
    number in a shifted period. I1 must catch the second.

    Imports `generate` inside the function body: `generate.py` imports
    `ErrorClass`/`inject` from this module, so importing `generate` at module
    scope here would be circular.
    """
    from ledger_sense.corpus.generate import generate

    contract, app, pdf = generate(seed=seed, error="clean")
    return [(contract, app, pdf),
            (contract, inject_duplicate(app), pdf)]
