"""Phase 03 · C2 — the vendor-neutral ``Tool`` + the built-in skills (calculator first).

A ``Tool`` is a name + description + optional args schema wrapping a plain callable; a built-in
skill is just a registered ``Tool``. These tests exercise the abstraction and the calculator
end-to-end offline (no model, no network) — the calculator carries forward the old repo's safe
AST-only evaluation (no ``eval``), so no functionality is lost.
"""

from __future__ import annotations

import json

import pytest
from agentship.errors import SpecError
from agentship.tools import TOOLS, Tool, resolve_tool


async def test_calculator_evaluates_a_safe_expression():
    """The built-in calculator resolves by name and evaluates arithmetic to a numeric result."""
    calc = resolve_tool("calculator")
    assert isinstance(calc, Tool)
    assert calc.name == "calculator"
    out = json.loads(await calc.run(expression="2 + 2 * 10"))
    assert out["result"] == 22


async def test_calculator_rejects_an_unsafe_expression():
    """Only arithmetic is allowed — an attempted call/import is refused, never executed."""
    calc = TOOLS.get("calculator")
    out = json.loads(await calc.run(expression="__import__('os').system('echo hi')"))
    assert "error" in out


async def test_calculator_reports_division_by_zero():
    """A math error returns a clean error payload, not a crash."""
    calc = TOOLS.get("calculator")
    out = json.loads(await calc.run(expression="1/0"))
    assert "error" in out


def test_resolve_unknown_tool_is_a_spec_error():
    """An unresolvable tool reference fails fast with an actionable :class:`SpecError`."""
    with pytest.raises(SpecError, match="nope-not-a-tool"):
        resolve_tool("nope-not-a-tool")
