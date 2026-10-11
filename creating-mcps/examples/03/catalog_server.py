"""
Unit 3: the curated catalog as an MCP server.

The same catalog, offered three ways, so you can measure which a host and a model use well:
  - as TOOLS the model calls when it needs them (list_datasets, describe_dataset);
  - as RESOURCES a host can load up front or show the user (catalog://datasets/...);
  - as a one-paragraph summary in the server's INSTRUCTIONS.

What a caller sees depends on who they are (`MCP_PERSONA`, default l1_analyst; Unit 7 makes
it the real caller), so every list carries cacheScope "private": a shared cache must never hand
one persona's catalog to another.

    python examples/03/catalog.py          # the catalog itself, per persona
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, TypedDict

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]   # catalog.py, then creating-mcps/examples/
import catalog  # noqa: E402
from common_mcp import joinable_log  # noqa: E402

PERSONA = os.environ.get("MCP_PERSONA", "l1_analyst")
PER_CALLER = CacheHint(ttl_ms=300_000, scope="private")

mcp = MCPServer(
    "catalog",
    version=catalog.VERSION,
    instructions=catalog.instructions_line(PERSONA),
    cache_hints={m: PER_CALLER for m in ("tools/list", "resources/list",
                                         "resources/templates/list")},
)
mcp.middleware.append(joinable_log("catalog"))


class DatasetSummary(TypedDict):
    name: str
    backend: str
    description: str


@mcp.tool()
def list_datasets() -> list[DatasetSummary]:
    """The tables and log indexes you may query, one line each. Start here."""
    return catalog.summary(PERSONA)


@mcp.tool()
def describe_dataset(name: str) -> dict[str, Any]:
    """Everything you need to query one dataset (e.g. cmdb.assets, logs-auth-*): what a row
    is, the time field, and per field its type, how to use it, and example values."""
    found = catalog.describe(name, PERSONA)
    if found is None:   # same answer for "doesn't exist" and "not yours": don't leak which
        raise ToolError(f"no dataset named {name!r} that you can use; list_datasets "
                        "shows yours.")
    return found


@mcp.resource("catalog://datasets", mime_type="application/json")
def all_datasets() -> str:
    """The whole catalog for your role: every dataset you may use, every field."""
    return json.dumps([catalog.describe(n, PERSONA) for n in catalog.datasets_for(PERSONA)])


@mcp.resource("catalog://datasets/{name}", mime_type="application/json")
def one_dataset(name: str) -> str:
    """One dataset of the catalog."""
    found = catalog.describe(name, PERSONA)
    if found is None:
        raise ValueError(f"no dataset named {name!r} that you can use")
    return json.dumps(found)


if __name__ == "__main__":
    mcp.run()
