from ledger_sense.corpus.errors import generate_bank_change_history
from ledger_sense.corpus.generate import generate
from ledger_sense.corpus.remit import Remit, remit_for
from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.extract.remit import extract_remit
from ledger_sense.extract.textlayer import extract_words


def test_remit_for_is_deterministic_and_the_variants_differ() -> None:
    assert remit_for(3) == remit_for(3)
    assert remit_for(3, "changed") != remit_for(3, "base")
    assert len(remit_for(3).routing) == 9


def test_it_extracts_the_rendered_account_and_routing() -> None:
    remit = remit_for(3)
    _c, _a, pdf = generate(seed=3, error="clean", remit=remit)
    result = extract_remit(extract_words(pdf))
    assert result.problem is None
    assert result.hit is not None
    assert (result.hit.account, result.hit.routing) == (remit.account, remit.routing)


def test_a_document_without_a_bank_block_has_nothing_to_extract() -> None:
    _c, _a, pdf = generate(seed=3, error="clean")
    result = extract_remit(extract_words(pdf))
    assert result.hit is None
    assert result.problem is None


def test_a_label_without_its_value_is_a_problem_not_a_guess() -> None:
    remit = remit_for(3)
    _c, _a, pdf = generate(seed=3, error="clean", remit=remit)
    words = [w for w in extract_words(pdf) if w.text != remit.routing]
    result = extract_remit(words)
    assert result.hit is None
    assert result.problem is not None


def test_a_repeated_label_is_refused_rather_than_resolved() -> None:
    _c, _a, pdf = generate(seed=3, error="clean", remit=remit_for(3))
    words = extract_words(pdf)
    i = next(i for i, w in enumerate(words) if w.text == "Account")
    assert words[i + 1].text == "Number"
    result = extract_remit([*words, *words[i:i + 2]])
    assert result.hit is None
    assert result.problem is not None


def test_the_hit_never_reveals_the_number_in_its_repr() -> None:
    remit = remit_for(3)
    _c, _a, pdf = generate(seed=3, error="clean", remit=remit)
    result = extract_remit(extract_words(pdf))
    assert remit.account not in repr(result)
    assert remit.routing not in repr(result)


def test_the_bank_block_does_not_disturb_pay_application_extraction() -> None:
    _c, _a, plain = generate(seed=4, error="clean")
    _c, _a, with_remit = generate(seed=4, error="clean", remit=remit_for(4))
    a = assemble_pay_application(plain, "x")
    b = assemble_pay_application(with_remit, "x")
    assert b.lines == a.lines
    assert b.provenance["_unextractable"] == a.provenance["_unextractable"]
    assert (b.vendor_name, b.contract_id, b.application_no, b.period_to) == (
        a.vendor_name, a.contract_id, a.application_no, a.period_to)


def test_the_bank_change_history_is_the_same_vendor_with_a_different_account() -> None:
    (_c1, app1, pdf1), (_c2, app2, pdf2) = generate_bank_change_history(seed=5)
    assert app1.vendor_name == app2.vendor_name
    first = extract_remit(extract_words(pdf1)).hit
    second = extract_remit(extract_words(pdf2)).hit
    assert first is not None and second is not None
    assert first.account != second.account


def _extract_for(account: str, routing: str = "021000021"):  # type: ignore[no-untyped-def]
    _c, _a, pdf = generate(seed=3, error="clean", remit=Remit(account=account, routing=routing))
    return extract_remit(extract_words(pdf))


def test_a_spaced_account_number_is_read_whole() -> None:
    result = _extract_for("4417 0093 2210")
    assert result.problem is None and result.hit is not None
    assert result.hit.account == "441700932210"


def test_a_hyphenated_account_number_normalises_to_the_same_digits() -> None:
    result = _extract_for("4417-0093-2210")
    assert result.problem is None and result.hit is not None
    assert result.hit.account == "441700932210"


def test_two_accounts_sharing_a_first_group_extract_differently() -> None:
    a = _extract_for("4417 0093 2210").hit
    b = _extract_for("4417 5555 6666").hit
    assert a is not None and b is not None
    assert a.account != b.account


def test_a_value_with_letters_mixed_in_is_ambiguous_not_a_hit() -> None:
    result = _extract_for("4417 00A3 2210")
    assert result.hit is None
    assert result.problem is not None and "ambiguous" in result.problem


def test_a_short_routing_number_is_a_problem() -> None:
    result = _extract_for("4417009322", routing="02100002")
    assert result.hit is None
    assert result.problem is not None


def test_only_one_of_the_two_labels_is_a_problem_not_no_block() -> None:
    remit = remit_for(3)
    _c, _a, pdf = generate(seed=3, error="clean", remit=remit)
    words = extract_words(pdf)
    i = next(i for i, w in enumerate(words) if w.text == "Routing")
    assert words[i + 1].text == "Number"
    kept = [w for j, w in enumerate(words) if j not in (i, i + 1)]
    result = extract_remit(kept)
    assert result.hit is None
    assert result.problem is not None