"""CSV / IIF export. A PEER adapter, not a fallback.

B12: accounting-platform API access is a gate we do not control. This adapter
means the product is useful on day one with no API approval at all, and it
satisfies exactly the same protocol as the QBO adapter, so nothing above the
port knows the difference.
"""

import csv
import io
import pathlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from ledger_sense.writeback.interface import (
    ReversalResult,
    WriteRequest,
    WriteResult,
)

if TYPE_CHECKING:
    from ledger_sense.writeback.interface import WritebackPort

CSV_HEADER = ["idempotency_key", "doc_id", "vendor", "txn_date", "account_ref",
              "description", "amount", "memo", "status"]

# IIF: a multi-line bill is ONE `TRNS` header row carrying the idempotency
# key (in DOCNUM) and the bill total, followed by one `SPL` row per line
# item, closed by a bare `ENDTRNS`. Not one `TRNS` row per line item — real
# QuickBooks Desktop IIF import treats every `TRNS\t...` line as the start of
# a NEW transaction, so a two-line-item bill written as two `TRNS` rows is
# two bills, not one, and the DOCNUM/idempotency key would be duplicated
# across unrelated transactions. `_existing_keys` and `reverse` key off the
# single `TRNS` row per bill.
IIF_HEADER = ("!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\n"
              "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tMEMO\n")
STATUS = "AWAITING REVIEW"

# FORMULA INJECTION — REVISED (F-27).
#
# `vendor_ref` and `description` are extracted from a client-supplied PDF and
# written into a .csv that a bookkeeper opens in Excel. A cell beginning =, +, -,
# @, tab or CR executes as a formula on open. This is the most likely path from
# "we processed your subcontractor's invoice" to "we executed code on your
# controller's workstation" — and the input is, by construction, attacker-
# influenced: anyone who can get an invoice in front of the client can set it.
#
# The IIF writer is worse. "\t".join(...) with a tab or newline inside a
# description corrupts the record and can inject an ADDITIONAL transaction line
# into a file destined for QuickBooks Desktop.
#
# CR is handled separately, not by this tuple: `_CONTROL` strips it (and every
# other C0 control character except tab and newline) from the text BEFORE the
# `startswith` check below ever runs, so a leading CR can never reach this
# branch. It is not listed here because doing so would be dead code, not
# because a leading CR is safe left alone — it still isn't, which is why
# stripping it happens unconditionally rather than only when it leads.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t")
_CONTROL = {ord(c): None for c in map(chr, list(range(9)) + list(range(11, 32)))}


def _safe_cell(value: str) -> str:
    """Neutralise a spreadsheet formula without altering what the human reads."""
    text = str(value).translate(_CONTROL)
    return "'" + text if text.startswith(_FORMULA_LEAD) else text


def _safe_iif_field(value: str) -> str:
    """IIF is tab-delimited and line-oriented; a tab or newline in a field is a
    structural break, not a formatting nuisance."""
    return _safe_cell(value).replace("\t", " ").replace("\n", " ").replace("\r", " ")


class ExportAdapter:
    name = "export"

    def __init__(self, out_dir: str, fmt: Literal["csv", "iif"] = "csv") -> None:
        self._dir = pathlib.Path(out_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fmt = fmt
        self._path = self._dir / f"bills.{fmt}"

    def is_period_closed(self, txn_date: date) -> bool:
        """There is no remote book to close."""
        return False

    def _existing_keys(self) -> set[str]:
        if not self._path.exists():
            return set()
        text = self._path.read_text(encoding="utf-8")
        if self._fmt == "csv":
            return {row["idempotency_key"] for row in csv.DictReader(io.StringIO(text))}
        return {line.split("\t")[6] for line in text.splitlines()
                if line.startswith("TRNS\t")}

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
        if request.idempotency_key in self._existing_keys():
            return WriteResult(remote_id=request.idempotency_key, adapter=self.name,
                               idempotency_key=request.idempotency_key,
                               created_at=datetime.now(UTC), was_replay=True)

        is_new = not self._path.exists()
        if self._fmt == "csv":
            with self._path.open("a", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                if is_new:
                    writer.writerow(CSV_HEADER)
                for item in request.line_items:
                    writer.writerow([
                        request.idempotency_key, request.doc_id,
                        _safe_cell(request.vendor_ref),
                        request.txn_date.isoformat(), item.account_ref,
                        _safe_cell(item.description), str(item.amount),
                        _safe_cell(request.memo), STATUS])
        else:
            with self._path.open("a", encoding="utf-8") as handle:
                if is_new:
                    handle.write(IIF_HEADER)
                total = sum((item.amount for item in request.line_items),
                            start=Decimal(0))
                first_account = (request.line_items[0].account_ref
                                 if request.line_items else "")
                handle.write("\t".join([
                    "TRNS", "BILL", request.txn_date.isoformat(),
                    first_account, _safe_iif_field(request.vendor_ref),
                    str(total), request.idempotency_key,
                    _safe_iif_field(request.memo)]) + "\n")
                for item in request.line_items:
                    handle.write("\t".join([
                        "SPL", "BILL", request.txn_date.isoformat(),
                        item.account_ref, _safe_iif_field(request.vendor_ref),
                        str(item.amount),
                        _safe_iif_field(f"{STATUS} — {item.description}")]) + "\n")
                handle.write("ENDTRNS\n")

        return WriteResult(remote_id=request.idempotency_key, adapter=self.name,
                           idempotency_key=request.idempotency_key,
                           created_at=datetime.now(UTC), was_replay=False)

    def reverse(self, remote_id: str, idempotency_key: str | None = None
                ) -> ReversalResult:
        """Remove every row carrying this key, preserving the header.

        REVISED (F-45). `remote_id not in ln` is a substring match over the whole
        line, so it also removed any row whose DESCRIPTION happened to contain the
        key. Compare the field.
        """
        key = idempotency_key or remote_id
        text = self._path.read_text(encoding="utf-8")
        if self._fmt == "csv":
            rows = list(csv.reader(io.StringIO(text)))
            kept = rows[:1] + [r for r in rows[1:] if r and r[0] != key]
            with self._path.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerows(kept)
        else:
            # Remove the whole TRNS/SPL.../ENDTRNS block matching this key,
            # not just the TRNS line — leaving its SPL rows behind would
            # corrupt every transaction after it (F-45 applies here too:
            # match the field, not scan the raw line text).
            lines = text.splitlines(keepends=True)
            kept_lines: list[str] = []
            dropping = False
            for ln in lines:
                if ln.startswith("TRNS\t"):
                    dropping = ln.split("\t")[6] == key
                    if dropping:
                        continue
                elif ln.startswith("SPL\t") and dropping:
                    continue
                elif ln.rstrip("\r\n") == "ENDTRNS":
                    if dropping:
                        dropping = False
                        continue
                kept_lines.append(ln)
            self._path.write_text("".join(kept_lines), encoding="utf-8")
        return ReversalResult(remote_id=remote_id,
                              reversed_at=datetime.now(UTC), method="deleted")


if TYPE_CHECKING:
    # Static conformance check, matching the pattern QBOAdapter established
    # (I-2, Task 37 review): this assignment never executes (TYPE_CHECKING is
    # False at runtime) but mypy --strict fails the module the moment
    # ExportAdapter's shape drifts from the protocol. The dynamic counterpart
    # — isinstance(adapter, WritebackPort) — lives in
    # tests/writeback/test_export.py.
    def _export_adapter_satisfies_writebackport(adapter: ExportAdapter) -> None:
        _conforms: WritebackPort = adapter
