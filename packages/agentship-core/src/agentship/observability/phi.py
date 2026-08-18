"""The PHI privacy gate helpers (§4.6).

Two rules protect a caller's identity and content from leaking into a trace store. Content is gated
in :mod:`agentship.observability.capture` (request/response bodies emitted only when
``capture_content`` is set) and enforced structurally elsewhere; this module owns the identity rule:
the caller's ``user_id`` is stamped onto the root span only as a **salted hash**, never in clear.

The hash is deterministic for a given ``(user_id, salt)`` so the same caller correlates across their
traces, but the raw id cannot be recovered from a trace store. The salt comes from the environment
(``AGENTSHIP_HASH_SALT``); with no salt set the hash is still stable and non-reversible, just not
salted against a rainbow table — set a salt in any deployment handling real user ids.
"""

from __future__ import annotations

import hashlib
import os

#: Environment variable holding the per-deployment salt mixed into the user-id hash.
HASH_SALT_ENV = "AGENTSHIP_HASH_SALT"

#: Length (hex chars) of the stamped user-id digest — enough to correlate, too short to brute-force.
_DIGEST_CHARS = 16


def hashed_user_id(user_id: str, *, salt: str | None = None) -> str:
    """Return the salted, truncated SHA-256 of ``user_id`` for stamping on a span (§4.6).

    Deterministic for a given ``(user_id, salt)`` so a caller correlates across their own traces,
    while the raw id never reaches a trace store. When ``salt`` is ``None`` the value of
    ``AGENTSHIP_HASH_SALT`` is used (empty string if unset).
    """
    if salt is None:
        salt = os.environ.get(HASH_SALT_ENV, "")
    digest = hashlib.sha256(f"{user_id}{salt}".encode()).hexdigest()
    return digest[:_DIGEST_CHARS]
