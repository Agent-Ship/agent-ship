from enum import Enum
import os
from typing import Any, Dict, List, Optional, Union

import yaml

from src.agent_framework.configs.llm.llm_provider_config import (
    LLMModel,
    LLMProviderName,
    LLMProviderConfig,
)
from src.agent_framework.configs.memory import MemoryConfig
from src.agent_framework.mcp.models import MCPServerConfig, MCPServerReference
from src.agent_framework.mcp.registry import MCPServerRegistry


class ExecutionEngine(Enum):
    """Execution engine for running agents.
    
    Attributes:
        ADK: Google ADK (Agent Development Kit) - default
        LANGGRAPH: LangGraph framework with LiteLLM
    """
    ADK = "adk"
    LANGGRAPH = "langgraph"


class StreamingMode(Enum):
    """Streaming mode configuration for agent responses.
    
    Attributes:
        NONE: No streaming, only final result returned
        EVENT_BASED: Chunk/event-based streaming (default)
        TOKEN_BASED: True token-level streaming (LangGraph only)
    """
    NONE = "none"
    EVENT_BASED = "event_based"
    TOKEN_BASED = "token_based"


class AgentConfig:
    """
    Agent configuration for all agents.
    This configuration is used to configure the agent.
    It is also used to validate that the model belongs to the correct provider.
    It is also used to load the agent configuration from a YAML file.
    It is also used to print the agent configuration.
    It is also used to get the agent configuration from a YAML file.
    """

    def __init__(
        self,
        llm_provider_name: LLMProviderName,
        llm_model: LLMModel = LLMModel.GPT_4O_MINI,
        temperature: float = 0.4,
        execution_engine: Union[ExecutionEngine, str] = ExecutionEngine.ADK,
        agent_name: str = "",
        description: str = "",
        instruction_template: str = "",
        tags: List[str] = [],
        tools: List[Dict[str, Any]] | None = None,
        skills: List[Dict[str, Any]] | None = None,
        memory: Optional[MemoryConfig] = None,
        streaming_mode: Union[StreamingMode, str] = StreamingMode.NONE,
        mcp_servers: Optional[List[Dict[str, Any]]] = None,
        max_tool_rounds: int = 10,
    ):
        self.model_provider = LLMProviderConfig.get_llm_provider(llm_provider_name)
        self.model = llm_model
        self.temperature = temperature
        
        # Execution engine - convert string to enum if needed
        if isinstance(execution_engine, str):
            try:
                execution_engine = ExecutionEngine(execution_engine)
            except ValueError:
                valid_engines = [e.value for e in ExecutionEngine]
                raise ValueError(
                    f"execution_engine must be one of {valid_engines}, got '{execution_engine}'"
                )
        self.execution_engine: ExecutionEngine = execution_engine
        
        self.agent_name = agent_name
        self.description = description
        self.tags = tags
        self.instruction_template = instruction_template

        # Optional tool configuration loaded from YAML.
        # Each entry is a dict describing how to construct a tool for this agent.
        # The BaseAgent class is responsible for interpreting this structure.
        self.tools: List[Dict[str, Any]] = tools or []

        # Optional skill configuration loaded from YAML.
        # Each entry references either a built-in template or a custom BaseSkill class.
        self.skills: List[Dict[str, Any]] = skills or []

        self.max_tool_rounds: int = max_tool_rounds

        # MCP server references: resolved from global registry with optional overrides.
        # Each entry is a full MCPServerConfig (registry base + agent overrides).
        self.mcp_servers: List[MCPServerConfig] = []
        if mcp_servers:
            registry = MCPServerRegistry.get_instance()
            for item in mcp_servers:
                ref_dict = {"id": item} if isinstance(item, str) else item
                ref = MCPServerReference(**ref_dict)
                base_config = registry.get_server(ref.id)
                if base_config is None:
                    available = registry.list_server_ids()
                    raise ValueError(
                        f"MCP server '{ref.id}' not found in registry. "
                        f"Available: {available or '(none)'}. "
                        "Ensure .mcp.settings.json or mcp_servers.yaml is loaded."
                    )
                resolved = self._merge_mcp_server_config(base_config, ref)
                self.mcp_servers.append(resolved)

        # Memory configuration (session and persistence memory)
        # If not provided, defaults to memory disabled
        self.memory: MemoryConfig = memory or MemoryConfig(enabled=False)
        
        # Streaming mode configuration - convert string to enum if needed
        if isinstance(streaming_mode, str):
            try:
                streaming_mode = StreamingMode(streaming_mode)
            except ValueError:
                valid_modes = [m.value for m in StreamingMode]
                raise ValueError(
                    f"streaming_mode must be one of {valid_modes}, got '{streaming_mode}'"
                )
        self.streaming_mode: StreamingMode = streaming_mode
        
        # Observability is configured purely via environment per Opik guide

        # Validate that the model belongs to the correct provider.
        # Providers with an empty models list (e.g. vLLM) accept any model name.
        if self.model_provider.models and self.model not in self.model_provider.models:
            available_models = [llm_model.value for llm_model in self.model_provider.models]
            raise ValueError(
                f"Model '{self.model.value}' is not compatible with provider '{self.model_provider.name.value}'. "
                f"Available models for {self.model_provider.name.value}: {available_models}"
            )

    @staticmethod
    def _merge_mcp_server_config(
        base: MCPServerConfig, ref: MCPServerReference
    ) -> MCPServerConfig:
        """Merge agent-specific overrides with base MCP server config."""
        update: Dict[str, Any] = {}
        if ref.env:
            update["env"] = {**base.env, **ref.env}
        if ref.timeout is not None:
            update["timeout"] = ref.timeout
        if ref.tools is not None:
            update["tools"] = ref.tools
        return base.model_copy(update=update)

    @classmethod
    def from_yaml(cls, file_path: str) -> 'AgentConfig':
        """
        Load agent configuration from a YAML file.
        
        Args:
            file_path: Path to YAML file. Can be absolute or relative.
                      For relative paths, use resolve_config_path() utility.
                      Convention: YAML filename should match the Python filename.
                      Example: main_agent.py → main_agent.yaml
        
        Returns:
            AgentConfig instance loaded from YAML file
        
        Raises:
            FileNotFoundError: If config file doesn't exist
        """
        # Resolve to absolute path if relative
        if not os.path.isabs(file_path):
            # If relative, resolve from current working directory
            file_path = os.path.abspath(file_path)
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Agent config file not found: {file_path}")
        
        with open(file_path, "r") as file:
            config = yaml.safe_load(file)

        # Parse memory configuration from YAML
        memory_config = None
        if "memory" in config:
            # Convert dict to MemoryConfig Pydantic model
            memory_config = MemoryConfig(**config["memory"])

        # Append agents.md from the same directory as the YAML if it exists.
        # Each agent's agents.md is strictly scoped to its own directory — there
        # is no inheritance or merging across agent directories, so files from
        # different agents never interfere with each other.
        # Set ``load_agents_md: false`` in the YAML to opt out explicitly.
        instruction_template = config["instruction_template"]
        if config.get("load_agents_md", True):
            agents_md_path = os.path.join(os.path.dirname(file_path), "agents.md")
            if os.path.exists(agents_md_path):
                with open(agents_md_path, "r") as md_file:
                    agents_md_content = md_file.read().strip()
                if agents_md_content:
                    instruction_template = f"{instruction_template}\n\n{agents_md_content}"

        return cls(
            llm_provider_name=LLMProviderName(config["llm_provider_name"]),
            llm_model=LLMModel(config["llm_model"]),
            temperature=config["temperature"],
            execution_engine=(
                config.get("execution_engine")
                or config.get("execution_backend")
                or config.get("runtime")
                or ExecutionEngine.ADK.value
            ),
            agent_name=config["agent_name"],
            description=config["description"],
            instruction_template=instruction_template,
            tags=config.get("tags", []),
            tools=config.get("tools", []) or [],
            skills=config.get("skills", []) or [],
            memory=memory_config,
            streaming_mode=config.get("streaming_mode", StreamingMode.NONE.value),
            mcp_servers=config.get("mcp_servers") or [],
            max_tool_rounds=config.get("max_tool_rounds", 10),
        )

    def __str__(self):
        return f"AgentConfig(model_provider={self.model_provider.name.value}, model={self.model.value}, \
            temperature={self.temperature}, agent_name={self.agent_name}, description={self.description}, \
            instruction_template={self.instruction_template}, memory_enabled={self.memory.enabled})"
