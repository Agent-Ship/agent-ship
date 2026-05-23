"""Unit tests for the AgentShip skills system."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agent_framework.skills.base_skill import BaseSkill
from src.agent_framework.skills.templates import SKILL_REGISTRY
from src.agent_framework.skills.templates.calculator.skill import CalculatorSkill
from src.agent_framework.skills.templates.http_request.skill import HttpRequestSkill
from src.agent_framework.skills.templates.web_search.skill import WebSearchSkill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json(result: str) -> dict:
    return json.loads(result)


# ---------------------------------------------------------------------------
# CalculatorSkill
# ---------------------------------------------------------------------------

class TestCalculatorSkill:
    def setup_method(self):
        self.skill = CalculatorSkill()

    def test_basic_addition(self):
        out = _json(self.skill.run('{"expression": "2 + 2"}'))
        assert out["result"] == 4

    def test_basic_subtraction(self):
        out = _json(self.skill.run('{"expression": "10 - 3"}'))
        assert out["result"] == 7

    def test_multiplication(self):
        out = _json(self.skill.run('{"expression": "42 * 17"}'))
        assert out["result"] == 714

    def test_division_returns_int_for_whole(self):
        out = _json(self.skill.run('{"expression": "10 / 2"}'))
        assert out["result"] == 5
        assert isinstance(out["result"], int)

    def test_complex_expression(self):
        out = _json(self.skill.run('{"expression": "(10 + 5) * 3 / 5"}'))
        assert out["result"] == 9

    def test_power(self):
        out = _json(self.skill.run('{"expression": "2 ** 10"}'))
        assert out["result"] == 1024

    def test_floor_division(self):
        out = _json(self.skill.run('{"expression": "7 // 2"}'))
        assert out["result"] == 3

    def test_modulo(self):
        out = _json(self.skill.run('{"expression": "10 % 3"}'))
        assert out["result"] == 1

    def test_plain_string_input(self):
        out = _json(self.skill.run("3 + 4"))
        assert out["result"] == 7

    def test_unsafe_import_rejected(self):
        out = _json(self.skill.run('{"expression": "__import__(\'os\')"}'))
        assert "error" in out

    def test_unsafe_call_rejected(self):
        out = _json(self.skill.run('{"expression": "print(1)"}'))
        assert "error" in out

    def test_division_by_zero(self):
        out = _json(self.skill.run('{"expression": "1 / 0"}'))
        assert "error" in out
        assert "zero" in out["error"].lower()

    def test_empty_expression(self):
        out = _json(self.skill.run('{"expression": ""}'))
        assert "error" in out

    def test_expression_in_result(self):
        out = _json(self.skill.run('{"expression": "5 + 5"}'))
        assert out["expression"] == "5 + 5"

    def test_is_base_skill(self):
        assert isinstance(self.skill, BaseSkill)

    def test_has_input_schema(self):
        assert self.skill.input_schema is not None


# ---------------------------------------------------------------------------
# WebSearchSkill
# ---------------------------------------------------------------------------

class TestWebSearchSkill:
    def setup_method(self):
        self.skill = WebSearchSkill()

    def test_stub_mode_returns_note(self):
        out = _json(self.skill.run('{"query": "python tips"}'))
        assert "note" in out
        assert "stub" in out["note"].lower()

    def test_stub_mode_returns_query(self):
        out = _json(self.skill.run('{"query": "hello world"}'))
        assert out["query"] == "hello world"

    def test_stub_mode_empty_results(self):
        out = _json(self.skill.run('{"query": "test"}'))
        assert out["results"] == []

    def test_plain_string_query(self):
        out = _json(self.skill.run("what is python"))
        assert "note" in out

    def test_missing_api_key_brave(self, monkeypatch):
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        skill = WebSearchSkill(config={"provider": "brave", "api_key_env": "BRAVE_API_KEY"})
        out = _json(skill.run('{"query": "test"}'))
        assert "error" in out
        assert "BRAVE_API_KEY" in out["error"]

    def test_is_base_skill(self):
        assert isinstance(self.skill, BaseSkill)

    def test_has_input_schema(self):
        assert self.skill.input_schema is not None


# ---------------------------------------------------------------------------
# HttpRequestSkill
# ---------------------------------------------------------------------------

class TestHttpRequestSkill:
    def setup_method(self):
        self.skill = HttpRequestSkill()

    def _mock_response(self, body: dict, status: int = 200):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(body).encode()
        mock_resp.status = status
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_get_request_success(self):
        mock_resp = self._mock_response({"hello": "world"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            out = _json(self.skill.run('{"url": "https://example.com/api"}'))
        assert out["status"] == 200
        assert out["body"] == {"hello": "world"}
        assert out["method"] == "GET"

    def test_post_request(self):
        mock_resp = self._mock_response({"ok": True})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            out = _json(self.skill.run('{"url": "https://example.com/api", "method": "POST", "body": {"key": "val"}}'))
        assert out["status"] == 200
        assert out["method"] == "POST"

    def test_missing_url(self):
        out = _json(self.skill.run("{}"))
        assert "error" in out

    def test_plain_string_url(self):
        mock_resp = self._mock_response({"data": 1})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            out = _json(self.skill.run("https://example.com"))
        assert out["status"] == 200

    def test_non_json_response_returned_as_text(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"plain text response"
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            out = _json(self.skill.run('{"url": "https://example.com"}'))
        assert out["body"] == "plain text response"

    def test_is_base_skill(self):
        assert isinstance(self.skill, BaseSkill)

    def test_has_input_schema(self):
        assert self.skill.input_schema is not None


# ---------------------------------------------------------------------------
# SKILL_REGISTRY
# ---------------------------------------------------------------------------

class TestSkillRegistry:
    def test_calculator_in_registry(self):
        assert "calculator" in SKILL_REGISTRY

    def test_web_search_in_registry(self):
        assert "web_search" in SKILL_REGISTRY

    def test_http_request_in_registry(self):
        assert "http_request" in SKILL_REGISTRY

    def test_registry_paths_importable(self):
        import importlib
        for name, path in SKILL_REGISTRY.items():
            module_path, cls_name = path.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            assert hasattr(mod, cls_name), f"Class {cls_name} not found in {module_path}"


# ---------------------------------------------------------------------------
# ToolManager skill integration
# ---------------------------------------------------------------------------

class TestToolManagerSkillIntegration:
    """Tests that ToolManager correctly loads skills and converts them to engine tools."""

    def _make_agent_config(self, skills: list):
        cfg = MagicMock()
        cfg.tools = []
        cfg.skills = skills
        cfg.mcp_servers = []
        cfg.agent_name = "test_agent"
        return cfg

    def test_loads_built_in_template_calculator(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        cfg = self._make_agent_config([{"id": "calc", "template": "calculator"}])
        # ADK needs google.adk — just test the skill-tool creation path directly
        skill_tool = ToolManager._create_skill_tool(
            {"id": "calc", "template": "calculator"}, "langgraph"
        )
        assert skill_tool is not None
        assert skill_tool.name == "calc"

    def test_loads_custom_import(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        skill_tool = ToolManager._create_skill_tool(
            {
                "id": "greeter",
                "import": "src.all_agents.skills_demo_agent.skills.example_custom_skill.GreeterSkill",
            },
            "langgraph",
        )
        assert skill_tool is not None
        assert skill_tool.name == "greeter"

    def test_unknown_template_returns_none(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        result = ToolManager._create_skill_tool(
            {"id": "missing", "template": "does_not_exist"}, "langgraph"
        )
        assert result is None

    def test_missing_template_and_import_returns_none(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        result = ToolManager._create_skill_tool({"id": "bad"}, "langgraph")
        assert result is None

    def test_skill_config_passed_to_skill(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        skill_tool = ToolManager._create_skill_tool(
            {"id": "search", "template": "web_search", "config": {"provider": "stub"}},
            "langgraph",
        )
        assert skill_tool is not None

    def test_langgraph_tool_has_correct_name(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        tool = ToolManager._create_skill_tool(
            {"id": "my_calc", "template": "calculator"}, "langgraph"
        )
        assert tool.name == "my_calc"

    def test_create_skill_tools_aggregates(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        cfg = self._make_agent_config([
            {"id": "calc", "template": "calculator"},
            {"id": "req", "template": "http_request"},
        ])
        tools = ToolManager._create_skill_tools(cfg, "langgraph")
        assert len(tools) == 2

    def test_disabled_skill_is_skipped(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        result = ToolManager._create_skill_tool(
            {"id": "calc", "template": "calculator", "enabled": False}, "langgraph"
        )
        assert result is None

    def test_enabled_true_skill_loads(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        result = ToolManager._create_skill_tool(
            {"id": "calc", "template": "calculator", "enabled": True}, "langgraph"
        )
        assert result is not None

    def test_disabled_skills_excluded_from_aggregation(self):
        from src.agent_framework.tools.tool_manager import ToolManager
        cfg = self._make_agent_config([
            {"id": "calc", "template": "calculator", "enabled": True},
            {"id": "req", "template": "http_request", "enabled": False},
        ])
        tools = ToolManager._create_skill_tools(cfg, "langgraph")
        assert len(tools) == 1
        assert tools[0].name == "calc"
