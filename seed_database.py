"""Shared by run_local_demo.py and try_nexus.py: load scenarios from demo_scenarios.yaml, run them, judge the result, print it."""
import os

from config import load_yaml
from guardrails import check_output, redact_for_logging
from knowledge import index_path
from logging_config import get_logger, trace

logger = get_logger("scenario_runner")
SHORT = {"banking_server": "banking", "insurance_server": "insurance", "wealth_server": "wealth", "concierge_ops_server": "ops"}


def _r(data):
    return redact_for_logging(data)


@trace(logger, redact=_r)
def load_scenarios():
    return load_yaml("demo_scenarios.yaml")["scenarios"]


@trace(logger, redact=_r)
def find_scenario(name):
    return next((s for s in load_scenarios() if s["name"] == name), None)


@trace(logger, redact=_r)
def run_scenario(scenario):
    """Run one scenario through the real pipeline, applying its test switches (offline servers, demo index) temporarily."""
    from nexus_orchestrator.pipeline import handle_request
    switches = {"NEXUS_OFFLINE": ",".join(scenario.get("offline", [])),
                "NEXUS_DEMO": "1" if scenario.get("needs_demo_index") else os.environ.get("NEXUS_DEMO", "")}
    if switches["NEXUS_OFFLINE"]:  # an offline-server scenario is a test action: enable test mode for this run only
        switches["NEXUS_TEST_MODE"] = "1"
    saved = {key: os.environ.get(key) for key in switches}
    for key, value in switches.items():
        if value:
            os.environ[key] = value
    try:
        return handle_request(scenario["text"])
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@trace(logger, redact=_r)
def judge(scenario, result):
    """Compare a result with the scenario's expectations (properties, not exact wording). Returns a list of problems."""
    expect, problems = scenario["expect"], []
    called = {call["server"] for call in result["routing"]}
    if "status" in expect and result["status"] != expect["status"]:
        problems.append(f"status is {result['status']}, expected {expect['status']}")
    if "status_in" in expect and result["status"] not in expect["status_in"]:
        problems.append(f"status is {result['status']}, expected one of {expect['status_in']}")
    if "product_line" in expect and (result["intake"] or {}).get("product_line") != expect["product_line"]:
        problems.append(f"product_line is {(result['intake'] or {}).get('product_line')}, expected {expect['product_line']}")
    if "rule" in expect and (result["escalation"] or {}).get("rule") != expect["rule"]:
        problems.append(f"escalation rule is {(result['escalation'] or {}).get('rule')}, expected {expect['rule']}")
    if "error_code" in expect and (result["error"] or {}).get("code") != expect["error_code"]:
        problems.append(f"error code is {(result['error'] or {}).get('code')}, expected {expect['error_code']}")
    for server in expect.get("servers_include", []):
        if server not in called:
            problems.append(f"{server} was not called")
    if expect.get("no_mcp_calls") and result["routing"]:
        problems.append("an MCP tool call was made although intake should have stopped the request")
    if expect.get("quarantined") and not (result["context"] or {}).get("quarantined"):
        problems.append("no poisoned document was quarantined")
    if expect.get("conflicts") and not (result["validation"] or {}).get("conflicts_seen") and not (result["context"] or {}).get("conflicts"):
        problems.append("no retrieval conflict was surfaced")
    if result["status"] == "resolved" and result["context"]:
        verdict = check_output(result["customer_message"], "\n".join(f["text"] for f in result["context"]["facts"]))
        if any(i["type"] == "leaked_identifier" for i in verdict["issues"]):
            problems.append("reply contains an unmasked identifier")
    return problems


@trace(logger, redact=_r)
def demo_index_missing(scenario):
    return bool(scenario.get("needs_demo_index")) and not index_path(include_demo=True).exists()


@trace(logger, redact=_r)
def show(result):
    """Human-readable view: status, classification, ROUTING (which MCP servers), GROUNDING (facts + sources), decision, reply."""
    print(f"\n  STATUS        : {result['status'].upper()}" + (f"   ({result['error']['code']})" if result["error"] else ""))
    if result["error"]:
        print(f"  ERROR DETAIL  : {result['error']['message']}")
    if result["intake"]:
        i = result["intake"]
        print(f"  CLASSIFIED    : {i['product_line']} / {i['request_type']} / {i['urgency']} / escalation_category={i['escalation_category']}")
    print("  AGENT STEPS   : " + " -> ".join(dict.fromkeys(s["agent"] for s in result["steps"])))
    print("\n  ROUTING (MCP tool calls recorded in the servers' own trace):")
    for call in result["routing"]:
        print(f"    {SHORT[call['server']]:<10} {call['tool']}")
    if not result["routing"]:
        print("    (no MCP tool call was made)")
    context = result["context"]
    if context:
        print("\n  GROUNDING (facts the reply was built from):")
        for fact in context["facts"]:
            print(f"    [{fact['source'].replace('_server', '')}] {fact['text'][:120]}")
        for conflict in context["conflicts"]:
            print(f"    !! CONFLICT: {conflict}  -> live MCP data wins")
        for gap in context["gaps"]:
            print(f"    gap: {gap}")
        if context.get("quarantined"):
            print(f"    quarantined (prompt injection): {[q['doc_id'] for q in context['quarantined']]}")
    if result["validation"]:
        v = result["validation"]
        print(f"\n  VALIDATION    : confidence={v['confidence']}  answers_question={v.get('answers_question')}  "
              f"conflicts_seen={len(v['conflicts_seen'])}  issues={len(v['issues'])}")
        for issue in v["issues"][:6]:
            print(f"    issue: {str(issue)[:140]}")
    if result["escalation"]:
        e = result["escalation"]
        print(f"  ESCALATION    : escalate={e['escalate']} rule={e['rule']} route={e.get('route_to') or '-'}  agent_agreed={e.get('agent_agreed')}")
    if result["status"] == "resolved" and context:
        verdict = check_output(result["customer_message"], "\n".join(f["text"] for f in context["facts"]))
        print("  GROUNDED?     : " + ("YES - no unmasked ids; every figure in the reply is in the facts" if not verdict["issues"]
                                      else "CHECK - " + "; ".join(i["detail"] for i in verdict["issues"])))
    if result.get("draft") and result["status"] == "escalated" and not (result["escalation"] or {}).get("rule") == "hard_category":
        print("\n  DRAFT (written by the Drafter but NOT sent - the request was escalated):\n    " + result["draft"].strip().replace("\n", "\n    "))
    print("\n  REPLY TO CUSTOMER:\n    " + (result["customer_message"].strip().replace("\n", "\n    ") or "(none)"))
