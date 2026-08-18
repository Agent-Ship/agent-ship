"""A2A wire models and the ``AgentRef`` handle (Phase 05 · C3, C4).

Two families live here, both transport-agnostic (no ``httpx``/FastAPI), so they belong in core:

* **Authoring models** — :class:`AgentRef` and :class:`RemoteSpec`: how an author declares a
  specialist that may be in-process *or* networked. Exactly one target is required, validated at
  construction so a bad YAML block fails fast at load (§C3).
* **A2A protocol models** — :class:`AgentCard`, :class:`Message`/:class:`TextPart`, and the
  :class:`JsonRpcRequest`/:class:`JsonRpcResponse` envelope. These mirror the A2A spec's JSON
  shapes (camelCase on the wire via field aliases) so our server and client speak the protocol
  without pulling in the ``a2a-sdk`` dependency; the models are deliberately a faithful subset of
  what we implement (``message/send``, ``message/stream``, the Agent Card), and sit behind the
  same seam so ``a2a-sdk`` types can replace them later without touching callers.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import CapabilityError

#: The A2A protocol revision our wire models target. Pinned so a spec bump is a deliberate,
#: reviewable change rather than a silent drift (R1 in the phase risks).
A2A_PROTOCOL_VERSION = "0.3.0"


# --------------------------------------------------------------------------------------------
# Authoring models — how a specialist reference is declared (in-process or networked).
# --------------------------------------------------------------------------------------------


class A2AAuthConfig(BaseModel):
    """How to authenticate an *outbound* A2A call to a remote agent (§C6).

    ``type`` selects the credential injector the client builds: ``bearer`` reads a static token
    from ``token_env``; ``oauth2`` runs client-credentials against ``token_url``; ``mtls`` uses a
    client cert/key pair. Only the fields the chosen ``type`` needs are set; the rest stay ``None``.
    """

    type: Literal["none", "bearer", "oauth2", "mtls"] = "none"
    #: Environment variable holding the static bearer token (``bearer``).
    token_env: str | None = None
    #: OAuth2 token endpoint + client credentials env vars (``oauth2``).
    token_url: str | None = None
    client_id_env: str | None = None
    client_secret_env: str | None = None
    #: Client certificate/key file paths (``mtls``).
    cert_path: str | None = None
    key_path: str | None = None


class RemoteSpec(BaseModel):
    """Where a networked specialist lives and how to reach it (§C3).

    ``url`` is the remote agent's A2A base URL; its Agent Card is fetched from ``url + card_path``
    unless a caller passes one in. ``auth`` selects the outbound credential; ``timeout_seconds``
    bounds a synchronous consult.
    """

    url: str
    card_path: str = "/.well-known/agent-card.json"
    auth: A2AAuthConfig = Field(default_factory=A2AAuthConfig)
    timeout_seconds: int = 60


class AgentRef(BaseModel):
    """A reference to a specialist, resolved either in-process or over A2A (§C3).

    Exactly one of ``local_ref`` (a locally-registered agent name) or ``remote`` (a
    :class:`RemoteSpec`) must be set — presence of ``remote`` means networked, presence of
    ``local_ref`` means in-process. Both or neither is a load-time :class:`CapabilityError` so a
    malformed ``specialists:`` block never reaches build. One reference type, two resolutions: the
    supervisor's authoring code never branches on transport.
    """

    name: str
    local_ref: str | None = None
    remote: RemoteSpec | None = None

    def model_post_init(self, _context: Any) -> None:
        """Enforce the exactly-one-target rule at construction (fail-fast at load)."""
        has_local = self.local_ref is not None
        has_remote = self.remote is not None
        if has_local == has_remote:
            raise CapabilityError(
                f"specialist {self.name!r} must set exactly one of local_ref (in-process) or "
                f"a2a/remote (networked) — got "
                f"{'both' if has_local else 'neither'}; fix the specialists block"
            )

    def is_remote(self) -> bool:
        """Whether this specialist is reached over the network (A2A) rather than in-process."""
        return self.remote is not None


# --------------------------------------------------------------------------------------------
# A2A protocol models — the Agent Card and the JSON-RPC message envelope.
# --------------------------------------------------------------------------------------------


class _A2AModel(BaseModel):
    """Base for wire models: serialise with A2A's camelCase aliases, accept either casing in."""

    model_config = ConfigDict(populate_by_name=True)


