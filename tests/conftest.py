"""Top-level pytest config.

`pytest_plugins` registers `tests/run/conftest.py`'s fixtures (the `db`
fixture / `RunDb`) for the whole suite, not just `tests/run/`. This is the
cleaner alternative to importing the `db` fixture function by name into each
test module that needs it (see tests/test_main.py) — it avoids importing a
fixture just for pytest's collection to see it, which ruff flags as unused.
"""

pytest_plugins = ["tests.run.conftest"]
