from typing import NamedTuple

from ledger_sense.domain.money import Money


class Finding(NamedTuple):
    rule_id: str
    field: str
    message: str
    expected: Money
    actual: Money
