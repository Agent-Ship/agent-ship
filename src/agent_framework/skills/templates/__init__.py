"""Built-in skill template registry.

Maps template names (used in agent YAML ``skills[].template``) to their
fully-qualified class paths so ToolManager can import them by name.
"""

SKILL_REGISTRY: dict[str, str] = {
    "calculator": "src.agent_framework.skills.templates.calculator.skill.CalculatorSkill",
    "web_search": "src.agent_framework.skills.templates.web_search.skill.WebSearchSkill",
    "http_request": "src.agent_framework.skills.templates.http_request.skill.HttpRequestSkill",
}
