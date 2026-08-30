"""Per-tenant layout profile cache (F-18).

Phase 2 builds the cache itself. What lands in Phase 0 is the one thing that
cannot be retrofitted safely: the tenant is a required constructor argument,
so no profile store can be opened without one.

Why a *geometry-only* cache still needs tenancy: a profile learned from one
client's pay applications — where the retainage column sits, which anchors a
field is found by — changes how the extractor reads the next document. That is
not leakage of the first client's data, it is influence of one client's
documents over another's results, and neither the client whose profile leaked
nor the client whose extraction it steered consented to it. Under a data
processing agreement the profile is derived from client documents, so it
inherits their boundary.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileCache:
    """Handle on one tenant's layout profiles.

    tenant_id is positional-first and has no default on purpose: constructing
    a cache without one is a TypeError at the call site, not a runtime
    surprise once two clients share a deployment.
    """

    tenant_id: str
    db_path: str
