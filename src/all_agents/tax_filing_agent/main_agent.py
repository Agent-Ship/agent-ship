from src.all_agents.base_agent import BaseAgent
from src.service.models.base_models import TextInput, TextOutput
from src.agent_framework.utils.path_utils import resolve_config_path


class TaxFilingAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            config_path=resolve_config_path(relative_to=__file__),
            input_schema=TextInput,
            output_schema=TextOutput,
        )
