"""Tools: the vendor-neutral ``Tool`` an agent calls, plus the built-in tools (Phase 03).

Public surface: :class:`Tool` (name + description + args schema + callable), the :data:`TOOLS`
registry, and :func:`resolve_tool` (turn a ``tools:`` reference into a runnable ``Tool``). Built-in
tools (calculator, and — later in this phase — ``http_request``/``web_search``) register on import.
A *tool* is an executable capability; a *skill* (:mod:`agentship.skills`) is separate — how-to
guidance that teaches the agent when/how to use tools.
"""

from __future__ import annotations

from .registry import TOOLS, resolve_tool
from .tool import Tool

__all__ = ["TOOLS", "Tool", "resolve_tool"]
