"""
Shared Neo4j helper for the Agent Memory course (course 2).

Like the foundations course's `examples/common.py`, this factors out one bit of
boilerplate — opening a driver to your Neo4j database — into a single place. It is
OPT-IN: every script that uses it skips cleanly when NEO4J_URI is unset or the `neo4j`
driver isn't installed, mirroring the Section 16 `DATABASE_URL` pattern in the
foundations course. That way the graph lessons read end-to-end even with no database.

The course-2 example scripts live in numbered folders (agent-memory/examples/05/, ...),
so they aren't an importable package. Add this directory to the import path the same way
the foundations scripts do, then import what you need:

    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[1]))  # agent-memory/examples
    from common_graph import get_graph

    driver = get_graph()
    if driver is None:
        return   # skip notice already printed -- the lesson still reads

For the LLM client, model id, and embedding helpers, reuse the foundations `common.py`
(it lives one tree over). The graph units that need it add BOTH directories to the path:

    sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations
    from common import get_client, MODEL, EMBED_MODEL
"""

import os


def get_graph():
    """Open a Neo4j driver from the environment, or return None (with a notice).

    Reads NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD. Returns a connected driver, or None
    when the env isn't set, the `neo4j` driver isn't installed, or the server can't be
    reached — so a lesson script can `if driver is None: return` and the section still
    reads. The caller owns the driver and should close it (e.g. `with driver:`).
    """
    uri = os.environ.get("NEO4J_URI")
    if not uri:
        print("NEO4J_URI not set -- skipping the graph demo (this is optional).")
        print("Start Neo4j (see the lesson) and set NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD.")
        return None
    try:
        from neo4j import GraphDatabase  # lazy import: only needed for the opt-in path
        from neo4j.exceptions import Neo4jError, ServiceUnavailable
    except ImportError:
        print("neo4j driver not installed -- skipping. Install with: pip install neo4j")
        return None

    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "neo4j")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
    except (ServiceUnavailable, Neo4jError) as e:
        print(f"Could not reach Neo4j at {uri}: {e}")
        print("Is the container running? See the lesson's `docker run` command.")
        driver.close()
        return None
    return driver
