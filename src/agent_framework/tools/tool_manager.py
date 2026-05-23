"""Clean Tool Manager - Pure New Architecture

This module now uses only the Universal Tool architecture.
All legacy code and backward compatibility has been removed.
"""

import asyncio
import importlib
import logging
from typing import Any, Dict, List, Optional

from src.agent_framework.configs.agent_config import AgentConfig
from src.agent_framework.factories.tool_factory import ToolFactory

logger = logging.getLogger(__name__)


class ToolManager:
    """Clean tool manager using only the new architecture.
    
    This class now provides a clean interface without any legacy fallback.
    All tool creation uses the new SimplifiedToolFactory.
    """
    
    @staticmethod
    def create_tools(agent_config: AgentConfig, engine_type: str) -> List[Any]:
        """Create tools for the specified engine using working legacy methods.
        
        Args:
            agent_config: Agent configuration with tool definitions
            engine_type: Target engine ('adk', 'langgraph')
            
        Returns:
            List of engine-specific tool objects
            
        Raises:
            ValueError: If engine_type is unsupported
            Exception: If tool creation fails (no fallback)
        """
        if engine_type not in ['adk', 'langgraph']:
            raise ValueError(f"Unsupported engine type: {engine_type}")
        
        tools = []
        tool_configs = agent_config.tools or []
        
        for tool_config in tool_configs:
            tool = ToolManager._create_single_tool(tool_config, engine_type)
            if tool:
                tools.append(tool)
            else:
                logger.warning(f"Failed to create tool: {tool_config.get('id', 'unknown')}")

        # Skill tools: resolve built-in templates or user-defined BaseSkill classes
        if getattr(agent_config, "skills", None):
            skill_tools = ToolManager._create_skill_tools(agent_config, engine_type)
            tools.extend(skill_tools)
            logger.info(f"Added {len(skill_tools)} skill tools")

        # MCP tools: discover from agent's mcp_servers and convert to engine format
        if getattr(agent_config, "mcp_servers", None):
            mcp_tools = ToolManager._create_mcp_tools(agent_config, engine_type)
            tools.extend(mcp_tools)
            logger.info(f"Added {len(mcp_tools)} MCP tools (total {len(tools)} for {engine_type} engine)")
        else:
            logger.info(f"Created {len(tools)} tools for {engine_type} engine")
        return tools
    
    @staticmethod
    def _create_single_tool_legacy(tool_config: Dict, engine_type: str) -> Optional[Any]:
        """Create a single tool for the specified engine (legacy method)."""
        tool_type = tool_config.get("type")
        
        if tool_type == "function":
            return ToolManager._create_function_tool_legacy(tool_config, engine_type)
        elif tool_type == "agent":
            return ToolManager._create_agent_tool_legacy(tool_config, engine_type)
        else:
            logger.warning(f"Unsupported tool type: {tool_type}")
            return None
    
    @staticmethod
    def _create_single_tool(tool_config: Dict, engine_type: str) -> Optional[Any]:
        """Create a single tool for the specified engine."""
        tool_type = tool_config.get("type")
        
        if tool_type == "function":
            return ToolManager._create_function_tool(tool_config, engine_type)
        elif tool_type == "agent":
            return ToolManager._create_agent_tool(tool_config, engine_type)
        else:
            logger.warning(f"Unsupported tool type: {tool_type}")
            return None
    
    @staticmethod
    def _create_function_tool(tool_config: Dict, engine_type: str) -> Optional[Any]:
        """Create a function tool."""
        import_path = tool_config.get("import")
        method_name = tool_config.get("method")
        
        if not import_path:
            logger.warning("Tool missing 'import' path")
            return None
        
        # Single import logic - NO DUPLICATION!
        target = ToolManager._import_symbol(import_path)
        
        if method_name:
            # Create instance and get method
            target_obj = target()
            func = getattr(target_obj, method_name, None)
            if not func:
                logger.warning(f"Method '{method_name}' not found on '{import_path}'")
                return None
        else:
            # Use the target directly
            func = target if callable(target) else None
            target_obj = target
            if not func:
                logger.warning(f"Imported object '{import_path}' is not callable")
                return None
        
        # Convert to engine-specific format
        if engine_type == "adk":
            return ToolManager._to_adk_function_tool(target_obj, func, tool_config)
        elif engine_type == "langgraph":
            return ToolManager._to_langgraph_function_tool(target_obj, func, tool_config)
        else:
            logger.warning(f"Unsupported engine type: {engine_type}")
            return None
    
    @staticmethod
    def _create_agent_tool(tool_config: Dict, engine_type: str) -> Optional[Any]:
        """Create an agent tool using the working legacy method."""
        agent_class_path = tool_config.get("agent_class")  # Use 'agent_class' like the working code
        if not agent_class_path:
            logger.warning("Agent tool missing 'agent_class'")
            return None
        
        # Import and instantiate agent
        module_path, class_name = agent_class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        agent_class = getattr(module, class_name)
        
        # Create agent instance directly (agents auto-load their config)
        try:
            agent_instance = agent_class()
        except Exception as e:
            logger.warning(f"Failed to create agent instance for {class_name}: {e}")
            return None
        
        # Convert to engine-specific format
        if engine_type == "adk":
            return ToolManager._to_adk_agent_tool(agent_instance, tool_config)
        elif engine_type == "langgraph":
            return ToolManager._to_langgraph_agent_tool(agent_instance, tool_config)
        else:
            logger.warning(f"Unsupported engine type: {engine_type}")
            return None
    
    @staticmethod
    def _create_mcp_tools(agent_config: AgentConfig, engine_type: str) -> List[Any]:
        """Discover tools from agent's MCP servers and return engine-specific tools.
        When no event loop is running, uses asyncio.run(). When a loop is already running
        (e.g. under uvicorn), runs discovery in a separate thread with a temporary client
        so tools resolve the client on the request loop at invoke time.
        """
        from src.agent_framework.mcp.adapters.adk import to_adk_tool
        from src.agent_framework.mcp.adapters.langgraph import to_langgraph_tool
        from src.agent_framework.mcp.tool_discovery import MCPToolDiscovery

        async def _gather(use_temporary_client: bool) -> List[Any]:
            discovery = MCPToolDiscovery()
            result: List[Any] = []
            for server_config in agent_config.mcp_servers:
                try:
                    if use_temporary_client:
                        tool_infos = await discovery.discover_tools_temporary(server_config)
                    else:
                        tool_infos = await discovery.discover_tools(server_config)
                except Exception as e:
                    logger.warning("MCP discovery failed for server %s: %s", server_config.id, e)
                    continue
                for info in tool_infos:
                    try:
                        if engine_type == "adk":
                            result.append(to_adk_tool(info, server_config, agent_name=agent_config.agent_name))
                        elif engine_type == "langgraph":
                            result.append(to_langgraph_tool(info, server_config, agent_name=agent_config.agent_name))
                    except Exception as e:
                        logger.warning("Failed to create MCP tool %s for %s: %s", info.name, server_config.id, e)
            return result

        try:
            return asyncio.run(_gather(use_temporary_client=False))
        except RuntimeError as e:
            if "no running event loop" not in str(e).lower() and "event loop" in str(e).lower():
                # Event loop already running (e.g. uvicorn): run discovery in a thread with a one-off client
                import concurrent.futures

                def _run_in_thread() -> List[Any]:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        return loop.run_until_complete(_gather(use_temporary_client=True))
                    finally:
                        loop.close()

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(_run_in_thread)
                    result = future.result(timeout=60)
                    return result
            raise

    @staticmethod
    def _import_symbol(dotted_path: str) -> Any:
        """Import a symbol from a dotted module path.
        
        SINGLE IMPLEMENTATION - NO DUPLICATION!
        """
        try:
            module_path, attr_name = dotted_path.rsplit(".", 1)
        except ValueError as exc:
            raise ImportError(f"Error parsing import path '{dotted_path}': {exc}") from exc
        
        module = importlib.import_module(module_path)
        try:
            return getattr(module, attr_name)
        except AttributeError as exc:
            raise ImportError(f"Module '{module_path}' has no attribute '{attr_name}'") from exc
    
    # Engine-specific conversion methods
    
    @staticmethod
    def _to_adk_function_tool(target_obj: Any, func: callable, config: Dict) -> Any:
        """Convert to Google ADK FunctionTool."""
        from google.adk.tools import FunctionTool
        
        # Check if object has to_function_tool method (our BaseTool)
        if hasattr(target_obj, 'to_function_tool') and callable(getattr(target_obj, 'to_function_tool')):
            return target_obj.to_function_tool()
        else:
            return FunctionTool(func)
    
    @staticmethod
    def _to_langgraph_function_tool(target_obj: Any, func: callable, config: Dict) -> Any:
        """Convert to LangChain StructuredTool.

        When the tool defines input_schema (Pydantic model), the LLM receives a proper
        JSON Schema and we wrap run(input: str) so the tool is invoked with **kwargs
        (parsed from the schema); we serialize to JSON and call run() for compatibility.
        """
        from langchain_core.tools import StructuredTool
        import json

        tool_name = getattr(target_obj, 'tool_name', config.get('id', func.__name__))
        tool_description = getattr(target_obj, 'tool_description', func.__doc__ or f"Call {tool_name}")
        input_schema = getattr(target_obj, 'input_schema', None)

        # When we have a schema, StructuredTool invokes the callable with **kwargs.
        # Our tools implement run(self, input: str), so we wrap: kwargs -> JSON string -> run().
        if input_schema is not None:

            def wrapped_sync(**kwargs: Any) -> str:
                return target_obj.run(json.dumps(kwargs, default=str))

            async def wrapped_async(**kwargs: Any) -> str:
                return target_obj.run(json.dumps(kwargs, default=str))

            import asyncio
            if asyncio.iscoroutinefunction(func):
                tool = StructuredTool.from_function(
                    func=None, coroutine=wrapped_async, name=tool_name,
                    description=tool_description, args_schema=input_schema,
                )
            else:
                tool = StructuredTool.from_function(
                    func=wrapped_sync, name=tool_name,
                    description=tool_description, args_schema=input_schema,
                )
        else:
            import asyncio
            if asyncio.iscoroutinefunction(func):
                tool = StructuredTool.from_function(
                    func=None, coroutine=func, name=tool_name,
                    description=tool_description, args_schema=input_schema,
                )
            else:
                tool = StructuredTool.from_function(
                    func=func, name=tool_name,
                    description=tool_description, args_schema=input_schema,
                )
        # Ensure metadata is always a dict (StructuredTool defaults to None)
        tool.metadata = tool.metadata or {"is_agent_tool": False}
        return tool
    
    @staticmethod
    def _to_adk_agent_tool(agent_instance: Any, config: Dict) -> Any:
        """Convert to Google ADK AgentTool."""
        from google.adk.tools import AgentTool
        
        # Extract ADK agent from instance
        adk_agent = ToolManager._extract_adk_agent(agent_instance)
        if adk_agent:
            return AgentTool(adk_agent)
        return None
    
    @staticmethod
    def _to_langgraph_agent_tool(agent_instance: Any, config: Dict) -> Any:
        """Convert to LangChain StructuredTool wrapping agent."""
        from langchain_core.tools import StructuredTool
        from pydantic import Field, create_model
        from src.service.models.base_models import AgentChatRequest
        import json
        
        agent_config = getattr(agent_instance, 'agent_config', None)
        if not agent_config:
            return None
        
        agent_name = agent_config.agent_name
        tool_name = config.get('id', agent_name)
        tool_description = agent_config.description or f"Invoke the {agent_name} agent"
        
        # Create input schema that matches the agent's expected input
        input_schema_cls = getattr(agent_instance, 'input_schema', None)
        if not input_schema_cls:
            input_schema_cls = create_model(
                f"{agent_name}Input",
                query=(str, Field(description="The query or input for the agent")),
            )
        
        async def invoke_agent(**kwargs) -> str:
            """Invoke the sub-agent."""
            try:
                # For agents with complex input schemas, we need to pass the kwargs as features
                # so the agent can extract them properly in _create_input_from_request
                
                # Convert kwargs to FeatureMap list for limit and other parameters
                from src.service.models.base_models import FeatureMap
                features = []
                for key, value in kwargs.items():
                    if key != 'query':  # Don't include query as a feature
                        features.append(FeatureMap(
                            feature_name=key,
                            feature_value=str(value)
                        ))
                
                chat_request = AgentChatRequest(
                    agent_name=agent_name,
                    user_id=kwargs.get('user_id', 'sub_agent_call'),
                    session_id=f"sub_agent_{agent_name}",
                    sender="USER",
                    query=kwargs.get('query', str(kwargs)),
                    features=features,
                )
                
                result = await agent_instance.chat(chat_request)
                return str(result.agent_response)
            except Exception as e:
                return f"Error calling {agent_name}: {str(e)}"
        
        tool = StructuredTool.from_function(
            func=None,
            coroutine=invoke_agent,
            name=tool_name,
            description=tool_description,
            args_schema=input_schema_cls,
        )
        # Mark this as an agent tool for observability
        tool.metadata = {"is_agent_tool": True, "agent_name": agent_name}
        return tool
    
    @staticmethod
    def _extract_adk_agent(agent_instance: Any) -> Optional[Any]:
        """Extract ADK agent from BaseAgent instance."""
        if not hasattr(agent_instance, 'engine'):
            return None
        
        engine = agent_instance.engine
        if hasattr(engine, '_inner'):
            engine = engine._inner
        if hasattr(engine, 'agent'):
            return engine.agent
        
        return None
    
    # Skill Methods

    @staticmethod
    def _create_skill_tools(agent_config: 'AgentConfig', engine_type: str) -> List[Any]:
        """Create engine-specific tools from the agent's ``skills:`` YAML section."""
        skill_tools: List[Any] = []
        for skill_config in agent_config.skills:
            tool = ToolManager._create_skill_tool(skill_config, engine_type)
            if tool:
                skill_tools.append(tool)
            else:
                logger.warning(f"Failed to create skill: {skill_config.get('id', 'unknown')}")
        return skill_tools

    @staticmethod
    def _create_skill_tool(skill_config: Dict, engine_type: str) -> Optional[Any]:
        """Resolve and instantiate a single skill, then convert to engine format.

        Lookup order:
        1. ``template`` key  → built-in SKILL_REGISTRY import path
        2. ``import`` key    → user-supplied fully-qualified class path

        Set ``enabled: false`` in the skill YAML entry to disable the skill
        without removing it from the configuration.
        """
        if skill_config.get("enabled", True) is False:
            logger.info(f"Skill '{skill_config.get('id', '?')}' is disabled, skipping")
            return None

        from src.agent_framework.skills.templates import SKILL_REGISTRY

        template = skill_config.get("template")
        import_path = skill_config.get("import")
        config_dict = skill_config.get("config") or {}

        if template:
            import_path = SKILL_REGISTRY.get(template)
            if not import_path:
                available = list(SKILL_REGISTRY.keys())
                logger.warning(
                    f"Unknown skill template '{template}'. Available: {available}"
                )
                return None

        if not import_path:
            logger.warning(
                f"Skill '{skill_config.get('id', '?')}' needs either 'template' or 'import'"
            )
            return None

        try:
            skill_cls = ToolManager._import_symbol(import_path)
        except ImportError as e:
            logger.warning(f"Failed to import skill '{import_path}': {e}")
            return None

        try:
            skill_obj = skill_cls(config=config_dict) if config_dict else skill_cls()
        except TypeError:
            # Skill __init__ doesn't accept config — instantiate without it
            skill_obj = skill_cls()

        # Override the skill id/name if the YAML specifies one
        skill_id = skill_config.get("id")
        if skill_id:
            skill_obj.name = skill_id

        # Re-use the existing function-tool converters (skills are BaseTool subclasses)
        if engine_type == "adk":
            return ToolManager._to_adk_function_tool(skill_obj, skill_obj.run, skill_config)
        elif engine_type == "langgraph":
            return ToolManager._to_langgraph_function_tool(skill_obj, skill_obj.run, skill_config)
        else:
            logger.warning(f"Unsupported engine type for skill: {engine_type}")
            return None

    # Legacy Methods for Backward Compatibility
    @staticmethod
    def _create_function_tool_legacy(tool_config: Dict, engine_type: str) -> Optional[Any]:
        """Create a function tool (legacy method)."""
        import_path = tool_config.get("import")
        method_name = tool_config.get("method")
        
        if not import_path:
            logger.warning("Tool missing 'import' path")
            return None
        
        # Import logic
        target = ToolManager._import_symbol(import_path)
        
        if method_name:
            # Create instance and get method
            target_obj = target()
            func = getattr(target_obj, method_name, None)
            if not func:
                logger.warning(f"Method '{method_name}' not found on '{import_path}'")
                return None
        else:
            # Use the target directly
            func = target if callable(target) else None
            target_obj = target
            if not func:
                logger.warning(f"Imported object '{import_path}' is not callable")
                return None
        
        # Convert to engine-specific format
        if engine_type == "adk":
            return ToolManager._to_adk_function_tool(target_obj, func, tool_config)
        elif engine_type == "langgraph":
            return ToolManager._to_langgraph_function_tool(target_obj, func, tool_config)
        else:
            logger.warning(f"Unsupported engine type: {engine_type}")
            return None
    
    @staticmethod
    def _create_agent_tool_legacy(tool_config: Dict, engine_type: str) -> Optional[Any]:
        """Create an agent tool (legacy method)."""
        agent_class_path = tool_config.get("agent_class")
        if not agent_class_path:
            logger.warning("Agent tool missing 'agent_class'")
            return None
        
        # Import and instantiate agent
        module_path, class_name = agent_class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        agent_class = getattr(module, class_name)
        
        # Get agent instance
        from src.agent_framework.registry.core import AgentRegistry
        agent_instance = AgentRegistry.get_agent_instance(agent_class)
        
        if not agent_instance:
            logger.warning(f"Failed to get agent instance for {class_name}")
            return None
        
        # Convert to engine-specific format
        if engine_type == "adk":
            return ToolManager._to_adk_agent_tool(agent_instance, tool_config)
        elif engine_type == "langgraph":
            return ToolManager._to_langgraph_agent_tool(agent_instance, tool_config)
        else:
            logger.warning(f"Unsupported engine type: {engine_type}")
            return None
