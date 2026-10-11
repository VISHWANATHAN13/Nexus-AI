"""Orchestrator as a standalone local process.

  python -m nexus_orchestrator --text "I am CUS-20077 ... <request>"     one request, JSON result
  python -m nexus_orchestrator --serve                                     JSON-lines loop on stdin/stdout ({"cmd":"ping"} or {"text": "..."})
  python -m nexus_orchestrator --ping                                      start-up check, no LLM needed
"""
import argparse
import json
import sys

from config import setup_environment
from guardrails import redact_for_logging
from logging_config import get_logger, trace

logger = get_logger("nexus_orchestrator")


def _r(data):
    return redact_for_logging(data)


@trace(logger, redact=_r)
def answer(request):
    """One JSON request -> one JSON reply."""
    if request.get("cmd") == "ping":
        return {"ok": True, "service": "nexus_orchestrator"}
    from nexus_orchestrator.pipeline import handle_request
    return handle_request(request.get("text", ""))


@trace(logger, redact=_r)
def main():
    setup_environment()
    parser = argparse.ArgumentParser(prog="nexus_orchestrator")
    parser.add_argument("--text", help="one free-text request that includes the customer id")
    parser.add_argument("--serve", action="store_true", help="read JSON requests from stdin, one per line")
    parser.add_argument("--ping", action="store_true", help="print a start-up confirmation and exit")
    args = parser.parse_args()
    if args.ping:
        print(json.dumps(answer({"cmd": "ping"})))
    elif args.serve:
        for line in sys.stdin:
            if line.strip():
                print(json.dumps(answer(json.loads(line)), default=str), flush=True)
    elif args.text:
        print(json.dumps(answer({"text": args.text}), indent=2, default=str))
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
