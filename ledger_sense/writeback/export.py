"""CSV / IIF export. A PEER adapter, not a fallback.

B12: accounting-platform API access is a gate we do not control. This adapter
means the product is useful on day one with no API approval at all, and it
satisfies exactly the same protocol as the QBO adapter, so nothing above the
port knows the difference.
"""

import contextlib
import csv
import io
import os
import pathlib
import tempfile
import threading
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


_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _lock_for(path: pathlib.Path) -> threading.Lock:
    """One lock per export file for the whole process, however many adapter
    instances the request handlers construct. Covers the API's thread pool; it
    does not coordinate separate OS processes writing the same file."""
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(str(path.resolve()), threading.Lock())


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

    @property
    def _newline(self) -> str | None:
        # csv.writer emits its own line terminator, so CSV is read and written
        # untranslated; IIF keeps the platform newline translation it always had.
        return "" if self._fmt == "csv" else None

    def _read(self) -> str:
        if not self._path.exists():
            return ""
        with self._path.open(encoding="utf-8", newline=self._newline) as handle:
            return handle.read()

    def _atomic_write(self, text: str) -> None:
        """LS-03: build the whole new file beside the target and swap it in.

        A crash or error at any point leaves either the old file or the new
        file, never a half-written bill.
        """
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=".bills-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline=self._newline) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    def _existing_keys(self) -> set[str]:
        text = self._read()
        if not text:
            return set()
        if self._fmt == "csv":
            return {row["idempotency_key"] for row in csv.DictReader(io.StringIO(text, newline=""))}
        return {line.split("\t")[6] for line in text.splitlines()
                if line.startswith("TRNS\t")}

    def _is_complete(self, text: str, key: str, line_count: int) -> bool:
        """True only if `key` is present as exactly one whole bill of
        `line_count` lines. A key found on some rows is not proof the bill was
        fully exported (LS-03)."""
        if self._fmt == "csv":
            rows = list(csv.reader(io.StringIO(text, newline="")))
            return sum(1 for r in rows[1:] if r and r[0] == key) == line_count
        complete_blocks = 0
        key_headers = 0
        in_block = False
        spl = 0
        for ln in text.splitlines():
            if ln.startswith("TRNS\t"):
                in_block = ln.split("\t")[6] == key
                spl = 0
                key_headers += in_block
            elif ln.startswith("SPL\t") and in_block:
                spl += 1
            elif ln == "ENDTRNS":
                if in_block and spl == line_count:
                    complete_blocks += 1
                in_block = False
        return complete_blocks == 1 and key_headers == 1

    def _without_key(self, text: str, key: str) -> str:
        """`text` minus every row / TRNS..ENDTRNS block carrying `key`, whole or partial.

        F-45: match the key FIELD, not a substring of the line. IIF: drop the
        whole TRNS/SPL.../ENDTRNS block, not just the TRNS line, or its SPL
        rows corrupt every transaction after it.
        """
        if self._fmt == "csv":
            rows = list(csv.reader(io.StringIO(text, newline="")))
            kept = rows[:1] + [r for r in rows[1:] if r and r[0] != key]
            out = io.StringIO(newline="")
            csv.writer(out).writerows(kept)
            return out.getvalue()
        kept_lines: list[str] = []
        dropping = False
        for ln in text.splitlines(keepends=True):
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
        return "".join(kept_lines)

    def _render(self, request: WriteRequest, with_header: bool) -> str:
        if self._fmt == "csv":
            out = io.StringIO(newline="")
            writer = csv.writer(out)
            if with_header:
                writer.writerow(CSV_HEADER)
            for item in request.line_items:
                writer.writerow([
                    request.idempotency_key, request.doc_id,
                    _safe_cell(request.vendor_ref),
                    request.txn_date.isoformat(), item.account_ref,
                    _safe_cell(item.description), str(item.amount),
                    _safe_cell(request.memo), STATUS])
            return out.getvalue()
        lines: list[str] = [IIF_HEADER] if with_header else []
        total = sum((item.amount for item in request.line_items), start=Decimal(0))
        first_account = (request.line_items[0].account_ref
                         if request.line_items else "")
        lines.append("\t".join([
            "TRNS", "BILL", request.txn_date.isoformat(),
            first_account, _safe_iif_field(request.vendor_ref),
            str(total), request.idempotency_key,
            _safe_iif_field(request.memo)]) + "\n")
        for item in request.line_items:
            lines.append("\t".join([
                "SPL", "BILL", request.txn_date.isoformat(),
                item.account_ref, _safe_iif_field(request.vendor_ref),
                str(item.amount),
                _safe_iif_field(f"{STATUS} — {item.description}")]) + "\n")
        lines.append("ENDTRNS\n")
        return "".join(lines)

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
        key = request.idempotency_key
        # LS-03: check, repair and write under one per-file lock, and replace
        # the file atomically. Concurrent same-key posts write the bill once; a
        # key found on a partial bill (legacy crash) is completed, not replayed.
        with _lock_for(self._path):
            text = self._read()
            if key in self._existing_keys():
                if self._is_complete(text, key, len(request.line_items)):
                    return WriteResult(remote_id=key, adapter=self.name,
                                       idempotency_key=key,
                                       created_at=datetime.now(UTC), was_replay=True)
                text = self._without_key(text, key)
            is_new = not text.strip()
            self._atomic_write(("" if is_new else text) + self._render(request, is_new))

        return WriteResult(remote_id=key, adapter=self.name, idempotency_key=key,
                           created_at=datetime.now(UTC), was_replay=False)

    def reverse(self, remote_id: str, idempotency_key: str) -> ReversalResult:
        """Remove every row carrying this key, preserving the header.

        F-45 / M-A / M-B: match the key field; the signature matches
        `WritebackPort` (key required); a missing export is a clear error, not
        a bare FileNotFoundError. LS-03: read-modify-replace happens under the
        per-file lock and is atomic, so a concurrent create is never lost.
        """
        with _lock_for(self._path):
            if not self._path.exists():
                raise ValueError(
                    f"no {self._fmt} export exists yet at {self._path}; refusing "
                    f"to reverse remote id {remote_id!r} against a file that was "
                    f"never written")
            self._atomic_write(self._without_key(self._read(), idempotency_key))
        return ReversalResult(remote_id=remote_id,
                              reversed_at=datetime.now(UTC), method="deleted")


_WRITEBACK_DIR_ENV = "LEDGER_SENSE_WRITEBACK_DIR"


def export_adapter_from_environment() -> ExportAdapter:
    """The deployment's CSV export adapter, writing to
    `<LEDGER_SENSE_WRITEBACK_DIR>/export/`. Mirrors store/blobs.py's factory
    pattern."""
    root = os.environ.get(_WRITEBACK_DIR_ENV)
    if not root:
        raise RuntimeError(
            f"{_WRITEBACK_DIR_ENV} is unset: refusing to guess where "
            f"exported bills should be written.")
    return ExportAdapter(os.path.join(root, "export"))


if TYPE_CHECKING:
    # Static conformance check, matching the pattern QBOAdapter established
    # (I-2, Task 37 review): this assignment never executes (TYPE_CHECKING is
    # False at runtime) but mypy --strict fails the module the moment
    # ExportAdapter's shape drifts from the protocol. The dynamic counterpart
    # — isinstance(adapter, WritebackPort) — lives in
    # tests/writeback/test_export.py.
    def _export_adapter_satisfies_writebackport(adapter: ExportAdapter) -> None:
        _conforms: WritebackPort = adapter
