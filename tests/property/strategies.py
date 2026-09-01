from datetime import date
from decimal import Decimal

from hypothesis import strategies as st

from ledger_sense.domain.models import ContinuationLine, Contract, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money

_amounts = st.decimals(min_value=Decimal(0), max_value=Decimal(1000000),
                       places=2, allow_nan=False, allow_infinity=False)


@st.composite
def consistent_pay_applications(draw: st.DrawFn) -> tuple[Contract, PayApplication]:
    """Yields a (contract, application) pair satisfying A1-A8 by construction (A9 is a convention rule, Phase 3 Task 24).
    Any rule that fires on this is a false positive."""
    n = draw(st.integers(min_value=1, max_value=12))
    lines: list[ContinuationLine] = []
    for i in range(n):
        previous = money(draw(_amounts))
        this_period = money(draw(_amounts))
        stored = money(draw(_amounts))
        total = money(previous + this_period + stored)
        lines.append(ContinuationLine(
            item_no=str(i), description="item", scheduled_value=total,
            previous=previous, this_period=this_period, stored=stored,
            total_completed_and_stored=total, retainage=money("0.00")))
    grand_total = money(sum((ln.total_completed_and_stored for ln in lines), money("0")))
    contract = Contract(id="C", original_sum=grand_total,
                        rounding=RoundingPolicy(2, 4), retainage_rate=Decimal("0.10"),
                        retainage_steps=[], jurisdiction=None)
    app = PayApplication(contract_id="C", application_no=1, period_to=date(2026, 8, 31),
                         lines=lines, change_orders_net=money("0.00"),
                         retainage_total=money("0.00"),
                         previous_payments=money("0.00"),
                         total_completed_and_stored=grand_total)
    return contract, app
