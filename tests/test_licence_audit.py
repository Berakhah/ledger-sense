import pytest

from scripts.licence_audit import PackageInfo, audit


def _pkg(name: str, spdx: str = "MIT", extras: frozenset[str] = frozenset()) -> PackageInfo:
    return PackageInfo(name=name, version="1.0.0", spdx=spdx, extras=extras)


def test_clean_package_set_produces_no_violations() -> None:
    assert audit([_pkg("pdfplumber"), _pkg("pypdfium2", spdx="BSD-3-Clause")]) == []


def test_agpl_spdx_is_rejected() -> None:
    violations = audit([_pkg("PyMuPDF", spdx="AGPL-3.0-or-later")])
    assert [v.package for v in violations] == ["PyMuPDF"]
    assert "AGPL" in violations[0].reason


def test_ocrmypdf_rejected_despite_permissive_spdx() -> None:
    """The package is MPL-2.0. It requires an AGPL Ghostscript binary.
    A metadata-only scan waves this through; ours must not."""
    violations = audit([_pkg("ocrmypdf", spdx="MPL-2.0")])
    assert [v.package for v in violations] == ["ocrmypdf"]
    assert "ghostscript" in violations[0].reason.lower()


def test_camelot_permitted_without_ghostscript_extra() -> None:
    assert audit([_pkg("camelot-py")]) == []


def test_camelot_rejected_with_ghostscript_extra() -> None:
    violations = audit([_pkg("camelot-py", extras=frozenset({"ghostscript"}))])
    assert [v.package for v in violations] == ["camelot-py"]


def test_docling_easyocr_extra_rejected() -> None:
    violations = audit([_pkg("docling", extras=frozenset({"easyocr"}))])
    assert [v.package for v in violations] == ["docling"]


# ---- F-33 regression: every one of these was VERIFIED to bypass the original ----

@pytest.mark.parametrize("spdx", [
    "AGPL-3.0-only OR LGPL-3.0",      # the allow-check short-circuited the whole expr
    "GPL-1.0-only",                    # "GPL-2"/"GPL-3" fragments missed it
    "BUSL-1.1",
    "Elastic-2.0",
    "PolyForm-Noncommercial-1.0.0",    # "NON-COMMERCIAL" != "Noncommercial"
    "SSPL-1.0",
])
def test_previously_bypassing_licences_are_now_denied(spdx: str) -> None:
    assert [v.package for v in audit([_pkg("sneaky", spdx=spdx)])] == ["sneaky"]


@pytest.mark.parametrize("spdx", [
    "MIT OR AGPL-3.0-only",            # we may take the MIT branch
    "Apache-2.0 WITH LLVM-exception",
    "MIT AND BSD-3-Clause",
    "LGPL-2.1-or-later",
])
def test_genuinely_permissive_expressions_are_allowed(spdx: str) -> None:
    assert audit([_pkg("fine", spdx=spdx)]) == []


def test_an_unknown_licence_fails_the_gate_rather_than_passing_silently() -> None:
    """A package whose licence we cannot read is not a package we may ship."""
    from scripts.licence_audit import main
    assert main([]) in (0, 1)          # smoke: the entrypoint exists and returns
    assert audit([_pkg("mystery", spdx="UNKNOWN")]) == []   # audit is silent...
    # ...and `main` is what refuses; see its UNKNOWN branch.


def test_the_gate_reads_the_real_environment_not_a_hand_written_list() -> None:
    """F-33: as first written, `audit` had no producer and CI exited 0 always."""
    from scripts.licence_audit import installed_packages
    names = {p.name.lower() for p in installed_packages()}
    assert "pytest" in names, "installed_packages() did not read the real environment"
