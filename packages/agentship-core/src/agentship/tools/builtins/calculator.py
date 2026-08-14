"""The ``calculator`` built-in skill — safe arithmetic, carried forward from the old repo.

Evaluates ``+ - * / // % **`` and parentheses over an AST allow-list — never Python ``eval`` — so a
model can do math without opening a code-execution hole. Any function call, name, or import in the
expression is refused. This is a faithful carry-forward of ``agent-ship``'s ``CalculatorSkill`` (no
functionality lost); it is registered as a :class:`~agentship.tools.tool.Tool` named ``calculator``.
"""

from __future__ import annotations

import ast
import json
import operator

from pydantic import BaseModel, Field

from ..tool import Tool

#: AST node types a safe arithmetic expression may contain — anything else is refused.
_SAFE_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
)

#: The arithmetic operators the evaluator implements, keyed by their AST node type.
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    """Recursively evaluate an allow-listed arithmetic AST node, raising on anything unsupported."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        op_fn = _OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"unsupported operator: {type(node.op).__name__}")
        return op_fn(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_fn = _OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"unsupported operator: {type(node.op).__name__}")
        return op_fn(_safe_eval(node.operand))
    raise ValueError(f"unsafe expression node: {type(node).__name__}")


class CalculatorArgs(BaseModel):
    """Arguments for the calculator tool: one arithmetic ``expression`` to evaluate."""

    expression: str = Field(description="Math expression to evaluate, e.g. '2 + 2 * 10'")


def _calculate(expression: str) -> str:
    """Evaluate ``expression`` safely and return a JSON payload with the result or a clean error."""
    expression = expression.strip()
    if not expression:
        return json.dumps({"error": "no expression provided"})
    try:
        tree = ast.parse(expression, mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, _SAFE_NODES):
                return json.dumps({"error": "unsafe expression: only arithmetic is allowed"})
        result: float = _safe_eval(tree)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return json.dumps({"expression": expression, "result": result})
    except ZeroDivisionError:
        return json.dumps({"error": "division by zero"})
    except (SyntaxError, ValueError) as exc:
        return json.dumps({"error": str(exc)})


#: The built-in calculator tool, registered under the name ``calculator``.
calculator = Tool(
    name="calculator",
    description=(
        "Evaluate a mathematical expression and return the numeric result. "
        "Supports +, -, *, /, //, %, ** and parentheses."
    ),
    func=_calculate,
    args_schema=CalculatorArgs,
)
