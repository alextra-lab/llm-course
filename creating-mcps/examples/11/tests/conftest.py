"""Shared fixtures for Unit 11's tests.

Tests marked with the `estate` fixture need Postgres and OpenSearch (`python env/env.py up`);
without them they're skipped, and the record tests still run."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parents[1]
EXAMPLES = UNIT.parent
sys.path[:0] = [str(UNIT), str(EXAMPLES / "07"), str(EXAMPLES)]
from common_mcp import load_env  # noqa: E402

load_env()


def _estate_up() -> bool:
    import psycopg

    from common_mcp import _pg_params

    try:
        with psycopg.connect(**_pg_params("auditor")):
            return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def estate():
    if not _estate_up():
        pytest.skip("the estate isn't running (python env/env.py up)")


@pytest.fixture(scope="session")
def gateway(estate):
    """Unit 7's gateway module, loaded once. Tests build servers from it in memory."""
    name = "unit7_gateway"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, EXAMPLES / "07" / "gateway_server.py")
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return sys.modules[name]


def as_caller(gateway, sub: str, groups: list[str]) -> None:
    """Every in-memory request is from this caller, as if their token had been verified."""
    gateway._STDIO_CALLER = gateway.POLICY.caller(sub, groups, "unit11-tests", f"t-{sub}")


def run(coro):
    return asyncio.run(coro)
