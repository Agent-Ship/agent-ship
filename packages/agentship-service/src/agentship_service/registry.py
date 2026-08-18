"""The set of built agents the service exposes, resolved by name.

The service does not build agents — it serves already-built :class:`RunnableAgent`
instances. :class:`AgentRegistry` is the small name→agent map the routes look up:
``:invoke``/``:stream`` resolve the agent for a path ``name``, and discovery lists every
registered agent as an :class:`~agentship_service.models.v1.AgentCard`. A missing name is a
``KeyError`` here; the route turns that into a 404 (see :mod:`agentship_service.routers`).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from agentship.runtime import RunnableAgent


class AgentRegistry:
    """A name-keyed collection of built agents the service can serve."""

    def __init__(self, agents: Iterable[RunnableAgent] = ()) -> None:
        """Build a registry from an optional initial set of agents (keyed by spec name)."""
        self._agents: dict[str, RunnableAgent] = {}
        for agent in agents:
            self.add(agent)

    def add(self, agent: RunnableAgent) -> None:
        """Register ``agent`` under its spec name, replacing any agent of the same name."""
        self._agents[agent.spec.name] = agent

    def get(self, name: str) -> RunnableAgent:
        """Return the agent named ``name``; raise :class:`KeyError` if none is registered."""
        return self._agents[name]

    def __contains__(self, name: object) -> bool:
        """True if an agent named ``name`` is registered."""
        return name in self._agents

    def __iter__(self) -> Iterator[RunnableAgent]:
        """Iterate the registered agents (insertion order)."""
        return iter(self._agents.values())

    def __len__(self) -> int:
        """The number of registered agents."""
        return len(self._agents)
