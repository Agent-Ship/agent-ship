"""The engine×capability conformance matrix (DESIGN §11, "declare, don't fake").

This top-level suite is the backstop that lets AgentShip make correctness claims
across engines: a capability cell runs iff the engine declares that capability, and
a declared-but-red cell is a build failure. See :mod:`conformance.test_matrix` for
the grid, :mod:`conformance.capabilities` for the catalogue (the extension point),
and ``conformance/README.md`` for how to add an engine or a capability.
"""
