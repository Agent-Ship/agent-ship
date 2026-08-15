"""The tool registry + ``resolve_tool`` — how a ``tools:`` reference becomes a runnable ``Tool``.

Built-in tools register here at import; third parties add tools via the ``agentship.tools``
entry-point group (same mechanism as engines). A spec's ``tools:`` entry is resolved by
:func:`resolve_tool`, which accepts either a **registered name** (``"calculator"``) or a
**``"module:attr"``** reference to a :class:`~agentship.tools.tool.Tool` an author defines. An
unresolvable reference fails fast with :class:`~agentship.errors.SpecError` (declare, don't fake).
"""

from __future__ import annotations

import importlib

from ..errors import SpecError
from ..registry import Registry
from .builtins.calculator import calculator
from .builtins.http_request import http_request
from .builtins.web_search import web_search
from .tool import Tool

#: The registry of tools, discoverable via the ``agentship.tools`` entry-point group.
TOOLS: Registry[Tool] = Registry("agentship.tools", label="tool")

# Register the built-in tools carried forward from the old repo.
TOOLS.register("calculator", calculator)
TOOLS.register("http_request", http_request)
TOOLS.register("web_search", web_search)


def resolve_tool(ref: str) -> Tool:
    """Resolve a ``tools:`` reference to a :class:`Tool` — a registry name or ``"module:attr"``.

    A bare name (``"calculator"``) is looked up in :data:`TOOLS`. A ``"module:attr"`` reference
    imports the module and reads the attribute, which must be a :class:`Tool`. Anything that does
    not resolve to a ``Tool`` raises :class:`~agentship.errors.SpecError` with the offending ref.
    """
    if ":" in ref:
        module_name, _, attr = ref.partition(":")
        try:
            module = importlib.import_module(module_name)
            candidate = getattr(module, attr)
        except (ImportError, AttributeError) as exc:
            raise SpecError(f"tool {ref!r} could not be imported: {exc}") from exc
        if not isinstance(candidate, Tool):
            raise SpecError(f"tool {ref!r} is not a Tool (got {type(candidate).__name__})")
        return candidate

    tool = TOOLS.get(ref)
    if tool is None:
        raise SpecError(
            f"no tool registered as {ref!r} — use a built-in name, a 'module:attr' Tool "
            f"reference, or install the package that provides it"
        )
    return tool
