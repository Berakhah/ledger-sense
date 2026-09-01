"""The baseline study. The whole differentiator collapses if this is vibes.

Every number carries its method: sample size, sampling frame, timing protocol,
what counts as touch time, loaded-rate definition, and a real interval.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from ledger_sense.domain.money import Money, money, quantize

SECONDS_PER_HOUR = 3600
MIN_SAMPLES_FOR_AN_INTERVAL = 8

# Student's t, two-tailed, 95%, indexed by degrees of freedom (n - 1).
#
# REVISED (F-52). The original table stopped at df=9 and fell back to 1.96 for
# everything larger. VERIFIED understatement of the margin, at exactly the sample
# sizes a real baseline study uses:
#     df= 9  true 2.262  vs 1.96  ->  15.4% too narrow
#     df=19  true 2.093  vs 1.96  ->   6.8% too narrow
#     df=49  true 2.010  vs 1.96  ->   2.6% too narrow
# Every error was in the direction that makes our interval look tighter than it
# is, in the artifact whose entire purpose is defensibility (B13).
_T_95 = {1: Decimal("12.706"), 2: Decimal("4.303"), 3: Decimal("3.182"),
         4: Decimal("2.776"), 5: Decimal("2.571"), 6: Decimal("2.447"),
         7: Decimal("2.365"), 8: Decimal("2.306"), 9: Decimal("2.262"),
         10: Decimal("2.228"), 11: Decimal("2.201"), 12: Decimal("2.179"),
         13: Decimal("2.160"), 14: Decimal("2.145"), 15: Decimal("2.131"),
         16: Decimal("2.120"), 17: Decimal("2.110"), 18: Decimal("2.101"),
         19: Decimal("2.093"), 20: Decimal("2.086"), 22: Decimal("2.074"),
         24: Decimal("2.064"), 26: Decimal("2.056"), 28: Decimal("2.048"),
         30: Decimal("2.042"), 35: Decimal("2.030"), 40: Decimal("2.021"),
         50: Decimal("2.009"), 60: Decimal("2.000"), 80: Decimal("1.990"),
         100: Decimal("1.984"), 150: Decimal("1.976"), 200: Decimal("1.972")}
_T_95_ASYMPTOTIC = Decimal("1.960")


def _t_95(df: int) -> Decimal:
    """Conservative lookup: below 200 df, take the next SMALLER tabulated df, so
    an interpolation error can only widen the interval, never narrow it."""
    if df in _T_95:
        return _T_95[df]
    smaller = [k for k in _T_95 if k < df]
    return _T_95[max(smaller)] if smaller and df < 200 else _T_95_ASYMPTOTIC


INTERVAL_SCOPE = (
    "The interval covers sampling variability in observed touch time only. It "
    "holds the loaded rate fixed at the figure you supplied; if that figure is "
    "itself uncertain, the true interval on cost per document is wider. Supply "
    "a low and high hourly rate to have that uncertainty included."
)

TOUCH_TIME_DEFINITION = (
    "Touch time is wall-clock time from opening the document to the moment it is "
    "filed or posted, including interruptions shorter than 60 seconds and excluding "
    "longer ones. It does not include time waiting on a third party."
)


class TimedSample(BaseModel):
    document_id: str
    doc_type: str
    touch_seconds: int
    exceptions: int


class LoadedRate(BaseModel):
    # strict=True (F-01, belt and braces): overhead_multiplier is a bare Decimal,
    # not Money, so it carries no field-level Strict() of its own — without this,
    # pydantic's lax mode silently coerces a float into it.
    model_config = ConfigDict(strict=True)

    hourly: Money
    overhead_multiplier: Decimal
    # Optional. When supplied, the reported interval widens to include the
    # client's own uncertainty about the rate rather than treating a figure they
    # gave off the top of their head as exact (F-53).
    hourly_low: Money | None = None
    hourly_high: Money | None = None

    def effective_hourly(self) -> Money:
        return quantize(money(self.hourly * self.overhead_multiplier), 2)

    def effective_range(self) -> tuple[Money, Money]:
        low = self.hourly_low if self.hourly_low is not None else self.hourly
        high = self.hourly_high if self.hourly_high is not None else self.hourly
        return (quantize(money(low * self.overhead_multiplier), 2),
                quantize(money(high * self.overhead_multiplier), 2))


class BaselineStudy(BaseModel):
    samples: list[TimedSample]
    loaded_rate: LoadedRate
    population_size: int
    sampling_frame: str
    collection_period: tuple[date, date]

    def _mean_seconds(self) -> Decimal:
        total = sum((Decimal(s.touch_seconds) for s in self.samples), Decimal(0))
        return (total / Decimal(len(self.samples))).quantize(Decimal("0.0001"))

    def cost_per_document(self) -> Money:
        hours = self._mean_seconds() / SECONDS_PER_HOUR
        return quantize(money(hours * self.loaded_rate.effective_hourly()), 2)

    def median_seconds(self) -> Decimal:
        """Reported alongside the mean. Touch time is right-skewed; a controller
        who asks 'what does a typical document cost' means the median."""
        ordered = sorted(Decimal(s.touch_seconds) for s in self.samples)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2

    def confidence_interval(self, level: Decimal = Decimal("0.95")) -> tuple[Money, Money]:
        """REVISED (F-52, F-53, F-54).

        Three changes. The t value is looked up properly rather than collapsing
        to 1.96 above df=9. The floor rises from 2 to 8, because a t-interval on
        three observations of a right-skewed quantity is not defensible to a
        controller who asks — and B13 says the whole differentiator collapses if
        the baseline is vibes. And the rate's own uncertainty is propagated when
        the client supplies a range, rather than being silently assumed away.
        """
        n = len(self.samples)
        if n < MIN_SAMPLES_FOR_AN_INTERVAL:
            raise ValueError(
                f"a defensible interval requires at least {MIN_SAMPLES_FOR_AN_INTERVAL} "
                f"timed samples; you have {n}. Time more documents rather than "
                f"reporting an interval this narrow sample cannot support.")
        seconds = [Decimal(s.touch_seconds) for s in self.samples]
        mean = self._mean_seconds()
        variance = sum(((x - mean) ** 2 for x in seconds), Decimal(0)) / (n - 1)
        std_err = variance.sqrt() / Decimal(n).sqrt()
        margin_seconds = _t_95(n - 1) * std_err

        rate_lo, rate_hi = self.loaded_rate.effective_range()
        lo = quantize(money((mean - margin_seconds) / SECONDS_PER_HOUR * rate_lo), 2)
        hi = quantize(money((mean + margin_seconds) / SECONDS_PER_HOUR * rate_hi), 2)
        return lo, hi

    def method_statement(self) -> str:
        start, end = self.collection_period
        return (
            f"Sample size: {len(self.samples)} of a population of "
            f"{self.population_size}.\n"
            f"Sampling frame: {self.sampling_frame}.\n"
            f"Collection period: {start.isoformat()} to {end.isoformat()}.\n"
            f"Timing protocol: each sampled document timed individually by "
            f"observation, not recalled.\n"
            f"Touch time: {TOUCH_TIME_DEFINITION}\n"
            f"Loaded rate: base hourly {self.loaded_rate.hourly} multiplied by "
            f"{self.loaded_rate.overhead_multiplier} to cover employer overhead.\n"
            f"Mean touch time {self._mean_seconds()}s; median "
            f"{self.median_seconds()}s. Touch time is right-skewed, so the two "
            f"differ; the cost figure uses the mean.\n"
            f"Interval method: Student's t, two-tailed, 95%, on the mean.\n"
            f"Interval scope: {INTERVAL_SCOPE}"
        )
