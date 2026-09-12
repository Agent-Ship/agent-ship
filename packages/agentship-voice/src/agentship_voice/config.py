"""Where the ``voice:`` authoring surface lives.

The model itself is :class:`~agentship.spec.VoiceSpec` in ``agentship-core``, next to
``ObservabilitySpec``, because the kernel owns every authoring surface and imports no vendor.
It is not duplicated here: one model with one name, imported from one place.

Provider *names* are validated in :mod:`agentship_voice.factories`, not in the kernel — the same
split observability uses. The kernel knows the block exists; the adapter knows which providers it
can actually build.
"""

from __future__ import annotations

from agentship.spec import VoiceSpec

__all__ = ["VoiceSpec"]
