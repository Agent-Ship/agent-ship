"""Echo is the vendor-free engine that proves the base classes hold on a swap.

The Phase-00 neutrality goal is: *a second, non-LangGraph engine implements every
base class unchanged*. :class:`~agentship.engines.echo.EchoEngine` is that engine —
it subclasses ``Engine`` and implements ``build``/``run``/``stream`` with **zero**
vendor imports, so it demonstrates that swapping the engine does not move a single
base-class signature (neutrality is *tested*, not hoped).

This test locks that property in: it imports the echo engine module in a *fresh*
subprocess (immune to whatever the rest of the session already imported) and asserts
no LangChain/LangGraph/LiteLLM module landed in ``sys.modules``. It goes red the
moment ``echo.py`` (or anything it imports) drags in a vendor library — at which
point echo would no longer be the vendor-free neutrality proof.
"""

from __future__ import annotations

import subprocess
import sys

# The vendor libraries a truly engine-neutral base-class implementation must not
# need. If echo imports any of these it stops being the vendor-free proof.
FORBIDDEN = ("langgraph", "langchain", "litellm")

_PROBE = f"""
import sys

# Import the echo engine and exercise its public surface — build/run/stream all
# resolve through the base classes only, with no vendor type in sight.
from agentship.engines.echo import EchoEngine  # noqa: F401
from agentship.engines.base import Engine

assert issubclass(EchoEngine, Engine), "echo must implement the Engine base class"

forbidden = {FORBIDDEN!r}
leaked = sorted(
    name
    for name in sys.modules
    for prefix in forbidden
    if name == prefix or name.startswith(prefix + ".")
)
print(",".join(leaked))
"""


def test_echo_engine_imports_no_vendor_library():
    """Importing the echo engine loads no langgraph/langchain/litellm — it is vendor-free."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"probe subprocess failed:\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    leaked = [name for name in proc.stdout.strip().split(",") if name]
    assert not leaked, (
        "the echo engine leaked vendor modules into sys.modules — it must stay "
        f"vendor-free to remain the engine-neutrality proof: {leaked}"
    )
