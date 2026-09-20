import pytest

from ledger_sense.writeback.idempotency import IdempotencyLedger

KEY = "LS0123456789abcdef012"


@pytest.fixture()
def ledger(tmp_path):
    led = IdempotencyLedger(str(tmp_path / "writes.db"))
    yield led
    led.close()


def test_a_fresh_key_begins_pending(ledger):
    result = ledger.begin(KEY, adapter="qbo")
    assert result.record.state == "pending"
    assert result.created is True


def test_begin_is_idempotent_and_returns_the_existing_record(ledger):
    first = ledger.begin(KEY, adapter="qbo")
    assert first.created is True
    ledger.commit(KEY, remote_id="145")
    replayed = ledger.begin(KEY, adapter="qbo")
    assert replayed.record.state == "committed"
    assert replayed.record.remote_id == "145"
    assert replayed.created is False


def test_committing_records_the_remote_id(ledger):
    ledger.begin(KEY, adapter="qbo")
    ledger.commit(KEY, remote_id="145")
    assert ledger.lookup(KEY).remote_id == "145"


def test_a_crash_between_begin_and_commit_leaves_the_record_pending(ledger, tmp_path):
    """THE CASE THAT MATTERS. A pending record on retry means we do not know
    whether the remote write landed — the adapter must go and look."""
    ledger.begin(KEY, adapter="qbo")
    ledger.close()

    reopened = IdempotencyLedger(str(tmp_path / "writes.db"))
    assert reopened.lookup(KEY).state == "pending"
    reopened.close()


def test_a_failed_write_records_its_reason_and_can_be_retried(ledger):
    ledger.begin(KEY, adapter="qbo")
    ledger.fail(KEY, reason="502 from upstream")
    assert ledger.lookup(KEY).state == "failed"
    assert ledger.begin(KEY, adapter="qbo").record.state == "failed"


def test_committing_an_unknown_key_raises_rather_than_inventing_a_record(ledger):
    with pytest.raises(KeyError):
        ledger.commit("LSnever-seen-this-key", remote_id="1")


def test_two_adapters_do_not_share_a_key_namespace(ledger):
    ledger.begin(KEY, adapter="qbo")
    ledger.commit(KEY, remote_id="145")
    assert ledger.begin(KEY, adapter="export").record.state == "pending"


def test_a_reversed_write_reopens_pending_on_the_next_begin(ledger):
    """F-39. After a reversal the remote object is gone; a replay must not
    report success against a dead remote_id. begin() must open a fresh
    pending record, not return the stale reversed one."""
    ledger.begin(KEY, adapter="qbo")
    ledger.commit(KEY, remote_id="145")
    ledger.mark_reversed(KEY, adapter="qbo")
    assert ledger.lookup(KEY).state == "reversed"

    reopened = ledger.begin(KEY, adapter="qbo")
    assert reopened.record.state == "pending"
    assert reopened.record.remote_id is None
    assert reopened.created is True


def test_marking_an_unknown_key_reversed_raises(ledger):
    with pytest.raises(KeyError):
        ledger.mark_reversed("LSnever-seen-this-key", adapter="qbo")


def test_ledger_from_environment_builds_a_ledger_under_the_configured_dir(tmp_path, monkeypatch) -> None:
    from ledger_sense.writeback.idempotency import ledger_from_environment
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    ledger = ledger_from_environment()
    ledger.begin("key-1", adapter="qbo")
    assert (tmp_path / "writes.db").exists()


def test_ledger_from_environment_raises_when_unset(monkeypatch) -> None:
    from ledger_sense.writeback.idempotency import ledger_from_environment
    monkeypatch.delenv("LEDGER_SENSE_WRITEBACK_DIR", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_WRITEBACK_DIR"):
        ledger_from_environment()
