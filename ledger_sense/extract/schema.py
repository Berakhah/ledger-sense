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
