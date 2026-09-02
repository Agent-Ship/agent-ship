"""Every agent is traced by default; the ENVIRONMENT decides where the trace goes.

Two separate decisions were fused into one YAML block:

    is this agent traced?      -> should be yes, always. You cannot debug what you cannot see.
    where do the spans ship?   -> deployment config, not something each agent hardcodes.

Before this, an agent with no ``observability`` block emitted nothing, so most agents were
invisible — and the fix people reached for was pasting ``exporters: [opik]`` into every YAML,
which hardcodes a backend into the agent and makes an offline test suite dial out.
"""

from __future__ import annotations

from agentship.observability.registry import default_exporters, tracing_is_on
from agentship.spec import AgentSpec, ObservabilitySpec


def test_an_agent_with_no_block_is_still_traced():
    """No ``observability:`` in the YAML means traced, not silent."""
    assert tracing_is_on(AgentSpec(name="a", engine="echo").observability) is True


def test_provider_none_is_the_explicit_off_switch():
    """Turning tracing off stays possible, and stays explicit."""
    assert tracing_is_on(ObservabilitySpec(provider="none")) is False


def test_no_exporter_is_configured_by_default(monkeypatch):
    """With nothing set, the tree is built but ships nowhere — so offline runs never dial out.

    This is what keeps a keyless test suite from reaching for a backend: the spans exist and
    can be asserted on in-process, and no exporter is attached.
    """
    monkeypatch.delenv("AGENTSHIP_OTEL_EXPORTERS", raising=False)
    assert default_exporters() == []


def test_the_environment_names_the_backend(monkeypatch):
    """One env var points every agent at a backend — no YAML edit, no per-agent hardcoding."""
    monkeypatch.setenv("AGENTSHIP_OTEL_EXPORTERS", "opik")
    assert default_exporters() == ["opik"]


def test_several_backends_can_be_named_at_once(monkeypatch):
    """Comma-separated, whitespace tolerated, so `opik, langsmith` fans out to both."""
    monkeypatch.setenv("AGENTSHIP_OTEL_EXPORTERS", "opik, langsmith")
    assert default_exporters() == ["opik", "langsmith"]


def test_an_explicit_yaml_exporter_still_wins(monkeypatch):
    """An agent that names its own exporters keeps them — the env is only the default."""
    monkeypatch.setenv("AGENTSHIP_OTEL_EXPORTERS", "opik")
    spec = ObservabilitySpec(exporters=["console"])
    assert spec.exporters == ["console"]


def test_saas_egress_is_still_blocked_by_default(monkeypatch):
    """Naming a SaaS backend is not enough — egress stays off until it is allowed.

    The gate exists so an agent YAML cannot quietly ship spans off-box. Making tracing
    default-on must not weaken it.
    """
    monkeypatch.delenv("AGENTSHIP_OTEL_ALLOW_SAAS", raising=False)
    assert ObservabilitySpec().allow_saas_exporter is False


def test_an_operator_can_allow_saas_egress_for_every_agent(monkeypatch):
    """``AGENTSHIP_OTEL_ALLOW_SAAS`` is the deployment-wide counterpart of the YAML flag.

    Without it, pointing every agent at a SaaS backend through the environment would fail at
    build time for each agent whose YAML omits the flag — the operator would have to edit every
    file to consent to a decision they already made in one place.
    """
    monkeypatch.setenv("AGENTSHIP_OTEL_ALLOW_SAAS", "1")
    assert ObservabilitySpec().allow_saas_exporter is True


def test_an_agent_can_still_refuse_saas_egress(monkeypatch):
    """An explicit ``false`` in the YAML wins over the environment — opt-out stays possible."""
    monkeypatch.setenv("AGENTSHIP_OTEL_ALLOW_SAAS", "1")
    assert ObservabilitySpec(allow_saas_exporter=False).allow_saas_exporter is False
