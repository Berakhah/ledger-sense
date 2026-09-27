from ledger_sense.domain.vendor import VendorLedger


def test_alias_confirmed_from_finds_the_alias_a_document_added() -> None:
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.confirm_alias(vendor_id, "Acme Steel Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")

    alias = ledger.alias_confirmed_from(vendor_id, "doc-1")

    assert alias is not None
    assert alias.name == "Acme Steel Co."
    assert alias.source_document == "doc-1"


def test_alias_confirmed_from_is_none_for_an_unrelated_document() -> None:
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.confirm_alias(vendor_id, "Acme Steel Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")

    assert ledger.alias_confirmed_from(vendor_id, "doc-2") is None


def test_undo_confirmation_removes_the_alias_by_identity() -> None:
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.confirm_alias(vendor_id, "Acme Steel Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")
    ledger.confirm_alias(vendor_id, "Acme Steel Corp.", source_document="doc-2",
                         confirmed_by="reviewer-1")
    alias = ledger.alias_confirmed_from(vendor_id, "doc-1")
    assert alias is not None

    ledger.undo_confirmation(vendor_id, alias.id)

    vendor = ledger.get(vendor_id)
    assert vendor is not None
    assert [a.name for a in vendor.aliases] == ["Acme Steel Corp."]


def test_undo_confirmation_deletes_a_vendor_left_with_no_evidence() -> None:
    """A vendor created solely for one confirmation leaves no orphan record
    behind once that confirmation is undone."""
    ledger = VendorLedger()
    vendor_id = ledger.create("Brand New Roofing Co.")
    ledger.confirm_alias(vendor_id, "Brand New Roofing Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")
    alias = ledger.alias_confirmed_from(vendor_id, "doc-1")
    assert alias is not None

    ledger.undo_confirmation(vendor_id, alias.id)

    assert ledger.get(vendor_id) is None


def test_undo_confirmation_keeps_a_vendor_with_other_evidence() -> None:
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.confirm_alias(vendor_id, "Acme Steel Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")
    ledger.confirm_alias(vendor_id, "Acme Steel Corp.", source_document="doc-2",
                         confirmed_by="reviewer-1")
    alias = ledger.alias_confirmed_from(vendor_id, "doc-1")
    assert alias is not None

    ledger.undo_confirmation(vendor_id, alias.id)

    vendor = ledger.get(vendor_id)
    assert vendor is not None
    assert [a.name for a in vendor.aliases] == ["Acme Steel Corp."]


def test_undo_confirmation_on_an_unknown_vendor_is_a_no_op() -> None:
    ledger = VendorLedger()
    ledger.undo_confirmation("nope", "also-nope")
