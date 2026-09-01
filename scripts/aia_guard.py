"""No AIA form artifact in the repo; the non-affiliation notice is present.

AIA G702/G703 are copyrighted and actively enforced. We mimic the column math,
never the form design.
"""

import os
import pathlib
import re

from ledger_sense.notice import NON_AFFILIATION_NOTICE

FORM_NAME = re.compile(r"g70[23]", re.IGNORECASE)
BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".dwg"}
NOTICE_FRAGMENT = NON_AFFILIATION_NOTICE[:60].lower()   # F-55: one definition
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".ruff_cache",
             ".pytest_cache", ".mypy_cache", ".hypothesis", ".next"}


def guard(repo_root: str) -> list[str]:
    """One walk: flag form-named binaries, confirm the notice is somewhere in the docs.

    os.walk with in-place pruning rather than rglob: rglob enumerates every path
    under .venv before the skip test can reject it, which on this repo is tens of
    thousands of files for a check that reads none of them.
    """
    problems: list[str] = []
    notice_found = False

    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = pathlib.Path(dirpath) / name
            suffix = path.suffix.lower()
            if suffix in BINARY_SUFFIXES and FORM_NAME.search(name):
                problems.append(f"{path}: filename suggests an AIA form artifact")
            if not notice_found and suffix == ".md":
                text = path.read_text(encoding="utf-8", errors="ignore")
                notice_found = NOTICE_FRAGMENT in text.lower()

    if not notice_found:
        problems.append("non-affiliation notice not found in any markdown file")
    return problems


if __name__ == "__main__":
    import sys

    found = guard(".")
    for problem in found:
        print(problem, file=sys.stderr)
    sys.exit(1 if found else 0)
