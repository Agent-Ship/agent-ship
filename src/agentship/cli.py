"""The ``agentship`` command-line interface.

A thin wrapper over the harness: ``agentship run <file> --input ... [--stream]``
loads a YAML spec, builds the agent, runs (or streams) one turn, and prints the
output. Harness errors are caught and printed as a clean message with a non-zero
exit code rather than a traceback.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from .errors import AgentShipError
from .runtime import build_agent


def load_env_for_run(env_file: str | None) -> None:
    """Load provider credentials from a ``.env`` for a real ``agentship run``.

    Reads environment variables from a ``.env`` file so a user can supply a key
    like ``OPENAI_API_KEY`` without exporting it by hand. This is called only from
    inside a command function — never at import time — so importing ``agentship``
    or running the test suite never pulls a ``.env`` into the environment (which is
    what once caused tests to fire stray paid provider calls).

    When ``env_file`` is given it must exist: a missing path raises
    :class:`~agentship.errors.AgentShipError` so the CLI reports a clean ``Error:``
    rather than silently ignoring a typo. When it is ``None`` the current
    directory's ``.env`` is loaded if present (and skipped silently if absent).

    Loading uses ``override=False`` so a variable already exported in the real
    environment always wins over the ``.env`` value.
    """
    from dotenv import load_dotenv

    if env_file is not None:
        path = Path(env_file)
        if not path.is_file():
            raise AgentShipError(f"--env-file not found: {env_file}")
        load_dotenv(dotenv_path=path, override=False)
        return

    default = Path.cwd() / ".env"
    if default.is_file():
        load_dotenv(dotenv_path=default, override=False)


@click.group()
def main() -> None:
    """AgentShip — run agents authored in YAML or Python."""


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--input", "input_text", required=True, help="The input text for one turn.")
@click.option("--stream", is_flag=True, help="Stream the response as events instead of one result.")
@click.option(
    "--env-file",
    "env_file",
    default=None,
    help="Load environment variables from this .env file (defaults to ./.env if present).",
)
@click.option("--debug", is_flag=True, help="Re-raise on failure so the full traceback is shown.")
def run(file: str, input_text: str, stream: bool, env_file: str | None, debug: bool) -> None:
    """Run one turn of the agent declared in FILE and print its output.

    Before running, environment variables are loaded from a ``.env`` — the current
    directory's ``.env`` by default, or the file named by ``--env-file`` — so a
    provider key such as ``OPENAI_API_KEY`` need not be exported by hand. An
    already-exported variable is never overwritten by the ``.env``.

    On failure the CLI prints a single clean ``Error: …`` line to stderr and exits
    ``1`` — never a raw traceback. Known harness failures
    (:class:`~agentship.errors.AgentShipError` and its subclasses, e.g.
    ``ModelError`` / ``SpecError`` / ``CapabilityError``) print their actionable
    message as-is. Any unexpected error prints its concise message plus a hint to
    re-run with ``--debug``. With ``--debug`` set, the original exception is
    re-raised so the full traceback surfaces for diagnosis.
    """
    try:
        load_env_for_run(env_file)
        agent = build_agent(file)
        if stream:
            asyncio.run(_stream_turn(agent, input_text))
        else:
            result = asyncio.run(agent.run(input_text))
            click.echo(result.output)
    except AgentShipError as exc:
        # Expected harness failure: already carries an actionable message.
        if debug:
            raise
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        # Unexpected failure: show a concise message, not a wall of traceback,
        # and point at --debug for the full trace.
        if debug:
            raise
        click.echo(
            f"Error: {exc} (run with --debug for the full traceback)", err=True
        )
        sys.exit(1)


async def _stream_turn(agent, input_text: str) -> None:
    """Drive the agent's stream, printing each content chunk as it arrives.

    Content events are printed inline; the terminal ``done`` event ends the line.
    """
    async for event in agent.stream(input_text):
        if event.type == "content" and event.data is not None:
            click.echo(event.data, nl=False)
        elif event.type == "done":
            click.echo()  # newline terminating the streamed line
