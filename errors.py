
"""Typed errors without exception classes.
 
A tool/guardrail signals a typed error with fail(code, message): it raises a plain ValueError whose text is a JSON

payload. The typed_errors decorator (tool_support.py) turns that into {"ok": False, "error": {...}} at the tool boundary.

"""

import json
 
from logging_config import get_logger, trace
 
logger = get_logger("errors")
 
 
def _r(data):

    from guardrails import redact_for_logging  # lazy: guardrails imports this module

    return redact_for_logging(data)
 
 
@trace(logger, redact=_r)

def error_response(code, message, details=None):

    """The standard error envelope returned by every tool, resource and pipeline stage."""

    return {"ok": False, "error": {"code": code, "message": message, "details": details or {}}}
 
 
@trace(logger, redact=_r)

def fail(code, message, details=None):

    """Raise a typed error (a ValueError carrying a JSON payload)."""

    raise ValueError(json.dumps(error_response(code, message, details)["error"]))
 
 
@trace(logger, redact=_r)

def parse_error(exc):

    """Return the typed-error payload carried by an exception, or None if it is not one of ours."""

    try:

        payload = json.loads(str(exc))

    except (ValueError, TypeError):

        return None

    return payload if isinstance(payload, dict) and "code" in payload else None
 