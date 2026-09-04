"""Extraction's OWN vocabulary for the continuation-sheet grid.

extract/ must never import `ledger_sense.corpus` (a test enforces this with an
AST check over the whole package). The generator and the extractor sharing one
source of truth would make the twenty-seed ground-truth test prove only that
the generator agrees with itself — see `tables.py`'s module docstring and F-14.

The header phrases below duplicate strings that also live in
`ledger_sense.corpus.layouts` (`CONTINUATION_COLUMNS`, `_HEADER_SYNONYMS`).
That duplication is deliberate: real pay applications vary this wording ("Sched.
Value", "Work in Place", "Materials Presently Stored") and the extractor has to
recognise it independently of whatever the synthetic generator happens to emit.
"""

COLUMN_SYNONYMS: dict[str, list[str]] = {
    "item_no": ["Item"],
    "description": ["Description"],
    "scheduled_value": ["Scheduled Value", "Sched. Value"],
    "previous": ["Previous"],
    "this_period": ["This Period", "Work in Place"],
    "stored": ["Stored Materials", "Materials Presently Stored"],
    "total_completed_and_stored": ["Total Completed and Stored"],
    "retainage": ["Retainage"],
}

REQUIRED_FIELDS: frozenset[str] = frozenset(COLUMN_SYNONYMS)

# SUMMARY_LABELS — the summary certificate's own vocabulary (Phase 2 Task 17,
# F-14 applied a second time). `extract/anchors.py` searches for these labels
# by literal text; the corpus prefixes every summary label with "Summary —"
# (`ledger_sense.corpus.layouts.SUMMARY_LABEL_PREFIX`) purely so its own two
# colliding strings ("Retainage", "Total Completed and Stored" — also grid
# column headers) don't mask a real bug. A real, unprefixed G702-style
# document has the identical collision, so the bare form (what a real document
# renders) is listed alongside the prefixed form (what our corpus renders) —
# `extract_summary`'s `grid_bottom` argument and its refuse-rather-than-resolve
# handling of an ambiguous match are what actually make the bare form safe to
# use, not the vocabulary.
#
# `retainage_total` is the ONE field that does NOT get this bare entry — VERIFIED
# empirically (task-17-report.md carries the full trace). The summary block's
# OWN "Total Earned Less Retainage" row also ends in the bare word "Retainage",
# so a plain one-word "Retainage" search is ambiguous (2 matches: its own row
# AND that row) on every clean document, even below the grid, with zero
# duplication involved — a permanent false positive, not a guard against a
# real collision. Only the prefixed form is listed for `retainage_total`.
#
# Two real-document synonyms from the brief: "Less Previous Certificates for
# Payment" maps cleanly to `previous_payments` (the AIA G702 line for
# cumulative prior payments is exactly that field). "Balance to Finish
# Including Retainage" is SKIPPED — a real G702's line 9 equals
# `adjusted_contract_sum - total_completed_and_stored`, a value none of our 8
# fields holds; mapping it to the nearest-sounding field anyway would guess a
# number under a confident label, which is what this whole module exists to
# refuse (see `extract_summary`'s own docstring, "never take the nearest
# number and hope").
SUMMARY_LABELS: tuple[tuple[str, str], ...] = (
    ("Summary — Original Contract Sum", "original_sum"),
    ("Summary — Net Change by Change Orders", "change_orders_net"),
    ("Summary — Contract Sum to Date", "adjusted_contract_sum"),
    ("Summary — Total Completed and Stored", "total_completed_and_stored"),
    ("Total Completed and Stored", "total_completed_and_stored"),
    ("Summary — Retainage", "retainage_total"),
    ("Summary — Total Earned Less Retainage", "total_earned_less_retainage"),
    ("Summary — Less Previous Payments", "previous_payments"),
    ("Less Previous Certificates for Payment", "previous_payments"),
    ("Summary — Current Payment Due", "current_payment_due"),
)
