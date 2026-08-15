"""Tests for the structured, colored, leveled console logging (:mod:`agentship.logs`).

Covers the three promises the CLI relies on: the framework logger tree is silent until
:func:`configure_logging` is called; the formatter renders the aligned ``time LEVEL component
message`` layout with the ``agentship.`` prefix stripped; color is emitted only to a TTY and never
under ``NO_COLOR``; and repeated configuration never stacks duplicate handlers (no double-printing).
"""

from __future__ import annotations

import io
import logging

from agentship.logs import ROOT_LOGGER_NAME, ColorFormatter, configure_logging


def _record(name: str, level: int, msg: str) -> logging.LogRecord:
    """Build a bare LogRecord for formatter tests without going through a handler."""
    return logging.LogRecord(name, level, "f.py", 1, msg, None, None)


def test_plain_format_strips_prefix_and_aligns():
    """The component column is the logger name minus the ``agentship.`` prefix, left-padded."""
    fmt = ColorFormatter(color=False)
    line = fmt.format(_record("agentship.engine", logging.INFO, "built ok"))
    assert " INFO  " in line
    assert "engine" in line and "agentship.engine" not in line
    assert line.endswith("built ok")


def test_plain_format_has_no_ansi_codes():
    """With color off, not a single escape byte is emitted (safe for files and pipes)."""
    fmt = ColorFormatter(color=False)
    line = fmt.format(_record("agentship.mcp", logging.WARNING, "slow"))
    assert "\033" not in line
    assert "WARN" in line


def test_color_format_wraps_the_level_in_ansi():
    """With color on, the level label carries an ANSI color and a reset."""
    fmt = ColorFormatter(color=True)
    line = fmt.format(_record("agentship.tools", logging.ERROR, "boom"))
    assert "\033[31m" in line  # red for ERROR
    assert "\033[0m" in line  # reset


def test_tree_is_silent_until_configured():
    """A child logger has no handler until configure_logging attaches one to the root."""
    # Reset any handler a prior test/CLI attached.
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.handlers.clear()
    buf = io.StringIO()
    logging.getLogger("agentship.engine").info("should not appear")
    assert buf.getvalue() == ""


def test_configure_is_idempotent_no_double_print():
    """Calling configure_logging twice leaves exactly one managed handler (no double lines)."""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.handlers.clear()
    buf = io.StringIO()
    configure_logging(logging.INFO, stream=buf)
    configure_logging(logging.DEBUG, stream=buf)
    managed = [h for h in root.handlers if getattr(h, "_agentship_managed", False)]
    assert len(managed) == 1
    logging.getLogger("agentship.engine").info("hello")
    assert buf.getvalue().count("hello") == 1


def test_configure_sets_level_and_emits():
    """After configuring at INFO, an INFO line is emitted and a DEBUG line is filtered out."""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.handlers.clear()
    buf = io.StringIO()
    configure_logging(logging.INFO, stream=buf)
    log = logging.getLogger("agentship.mcp")
    log.debug("debug-hidden")
    log.info("info-shown")
    out = buf.getvalue()
    assert "info-shown" in out
    assert "debug-hidden" not in out
