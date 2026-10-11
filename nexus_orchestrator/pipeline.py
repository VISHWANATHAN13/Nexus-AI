"""The ONLY way the pipeline touches backend systems: tool calls, resource reads and prompt fetches on the four MCP
servers, each started as its own local stdio process. Functions never raise: failures come back as typed error envelopes
({"ok": False, "error": {...}}), and an unreachable server is SERVER_UNAVAILABLE - never a guessed answer.

Two ways to reach a server (the call functions below are identical either way):
  * default: one short-lived server process per call (simple, isolated; ~6 s of process start-up per call);
  * opt-in persistent sessions (config mcp.persistent_sessions / NEXUS_PERSISTENT_MCP=1): one long-lived stdio session per
    server, reused. If a session cannot be OPENED the call silently uses the per-call path; if a call on an open session
    fails it is reported as SERVER_UNAVAILABLE and the session is discarded (never retried: a retried write could repeat a send).
"""
import asyncio
import atexit
import json
import os
import sys
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from config import ROOT, env_switch, load_config
from errors import error_response
from guardrails import redact_for_logging
from logging_config import get_logger, log_file_path, trace

logger = get_logger("mcp_client")


def _r(data):
    return redact_for_logging(data)


@trace(logger, redact=_r)
def offline_servers():
    """Test switch (needs NEXUS_TEST_MODE=1): NEXUS_OFFLINE=insurance_server,... makes those servers fail to start (stress test 4)."""
    return {name.strip() for name in env_switch("NEXUS_OFFLINE").split(",") if name.strip()}


@trace(logger, redact=_r)
def ledger_dir():
    """Where every server process appends its call ledger (tool_support._record_call). Stable across requests, and exported to
    this process's environment so in-process tool calls and every spawned server write to the same place."""
    if not os.environ.get("NEXUS_CALL_LEDGER_DIR"):
        base = log_file_path()
        os.environ["NEXUS_CALL_LEDGER_DIR"] = str(Path(tempfile.gettempdir()) / "nexus_ledger") if base == "stderr" \
            else str(Path(base).parent / "ledger")
    return os.environ["NEXUS_CALL_LEDGER_DIR"]


@trace(logger, redact=_r)
def server_params(server, bound_customer=None, max_scope=None):
    """StdioServerParameters that start `python -m <server>` (or a process that exits at once if the server is 'offline').
    bound_customer / max_scope are set only for servers started FOR AN AGENT: that process then refuses any record of another
    customer and any caller_scope above max_scope (tool_support.require_owned / enforce_max_scope)."""
    from mcp import StdioServerParameters
    args = ["-c", "raise SystemExit(1)"] if server in offline_servers() else ["-m", server]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["NEXUS_LOG_FILE"] = log_file_path()  # servers log where the pipeline can read the evidence
    env["NEXUS_CALL_LEDGER_DIR"] = ledger_dir()
    for key in ("NEXUS_BOUND_CUSTOMER", "NEXUS_MAX_SCOPE"):
        env.pop(key, None)
    if bound_customer:
        env["NEXUS_BOUND_CUSTOMER"] = bound_customer
        if max_scope:
            env["NEXUS_MAX_SCOPE"] = max_scope
    return StdioServerParameters(command=sys.executable, args=args, env=env, cwd=str(ROOT))


@trace(logger, redact=_r)
def _open_session(server):
    """Async context manager yielding an initialised ClientSession for ONE server process (replaced by a fake in tests)."""
    @asynccontextmanager
    async def opened():
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client
        async with stdio_client(server_params(server)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    return opened()


@trace(logger, redact=_r)
async def _with_session(server, action):
    async with _open_session(server) as session:
        return await action(session)


# ------------------------------------------------------------------ opt-in persistent sessions
_POOL = {"loop": None, "states": {}, "lock": threading.Lock()}


@trace(logger, redact=_r)
def persistent_enabled():
    flag = os.environ.get("NEXUS_PERSISTENT_MCP", "")
    if flag:
        return flag == "1"
    return bool(load_config()["mcp"].get("persistent_sessions"))


@trace(logger, redact=_r)
def _pool_loop():
    """One background event loop owns every persistent session, so each session's context managers are entered and exited by
    the same task (anyio requires that)."""
    with _POOL["lock"]:
        if _POOL["loop"] is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="nexus-mcp-sessions", daemon=True).start()
            _POOL["loop"] = loop
        return _POOL["loop"]


@trace(logger, redact=_r)
async def _serve(server, state):
    """Owns one session: opens it, answers queued actions one at a time, closes it. Everything it holds is released in finally."""
    try:
        async with _open_session(server) as session:
            state["ready"].set_result(True)
            while True:
                item = await state["queue"].get()
                if item is None:
                    break
                action, future = item
                try:
                    future.set_result(await action(session))
                except asyncio.CancelledError:
                    future.cancel()
                    raise
                except Exception as exc:
                    future.set_exception(exc)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # failed to open, or the process died
        if not state["ready"].done():
            state["ready"].set_exception(exc)
    finally:
        state["dead"] = True
        while not state["queue"].empty():  # nobody is left to answer these
            item = state["queue"].get_nowait()
            if item is not None and not item[1].done():
                item[1].set_exception(RuntimeError("session closed"))


