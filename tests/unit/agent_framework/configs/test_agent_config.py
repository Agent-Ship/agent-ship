import pathlib
import tempfile
import os

from src.agent_framework.configs.agent_config import AgentConfig


def _get_project_root():
    """Find the project root by looking for a marker file (like pyproject.toml)."""
    current = pathlib.Path(__file__).resolve()
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    raise RuntimeError("Could not find project root")


def test_from_yaml_loads_basic_fields():
    project_root = _get_project_root()
    yaml_path = project_root / "src" / "all_agents" / "file_analysis_agent" / "main_agent.yaml"

    config = AgentConfig.from_yaml(str(yaml_path))

    assert config.agent_name == "file_analysis_agent"
    assert config.model_provider is not None
    assert config.model is not None
    assert isinstance(config.tags, list)


def test_from_yaml_loads_tools_list_if_present():
    project_root = _get_project_root()
    # Use orchestrator (trip_planner) which has sub-agent tools
    yaml_path = project_root / "src" / "all_agents" / "orchestrator_pattern" / "main_agent.yaml"

    config = AgentConfig.from_yaml(str(yaml_path))

    assert isinstance(config.tools, list)
    assert any(tool.get("type") == "agent" for tool in config.tools)


# ---------------------------------------------------------------------------
# agents.md support
# ---------------------------------------------------------------------------

_MINIMAL_YAML = """\
agent_name: test_agent
tags: []
llm_provider_name: openai
llm_model: gpt-4o
temperature: 0.4
execution_engine: adk
streaming_mode: none
description: Test agent
instruction_template: |
  You are a test agent.
"""


def _write_yaml(directory: str, content: str = _MINIMAL_YAML) -> str:
    yaml_path = os.path.join(directory, "main_agent.yaml")
    with open(yaml_path, "w") as f:
        f.write(content)
    return yaml_path


def test_agents_md_appended_to_instruction_template():
    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = _write_yaml(tmp)
        agents_md = os.path.join(tmp, "agents.md")
        with open(agents_md, "w") as f:
            f.write("# Extra Rules\n\nAlways be concise.")

        config = AgentConfig.from_yaml(yaml_path)

        assert "You are a test agent." in config.instruction_template
        assert "# Extra Rules" in config.instruction_template
        assert "Always be concise." in config.instruction_template


def test_agents_md_appended_after_instruction_template():
    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = _write_yaml(tmp)
        with open(os.path.join(tmp, "agents.md"), "w") as f:
            f.write("## Appended section")

        config = AgentConfig.from_yaml(yaml_path)

        idx_base = config.instruction_template.index("You are a test agent.")
        idx_md = config.instruction_template.index("## Appended section")
        assert idx_base < idx_md


def test_no_agents_md_leaves_instruction_template_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = _write_yaml(tmp)

        config = AgentConfig.from_yaml(yaml_path)

        assert config.instruction_template.strip() == "You are a test agent."


def test_empty_agents_md_leaves_instruction_template_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = _write_yaml(tmp)
        with open(os.path.join(tmp, "agents.md"), "w") as f:
            f.write("   \n  ")  # whitespace only

        config = AgentConfig.from_yaml(yaml_path)

        assert config.instruction_template.strip() == "You are a test agent."


def test_load_agents_md_false_skips_file():
    with tempfile.TemporaryDirectory() as tmp:
        yaml_content = _MINIMAL_YAML + "load_agents_md: false\n"
        yaml_path = _write_yaml(tmp, yaml_content)
        with open(os.path.join(tmp, "agents.md"), "w") as f:
            f.write("# Should not appear")

        config = AgentConfig.from_yaml(yaml_path)

        assert "Should not appear" not in config.instruction_template


def test_each_agent_loads_only_its_own_agents_md():
    """agents.md in one agent directory must not affect a different agent."""
    with tempfile.TemporaryDirectory() as agent_a_dir:
        with tempfile.TemporaryDirectory() as agent_b_dir:
            yaml_a = _write_yaml(agent_a_dir)
            with open(os.path.join(agent_a_dir, "agents.md"), "w") as f:
                f.write("Agent A rules")

            yaml_b = _write_yaml(agent_b_dir)
            # agent_b has no agents.md

            config_b = AgentConfig.from_yaml(yaml_b)

            assert "Agent A rules" not in config_b.instruction_template


def test_skills_demo_agent_agents_md_is_loaded():
    project_root = _get_project_root()
    yaml_path = project_root / "src" / "all_agents" / "skills_demo_agent" / "main_agent.yaml"

    config = AgentConfig.from_yaml(str(yaml_path))

    assert "agents.md" not in config.instruction_template  # file path not leaked
    assert "calculator" in config.instruction_template.lower() or "Skills Demo Agent" in config.instruction_template