class AgentCapabilities(_A2AModel):
    """The ``capabilities`` block of an Agent Card — honest flags, generated from the engine."""

    streaming: bool = False
    push_notifications: bool = Field(default=False, alias="pushNotifications")
    state_transition_history: bool = Field(default=False, alias="stateTransitionHistory")


class AgentSkill(_A2AModel):
    """One advertised skill on an Agent Card (A2A ``skills[]`` entry)."""

    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)


class SecurityScheme(_A2AModel):
    """A single accepted auth scheme on the card (A2A ``securitySchemes`` value).

    We advertise exactly the schemes the service enforces (they are generated from one config so
    the card and the inbound check can never drift — §C6).
    """

    type: str
    description: str | None = None


class AgentCard(_A2AModel):
    """The A2A Agent Card served at ``/.well-known/agent-card.json`` (§7, §C4).

    Generated from an :class:`~agentship.spec.AgentSpec` plus the engine's real
    :class:`~agentship.engines.base.EngineCapabilities` — never hand-written — so what the card
    claims (streaming, skills, security) is exactly what the agent does. Serialise with
    ``by_alias=True`` to emit the A2A camelCase field names.
    """

    name: str
    description: str | None = None
    url: str
    version: str = "1.0.0"
    protocol_version: str = Field(default=A2A_PROTOCOL_VERSION, alias="protocolVersion")
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    default_input_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"], alias="defaultInputModes"
    )
    default_output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"], alias="defaultOutputModes"
    )
    skills: list[AgentSkill] = Field(default_factory=list)
    security_schemes: dict[str, SecurityScheme] = Field(
        default_factory=dict, alias="securitySchemes"
    )
    security: list[dict[str, list[str]]] = Field(default_factory=list)


class TextPart(_A2AModel):
    """A text ``Part`` of an A2A ``Message`` (the only part kind we speak today)."""

    kind: Literal["text"] = "text"
    text: str


class Message(_A2AModel):
    """An A2A ``Message``: a role plus an ordered list of parts (§C3 mapping)."""

    role: Literal["user", "agent"] = "user"
    parts: list[TextPart] = Field(default_factory=list)
    message_id: str | None = Field(default=None, alias="messageId")

    @classmethod
    def user(cls, text: str) -> Message:
        """Build a single-text-part ``user`` message (the common inbound/outbound shape)."""
        return cls(role="user", parts=[TextPart(text=text)])

    @classmethod
    def agent(cls, text: str) -> Message:
        """Build a single-text-part ``agent`` message (a specialist's reply)."""
        return cls(role="agent", parts=[TextPart(text=text)])

    def text(self) -> str:
        """Concatenate every text part into one string (what an engine's ``run`` consumes)."""
        return "".join(part.text for part in self.parts)


class JsonRpcError(_A2AModel):
    """The ``error`` member of a JSON-RPC failure response."""

    code: int
    message: str
    data: Any | None = None


class JsonRpcRequest(_A2AModel):
    """A JSON-RPC 2.0 request envelope (``message/send``, ``message/stream``, ``tasks/*``)."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcResponse(_A2AModel):
    """A JSON-RPC 2.0 response envelope — exactly one of ``result`` or ``error`` is set."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    result: Any | None = None
    error: JsonRpcError | None = None

    @classmethod
    def ok(cls, id: str | int | None, result: Any) -> JsonRpcResponse:
        """A success response carrying ``result``."""
        return cls(id=id, result=result)

    @classmethod
    def fail(cls, id: str | int | None, code: int, message: str) -> JsonRpcResponse:
        """A failure response carrying a JSON-RPC ``error``."""
        return cls(id=id, error=JsonRpcError(code=code, message=message))
