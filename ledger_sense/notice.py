# ledger_sense/notice.py
"""The non-affiliation notice, defined once.

Spec §11 requires it in the README, the docs and the product UI. The audit found
two plan documents each hardcoding their own wording and each asserting it,
which is two tests that cannot both pass (F-55). One string, imported.
"""

NON_AFFILIATION_NOTICE = (
    "Not developed, endorsed, approved, sponsored by, or affiliated with the "
    "American Institute of Architects. AIA®, G702® and G703® are registered "
    "trademarks of the American Institute of Architects. References describe "
    "format compatibility only."
)
