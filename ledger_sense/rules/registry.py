"""Rule registry.

Convention rules encode negotiated or statutory behaviour and are the class
that is fluent to state and silent when wrong. They cannot be registered
without a RuleSource naming a human who confirmed them. Arithmetic and
integrity rules are verifiable from first principles and need no source.
"""

from collections.abc import Callable
from typing import Any, Literal, NamedTuple

from ledger_sense.rules.provenance import RuleSource

RuleClass = Literal["arithmetic", "integrity", "convention"]
NEEDS_SOURCE: frozenset[str] = frozenset({"convention"})


class UnsourcedRuleError(Exception):
    pass


class RegisteredRule(NamedTuple):
    rule_id: str
    rule_class: RuleClass
    fn: Callable[..., list[Any]]
    source: RuleSource | None


_REGISTRY: dict[str, RegisteredRule] = {}


def register(
    rule_id: str, rule_class: RuleClass, source: RuleSource | None = None
) -> Callable[[Callable[..., list[Any]]], Callable[..., list[Any]]]:
    def decorate(fn: Callable[..., list[Any]]) -> Callable[..., list[Any]]:
        if rule_class in NEEDS_SOURCE:
            if source is None:
                raise UnsourcedRuleError(
                    f"{rule_id} is a {rule_class} rule and requires a RuleSource"
                )
            if not getattr(source, "confirmed_by", None):
                raise UnsourcedRuleError(
                    f"{rule_id}: RuleSource.confirmed_by must name the person who "
                    f"confirmed this rule"
                )
        _REGISTRY[rule_id] = RegisteredRule(rule_id, rule_class, fn, source)
        return fn

    return decorate


def registered_rules() -> dict[str, RegisteredRule]:
    return dict(_REGISTRY)
