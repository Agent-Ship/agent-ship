"""Crash recovery for real: SIGKILL a process mid-turn, resume in a NEW process, count the writes.

Every earlier "kill" test resumed inside the process that ran the turn, through a shared in-memory
checkpointer and ledger — which cannot tell a crash-safe system from one that loses everything on
restart. Here the turn runs in a child process (``crash_agent.py``) against Postgres, the child is
killed with ``SIGKILL`` at a chosen instant, and a second child resumes it by session alone (the
first never returned a token). The assertion is the one that matters to whoever is being refunded:
how many times the refund actually fired.

The last test is the control: the same crash with the ledger rows removed — what the old in-memory
ledger amounted to after a restart — fires the refund twice. That is what shows the other tests
would catch the bug rather than pass by accident.

Needs ``AGENTSHIP_TEST_POSTGRES_URI``; skips without it.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from agentship.primitives.ledger import LEDGER_TABLE

_HERE = Path(__file__).parent
_AGENT = _HERE / "crash_agent.py"
_TIMEOUT_S = 60
_LEDGER_GRACE_S = 10


def _env(postgres_uri: str, workdir: Path, order: str, phase: str, mode: str) -> dict[str, str]:
    """The child's environment: the store, the scenario, and this interpreter's import path."""
    env = dict(os.environ)
    env.update(
        AGENT_SESSION_STORE_URI=postgres_uri,
        CRASH_PHASE=phase,
        CRASH_MODE=mode,
        CRASH_DIR=str(workdir),
        CRASH_ORDER=order,
        CRASH_TENANT=f"tenant-{order}",
        CRASH_SESSION=f"session-{order}",
        # The parent's own sys.path, so the child imports exactly the code under test.
        PYTHONPATH=os.pathsep.join([str(_HERE), *sys.path]),
    )
    return env


def _run_until_ready_then_kill(env: dict[str, str], workdir: Path, ledger_ready) -> None:
    """Start the first phase, wait for its kill point, then SIGKILL it — no cleanup runs."""
    proc = subprocess.Popen(  # noqa: S603 — our own interpreter and script
        [sys.executable, str(_AGENT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + _TIMEOUT_S
    reached: float | None = None
    try:
        while not ((workdir / "ready").exists() and ledger_ready()):
            if proc.poll() is not None:
                pytest.fail(f"the turn exited before its kill point:\n{proc.stderr.read()}")
            if time.monotonic() > deadline:
                pytest.fail("the turn never reached its kill point")
            if (workdir / "ready").exists():
                reached = reached or time.monotonic()
                # At the kill point with the ledger still not as expected: the write-ahead is not
                # reaching Postgres. Say so now rather than after the full timeout.
                if time.monotonic() - reached > _LEDGER_GRACE_S:
                    pytest.fail("at the kill point, but the ledger row never landed in Postgres")
            time.sleep(0.05)
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=_TIMEOUT_S)
    assert proc.returncode == -signal.SIGKILL, "the first process was not killed"


def _resume(env: dict[str, str]) -> str:
    """Resume in a brand-new process, by session only, and return the turn's output."""
    env = {**env, "CRASH_PHASE": "resume"}
    done = subprocess.run(  # noqa: S603
        [sys.executable, str(_AGENT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
    )
    assert done.returncode == 0, f"resume failed:\n{done.stderr}"
    return json.loads(done.stdout.strip().splitlines()[-1])["output"]


def _ledger_rows(postgres_uri: str, since) -> list[tuple[str, str]]:
    """The ledger rows this test created: (status, result-as-text)."""
    import psycopg

    with psycopg.connect(postgres_uri) as conn:
        return conn.execute(
            f"SELECT status, coalesce(result::text, '') FROM {LEDGER_TABLE} "
            "WHERE created_at >= %s ORDER BY created_at",
            (since,),
        ).fetchall()


def _db_now(postgres_uri: str):
    import psycopg

    with psycopg.connect(postgres_uri) as conn:
        return conn.execute("SELECT clock_timestamp()").fetchone()[0]


def _firings(workdir: Path) -> list[str]:
    effects = workdir / "effects"
    return effects.read_text().splitlines() if effects.exists() else []


@pytest.fixture
def scenario(postgres_uri, tmp_path):
    """A unique order, a scratch dir, and the database time the test started."""
    return postgres_uri, tmp_path, f"o{uuid.uuid4().hex[:10]}", _db_now(postgres_uri)


def test_killed_mid_refund_is_not_refunded_again_by_a_new_process(scenario):
    """SIGKILL inside the refund (ledger ``pending``) → the resuming process does NOT re-fire.

    The refund may or may not have reached the bank when the process died; the ledger only knows
    it was about to. Re-firing blind is the double charge, so the resumed turn reports it as
    unconfirmed instead, and the row is settled so a later retry does not ask again.
    """
    uri, workdir, order, since = scenario
    env = _env(uri, workdir, order, "first", "mid_effect")

    _run_until_ready_then_kill(
        env, workdir, lambda: [s for s, _ in _ledger_rows(uri, since)] == ["pending"]
    )
    assert _firings(workdir) == [order]

    output = _resume(env)

    assert _firings(workdir) == [order], "the refund fired again after the crash"
    assert "not re-run" in output
    assert [s for s, _ in _ledger_rows(uri, since)] == ["done"]


def test_killed_after_the_refund_replays_its_receipt_in_a_new_process(scenario):
    """SIGKILL after the refund returned (ledger ``done``) but before its task was saved.

    LangGraph has nothing for the task, so the resume re-runs it — refund included. The ledger
    answers from the recorded receipt: one firing, and the model is told the real result of the
    refund that did happen. The control test below shows this is the ledger's doing.
    """
    uri, workdir, order, since = scenario
    env = _env(uri, workdir, order, "first", "after_effect")

    _run_until_ready_then_kill(
        env, workdir, lambda: [s for s, _ in _ledger_rows(uri, since)] == ["done"]
    )
    assert _firings(workdir) == [order]

    output = _resume(env)

    assert _firings(workdir) == [order], "the refund fired again after the crash"
    assert f"refund #1 issued for {order}" in output, output


def test_killed_beside_a_finished_refund_only_the_unfinished_call_reruns(scenario):
    """SIGKILL in a parallel read-only call while the refund beside it has already finished.

    LangGraph runs each tool call as its own task and saves a finished task's result at once, so
    the resume re-runs only the unfinished call. Pinned because it is a guarantee the ledger does
    NOT provide — if an upgrade changed it, the ledger would be the only line left.
    """
    uri, workdir, order, since = scenario
    env = _env(uri, workdir, order, "first", "parallel")

    _run_until_ready_then_kill(
        env, workdir, lambda: [s for s, _ in _ledger_rows(uri, since)] == ["done"]
    )

    output = _resume(env)

    assert _firings(workdir) == [order]
    assert f"refund #1 issued for {order}" in output and "stall finished" in output, output


def test_control_without_the_durable_ledger_the_same_crash_refunds_twice(scenario):
    """The previous test with the ledger rows deleted before resuming → TWO refunds.

    Deleting them reproduces what the in-memory ledger was after a restart: nothing. That this
    double-charges proves the test above passes because of the durable ledger, not because the
    step never re-ran.
    """
    import psycopg

    uri, workdir, order, since = scenario
    env = _env(uri, workdir, order, "first", "after_effect")
    _run_until_ready_then_kill(
        env, workdir, lambda: [s for s, _ in _ledger_rows(uri, since)] == ["done"]
    )
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(f"DELETE FROM {LEDGER_TABLE} WHERE created_at >= %s", (since,))

    _resume(env)

    assert _firings(workdir) == [order, order]
