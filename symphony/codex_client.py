from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import __version__
from .config import CodexConfig, TrackerConfig
from .errors import CodexClientError
from .linear import LinearClient
from .logging_utils import log_event
from .models import utc_now

CodexEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class CodexTurnResult:
    thread_id: str
    turn_id: str
    session_id: str
    success: bool
    error: str | None = None


class JsonRpcCodexClient:
    def __init__(
        self,
        config: CodexConfig,
        *,
        tracker_config: TrackerConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.tracker_config = tracker_config
        self.logger = logger or logging.getLogger(__name__)
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._thread_id: str | None = None

    async def start(self, workspace_path: Path) -> str:
        argv = codex_shell_argv(self.config.command)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workspace_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise CodexClientError("codex_not_found", f"failed to start Codex app-server: {argv[0]} not found") from exc
        except OSError as exc:
            raise CodexClientError("codex_not_found", f"failed to start Codex app-server: {exc}") from exc

        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        await self._request(
            "initialize",
            {
                "clientInfo": {"name": "symphony-python", "version": __version__},
                "capabilities": {},
            },
            timeout_ms=self.config.read_timeout_ms,
        )

        params: dict[str, Any] = {
            "cwd": str(workspace_path),
            "serviceName": "symphony-python",
            "ephemeral": False,
            "threadSource": "user",
        }
        if self.config.approval_policy is not None:
            params["approvalPolicy"] = self.config.approval_policy
        if self.config.thread_sandbox is not None:
            params["sandbox"] = self.config.thread_sandbox
        result = await self._request("thread/start", params, timeout_ms=self.config.read_timeout_ms)
        thread = result.get("thread") or {}
        thread_id = thread.get("id") or result.get("threadId")
        if not thread_id:
            raise CodexClientError("response_error", "thread/start response did not include a thread id")
        self._thread_id = str(thread_id)
        return self._thread_id

    async def run_turn(
        self,
        prompt: str,
        workspace_path: Path,
        *,
        title: str | None = None,
        on_event: CodexEventCallback | None = None,
    ) -> CodexTurnResult:
        if not self._thread_id:
            raise CodexClientError("response_error", "Codex thread has not been started")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def sink(event: dict[str, Any]) -> None:
            await queue.put(event)
            if on_event:
                await on_event(event)

        previous_sink = getattr(self, "_event_sink", None)
        self._event_sink = sink
        params: dict[str, Any] = {
            "threadId": self._thread_id,
            "cwd": str(workspace_path),
            "input": [{"type": "text", "text": prompt}],
            "clientUserMessageId": title,
        }
        if self.config.approval_policy is not None:
            params["approvalPolicy"] = self.config.approval_policy
        if self.config.turn_sandbox_policy is not None:
            params["sandboxPolicy"] = self.config.turn_sandbox_policy

        try:
            result = await self._request("turn/start", params, timeout_ms=self.config.read_timeout_ms)
            turn = result.get("turn") or {}
            turn_id = turn.get("id")
            if not turn_id:
                raise CodexClientError("response_error", "turn/start response did not include a turn id")

            session_id = f"{self._thread_id}-{turn_id}"
            started = {
                "event": "session_started",
                "timestamp": utc_now(),
                "thread_id": self._thread_id,
                "turn_id": turn_id,
                "session_id": session_id,
                "codex_app_server_pid": self._proc.pid if self._proc else None,
            }
            if on_event:
                await on_event(started)
            return await self._wait_for_turn(turn_id=str(turn_id), session_id=session_id, queue=queue)
        finally:
            self._event_sink = previous_sink

    async def stop(self) -> None:
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        tasks = [task for task in (self._reader_task, self._stderr_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_for_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> CodexTurnResult:
        deadline = self.config.turn_timeout_ms / 1000
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=deadline)
            except asyncio.TimeoutError as exc:
                raise CodexClientError("turn_timeout", f"turn {turn_id} timed out") from exc
            if event.get("turn_id") != turn_id:
                continue
            if event.get("event") == "turn_completed":
                return CodexTurnResult(self._thread_id or "", turn_id, session_id, True)
            if event.get("event") in {"turn_failed", "turn_cancelled", "turn_input_required"}:
                return CodexTurnResult(self._thread_id or "", turn_id, session_id, False, str(event.get("message") or event["event"]))

    async def _request(self, method: str, params: dict[str, Any], *, timeout_ms: int) -> dict[str, Any]:
        if not self._proc or not self._proc.stdin:
            raise CodexClientError("port_exit", "Codex app-server process is not running")
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = fut
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self._proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout_ms / 1000)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise CodexClientError("response_timeout", f"{method} timed out") from exc

    async def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                self._fail_pending(CodexClientError("port_exit", "Codex app-server stdout closed"))
                return
            try:
                message = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                log_event(self.logger, logging.WARNING, "codex_malformed", line=line[:500])
                continue
            await self._handle_message(message)

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                return
            log_event(self.logger, logging.DEBUG, "codex_stderr", message=line.decode("utf-8", errors="replace").strip())

    async def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            request_id = int(message["id"])
            fut = self._pending.pop(request_id, None)
            if fut is not None and not fut.done():
                if "error" in message:
                    fut.set_exception(CodexClientError("response_error", str(message["error"])))
                else:
                    fut.set_result(message.get("result") or {})
            return

        if "id" in message and "method" in message:
            await self._handle_server_request(message)
            return

        method = str(message.get("method") or "other_message")
        params = message.get("params") or {}
        event = self._notification_to_event(method, params)
        sink = getattr(self, "_event_sink", None)
        if sink:
            await sink(event)

    async def _handle_server_request(self, request: dict[str, Any]) -> None:
        method = str(request.get("method") or "")
        request_id = request.get("id")
        result: dict[str, Any]
        event_name = "other_message"
        if "commandExecution" in method and "approval" in method.lower():
            result = {"decision": "acceptForSession"}
            event_name = "approval_auto_approved"
        elif "exec" in method.lower() and "approval" in method.lower():
            result = {"decision": "approved_for_session"}
            event_name = "approval_auto_approved"
        elif "fileChange" in method and "approval" in method.lower():
            result = {"decision": "acceptForSession"}
            event_name = "approval_auto_approved"
        elif "applyPatch" in method and "approval" in method.lower():
            result = {"decision": "approved_for_session"}
            event_name = "approval_auto_approved"
        elif "tool" in method.lower() and "userinput" in method.replace("/", "").lower():
            result = {"answers": {}}
            event_name = "turn_input_required"
        elif "dynamicTool" in method or "dynamic_tool" in method:
            result = await self._handle_dynamic_tool(request.get("params") or {})
            event_name = "unsupported_tool_call" if not result.get("success") else "tool_call_completed"
        else:
            result = {"contentItems": [{"type": "inputText", "text": f"Unsupported request: {method}"}], "success": False}
            event_name = "unsupported_tool_call"

        if self._proc and self._proc.stdin:
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            self._proc.stdin.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()

        sink = getattr(self, "_event_sink", None)
        if sink:
            await sink({"event": event_name, "timestamp": utc_now(), "method": method, "message": method})

    async def _handle_dynamic_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name") or params.get("toolName") or params.get("tool_name")
        arguments = params.get("arguments") or params.get("input") or {}
        if tool_name != "linear_graphql":
            return {"success": False, "contentItems": [{"type": "inputText", "text": f"Unsupported tool: {tool_name}"}]}
        return await linear_graphql_tool(self.tracker_config, arguments)

    def _notification_to_event(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        event = method.replace("/", "_")
        if method == "turn/completed":
            event = "turn_completed"
        elif method == "turn/started":
            event = "turn_started"
        elif method == "thread/tokenUsage/updated":
            event = "token_usage_updated"
        elif method == "account/rateLimits/updated":
            event = "rate_limits_updated"
        elif method == "error":
            event = "turn_failed"
        turn = params.get("turn") if isinstance(params, dict) else None
        turn_id = params.get("turnId") or (turn or {}).get("id")
        thread_id = params.get("threadId") or self._thread_id
        if method == "turn/completed" and isinstance(turn, dict) and turn.get("status") in {"failed", "interrupted"}:
            event = "turn_failed" if turn.get("status") == "failed" else "turn_cancelled"
        return {
            "event": event,
            "timestamp": utc_now(),
            "thread_id": thread_id,
            "turn_id": turn_id,
            "session_id": f"{thread_id}-{turn_id}" if thread_id and turn_id else None,
            "codex_app_server_pid": self._proc.pid if self._proc else None,
            "payload": params,
            "message": summarize_payload(method, params),
        }

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()


def codex_shell_argv(command: str) -> list[str]:
    if os.name == "nt":
        parts = command.split()
        if parts and parts[0] == "codex":
            codex_cmd = shutil.which("codex.cmd") or shutil.which("codex.exe")
            if codex_cmd:
                return [codex_cmd, *parts[1:]]
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
    bash = shutil.which("bash")
    if bash:
        return [bash, "-lc", command]
    return ["sh", "-lc", command]


def summarize_payload(method: str, params: dict[str, Any]) -> str:
    if method == "item/agentMessage/delta":
        return str(params.get("delta") or "")[:300]
    if method == "turn/completed":
        turn = params.get("turn") or {}
        return f"turn status={turn.get('status')}"
    if method == "error":
        return str(params.get("message") or params)[:300]
    return method


async def linear_graphql_tool(tracker_config: TrackerConfig | None, arguments: Any) -> dict[str, Any]:
    if tracker_config is None or tracker_config.kind != "linear" or not tracker_config.api_key:
        return {"success": False, "contentItems": [{"type": "inputText", "text": "Linear auth is not configured."}]}
    if isinstance(arguments, str):
        query = arguments
        variables: dict[str, Any] = {}
    elif isinstance(arguments, dict):
        query = arguments.get("query")
        variables = arguments.get("variables") or {}
    else:
        return {"success": False, "contentItems": [{"type": "inputText", "text": "Invalid linear_graphql input."}]}
    if not isinstance(query, str) or not query.strip():
        return {"success": False, "contentItems": [{"type": "inputText", "text": "query must be a non-empty string."}]}
    if not isinstance(variables, dict):
        return {"success": False, "contentItems": [{"type": "inputText", "text": "variables must be an object."}]}
    if _graphql_operation_count(query) != 1:
        return {"success": False, "contentItems": [{"type": "inputText", "text": "query must contain exactly one operation."}]}
    client = LinearClient(tracker_config)
    try:
        data = await client.execute_raw_graphql(query, variables)
    except Exception as exc:
        return {"success": False, "contentItems": [{"type": "inputText", "text": str(exc)}]}
    finally:
        await client.aclose()
    return {"success": True, "contentItems": [{"type": "inputText", "text": json.dumps(data, default=str)}]}


def _graphql_operation_count(query: str) -> int:
    tokens = [part for part in query.replace("{", " { ").split() if part in {"query", "mutation", "subscription"}]
    if tokens:
        return len(tokens)
    return 1 if "{" in query and "}" in query else 0
