"""Helpers shared by every MCP server: the success envelope, typed-error decorator, timestamps, the call ledger and the
server-side session binding."""
import functools
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from config import load_config
from errors import error_response, fail, parse_error
from guardrails import redact_for_logging, sanitize_tool_output
from logging_config import get_logger, trace

logger = get_logger("tool_support")
DENIED = "This session may only read the authenticated customer's own records"
_FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _r(data):
    return redact_for_logging(data)


@trace(logger, redact=_r)
def ok(data):
    """Success envelope. Every string in the result is sanitised first (tool output is untrusted input)."""
    clean, _findings = sanitize_tool_output(data)
    return {"ok": True, "data": clean}


# ------------------------------------------------------------------ server-side session binding
# When the orchestrator starts a server FOR AN AGENT (CrewAI's MCP adapter), it sets NEXUS_BOUND_CUSTOMER and NEXUS_MAX_SCOPE in
# that server process. The server itself then refuses any record that belongs to another customer and any caller_scope above the
# cap, whatever arguments the model chooses. The orchestrator's own deterministic calls (KYC, ownership checks, delivery) run in
# unbound processes. An unbound server behaves exactly as before.
@trace(logger, redact=_r)
def bound_customer():
    return os.environ.get("NEXUS_BOUND_CUSTOMER", "")


@trace(logger, redact=_r)
def enforce_owner(owner_customer_id):
    """In a bound session, only the bound customer's own id passes."""
    bound = bound_customer()
    if bound and owner_customer_id != bound:
        fail("ACCESS_DENIED", DENIED)


@trace(logger, redact=_r)
def require_owned(row, not_found_message):
    """A fetched record, or a typed error. Unbound: missing -> NOT_FOUND. Bound: missing OR another customer's -> ACCESS_DENIED
    (the same answer for both, so identifiers cannot be enumerated through an agent)."""
    bound = bound_customer()
    if row is None:
        fail("ACCESS_DENIED", DENIED) if bound else fail("NOT_FOUND", not_found_message)
    if bound and row.get("customer_id") != bound:
        fail("ACCESS_DENIED", DENIED)
    return row


@trace(logger, redact=_r)
def enforce_max_scope(caller_scope):
    """In a bound session caller_scope may not exceed NEXUS_MAX_SCOPE."""
    cap = os.environ.get("NEXUS_MAX_SCOPE", "")
    if cap:
        order = load_config()["scopes"]["order"]
        if caller_scope in order and cap in order and order.index(caller_scope) > order.index(cap):
            fail("ACCESS_DENIED", f"caller_scope '{caller_scope}' is above the '{cap}' this session is limited to")


# ------------------------------------------------------------------ call ledger (evidence that does not depend on log levels)
@trace(logger, redact=_r)
def figure_key(token):
    """Short hash of a figure in canonical form (125,000 == 125000.00), so the ledger can prove 'a tool returned this number'
    without storing balances or premiums in clear."""
    try:
        value = Decimal(token.replace(",", "").rstrip("."))
    except InvalidOperation:
        return None
    return hashlib.sha256(format(value.normalize(), "f").encode()).hexdigest()[:10]


@trace(logger, redact=_r)
def _record_call(function, result):
    """Append one line {ts, server, tool, ok, figures} to <NEXUS_CALL_LEDGER_DIR>/calls.<pid>.jsonl. Best effort: never raises."""
    directory = os.environ.get("NEXUS_CALL_LEDGER_DIR")
    if not directory:
        return
    try:
        succeeded = isinstance(result, dict) and bool(result.get("ok"))
        figures = []
        if succeeded:
            text = json.dumps(result.get("data", {}), default=str)
            figures = sorted({key for key in (figure_key(token) for token in _FIGURE.findall(text)) if key})
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "server": (function.__module__ or "").split(".")[0],
                 "tool": function.__name__, "ok": succeeded, "figures": figures}
        path = Path(directory) / f"calls.{os.getpid()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception:  # evidence must never break a tool call
        pass


def typed_errors(fn):
    """Outermost wrapper of every tool: typed errors and database denials become {"ok": False, "error": ...} payloads, and each
    call is written to the call ledger."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except ValueError as exc:
            payload = parse_error(exc)
            if payload is None:
                raise
            result = error_response(payload["code"], payload["message"], payload.get("details"))
        except sqlite3.DatabaseError as exc:
            denied = "not authorized" in str(exc).lower() or "prohibited" in str(exc).lower()
            result = error_response("ACCESS_DENIED" if denied else "DATABASE_ERROR",
                                   "Database access refused" if denied else "Database error")
        _record_call(fn, result)
        return result

    return wrapper


def as_json_str(fn):
    """Resources must return text: serialise the (already typed) payload to JSON."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        return result if isinstance(result, str) else json.dumps(result)

    return wrapper


@trace(logger, redact=_r)
def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
