import json
import logging
import os
import uuid

from guardrails import redact_for_logging
from logging_config import get_logger, trace


def _capture():
    """A logger writing JSON lines into a list (no custom classes: a plain handler is enough)."""
    logger = logging.getLogger("t_" + uuid.uuid4().hex)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    lines = []
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(json.loads(record.getMessage()))
    logger.addHandler(handler)
    return logger, lines


def test_trace_logs_enter_exit_failed_as_redacted_json_and_log_level_defaults_to_debug():
    logger, lines = _capture()

    @trace(logger)
    def add(a, b=2):
        return a + b

    assert add(1) == 3
    enter, exit_ = lines
    assert (enter["event"], enter["level"]) == ("ENTER", "DEBUG") and (exit_["event"], exit_["level"]) == ("EXIT", "DEBUG")
    assert enter["call_id"] == exit_["call_id"] and enter["args"] == {"a": 1, "b": 2}
    assert enter["function"].endswith("add") and "duration_ms" in exit_ and exit_["return_preview"] == "3"

    logger, lines = _capture()

    @trace(logger)
    def boom():
        raise KeyError("nope")

    try:
        boom()
        raise AssertionError("not re-raised")
    except KeyError:
        pass
    failed = lines[-1]
    assert failed["event"] == "FAILED" and failed["level"] == "ERROR" and failed["exception_type"] == "KeyError"
    assert "Traceback" in failed["traceback"] and failed["call_id"] == lines[0]["call_id"] and "duration_ms" in failed

    logger, lines = _capture()

    @trace(logger, redact=redact_for_logging)
    def lookup(customer_id, email):
        return {"customer_id": customer_id}

    lookup("CUS-20077", "arjun@example.com")
    blob = json.dumps(lines)
    assert "CUS-20077" not in blob and "arjun@example.com" not in blob and "****0077" in blob

    logger, lines = _capture()

    @trace(logger)
    def big():
        return "x" * 5000

    big()
    assert len(lines[1]["return_preview"]) <= 205

    out = redact_for_logging({"account_id": "ACC-20077", "customer_id": "CUS-20077", "policy_id": "POL-IN-30091",
                              "phone": "+91 98765 20077", "email": "arjun@example.com", "full_name": "Arjun Mehta",
                              "note": "call 9876543210 or mail a.b@x.org about ACC-20077", "n": 3, "message": "my private text"})
    blob = str(out)
    for raw in ("ACC-20077", "CUS-20077", "POL-IN-30091", "98765", "arjun@example.com", "a.b@x.org", "Arjun", "my private text"):
        assert raw not in blob, raw
    assert out["account_id"] == "****0077" and out["n"] == 3 and out["message"].startswith("<free-text")

    saved = os.environ.pop("LOG_LEVEL", None)
    try:
        assert get_logger("level_probe").level == logging.DEBUG
        os.environ["LOG_LEVEL"] = "WARNING"
        assert get_logger("level_probe").level == logging.WARNING
    finally:
        os.environ["LOG_LEVEL"] = saved or "DEBUG"
        get_logger("level_probe")

    import logging_config
    names = [p.name for p in logging_config.log_files()]
    assert names and all(n.startswith("tests_trace.") and n.endswith(".jsonl") for n in names)
    for path in logging_config.log_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            json.loads(line)  # every line of every per-process trace file is a complete JSON object
