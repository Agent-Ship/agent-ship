"""Best-effort PII/PHI redaction for captured span content (§4.6).

Content capture is opt-in and off by default (the PHI gate in :class:`ObservabilityConfig`). When an
operator *does* turn it on — to debug what a model actually saw — this module scrubs the obvious
direct identifiers (emails, phone numbers, national ids, card-like digit runs) before the text is
stamped on a span and shipped to a backend. It is a coarse safety net, **not** a compliance-grade
de-identifier: it pattern-matches surface forms and cannot understand context. For a health product
the durable answer is a real NER/PHI pass (e.g. OpenMed) upstream of capture; this keeps casual
identifiers from leaking in the meantime and caps payload size so a span never carries a whole
transcript. Nothing here runs unless ``capture_content`` is explicitly enabled.
"""

from __future__ import annotations

import re

#: The redaction marker substituted for a matched identifier, so a reader sees *that* something was
#: scrubbed (and its kind) rather than a silent gap.
_MASK = "[REDACTED:{kind}]"

#: Ordered (kind, pattern) rules. Email before the digit rules so an address is not half-masked by
#: the phone rule first. Each pattern targets a direct identifier, not free text.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("phone", re.compile(r"\b(?:\+?\d{1,3}[ -]?)?(?:\(?\d{3}\)?[ -]?)?\d{3}[ -]?\d{4}\b")),
)

#: Hard cap on a captured string's length so a span never ships an unbounded transcript.
_MAX_LEN = 4000


def redact_pii(text: str) -> str:
    """Return ``text`` with obvious direct identifiers masked and its length capped.

    Applies each rule in order, replacing every match with ``[REDACTED:<kind>]``, then truncates to
    :data:`_MAX_LEN` characters (appending an ellipsis marker) so captured content stays bounded.
    Idempotent on already-masked text — the mask itself matches no rule.
    """
    for kind, pattern in _RULES:
        text = pattern.sub(_MASK.format(kind=kind), text)
    if len(text) > _MAX_LEN:
        text = text[:_MAX_LEN] + "…[truncated]"
    return text
