"""Structured, colored, leveled console logging for the ``agentship`` logger tree.

Every part of the framework logs to a child of the ``agentship`` logger — ``agentship.engine``,
``agentship.mcp``, ``agentship.tools``, ``agentship.skills``, ``agentship.supervisor``, and so on.
By default those loggers are silent (no handler), so importing the library never prints anything.
:func:`configure_logging` attaches one colored handler to the top-level ``agentship`` logger and
sets its level, turning the whole tree on at once — the CLI calls it for ``--verbose`` (INFO) and
``--debug`` (DEBUG).

The output is deliberately close to the old repo's beloved format::

    12:41:07 INFO  engine    build path=template model=gpt-4o-mini tools=1
    12:41:07 DEBUG mcp       connecting to 1 server: time
    12:41:08 WARN  engine    allowed_tools dropped 2 tools: x, y
    12:41:09 ERROR tools     tool 'web_search' failed: 401 Unauthorized

Each line is ``time LEVEL component message``: the level is colorized per severity, the component is
the logger name with the ``agentship.`` prefix stripped, and everything is column-aligned so a long
run scans cleanly. Color is emitted only to a real terminal (and never when ``NO_COLOR`` is set), so
piping to a file or another process yields clean, uncolored text. The formatter has **no third-party
dependency** — the kernel stays vendor-free — so it is safe to import anywhere in core.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TextIO

#: The root of the framework's logger tree. Every ``agentship.<component>`` logger is a child, so
#: attaching one handler here lights up the whole tree and setting the level here gates all of them.
ROOT_LOGGER_NAME = "agentship"

#: ANSI SGR codes keyed by level name. Chosen to match the old repo: DEBUG cyan, INFO green,
#: WARNING yellow, ERROR red, CRITICAL bold red. ``RESET`` clears all attributes.
_LEVEL_COLORS = {
    "DEBUG": "\033[36m",  # cyan
    "INFO": "\033[32m",  # green
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",  # red
    "CRITICAL": "\033[1;31m",  # bold red
}
_RESET = "\033[0m"
_DIM = "\033[2m"

#: Displayed, fixed-width level labels — ``WARNING`` is shortened to ``WARN`` so every label fits in
#: five columns and the message column stays aligned across levels.
_LEVEL_LABELS = {
    "DEBUG": "DEBUG",
    "INFO": "INFO ",
    "WARNING": "WARN ",
    "ERROR": "ERROR",
    "CRITICAL": "CRIT ",
}

#: Noisy third-party loggers pinned to WARNING so a ``--debug`` run shows *our* DEBUG lines without
#: drowning in library chatter (LiteLLM request dumps, httpx connection logs, asyncio task noise).
_NOISY_LIBRARIES = (
    "litellm",
    "LiteLLM",
    "httpx",
    "httpcore",
    "urllib3",
    "openai",
    "anthropic",
    "asyncio",
)


class ColorFormatter(logging.Formatter):
    """Render a record as ``time LEVEL component message``, colorizing the level on a terminal.

    ``component`` is the logger name with the ``agentship.`` prefix stripped (so
    ``agentship.engine`` shows as ``engine``) and padded to a fixed width for column alignment.
    When ``color`` is true the level label is wrapped in its ANSI color and the time/component are
    dimmed; when false the same layout is emitted as plain text (for pipes, files, and
    ``NO_COLOR``). An exception attached to the record is appended by the base class as an indented
    traceback.
    """

    def __init__(self, *, color: bool, component_width: int = 10) -> None:
        """Configure the formatter with time as ``HH:MM:SS`` and whether to emit ANSI color."""
        super().__init__(datefmt="%H:%M:%S")
        self._color = color
        self._component_width = component_width

    def format(self, record: logging.LogRecord) -> str:
        """Format one record into the aligned ``time LEVEL component message`` line."""
        time_str = self.formatTime(record, self.datefmt)
        label = _LEVEL_LABELS.get(record.levelname, record.levelname[:5].ljust(5))
        component = record.name
        if component.startswith(ROOT_LOGGER_NAME + "."):
            component = component[len(ROOT_LOGGER_NAME) + 1 :]
        elif component == ROOT_LOGGER_NAME:
            component = "-"
        component = component.ljust(self._component_width)
        message = record.getMessage()

        if self._color:
            color = _LEVEL_COLORS.get(record.levelname, "")
            line = (
                f"{_DIM}{time_str}{_RESET} {color}{label}{_RESET} "
                f"{_DIM}{component}{_RESET} {message}"
            )
        else:
            line = f"{time_str} {label} {component} {message}"

        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def _should_use_color(stream: TextIO) -> bool:
    """Decide whether to emit ANSI color: only to a real TTY and never when ``NO_COLOR`` is set.

    Honors the informal ``NO_COLOR`` convention (any value disables color) and falls back to plain
    text whenever the stream is not an interactive terminal (a pipe, a file, a CI log), so
    redirected output never contains escape codes.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def configure_logging(
    level: int | str = logging.INFO,
    *,
    stream: TextIO | None = None,
    quiet_libraries: bool = True,
) -> None:
    """Attach one colored handler to the ``agentship`` logger tree and set its level.

    Idempotent: the handler is tagged so repeated calls (e.g. two commands in one process) update
    the level in place rather than stacking duplicate handlers that would double-print every line.
    ``level`` accepts a name (``"DEBUG"``) or a numeric level; ``stream`` defaults to ``stderr`` so
    the logs never pollute a piped stdout answer. When ``quiet_libraries`` is set, noisy third-party
    loggers are pinned to WARNING so our own DEBUG lines stay readable under ``--debug``.
    """
    if isinstance(level, str):
        level = logging.getLevelName(level.upper())
    target = stream if stream is not None else sys.stderr

    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level)
    # Don't bubble up to the Python root logger — otherwise a host app that configured the root
    # (e.g. a server, pytest) would print every line a second time in its own format.
    root.propagate = False

    handler = _existing_handler(root)
    if handler is None:
        handler = logging.StreamHandler(target)
        handler._agentship_managed = True  # type: ignore[attr-defined]  # tag so we find it again
        root.addHandler(handler)
    handler.setLevel(level)
    handler.setFormatter(ColorFormatter(color=_should_use_color(target)))

    if quiet_libraries:
        for name in _NOISY_LIBRARIES:
            logging.getLogger(name).setLevel(logging.WARNING)


def _existing_handler(logger: logging.Logger) -> logging.Handler | None:
    """Return the managed handler this module attached to ``logger`` (or ``None`` if there isn't).

    Used to keep :func:`configure_logging` idempotent: the managed handler carries an
    ``_agentship_managed`` tag so a second call reconfigures it in place instead of adding another.
    """
    for handler in logger.handlers:
        if getattr(handler, "_agentship_managed", False):
            return handler
    return None
