"""Licence gate. Checks SPDX metadata AND the native/system layer.

A metadata-only scan passes ocrmypdf (MPL-2.0) while it drags in an AGPL-3.0
Ghostscript binary. That is the exact failure this exists to prevent.
"""

import re
from typing import NamedTuple


class PackageInfo(NamedTuple):
    name: str
    version: str
    spdx: str
    extras: frozenset[str]


class Violation(NamedTuple):
    package: str
    reason: str


DENIED = ("AGPL", "GPL-1", "GPL-2", "GPL-3", "SSPL", "BUSL", "ELASTIC",
          "POLYFORM", "RSAL", "COMMONSCLAUSE", "NONCOMMERCIAL", "CCBYNC")
ALLOWED = ("LGPL",)  # LGPL is permissive enough for us and contains "GPL"

# Packages whose Python metadata is permissive but whose *native* dependency is not.
NATIVE_DENY = {
    "ocrmypdf": "requires an external Ghostscript binary, which is AGPL-3.0 (Artifex)",
}

# Extras that pull a denied licence into an otherwise clean package.
EXTRA_DENY = {
    ("camelot-py", "ghostscript"): "the ghostscript extra pulls AGPL-3.0 Ghostscript",
    ("docling", "easyocr"): "easyocr bundles CRAFT weights with research-use ambiguity",
}


def _normalise(token: str) -> str:
    return "".join(ch for ch in token.upper() if ch.isalnum())


def _atom_denied(atom: str, *, allow_carveout: bool = True) -> bool:
    flat = _normalise(atom)
    if allow_carveout and any(_normalise(a) in flat for a in ALLOWED):
        return False
    return any(_normalise(d) in flat for d in DENIED)


def _spdx_denied(spdx: str) -> bool:
    """Parse the SPDX expression rather than substring-matching it.

    The original substring form had verified bypasses:
        "AGPL-3.0-only OR LGPL-3.0"     -> allowed  (the allow-check short-circuited)
        "GPL-1.0-only"                  -> allowed
        "BUSL-1.1" / "Elastic-2.0"      -> allowed
        "PolyForm-Noncommercial-1.0.0"  -> allowed  ("NON-COMMERCIAL" != "Noncommercial")

    A DISJUNCTION is denied unless some branch is unambiguously clean. Note
    that check does NOT use the ALLOWED carve-out: LGPL survives solo (its
    normalised form coincidentally contains "GPL-3"/"GPL-2", which ALLOWED
    exists to rescue), but that rescue must not be spendable to redeem a
    denied sibling like AGPL in "AGPL-3.0-only OR LGPL-3.0" — only a
    genuinely clean branch (e.g. MIT) can do that. A CONJUNCTION is denied
    if ANY branch is.
    """
    expression = spdx.replace("(", " ").replace(")", " ")
    if " OR " in expression.upper():
        branches = (re.split(r"\s+WITH\s+", part, flags=re.IGNORECASE)[0]
                    for part in re.split(r"\s+OR\s+", expression, flags=re.IGNORECASE))
        return not any(not _atom_denied(b, allow_carveout=False) for b in branches)
    if " AND " in expression.upper():
        return any(_spdx_denied(part)
                   for part in re.split(r"\s+AND\s+", expression, flags=re.IGNORECASE))
    return _atom_denied(re.split(r"\s+WITH\s+", expression, flags=re.IGNORECASE)[0])


def installed_packages() -> list[PackageInfo]:
    """Read the REAL environment. Without this, `audit` has no input and the
    gate exits 0 unconditionally — which is what it did as first written (F-33).
    """
    from importlib.metadata import distributions

    found: list[PackageInfo] = []
    for dist in distributions():
        meta = dist.metadata
        spdx = (meta.get("License-Expression") or meta.get("License") or "").strip()
        if not spdx or spdx.upper() == "UNKNOWN":
            classifiers = [c for c in meta.get_all("Classifier") or []
                           if c.startswith("License ::")]
            spdx = classifiers[-1].split("::")[-1].strip() if classifiers else "UNKNOWN"
        found.append(PackageInfo(name=dist.name, version=dist.version or "",
                                 spdx=spdx, extras=frozenset()))
    return found


def declared_extras(lockfile: str) -> set[tuple[str, str]]:
    """(package, extra) pairs actually resolved in uv.lock — an extra that is
    never installed cannot pull a licence in."""
    import tomllib

    with open(lockfile, "rb") as handle:
        lock = tomllib.load(handle)
    return {(pkg["name"], extra)
            for pkg in lock.get("package", [])
            for extra in pkg.get("optional-dependencies", {})}


def audit(packages: list[PackageInfo]) -> list[Violation]:
    violations: list[Violation] = []
    for pkg in packages:
        if _spdx_denied(pkg.spdx):
            violations.append(Violation(pkg.name, f"denied licence: {pkg.spdx}"))
            continue
        if pkg.name in NATIVE_DENY:
            violations.append(Violation(pkg.name, NATIVE_DENY[pkg.name]))
            continue
        for extra in pkg.extras:
            if (pkg.name, extra) in EXTRA_DENY:
                violations.append(Violation(pkg.name, EXTRA_DENY[(pkg.name, extra)]))
                break
    return violations


def main(argv: list[str]) -> int:
    """The entrypoint CI invokes. Without it the job exits 0 regardless (F-33)."""
    import sys

    packages = installed_packages()
    if "--check-lockfile" in argv:
        lockfile = argv[argv.index("--check-lockfile") + 1]
        extras = declared_extras(lockfile)
        packages = [p._replace(extras=frozenset(
            e for (name, e) in extras if name == p.name)) for p in packages]

    unknown = [p.name for p in packages if p.spdx == "UNKNOWN"]
    violations = audit(packages)
    for v in violations:
        print(f"DENIED {v.package}: {v.reason}", file=sys.stderr)
    for name in unknown:
        print(f"UNKNOWN LICENCE {name}: resolve manually before shipping",
              file=sys.stderr)
    return 1 if violations or unknown else 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
