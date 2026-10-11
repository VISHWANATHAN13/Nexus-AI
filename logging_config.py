

"""JSON structured logging with a @trace decorator that logs ENTER/EXIT/FAILED for every function.
 
No classes: each log line is built here as JSON and passed to the standard logger as a plain message.

Each process gets its own trace file (nexus_trace.<pid>.jsonl) to avoid interleaving on Windows.
 
Tracing coverage: every function the project defines is decorated with @trace, EXCEPT this short, deliberate list:

  1. the logging bootstrap, the tracer and the redaction primitives it calls (get_logger, log_file_path, process_log_path,

     log_files, clean_old_trace_files, log_json, trace, _clip, _jsonable, _preview, _scrub_traceback,

     guardrails.redact_for_logging / scrub_text / _fingerprint / _mask_value): tracing them would recurse;

  2. the one-line `_r` / `_rt` adapters that ARE the redact= callables;

  3. cached configuration getters on the hot path (config.load_yaml / load_config, intake.allowed_values);

  4. context managers, decorator factories and Pydantic validator hooks (database.db, nexus_orchestrator.tools.mcp_tools,

     tool_support.typed_errors / as_json_str, intake validators): the functions they wrap or call are traced;

  5. demo / CLI / UI scripts and the tests.

Coroutines (the persistent MCP sessions) are traced by an async-aware wrapper so ENTER/EXIT bracket the real work.

"""

import asyncio

import functools

import inspect

import json

import logging

import os

import sys

import time

import traceback

import uuid

from datetime import datetime, timezone
 
from pathlib import Path
 
PREVIEW_CHARS = 200

ARG_CLIP = 300

DEFAULT_LOG_FILE = Path(__file__).resolve().parent / "logs" / "nexus_trace.jsonl"
 
 
def log_file_path():

    """BASE name of the JSON trace: NEXUS_LOG_FILE, or logs/nexus_trace.jsonl. The value 'stderr' means the console."""

    return os.environ.get("NEXUS_LOG_FILE", str(DEFAULT_LOG_FILE))
 
 
def process_log_path():

    """Each process writes its OWN file (nexus_trace.<pid>.jsonl): several processes appending to one shared file can

    overwrite or interleave each other's lines on Windows."""

    base = Path(log_file_path())

    return base.with_name(f"{base.stem}.{os.getpid()}{base.suffix}")
 
 
def log_files():

    """Every per-process trace file belonging to the current base name."""

    base = Path(log_file_path())

    return sorted(base.parent.glob(f"{base.stem}.*{base.suffix}")) if base.parent.is_dir() else []
 
 
def get_logger(name):

    logger = logging.getLogger(name)

    if not logger.handlers:

        log_file = log_file_path()

        if log_file == "stderr":

            handler = logging.StreamHandler(sys.stderr)

        else:

            Path(log_file).parent.mkdir(parents=True, exist_ok=True)

            handler = logging.FileHandler(process_log_path(), encoding="utf-8")

        handler.setFormatter(logging.Formatter("%(message)s"))

        logger.addHandler(handler)

        logger.propagate = False

    logger.setLevel(os.environ.get("LOG_LEVEL", "DEBUG").upper())

    return logger
 
 
def log_json(logger, level, event, **fields):

    """Emit one structured JSON line."""

    line = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),

            "level": logging.getLevelName(level), "logger": logger.name, "event": event}

    line.update(fields)

    logger.log(level, json.dumps(line, default=str))
 
 
def _scrub_traceback(text):

    """Mask ids, e-mails and phone/long digit runs inside a traceback (exception messages can carry them). guardrails imports

    this module, so it is imported lazily; if it is unavailable the traceback is logged as is."""

    try:

        from guardrails import scrub_text

        return scrub_text(text)

    except Exception:

        return text
 
 
def _clip(value):

    if isinstance(value, str) and len(value) > ARG_CLIP:

        return value[:ARG_CLIP] + "…"

    if isinstance(value, dict):

        return {k: _clip(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):

        return [_clip(v) for v in value]

    return value
 
 
def _jsonable(value, redact):

    """Round-trip through JSON (objects -> str) and only then redact, so object reprs are scrubbed too."""

    try:

        data = json.loads(json.dumps(value, default=str))

    except Exception:

        data = repr(value)

    return redact(data) if redact else data
 
 
def _preview(value, redact):

    text = json.dumps(_jsonable(value, redact), default=str)

    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "…"
 
 
def trace(logger, redact=None):

    """Decorator factory: ENTER / EXIT / FAILED lines sharing one call_id. Works for plain functions and coroutines."""
 
    def decorator(func):

        qualified = f"{func.__module__}.{func.__qualname__}"

        signature = inspect.signature(func)
 
        def enter(args, kwargs):

            call_id = uuid.uuid4().hex[:12]

            try:

                bound = signature.bind(*args, **kwargs)

                bound.apply_defaults()

                arguments = dict(bound.arguments)

            except TypeError:

                arguments = {"args": list(args), "kwargs": kwargs}

            log_json(logger, logging.DEBUG, "ENTER", function=qualified, call_id=call_id,

                     args=_clip(_jsonable(arguments, redact)))

            return call_id, time.perf_counter()
 
        def failed(call_id, start, exc):

            log_json(logger, logging.ERROR, "FAILED", function=qualified, call_id=call_id,

                     duration_ms=round((time.perf_counter() - start) * 1000, 3),

                     exception_type=type(exc).__name__, traceback=_scrub_traceback(traceback.format_exc()))
 
        def leave(call_id, start, result):

            log_json(logger, logging.DEBUG, "EXIT", function=qualified, call_id=call_id,

                     duration_ms=round((time.perf_counter() - start) * 1000, 3), return_preview=_preview(result, redact))
 
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)

            async def async_wrapper(*args, **kwargs):

                call_id, start = enter(args, kwargs)

                try:

                    result = await func(*args, **kwargs)

                except (Exception, asyncio.CancelledError) as exc:  # a cancelled session is a FAILED call too

                    failed(call_id, start, exc)

                    raise

                leave(call_id, start, result)

                return result
 
            return async_wrapper
 
        @functools.wraps(func)

        def wrapper(*args, **kwargs):

            call_id, start = enter(args, kwargs)

            try:

                result = func(*args, **kwargs)

            except Exception as exc:

                failed(call_id, start, exc)

                raise

            leave(call_id, start, result)

            return result
 
        return wrapper
 
    return decorator
 
 
def clean_old_trace_files():

    """Delete trace files left by earlier (finished) processes; this process's own file is only emptied."""

    mine = process_log_path()

    for path in log_files():

        try:

            if path == mine:

                path.write_text("")

            else:

                path.unlink()

        except OSError:  # still open in another running process

            pass
 