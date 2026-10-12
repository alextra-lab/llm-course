"""
E1: the Tasks extension (io.modelcontextprotocol/tasks, spec 2026-07-28), both halves.

A tool call that takes longer than a request should wait for (isolating a host takes the EDR
tens of seconds on a real estate) becomes a task: the server answers at once with a task id,
the work carries on, and the client polls `tasks/get` until it's done. The server decides; the
client only says, in each request's capabilities, that it understands tasks.

The SDK ships the extension *framework* (`MCPServer(extensions=[...])`,
`Client(extensions=[...])`) but not the Tasks extension itself yet, so this module implements
the part of the spec the course needs, on that framework:

    server   Tasks(after_s=0.5)   a tools/call still running after `after_s` seconds becomes a
                                  task (only for clients that declared the extension); serves
                                  tasks/get and tasks/cancel. tasks/update is answered, but no
                                  tool here asks for input mid-task: approvals happen before
                                  the task starts, on the original call (the spec's advice).
    client   TasksClient()        resolves a task result by polling tasks/get at the server's
                                  pollIntervalMs, so `call_tool` returns the final result as if
                                  it had been synchronous. `on_task` sees each poll.

Spec: https://github.com/modelcontextprotocol/ext-tasks/blob/main/specification/2026-07-28/tasks.md
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.client.extension import ClaimContext, ClientExtension, ResultClaim
from mcp.server.extension import Extension, MethodBinding
from mcp.types import CallToolResult, Request, RequestParams, Result

EXTENSION_ID = "io.modelcontextprotocol/tasks"
Status = Literal["working", "input_required", "completed", "failed", "cancelled"]
TERMINAL = ("completed", "failed", "cancelled")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# =========================================================================================
# Wire shapes
# =========================================================================================

class TaskParams(RequestParams):
    taskId: str


class UpdateTaskParams(TaskParams):
    inputResponses: dict[str, Any] = {}


class GetTaskRequest(Request[TaskParams, Literal["tasks/get"]]):
    method: Literal["tasks/get"] = "tasks/get"
    name_param = "taskId"                  # Streamable HTTP: Mcp-Name carries the task id


class CancelTaskRequest(Request[TaskParams, Literal["tasks/cancel"]]):
    method: Literal["tasks/cancel"] = "tasks/cancel"
    name_param = "taskId"


class CreateTaskResult(Result):
    """What a tools/call returns instead of a CallToolResult when it became a task."""
    result_type: Literal["task"]
    taskId: str
    status: str
    statusMessage: str | None = None
    createdAt: str
    lastUpdatedAt: str
    ttlMs: int | None = None
    pollIntervalMs: int | None = None


class GetTaskResult(Result):
    taskId: str
    status: str
    statusMessage: str | None = None
    createdAt: str
    lastUpdatedAt: str
    ttlMs: int | None = None
    pollIntervalMs: int | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


# =========================================================================================
# The server half
# =========================================================================================

@dataclass
class _Task:
    id: str
    tool: str
    status: Status = "working"
    message: str = "The operation is in progress."
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    created_mono: float = field(default_factory=time.monotonic)
    result: dict | None = None
    error: dict | None = None
    job: asyncio.Task | None = None

    def wire(self, ttl_ms: int, poll_ms: int) -> dict[str, Any]:
        out = {"taskId": self.id, "status": self.status, "statusMessage": self.message,
               "createdAt": self.created, "lastUpdatedAt": self.updated,
               "ttlMs": ttl_ms, "pollIntervalMs": poll_ms}
        if self.status == "completed":
            out["result"] = self.result
        if self.status == "failed":
            out["error"] = self.error
        return out


def _dump(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    return result.model_dump(by_alias=True, mode="json", exclude_none=True)


def declared(ctx) -> bool:
    """Did THIS request's client declare the Tasks extension? (Asked per request.)"""
    caps = ctx.session.client_capabilities
    return bool(caps and caps.extensions and EXTENSION_ID in caps.extensions)


