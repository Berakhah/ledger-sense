import pathlib

DOC = pathlib.Path("docs/security.md").read_text(encoding="utf-8")


def test_the_document_has_the_sections_00_section_6_requires():
    for heading in ("## Threat model", "## Coordinated disclosure", "## Data we hold",
                    "## Retention and deletion", "## Encryption"):
        assert heading in DOC


def test_the_deletion_procedure_is_written_down_not_implied():
    assert len(DOC.split("## Retention and deletion")[1].strip()) > 400


def test_the_document_contains_no_unresolved_placeholder():
    for token in ("TODO", "TBD", "REPLACE:", "XXX", "<insert"):
        assert token not in DOC
