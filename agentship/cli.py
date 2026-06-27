"""AgentShip CLI: init, new-agent, serve."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click


# ---------------------------------------------------------------------------
# Templates (inline — no external template files needed)
# ---------------------------------------------------------------------------

_AGENT_PY_TEMPLATE = '''\
from agentship import Agent


class {class_name}(Agent):
    pass
'''

_AGENT_YAML_TEMPLATE = '''\
agent_name: {agent_name}
engine: langgraph
model: openai/gpt-4o-mini   # or anthropic/claude-3-5-haiku-20241022, etc.
temperature: 0.4

# System prompt — describe what this agent does
system_prompt: |
  You are a helpful assistant named {agent_name}.

# Session persistence
memory:
  backend: memory             # memory | postgres
  # url: "${{AGENTSHIP_DATABASE_URL}}"  # uncomment for postgres

# Observability
observability:
  backend: none               # none | langfuse
  # Set LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY env vars when using langfuse

# MCP tool servers (STDIO only in this release)
mcp:
  servers: []
  # - name: filesystem
  #   transport: stdio
  #   command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
'''

_TOML_TEMPLATE = '''\
[project]
name = "{project_name}"

[serve]
host = "0.0.0.0"
port = 7001

# Set environment variables in .env or export them before running agentship serve
'''

_SKILLS_EXAMPLE = '''\
# Domain Knowledge

Add markdown files here to inject domain knowledge into this agent's system prompt.
Each .md file in this directory is automatically loaded — no code changes needed.

Delete this file and replace it with your own knowledge files.
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_class_name(name: str) -> str:
    """Convert snake_case or kebab-case to PascalCase."""
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_")) + "Agent"


def _check_project_root() -> Path:
    """Verify cwd is an agentship project or exit with a clear error."""
    toml = Path.cwd() / "agentship.toml"
    if not toml.exists():
        click.echo(
            click.style("Error: ", fg="red") +
            "No agentship.toml found in current directory.\n"
            "Run 'agentship init <project-name>' first, then cd into the project.",
            err=True,
        )
        sys.exit(1)
    return Path.cwd()


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.1.0", prog_name="agentship")
def cli():
    """AgentShip — production-ready AI agents.

    \b
    Quick start:
      agentship init my-project
      cd my-project
      agentship new-agent triage
      agentship serve
    """


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("project_name")
@click.option("--force", is_flag=True, help="Overwrite an existing project directory.")
def init(project_name: str, force: bool) -> None:
    """Scaffold a new AgentShip project directory."""
    target = Path.cwd() / project_name

    if target.exists() and not force:
        click.echo(
            click.style("Error: ", fg="red") +
            f"'{project_name}' already exists. Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    # Create directories
    (target / "agents").mkdir(parents=True, exist_ok=True)

    # agentship.toml
    toml_path = target / "agentship.toml"
    if not toml_path.exists() or force:
        toml_path.write_text(_TOML_TEMPLATE.format(project_name=project_name))

    # .env stub
    env_path = target / ".env"
    if not env_path.exists():
        env_path.write_text(
            "# AgentShip environment variables\n"
            "OPENAI_API_KEY=\n"
            "# AGENTSHIP_DATABASE_URL=postgresql://user:pass@localhost:5432/agentship\n"
            "# LANGFUSE_PUBLIC_KEY=\n"
            "# LANGFUSE_SECRET_KEY=\n"
        )

    # .gitignore
    gi_path = target / ".gitignore"
    if not gi_path.exists():
        gi_path.write_text(".env\n__pycache__/\n*.pyc\n")

    click.echo(click.style("✓ ", fg="green") + f"Project '{project_name}' created at {target}")
    click.echo(f"\n  cd {project_name}")
    click.echo("  agentship new-agent <name>")
    click.echo("  agentship serve")


# ---------------------------------------------------------------------------
# new-agent
# ---------------------------------------------------------------------------

@cli.command("new-agent")
@click.argument("name")
@click.option("--force", is_flag=True, help="Overwrite existing agent files.")
def new_agent(name: str, force: bool) -> None:
    """Scaffold a new agent inside the current project's agents/ directory."""
    root = _check_project_root()
    agent_dir = root / "agents" / name

    if agent_dir.exists() and not force:
        click.echo(
            click.style("Error: ", fg="red") +
            f"agents/{name} already exists. Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "__skills__").mkdir(exist_ok=True)

    class_name = _to_class_name(name)

    # agent.py
    py_path = agent_dir / "agent.py"
    if not py_path.exists() or force:
        py_path.write_text(_AGENT_PY_TEMPLATE.format(class_name=class_name))

    # agent.yaml
    yaml_path = agent_dir / "agent.yaml"
    if not yaml_path.exists() or force:
        yaml_path.write_text(_AGENT_YAML_TEMPLATE.format(agent_name=name))

    # Example skill file
    skill_path = agent_dir / "__skills__" / "example.md"
    if not skill_path.exists():
        skill_path.write_text(_SKILLS_EXAMPLE)

    click.echo(click.style("✓ ", fg="green") + f"Agent '{name}' created at agents/{name}/")
    click.echo(f"\n  agent.py  → class {class_name}(Agent)")
    click.echo(f"  agent.yaml → model, memory, MCP, observability config")
    click.echo(f"  __skills__/ → drop .md files here to inject domain knowledge")
    click.echo(f"\nStart the server:  agentship serve")
    click.echo(f"Chat:              POST /agents/{name}/chat")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host.")
@click.option("--port", default=7001, show_default=True, help="Bind port.")
@click.option("--reload", is_flag=True, help="Enable auto-reload on file changes.")
@click.option("--workers", default=1, show_default=True, help="Number of worker processes.")
def serve(host: str, port: int, reload: bool, workers: int) -> None:
    """Start the AgentShip server and serve all agents in agents/."""
    root = _check_project_root()

    # Load .env if present
    env_file = root / ".env"
    if env_file.exists():
        _load_dotenv(env_file)

    click.echo(click.style("AgentShip ", fg="cyan", bold=True) + f"serving from {root}")
    click.echo(f"  Host: {host}:{port}")
    click.echo(f"  Docs: http://localhost:{port}/docs\n")

    import uvicorn
    from agentship.core.runtime import create_app

    app = create_app(root)

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,
        log_level="info",
    )


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader — sets unset env vars from key=value lines."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    cli()
