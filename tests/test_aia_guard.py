import pathlib

# REVISED (F-55). The notice was hardcoded here AND, with DIFFERENT wording, in
# Phase 6 Task 48's close-out test. Both run in the same suite; they could not
# both pass. One definition (Phase 0 Task P1), imported everywhere.
from ledger_sense.notice import NON_AFFILIATION_NOTICE
from scripts.aia_guard import guard

NOTICE_FRAGMENT = NON_AFFILIATION_NOTICE[:60].lower()


def test_guard_flags_a_file_named_after_an_aia_form(tmp_path: pathlib.Path) -> None:
    (tmp_path / "g702_template.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "README.md").write_text(NOTICE_FRAGMENT)
    assert any("g702_template.pdf" in hit for hit in guard(str(tmp_path)))


def test_guard_flags_a_missing_non_affiliation_notice(tmp_path: pathlib.Path) -> None:
    (tmp_path / "README.md").write_text("Ledger Sense")
    assert any("non-affiliation notice" in hit for hit in guard(str(tmp_path)))


def test_guard_passes_a_clean_repo(tmp_path: pathlib.Path) -> None:
    (tmp_path / "README.md").write_text(NOTICE_FRAGMENT)
    (tmp_path / "our_layout.py").write_text("COLUMNS = ['scheduled_value']")
    assert guard(str(tmp_path)) == []


def test_real_repo_passes_the_guard() -> None:
    assert guard(str(pathlib.Path(__file__).resolve().parent.parent)) == []
