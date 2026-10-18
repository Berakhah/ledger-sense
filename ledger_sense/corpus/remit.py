"""Synthetic remit-to bank details for the corpus (LS-10).

Numbers are derived from the seed so a document's ground truth is reproducible,
and are obviously fake. Never real account data; no secret material."""

import random
from typing import Literal, NamedTuple


class Remit(NamedTuple):
    account: str
    routing: str


def remit_for(seed: int, variant: Literal["base", "changed"] = "base") -> Remit:
    rng = random.Random(f"remit-{seed}-{variant}")
    return Remit(account=str(rng.randrange(10**9, 10**10)),
                 routing=str(rng.randrange(10**8, 10**9)))
