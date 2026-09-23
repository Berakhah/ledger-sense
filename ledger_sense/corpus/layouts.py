"""OUR layout. Equivalent column semantics; never the AIA form's design.

Column *math* is a fact and may be mimicked. Column lettering, field ordering,
box geometry and typography of the AIA form are expression and may not be.
These headers are plain descriptive English chosen by us.
"""

from typing import NamedTuple


class Column(NamedTuple):
    header: str
    field: str
    x_pt: float
    width_pt: float


PAGE_WIDTH_PT = 792.0   # US Letter landscape
PAGE_HEIGHT_PT = 612.0
MARGIN_PT = 36.0
HEADER_Y_PT = 540.0
ROW_HEIGHT_PT = 18.0
BODY_FONT_PT = 9.0
HEADER_FONT_PT = 8.0

CONTINUATION_COLUMNS: tuple[Column, ...] = (
    Column("Item",                       "item_no",                    36.0,  40.0),
    Column("Description",                "description",                80.0, 200.0),
    Column("Scheduled Value",            "scheduled_value",           288.0,  80.0),
    Column("Previous",                   "previous",                  372.0,  70.0),
    Column("This Period",                "this_period",               446.0,  70.0),
    Column("Stored Materials",           "stored",                    520.0,  80.0),
    Column("Total Completed and Stored", "total_completed_and_stored", 604.0,  90.0),
    Column("Retainage",                  "retainage",                 698.0,  58.0),
)

# SUMMARY LABELS MUST NOT COLLIDE WITH COLUMN HEADERS — REVISED (F-09).
#
# VERIFIED: the original set shared two label strings with CONTINUATION_COLUMNS,
# 'Retainage' and 'Total Completed and Stored'. extract_summary._find_label takes
# the FIRST match in reading order; the grid header is above the summary block, so
# it wins, and _value_near then returns line item 010's cell as the summary figure
# — with a bbox pointing at the wrong cell. A1 would pass on a document that does
# not foot.
#
# This is NOT a synthetic-corpus artifact: real G702-format documents carry the
# same words in both places, for the same reason. The extractor fix (Phase 2 Task
# 17 — search below the grid, refuse an ambiguous label) is the load-bearing one.
# Prefixing here is belt and braces so our own corpus does not mask it.
SUMMARY_LABEL_PREFIX = "Summary —"

SUMMARY_FIELDS: tuple[tuple[str, str], ...] = tuple(
    (f"{SUMMARY_LABEL_PREFIX} {label}", field) for label, field in (
        ("Original Contract Sum",          "original_sum"),
        ("Net Change by Change Orders",    "change_orders_net"),
        ("Contract Sum to Date",           "adjusted_contract_sum"),
        ("Total Completed and Stored",     "total_completed_and_stored"),
        ("Retainage",                      "retainage_total"),
        ("Total Earned Less Retainage",    "total_earned_less_retainage"),
        ("Less Previous Payments",         "previous_payments"),
        ("Current Payment Due",            "current_payment_due"),
    ))

SUMMARY_TOP_PT = 300.0     # below the deepest continuation row
SUMMARY_LABEL_X_PT = 380.0
SUMMARY_VALUE_X_PT = 620.0
SUMMARY_ROW_HEIGHT_PT = 16.0

# DOCUMENT HEADER BLOCK (Task 18) — contract/application/period identifiers,
# drawn ABOVE the continuation grid's own header row (HEADER_Y_PT = 540.0) on
# page one only, same label/value pattern as SUMMARY_FIELDS above.
#
# None of these four phrases share a word sequence with a CONTINUATION_COLUMNS
# header or a SUMMARY_LABELS entry (F-09's lesson: a shared label string is a
# wrong-cell bug waiting to happen) — verified empirically, see task-18-report.md.
# "Period Ending" deliberately contains the bare word "Period" so a test that
# drops that exact token can simulate a missing label.
DOC_HEADER_FIELDS: tuple[tuple[str, str], ...] = (
    ("Contract Number",     "contract_id"),
    ("Application Number",  "application_no"),
    ("Period Ending",       "period_to"),
    ("Contractor",          "vendor_name"),
)
DOC_HEADER_TOP_PT = 588.0
DOC_HEADER_LABEL_X_PT = 36.0
DOC_HEADER_VALUE_X_PT = 160.0
DOC_HEADER_ROW_HEIGHT_PT = 12.0

# ADVERSARIAL LAYOUT VARIANTS — REVISED (F-14).
#
# `extract/tables.py` originally imported CONTINUATION_COLUMNS from this module,
# so the extractor and the generator shared one source of truth and the twenty-seed
# test proved only that the generator agrees with itself. Extraction owns its own
# header synonym table (Phase 2 Task 16); these five layouts give the tests
# something to disagree with.
#
#   right_aligned          money right-aligned in its column — column assignment
#                          must use the span, not the left edge.
#   alternate_headers      synonyms for three of our headers — the extractor needs
#                          a synonym table, not our own wording.
#   wrapped_descriptions   two-line descriptions — row banding must survive a
#                          wrapped cell.
#   two_page               the rows split over two pages with the header repeated —
#                          the header row must not read as a data row on page 2.
#
# Only `alternate_headers` changes the column set; the other three are rendering
# decisions and live in generate._render, which is where the difference actually is.
LAYOUTS = ("baseline", "right_aligned", "alternate_headers", "wrapped_descriptions",
           "two_page")

_HEADER_SYNONYMS = {
    "scheduled_value": "Sched. Value",
    "this_period": "Work in Place",
    "stored": "Materials Presently Stored",
}

_ALTERNATE_COLUMNS: tuple[Column, ...] = tuple(
    column._replace(header=_HEADER_SYNONYMS.get(column.field, column.header))
    for column in CONTINUATION_COLUMNS
)


def columns_for(layout: str) -> tuple[Column, ...]:
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout: {layout}")
    return _ALTERNATE_COLUMNS if layout == "alternate_headers" else CONTINUATION_COLUMNS