@trace(logger, redact=_r)
def _state_for(server):
    state = _POOL["states"].get(server)
    if state is None or state["dead"]:
        loop = asyncio.get_running_loop()
        state = {"queue": asyncio.Queue(), "ready": loop.create_future(), "dead": False, "task": None}
        state["task"] = loop.create_task(_serve(server, state))
        _POOL["states"][server] = state
    return state


@trace(logger, redact=_r)
async def _submit(server, action, timeout):
    """("ok", value) | ("failed", error name) | ("not_opened", error name). 'not_opened' means nothing was sent."""
    state = _state_for(server)
    try:
        await asyncio.wait_for(asyncio.shield(state["ready"]), timeout)
    except BaseException as exc:
        state["task"].cancel()
        _POOL["states"].pop(server, None)
        return "not_opened", type(exc).__name__
    future = asyncio.get_running_loop().create_future()
    await state["queue"].put((action, future))
    try:
        return "ok", await asyncio.wait_for(future, timeout)
    except BaseException as exc:  # timed out or the call/process failed: drop the session, do NOT retry
        state["task"].cancel()
        _POOL["states"].pop(server, None)
        return "failed", type(exc).__name__


@trace(logger, redact=_r)
def _run_persistent(server, action, timeout):
    loop = _pool_loop()
    return asyncio.run_coroutine_threadsafe(_submit(server, action, timeout), loop).result(timeout * 2 + 5)


@trace(logger, redact=_r)
def shutdown_sessions():
    """Close every persistent session (also registered with atexit)."""
    loop = _POOL["loop"]
    if loop is None:
        return

    async def close_all():
        for state in list(_POOL["states"].values()):
            state["task"].cancel()
        await asyncio.gather(*[s["task"] for s in _POOL["states"].values()], return_exceptions=True)
        _POOL["states"].clear()

    try:
        asyncio.run_coroutine_threadsafe(close_all(), loop).result(10)
    except Exception:
        pass


atexit.register(shutdown_sessions)


@trace(logger, redact=_r)
def _run(server, action):
    """Run one MCP action against one server; any failure to start/answer becomes SERVER_UNAVAILABLE."""
    timeout = load_config()["mcp"]["call_timeout_seconds"]
    if persistent_enabled() and server not in offline_servers():
        try:
            status, payload = _run_persistent(server, action, timeout)
        except Exception as exc:
            status, payload = "not_opened", type(exc).__name__
        if status == "ok":
            return {"ok": True, "result": payload}
        if status == "failed":
            return error_response("SERVER_UNAVAILABLE", f"{server} is unavailable ({payload})", {"server": server})
        # "not_opened": the session could not be started and nothing was sent - use the per-call path below
    try:
        return {"ok": True, "result": asyncio.run(asyncio.wait_for(_with_session(server, action), timeout))}
    except (Exception, BaseExceptionGroup) as exc:  # process failed to start / died / timed out
        return error_response("SERVER_UNAVAILABLE", f"{server} is unavailable ({type(exc).__name__})", {"server": server})


@trace(logger, redact=_r)
def call_tool(server, tool, arguments):
    """Call one tool; returns the tool's own envelope {"ok": True, "data": ...} or a typed error envelope."""
    async def action(session):
        return await session.call_tool(tool, arguments)

    outcome = _run(server, action)
    if not outcome["ok"]:
        return outcome
    result = outcome["result"]
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "ok" in structured:
        return structured
    text = result.content[0].text if result.content else ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return error_response("TOOL_ERROR", text[:200] or "Tool returned no result", {"server": server, "tool": tool})
    return parsed if isinstance(parsed, dict) and "ok" in parsed else error_response("MALFORMED_RESPONSE", "Unexpected tool result")


@trace(logger, redact=_r)
def read_resource(server, uri):
    """Read one resource (e.g. customer://CUS-20077/profile); returns the resource's envelope."""
    from pydantic import AnyUrl

    async def action(session):
        return await session.read_resource(AnyUrl(uri))

    outcome = _run(server, action)
    if not outcome["ok"]:
        return outcome
    try:
        return json.loads(outcome["result"].contents[0].text)
    except (ValueError, IndexError, AttributeError):
        return error_response("MALFORMED_RESPONSE", "Unexpected resource content")


@trace(logger, redact=_r)
def get_prompt(server, name, arguments):
    """Render one MCP prompt; returns {"ok": True, "data": "<prompt text>"}."""
    async def action(session):
        return await session.get_prompt(name, {key: str(value) for key, value in arguments.items()})

    outcome = _run(server, action)
    if not outcome["ok"]:
        return outcome
    return {"ok": True, "data": outcome["result"].messages[0].content.text}


@trace(logger, redact=_r)
def list_primitives(server):
    """Start the server and list its tools / resource templates / prompts (used for startup evidence)."""
    async def action(session):
        tools = [t.name for t in (await session.list_tools()).tools]
        templates = [t.uriTemplate for t in (await session.list_resource_templates()).resourceTemplates]
        prompts = [p.name for p in (await session.list_prompts()).prompts]
        return {"tools": tools, "resource_templates": templates, "prompts": prompts}

    outcome = _run(server, action)
    return {"ok": True, "data": outcome["result"]} if outcome["ok"] else outcome
