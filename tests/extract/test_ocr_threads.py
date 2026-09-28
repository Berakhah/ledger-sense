"""ocr_words() renders pages with PDFium, which is not thread-safe — not even
across different documents. Nothing calls it from a request yet (scanned
documents route to review until OCR routing ships), but the day something
does, it shares FastAPI's thread pool with the demo seed routes' generate().

The race runs in a child interpreter, as in tests/corpus/test_generate_threads.py:
when it loses, it takes the whole process down, and that must fail this test
rather than kill pytest. The OCR engine is stubbed — the hazard is the render,
and loading the ONNX models would only slow the race down.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from ledger_sense.corpus.generate import generate
from ledger_sense.extract import ocr
from ledger_sense.pdfium_lock import PDFIUM_LOCK

ROOT = Path(__file__).resolve().parents[2]

_RACE = textwrap.dedent("""
    import threading
    from ledger_sense.corpus.generate import generate
    from ledger_sense.extract import ocr

    ocr._engine = lambda: (lambda image: ([], 0.0))
    _c, _a, pdf = generate(seed=1, error="clean")
    errors = []

    def generating():
        try:
            for i in range(15):
                generate(seed=i, error="clean")
        except BaseException as exc:
            errors.append(exc)

    def reading():
        try:
            for _ in range(15):
                ocr.ocr_words(pdf)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=fn) for fn in [generating, reading] * 4]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("errors:", len(errors), errors[:1])
    raise SystemExit(1 if errors else 0)
""")


def test_ocr_survives_concurrent_pdfium_callers() -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, "-c", _RACE], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=300, check=False)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr[-2000:])


def test_the_pdfium_lock_is_not_held_while_the_ocr_engine_runs(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Inference takes seconds a page and never touches PDFium. Holding the
    lock through it would stall every generate() behind a scan."""
    held: list[bool] = []

    def engine(image: np.ndarray) -> tuple[list[object], float]:
        held.append(PDFIUM_LOCK.locked())
        return [], 0.0

    monkeypatch.setattr(ocr, "_engine", lambda: engine)
    _c, _a, pdf = generate(seed=1, error="clean")
    ocr.ocr_words(pdf)
    assert held and not any(held)
