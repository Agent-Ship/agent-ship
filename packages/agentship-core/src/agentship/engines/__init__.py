"""Engines: the runtime seam (:mod:`agentship.engines.base`) plus concrete engines.

The zero-dependency :class:`~agentship.engines.echo.EchoEngine` ships here; vendor
engines (LangGraph, …) arrive as adapters in later phases, discovered via the same
``agentship.engines`` entry-point group.
"""

from __future__ import annotations
