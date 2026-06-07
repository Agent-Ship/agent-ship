"""Calculator skill — safely evaluates math expressions."""

import ast
import json
import operator
from typing import Any, Dict

from pydantic import BaseModel, Field

from src.agent_framework.skills.base_skill import BaseSkill

# Allowed AST node types for safe evaluation
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

_OPS: dict = {
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
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        op_fn = _OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op_fn(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_fn = _OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op_fn(_safe_eval(node.operand))
    raise ValueError(f"Unsafe expression node: {type(node).__name__}")


class CalculatorInput(BaseModel):
    expression: str = Field(description="Math expression to evaluate, e.g. '2 + 2 * 10'")


class CalculatorSkill(BaseSkill):
    """Safely evaluates arithmetic expressions without using eval().

    Supports: +  -  *  /  //  %  ** and grouping with parentheses.
    Does NOT support function calls, imports, or any Python builtins.
    """

    skill_version = "1.0.0"
    input_schema = CalculatorInput

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(
            name="calculator",
            description=(
                "Evaluate a mathematical expression and return the numeric result. "
                "Supports +, -, *, /, //, %, ** and parentheses. "
                "Input: {'expression': '<math expression>'}"
            ),
            config=config,
        )

    def run(self, input: str) -> str:
        try:
            params = json.loads(input) if input.strip().startswith("{") else {"expression": input.strip()}
            expression = params.get("expression", "").strip()
            if not expression:
                return json.dumps({"error": "No expression provided"})

            tree = ast.parse(expression, mode="eval")
            for node in ast.walk(tree):
                if not isinstance(node, _SAFE_NODES):
                    return json.dumps({"error": f"Unsafe expression: only arithmetic is allowed"})

            result = _safe_eval(tree)
            # Return int if result is a whole number
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            return json.dumps({"expression": expression, "result": result})
        except (SyntaxError, ValueError) as e:
            return json.dumps({"error": str(e)})
        except ZeroDivisionError:
            return json.dumps({"error": "Division by zero"})
        except Exception as e:
            return json.dumps({"error": f"Calculation failed: {e}"})
