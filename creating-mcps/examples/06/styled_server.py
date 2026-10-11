"""
Unit 6: one server, the same tool code, described three ways.

    MCP_SERVER_KIND = cmdb | siem                  which half of Unit 5's restrictive variant
    TOOL_STYLE      = terse | original | rewritten | lean (styles.py)
    ERROR_STYLE     = instructive | generic        generic replaces every refusal's message
                                                   with "invalid request", to measure what the
                                                   instructive messages are worth
    MCP_PERSONA     = l1_analyst | ...             as in every unit

The functions are Units 3, 4 and 5's, imported and registered again (`add_tool`), so any
difference in a measurement comes from what the model reads, never from what the tool does.
In the rewritten and lean styles, each parameter gets a description and, where the code
already enforces one, its bound or fixed set of choices in the schema; the SIEM's `dataset`
lists only the datasets this persona may read. The schema advertises those rules; the tool's
code still enforces them, so a violation gets the tool's own message.
"""

from __future__ import annotations

import functools
import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import styles  # noqa: E402
from common_mcp import joinable_log, load_env  # noqa: E402

load_env()
KIND = os.environ.get("MCP_SERVER_KIND", "cmdb")
STYLE = os.environ.get("TOOL_STYLE", "original")
ERRORS = os.environ.get("ERROR_STYLE", "instructive")
if KIND not in ("cmdb", "siem") or STYLE not in styles.STYLES or ERRORS not in (
        "instructive", "generic"):
    raise SystemExit(f"bad configuration: {KIND=} {STYLE=} {ERRORS=}")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if KIND == "cmdb":
    source = _load("unit5_cmdb_tasks", HERE.parent / "05" / "cmdb_tasks_server.py")
    functions = {"get_asset": source.unit3.get_asset, "find_assets": source.unit3.find_assets,
                 **{n: getattr(source, n) for n in ("service_dependencies",
                                                    "hosts_with_vulnerability",
                                                    "count_assets")}}
else:
    source = _load("unit4_siem", HERE.parent / "04" / "siem_server.py")
    functions = {n: getattr(source, n) for n in ("list_incidents", "get_incident",
                                                 "event_histogram", "top_values",
                                                 "search_events")}

mcp = MCPServer(KIND, version=f"0.6.0-{STYLE}", instructions=source.mcp.instructions)
mcp.middleware.append(joinable_log(f"{KIND}_{STYLE}"))


def restyle(fn, params: dict[str, dict]):
    """The same function, with a description (and bounds or choices) on each parameter.
    The SDK builds the input schema from the signature, so we give it a new signature."""
    sig = inspect.signature(fn, eval_str=True)
    new = []
    for p in sig.parameters.values():
        spec = dict(params.get(p.name, {}))
        if not spec:
            new.append(p)
            continue
        # Bounds and choices are advertised in the schema but enforced by the tool's own
        # code, as before: if the SDK validated them, a violation would come back as a
        # pydantic error dump instead of the tool's instruction.
        extra = {k: v for k, v in (("minimum", spec.pop("ge", None)),
                                   ("maximum", spec.pop("le", None)),
                                   ("enum", list(spec.pop("choices", ())) or None))
                 if v is not None}
        field = Field(**spec, json_schema_extra=extra or None)
        new.append(p.replace(annotation=Annotated[p.annotation, field]))
    new_sig = sig.replace(parameters=new)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    del wrapper.__wrapped__                   # so the SDK reads the new signature, not fn's
    wrapper.__signature__ = new_sig
    wrapper.__annotations__ = {p.name: p.annotation for p in new} | {
        "return": sig.return_annotation}
    return wrapper


def generic_errors(fn):
    """Every refusal becomes the same unhelpful message (for the A/B only)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise ToolError("invalid request") from None
    return wrapper


for name, fn in functions.items():
    spec = styles.TOOLS[name]
    if ERRORS == "generic":
        fn = generic_errors(fn)
    if STYLE == "original":
        mcp.add_tool(fn)
    elif STYLE == "terse":
        mcp.add_tool(fn, description=spec["terse"])
    else:
        params = styles.params_for(STYLE, name,       # datasets: only the persona's
                                   getattr(source, "MINE", None))
        mcp.add_tool(restyle(fn, params), description=spec["rewritten"],
                     annotations=ToolAnnotations(**styles.READ_ONLY))


if __name__ == "__main__":
    mcp.run()
