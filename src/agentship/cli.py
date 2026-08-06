"""The ``agentship`` command-line interface.

A thin wrapper over the harness: ``agentship run <file> --input ... [--stream]``
loads a YAML spec, builds the agent, runs (or streams) one turn, and prints the
output. Harness errors are caught and printed as a clean message with a non-zero
exit code rather than a traceback.
"""

from __future__ import annotations

import asyncio
import sys

import click

from .errors import AgentShipError
from .runtime import build_agent


@click.group()
def main() -> None:
    """AgentShip — run agents authored in YAML or Python."""


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--input", "input_text", required=True, help="The input text for one turn.")
@click.option("--stream", is_flag=True, help="Stream the response as events instead of one result.")
@click.option("--debug", is_flag=True, help="Re-raise on failure so the full traceback is shown.")
def run(file: str, input_text: str, stream: bool, debug: bool) -> None:
    """Run one turn of the agent declared in FILE and print its output.

    On failure the CLI prints a single clean ``Error: …`` line to stderr and exits
    ``1`` — never a raw traceback. Known harness failures
    (:class:`~agentship.errors.AgentShipError` and its subclasses, e.g.
    ``ModelError`` / ``SpecError`` / ``CapabilityError``) print their actionable
    message as-is. Any unexpected error prints its concise message plus a hint to
    re-run with ``--debug``. With ``--debug`` set, the original exception is
    re-raised so the full traceback surfaces for diagnosis.
    """
    try:
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
