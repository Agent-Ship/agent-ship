"""Example custom skill — copy-paste template for user-defined skills.

To create your own skill:
1. Copy this file into your agent's skills/ folder
2. Rename the class and update name/description
3. Implement run() — input is a JSON string, return a JSON string
4. Optionally define input_schema (Pydantic model) for structured LLM arguments
5. Reference in main_agent.yaml:
     skills:
       - id: my_skill
         import: src.all_agents.my_agent.skills.my_custom_skill.MySkill
"""

import json
from typing import Any, Dict

from pydantic import BaseModel, Field

from src.agent_framework.skills.base_skill import BaseSkill


class GreeterInput(BaseModel):
    name: str = Field(description="Name of the person to greet")
    formal: bool = Field(default=False, description="Use formal greeting (Dear) vs casual (Hey)")


class GreeterSkill(BaseSkill):
    """Greets a person by name. A minimal example of a custom skill."""

    skill_version = "1.0.0"
    input_schema = GreeterInput

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(
            name="greeter",
            description="Greet someone by name. Input: {'name': 'Alice', 'formal': false}",
            config=config,
        )

    def run(self, input: str) -> str:
        try:
            params = json.loads(input) if input.strip().startswith("{") else {"name": input.strip()}
            name = params.get("name", "World").strip()
            formal = params.get("formal", False)
            prefix = "Dear" if formal else "Hey"
            return json.dumps({"greeting": f"{prefix}, {name}!"})
        except Exception as e:
            return json.dumps({"error": str(e)})
