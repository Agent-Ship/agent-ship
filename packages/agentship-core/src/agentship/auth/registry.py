"""Discover auth providers by name and build them from config.

Auth providers register under the ``agentship.auth_providers`` entry-point group, so a
deployment can select one by name in service config and a vendor can ship a custom
provider without a core edit — the same extension mechanism engines use. Each entry point
is a *factory*: a callable taking a config mapping and returning a configured
:class:`~agentship.auth.AuthProvider`.

:func:`build_auth_provider` resolves a name through the registry and fails fast, naming
the installed providers, when the name is unknown — the check ``agentship serve`` /
``doctor`` run so a typo or a missing extra is caught before a socket is bound.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..errors import AgentShipError
from ..registry import Registry
from . import (
    ApiKeyAuthProvider,
    AuthProvider,
    CompositeAuthProvider,
    EnvApiKeyStore,
    ForwardedHeaderAuthProvider,
)

#: A factory that builds a configured provider from a config mapping.
AuthProviderFactory = Callable[[Mapping[str, Any]], AuthProvider]

#: The shared registry of auth-provider factories, discovered via entry points.
AUTH_PROVIDERS: Registry[AuthProviderFactory] = Registry(
    "agentship.auth_providers", label="auth provider"
)


def build_auth_provider(name: str, config: Mapping[str, Any] | None = None) -> AuthProvider:
    """Build the auth provider registered under ``name`` from ``config``.

    Raises :class:`~agentship.errors.AgentShipError` — naming the installed providers —
    when ``name`` is not registered, so a misconfigured or uninstalled provider fails
    fast with an actionable message rather than a lookup miss deep in request handling.
    """
    factory = AUTH_PROVIDERS.get(name)
    if factory is None:
        raise AgentShipError(
            f"auth provider {name!r} is not installed — available: {AUTH_PROVIDERS.names()} "
            f"(check the name, or install the package that provides it)"
        )
    return factory(config or {})


def forwarded_from_config(config: Mapping[str, Any]) -> ForwardedHeaderAuthProvider:
    """Build a :class:`ForwardedHeaderAuthProvider` (production path) from config.

    Requires ``trust_forwarded_from``; the header-name overrides are optional. A missing
    allow-list surfaces as the constructor's fail-fast error.
    """
    return ForwardedHeaderAuthProvider(
        trust_forwarded_from=config.get("trust_forwarded_from", ()),
        **_only(config, "forwarded_by_header", "tenant_header", "user_header", "scopes_header"),
    )


def api_key_from_config(config: Mapping[str, Any]) -> ApiKeyAuthProvider:
    """Build an :class:`ApiKeyAuthProvider` over an :class:`EnvApiKeyStore`.

    ``env_var`` names the environment variable holding the JSON key table; ``raw`` (used
    mainly by tests) supplies that JSON directly. ``api_key_header`` overrides the header
    the key is read from.
    """
    store = EnvApiKeyStore(config.get("env_var", "AGENTSHIP_API_KEYS"), raw=config.get("raw"))
    return ApiKeyAuthProvider(store, **_only(config, "api_key_header"))


def jwt_from_config(config: Mapping[str, Any]) -> AuthProvider:
    """Build the optional :class:`~agentship.auth.jwt.JwtAuthProvider` from config.

    Imported lazily so this factory (and the registry that loads it) works in a base
    install; constructing it without the ``[jwt]`` extra raises an actionable error.
    """
    from .jwt import JwtAuthProvider

    return JwtAuthProvider(
        issuer=config["issuer"],
        audience=config["audience"],
        **_only(
            config,
            "public_key",
            "jwks_url",
            "jwks_ttl_seconds",
            "algorithms",
            "user_claim",
            "tenant_claim",
            "scopes_claim",
        ),
    )


def composite_from_config(config: Mapping[str, Any]) -> CompositeAuthProvider:
    """Build a :class:`CompositeAuthProvider` from an ordered list of child provider configs.

    ``config["providers"]`` is a list of ``{"name": ..., "config": {...}}`` entries, each
    built by name through :func:`build_auth_provider`, so a composite can nest any
    registered provider (including a vendor's) in precedence order.
    """
    children = [
        build_auth_provider(entry["name"], entry.get("config", {}))
        for entry in config.get("providers", [])
    ]
    return CompositeAuthProvider(children)


def _only(config: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    """Return the subset of ``config`` whose keys are in ``keys`` (present ones only).

    Lets a factory forward just the optional keyword arguments a provider accepts,
    without passing ``None`` for every option the config did not set.
    """
    return {key: config[key] for key in keys if key in config}


# Register the built-in factories in-code too, so they resolve in a source checkout /
# test run where the package's entry points may not be installed. Entry-point discovery
# (a real install, or a vendor's package) still adds any others under the same group.
AUTH_PROVIDERS.register("forwarded", forwarded_from_config)
AUTH_PROVIDERS.register("api_key", api_key_from_config)
AUTH_PROVIDERS.register("jwt", jwt_from_config)
AUTH_PROVIDERS.register("composite", composite_from_config)

__all__ = ["AUTH_PROVIDERS", "AuthProviderFactory", "build_auth_provider"]
