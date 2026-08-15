"""``Tool`` — the vendor-neutral thing an agent can call (Phase 03 · C2).

A ``Tool`` is deliberately tiny: a ``name``, a ``description`` the model reads to decide whether to
call it, an optional Pydantic ``args_schema`` describing its arguments, and a plain callable that
does the work. A built-in like the calculator is just a ``Tool`` — there is no separate base class
to subclass. This keeps tools composition-first: you build one by handing it a function, not by
inheriting from it. (A ``Skill`` is a different concept — a *how-to guidance* bundle that teaches
the agent how to use tools/MCP; see :mod:`agentship.skills`. Tools are the hands; skills are the
playbook.)

The engine adapter (e.g. ``agentship-langgraph``) converts a ``Tool`` into whatever the underlying
framework wants (a LangChain ``StructuredTool``), so this type never imports a vendor. ``run`` is
async and takes keyword args matching ``args_schema``; a sync callable is awaited transparently.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel


class Tool:
    """A callable an agent can invoke: name + description + optional args schema + a function."""

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[..., Any],
        *,
        args_schema: type[BaseModel] | None = None,
        side_effecting: bool = False,
    ) -> None:
        """Bind the tool's ``name``/``description``, the ``func`` that runs it, and its args schema.

        ``args_schema`` (a Pydantic model) declares the tool's arguments for the model's tool-call
        and validates them before ``func`` runs. ``side_effecting`` marks a tool whose call changes
        the world (a write/POST) — Phase 03 wraps those in the exactly-once ledger and Phase 02's
        confirm-before-write can gate them; a pure read leaves it ``False``.
        """
        self.name = name
        self.description = description
        self.func = func
        self.args_schema = args_schema
        self.side_effecting = side_effecting

    async def run(self, **kwargs: Any) -> str:
        """Validate ``kwargs`` against ``args_schema`` (if any), call ``func``, return its result.

        A coroutine ``func`` is awaited; a plain function is called directly. The result is returned
        as a string (a non-string is ``str()``-ified) so it flows back into the turn as tool output.
        """
        if self.args_schema is not None:
            kwargs = self.args_schema(**kwargs).model_dump()
        result = self.func(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, str) else str(result)
