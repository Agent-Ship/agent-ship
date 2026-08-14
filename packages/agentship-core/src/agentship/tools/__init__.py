"""Tools & skills: the vendor-neutral ``Tool`` an agent calls, plus the built-in skills (Phase 03).

Public surface: :class:`Tool` (name + description + args schema + callable), the :data:`TOOLS`
registry, and :func:`resolve_tool` (turn a ``tools:`` reference into a runnable ``Tool``). Built-in
skills (calculator, and — later in this phase — ``http_request``/``web_search``) register on import.
"""

from __future__ import annotations

from .registry import TOOLS, resolve_tool
from .tool import Tool

__all__ = ["TOOLS", "Tool", "resolve_tool"]
