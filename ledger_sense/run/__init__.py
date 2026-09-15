"""The run: a stored document through the deterministic pipeline, persisted.

Sits above extract/, rules/ and store/ and below api/. Nothing in
extract/ or rules/ imports this package. Deterministic only — no gateway,
no provider client, no OCR escalation (tests/boundary pins it).
"""
