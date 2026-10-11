import importlib
import unittest
from contextlib import contextmanager


def need(*modules):
    """Skip the calling test when an optional framework is not installed."""
    for name in modules:
        try:
            importlib.import_module(name)
        except ImportError:
            raise unittest.SkipTest(f"{name} not installed")


def code_of(reply):
    """Error code of a typed error envelope."""
    assert reply["ok"] is False, reply
    return reply["error"]["code"]


@contextmanager
def raises_typed(code):
    """Expect a typed error (ValueError carrying a JSON payload) with this code."""
    from errors import parse_error
    try:
        yield
    except ValueError as exc:
        payload = parse_error(exc)
        assert payload and payload["code"] == code, f"expected {code}, got {payload or exc}"
        return
    raise AssertionError(f"typed error {code} was not raised")
