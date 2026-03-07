.. AgentShip documentation master file

.. raw:: html

   <div style="text-align: center; margin: 2rem 0;">
       <img src="_static/docs-header@3x.png" alt="AgentShip Documentation" style="width: 100%; max-width: 960px; border-radius: 8px;" />
   </div>

AgentShip
=========

**The runtime-agnostic production layer for AI agents.**

AgentShip is **LiteLLM for agent runtimes** — one interface to run your agents on Google ADK, LangGraph, or any future engine, plus the full production stack (REST API, PostgreSQL sessions, streaming, observability, MCP tools) that no engine ships with.

Write your agent once. Swap runtimes with one YAML line. Ship in an hour, not two weeks.

.. code-block:: bash

   git clone https://github.com/Agent-Ship/agent-ship.git
   cd agent-ship
   make docker-setup
   # ✅  API at http://localhost:7001   Studio at http://localhost:7001/studio

----

The Two Problems It Solves
==========================

**Production plumbing** — Every team rebuilds the same ~2,000 lines of infrastructure per agent: REST API, session storage, observability, streaming, Docker. That's two weeks of work that has nothing to do with what the agent does.

**Framework lock-in** — Teams pick ADK or LangGraph on day one and build deep. Migrating later costs 3–6 months and 50–80% of the codebase rewritten.

AgentShip eliminates both. Four pluggable layers — Engine, Memory, Observability, Tools — all swappable via YAML. Your Python class never changes.

----

What You Get
============

- **Auto-discovery** — agents register from two files (YAML + Python), no manual wiring
- **Dual engines** — Google ADK or LangGraph + LiteLLM, set per agent in YAML
- **Engine swap** — change ``execution_engine:`` in YAML; Python class unchanged
- **MCP integration** — STDIO and HTTP/OAuth transports, tools auto-documented for LLM
- **Token-by-token streaming** — real SSE with LangGraph + LiteLLM
- **Session memory** — PostgreSQL-backed conversations, zero config
- **Observability** — Opik tracing out of the box; LangFuse coming
- **AgentShip Studio** — browser chat UI at ``/studio`` with observability panel and stop button
- **One-command Docker setup** — ``make docker-setup`` starts everything

----

Quick Start
===========

.. code-block:: bash

   git clone https://github.com/Agent-Ship/agent-ship.git
   cd agent-ship
   make docker-setup          # creates .env, starts API + PostgreSQL

   # Open Studio and start chatting
   open http://localhost:7001/studio

Your first agent is included — talk to it immediately. To build your own, see :doc:`user-guides/getting-started/quickstart`.

----

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   why-agentship
   user-guides/architecture
   user-guides/getting-started/quickstart
   user-guides/getting-started/installation
   user-guides/getting-started/configuration
   user-guides/docker-setup

.. toctree::
   :maxdepth: 1
   :caption: Building Agents

   user-guides/building-agents/overview
   user-guides/building-agents/agent-configuration
   user-guides/building-agents/base-agent
   user-guides/building-agents/tools
   user-guides/building-agents/patterns/single-agent
   user-guides/building-agents/patterns/orchestrator
   user-guides/building-agents/patterns/tool-pattern

.. toctree::
   :maxdepth: 1
   :caption: MCP Integration

   user-guides/mcp-integration

.. toctree::
   :maxdepth: 1
   :caption: Deployment

   user-guides/deployment/overview
   user-guides/deployment/heroku

.. toctree::
   :maxdepth: 1
   :caption: Testing

   user-guides/testing/overview
   user-guides/testing/writing-tests

.. toctree::
   :maxdepth: 1
   :caption: Reference

   api/index
   contributing
