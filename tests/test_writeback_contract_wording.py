"""LS-08: the public writeback wording must describe what the QBO adapter
actually creates (an unpaid Bill that posts to Accounts Payable), not a
'draft' state QuickBooks does not have."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_readme_states_the_concrete_effect_of_a_qbo_write() -> None:
    readme = " ".join(_text("README.md").lower().split())
    assert "unpaid" in readme
    assert "accounts payable" in readme
    assert "never creates a billpayment" in readme.replace("`", "")


def test_readme_does_not_promise_a_draft_state_for_qbo() -> None:
    assert "writeback is draft-only" not in _text("README.md").lower()


def test_api_docstring_does_not_call_a_qbo_bill_a_draft() -> None:
    head = _text("ledger_sense/api/writeback.py")[:400].lower()
    assert "draft bill" not in head
    assert "unpaid" in head
