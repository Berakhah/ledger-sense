from scripts.check_no_float import scan

GUARDED = ["ledger_sense/domain", "ledger_sense/rules", "ledger_sense/writeback"]


def test_scan_flags_a_float_literal(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("rate = 0.10\n")
    assert any("bad.py:1" in hit for hit in scan([str(tmp_path)]))


def test_scan_flags_a_float_call(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("x = float('1.5')\n")
    assert any("bad.py:1" in hit for hit in scan([str(tmp_path)]))


def test_scan_ignores_integer_literals(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("places = 2\n")
    assert scan([str(tmp_path)]) == []


def test_guarded_packages_contain_no_floats():
    """Layer 2: the real scan over the real tree."""
    hits = scan(GUARDED)
    assert hits == [], "float found in the money path:\n" + "\n".join(hits)


def test_no_domain_model_field_resolves_to_float():
    """Layer 3: catches the serialisation case the AST scan cannot see.

    Walks every module in ledger_sense.domain, not just .models — vendor.py
    is a second domain module and previously sat entirely outside this
    guard. Match.score is a rapidfuzz fuzzy-match score (0-100), not money,
    and is the one deliberate, documented exception (see its field comment
    in ledger_sense/domain/vendor.py)."""
    import importlib
    import pkgutil

    import ledger_sense.domain
    from pydantic import BaseModel

    ALLOWED_FLOAT_FIELDS = {"Match.score"}

    offenders = []
    for info in pkgutil.walk_packages(ledger_sense.domain.__path__, "ledger_sense.domain."):
        module = importlib.import_module(info.name)
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                for field_name, field in obj.model_fields.items():
                    if field.annotation is float:
                        label = f"{obj.__name__}.{field_name}"
                        if label not in ALLOWED_FLOAT_FIELDS:
                            offenders.append(label)
    assert offenders == [], f"float-annotated fields: {offenders}"


def test_every_domain_model_is_strict():
    """Layer 4 (F-01). Without strict mode a JSON float is coerced into Decimal
    and every other layer stays green while the money is wrong.

    Walks every module in ledger_sense.domain, not just .models — see the
    comment on test_no_domain_model_field_resolves_to_float above."""
    import importlib
    import pkgutil

    import ledger_sense.domain
    from pydantic import BaseModel

    lax = []
    for info in pkgutil.walk_packages(ledger_sense.domain.__path__, "ledger_sense.domain."):
        module = importlib.import_module(info.name)
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                if not obj.model_config.get("strict"):
                    lax.append(obj.__name__)
    assert lax == [], f"models not in strict mode: {lax}"


def test_no_decimal_field_anywhere_accepts_a_float():
    """The end-to-end assertion. Walk every Decimal-annotated field in the whole
    package and prove each one refuses 1.5."""
    import importlib
    import pkgutil
    from decimal import Decimal

    import ledger_sense
    from pydantic import BaseModel, ValidationError

    offenders = []
    for info in pkgutil.walk_packages(ledger_sense.__path__, "ledger_sense."):
        module = importlib.import_module(info.name)
        for name in dir(module):
            obj = getattr(module, name)
            if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
                continue
            for field_name, field in obj.model_fields.items():
                if field.annotation is not Decimal:
                    continue
                try:
                    obj.__pydantic_validator__.validate_assignment(
                        obj.model_construct(), field_name, 1.5)
                    offenders.append(f"{obj.__name__}.{field_name}")
                except (ValidationError, Exception):
                    pass
    assert offenders == [], f"Decimal fields accepting a float: {offenders}"
