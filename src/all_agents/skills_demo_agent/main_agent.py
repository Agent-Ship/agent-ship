"""Skills demo agent — showcases built-in and custom skills."""

from src.all_agents.base_agent import BaseAgent
from src.agent_framework.utils.path_utils import resolve_config_path
from src.service.models.base_models import TextInput, TextOutput


class SkillsDemoAgent(BaseAgent):
    """Agent that demonstrates AgentShip's skill system.

    Skills are configured in main_agent.yaml under the ``skills:`` key.
    No Python wiring is needed for built-in templates; custom skills are
    imported by class path and follow the same BaseSkill interface.
    """

    def __init__(self):
        super().__init__(
            config_path=resolve_config_path(relative_to=__file__),
            input_schema=TextInput,
            output_schema=TextOutput,
        )
