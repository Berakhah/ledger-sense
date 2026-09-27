"""generate() is called from FastAPI's thread pool (both demo seed routes),
and PDFium is not thread-safe — not even across different documents.
Parallel Playwright workers seeding at once segfaulted the API container
(exit 139) mid-suite.

The race runs in a child interpreter: when it loses, it takes the whole
process down, and that must fail this test rather than kill pytest.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_RACE = textwrap.dedent("""
    import threading
    from ledger_sense.corpus.generate import generate

    errors = []

    def work():
        try:
            for i in range(15):
                generate(seed=i, error="clean")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("errors:", len(errors), errors[:1])
    raise SystemExit(1 if errors else 0)
""")


def test_generate_survives_concurrent_callers() -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, "-c", _RACE], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=300, check=False)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr[-2000:])