class Tasks(Extension):
    """Turn slow tools/call requests into tasks, for clients that understand them."""

    identifier = EXTENSION_ID

    def __init__(self, after_s: float = 0.5, ttl_ms: int = 600_000, poll_ms: int = 500,
                 tools: set[str] | None = None, on_change=None):
        self.after_s, self.ttl_ms, self.poll_ms = after_s, ttl_ms, poll_ms
        self.only = tools                  # None: any tool may become a task
        self.on_change = on_change         # called with (task_id, tool, status) on each change
        self.tasks: dict[str, _Task] = {}

    # -- tools/call -----------------------------------------------------------------------
    async def intercept_tool_call(self, params, ctx, call_next):
        if not declared(ctx) or (self.only is not None and params.name not in self.only):
            return await call_next(ctx)
        job = asyncio.ensure_future(call_next(ctx))
        done, _ = await asyncio.wait({job}, timeout=self.after_s)
        if done:                           # quick, or asked for input first: answer directly
            return job.result()
        task = _Task(id=str(uuid.uuid4()), tool=params.name, job=job)
        self.tasks[task.id] = task         # durably created before we answer (in memory here)
        job.add_done_callback(lambda j, t=task: self._finish(t, j))
        self._changed(task)
        self._expire()
        return {"resultType": "task", **task.wire(self.ttl_ms, self.poll_ms)}

    def _finish(self, task: _Task, job: asyncio.Task) -> None:
        if task.status in TERMINAL:
            return
        task.updated = _now()
        if job.cancelled():
            task.status, task.message = "cancelled", "Cancelled at the client's request."
        elif job.exception() is not None:
            e = job.exception()            # a JSON-RPC error: the task failed
            task.status, task.message = "failed", f"{type(e).__name__}"
            task.error = {"code": getattr(e, "code", -32603), "message": str(e)[:500]}
        else:                              # a tool error (isError) is still "completed"
            task.status, task.message = "completed", "Done."
            task.result = _dump(job.result())
        self._changed(task)

    def _changed(self, task: _Task) -> None:
        if self.on_change:
            self.on_change(task.id, task.tool, task.status)

    def _expire(self) -> None:
        horizon = time.monotonic() - self.ttl_ms / 1000
        for tid in [t.id for t in self.tasks.values()
                    if t.status in TERMINAL and t.created_mono < horizon]:
            del self.tasks[tid]

    # -- tasks/get, tasks/update, tasks/cancel ---------------------------------------------
    def _lookup(self, ctx, params) -> _Task:
        from mcp.shared.exceptions import MCPError

        if not declared(ctx):
            raise MCPError(-32021, "Missing required client capability", data={
                "requiredCapabilities": {"extensions": {EXTENSION_ID: {}}}})
        task = self.tasks.get(params.taskId)
        if task is None:
            raise MCPError(-32602, f"unknown or expired task {params.taskId}")
        return task

    async def _get(self, ctx, params: TaskParams):
        task = self._lookup(ctx, params)
        return {"resultType": "complete", **task.wire(self.ttl_ms, self.poll_ms)}

    async def _update(self, ctx, params: UpdateTaskParams):
        self._lookup(ctx, params)          # nothing here asks for input mid-task
        return {"resultType": "complete"}

    async def _cancel(self, ctx, params: TaskParams):
        task = self._lookup(ctx, params)
        if task.status not in TERMINAL and task.job is not None:
            task.job.cancel()              # cooperative: the tool may still finish first
        return {"resultType": "complete"}

    def methods(self):
        return (MethodBinding("tasks/get", TaskParams, self._get),
                MethodBinding("tasks/update", UpdateTaskParams, self._update),
                MethodBinding("tasks/cancel", TaskParams, self._cancel))


# =========================================================================================
# The client half
# =========================================================================================

class TasksClient(ClientExtension):
    """Declare the extension, and resolve a task into the tool's final result by polling."""

    identifier = EXTENSION_ID

    def __init__(self, on_task=None, timeout_s: float = 300):
        self.on_task = on_task             # called with each GetTaskResult (or the first one)
        self.timeout_s = timeout_s

    def claims(self):
        return (ResultClaim(result_type="task", model=_Claimed, resolve=self._resolve),)

    async def _resolve(self, created: _Claimed, ctx: ClaimContext) -> CallToolResult:
        if self.on_task:
            self.on_task(created)
        deadline = time.monotonic() + self.timeout_s
        poll = (created.pollIntervalMs or 500) / 1000
        status = created
        try:
            while True:
                await asyncio.sleep(poll)
                status = await ctx.session.send_request(
                    GetTaskRequest(params=TaskParams(taskId=created.taskId)), GetTaskResult)
                if self.on_task:
                    self.on_task(status)
                if status.status == "completed":
                    return CallToolResult.model_validate(status.result or {})
                if status.status in ("failed", "cancelled"):
                    detail = (status.error or {}).get("message") or status.statusMessage
                    return CallToolResult(is_error=True, content=[{
                        "type": "text", "text": f"task {status.status}: {detail}"}])
                if time.monotonic() > deadline:
                    raise TimeoutError(f"task {created.taskId} still {status.status}")
                poll = (status.pollIntervalMs or 500) / 1000
        except BaseException:
            if status.status not in TERMINAL:   # we're giving up: tell the server, quietly
                with contextlib.suppress(Exception):
                    await ctx.session.send_request(
                        CancelTaskRequest(params=TaskParams(taskId=created.taskId)), Result)
            raise


class _Claimed(CreateTaskResult):
    pass
