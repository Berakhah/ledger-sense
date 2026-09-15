"""Rules-based document classification. No model.

"unknown" is a valid answer and routes to review. A confident wrong type is
worse than an honest shrug.
"""

from typing import Literal

from ledger_sense.extract.textlayer import extract_words

DocType = Literal["pay_application", "continuation_sheet", "invoice",
                  "lien_waiver", "unknown"]

_MARKERS: tuple[tuple[DocType, tuple[str, ...]], ...] = (
    # pay_application first: our corpus's full pay application carries its
    # continuation grid on the same page as the summary certification (the
    # design spec's "corpus emits both pages in one PDF"), so a document
    # bearing both marker sets is a complete pay application, not a
    # standalone continuation sheet — that distinction only matters for a
    # document submitted without the summary block.
    ("pay_application", ("contract sum to date", "current payment due")),
    ("continuation_sheet", ("scheduled value", "total completed and stored")),
    ("lien_waiver", ("waiver", "lien")),
    ("invoice", ("invoice", "remit to")),
)


def classify(pdf: bytes) -> DocType:
    text = " ".join(w.text for w in extract_words(pdf)).lower()
    for doc_type, markers in _MARKERS:
        if all(marker in text for marker in markers):
            return doc_type
    return "unknown"
