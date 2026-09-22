"""The review queue's own small vocabulary.

`ledger_sense.api.review` is the actual review-queue router (Phase 4). This
package holds `ReviewAction`, the shape Phase 6's calibration labelling
(`ledger_sense.route.labels`) consumes — a standalone data type, not a
restatement of how the API router represents an accept/edit/reject
internally.
"""
