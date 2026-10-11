"""Chat turn records and their downloads (Markdown / JSON). Account, policy and portfolio numbers are masked to the last
four characters with the backend's own guardrails.mask_identifiers_in_text, in the query as well as the response."""
import json

from guardrails import mask_identifiers_in_text
from ui.progress import AGENTS, agent_meta

STATUS_LABEL = {"resolved": "RESOLVED", "escalated": "ESCALATED to a human", "rejected": "REJECTED", "error": "ERROR"}


def _mask(text):
    return mask_identifiers_in_text(text) if text else ""


def build_turn(job, state, scenario_problems=None):
    """Everything the chat shows and exports for one request (masked; no raw draft, no internal ids beyond the customer id)."""
    result = job["result"] or {"status": "error", "customer_message": "", "error": {"code": "UI_FAILURE", "message": job["error"] or ""},
                               "steps": [], "routing": []}
    intake, validation = result.get("intake") or {}, result.get("validation") or {}
    escalation, context = result.get("escalation") or {}, result.get("context") or {}
    meta = agent_meta()
    finished = [e["t"] for e in job["events"] if e["type"] == "done"]
    return {
        "submitted_at": job["submitted_at"], "mode": job["mode"], "scenario": (job["scenario"] or {}).get("name"),
        "query": _mask(job["text"]), "status": result["status"], "request_id": result.get("request_id"),
        "response": _mask(result.get("customer_message", "")),
        "error": {k: result["error"][k] for k in ("code", "message") if k in result["error"]} if result.get("error") else None,
        "classification": {k: intake[k] for k in ("product_line", "request_type", "urgency", "escalation_category") if k in intake},
        "validation": {"confidence": validation.get("confidence"), "answers_question": validation.get("answers_question"),
                       "conflicts_seen": len(validation.get("conflicts_seen", []))} if validation else None,
        "escalation": {k: escalation.get(k) for k in ("escalate", "rule", "route_to", "agent_agreed")} if escalation else None,
        "delivery_sent": (result.get("delivery") or {}).get("sent"),
        "facts": [{"source": f["source"], "text": _mask(f["text"])} for f in context.get("facts", [])],
        "conflicts": len(context.get("conflicts", [])), "quarantined": [q["doc_id"] for q in context.get("quarantined", [])],
        "mcp_calls": [{"server": c["server"], "tool": c["tool"]} for c in result.get("routing", [])],
        "agents": [{"agent": a["label"], "model_tier": meta[a["key"]]["tier"], **{k: state["agents"][a["key"]][k] for k in
                    ("status", "duration", "summary", "activity", "output")}} for a in AGENTS],

        "orchestrator_calls": list(state["orchestrator"]), "outcome": list(state.get("outcome", [])),
 
        "duration_s": finished[-1] if finished else None,
        "scenario_check": scenario_problems,
    }


def turn_markdown(turn, number=None):
    head = f"## Request {number}" if number else "## Nexus request"
    lines = [head, "", f"- **Submitted:** {turn['submitted_at']}  ", f"- **Mode:** {turn['mode']}"
             + (f" - scenario `{turn['scenario']}`" if turn["scenario"] else ""),
             f"- **Status:** {STATUS_LABEL.get(turn['status'], turn['status'])}"
             + (f" - request `{turn['request_id']}`" if turn["request_id"] else ""),
             f"- **Total time:** {turn['duration_s']} s" if turn["duration_s"] is not None else "",
             "", "### Query", "", "> " + turn["query"].replace("\n", "\n> "), "", "### Response", "",
             turn["response"] or "_(no customer message)_", ""]
    if turn["error"]:
        lines += [f"**Error:** `{turn['error'].get('code')}` - {turn['error'].get('message', '')}", ""]
    if turn["classification"]:
        c = turn["classification"]
        lines += ["### Outcome", "", f"- Classified: {c.get('product_line')} / {c.get('request_type')} / {c.get('urgency')}, "
                  f"escalation_category={c.get('escalation_category')}"]
        if turn["validation"]:
            lines.append(f"- Validation: confidence={turn['validation']['confidence']}, answers_question={turn['validation']['answers_question']}")
        if turn["escalation"]:
            e = turn["escalation"]
            lines.append(f"- Escalation: escalate={e['escalate']}, rule={e['rule']}, route={e['route_to'] or '-'}")
        lines.append("")
    lines += ["### Agent timeline", "", "| Agent | Model | Status | Time (s) | Summary |", "|---|---|---|---|---|"]
    for a in turn["agents"]:
        lines.append(f"| {a['agent']} | {a['model_tier']} | {a['status']} | {a['duration'] if a['duration'] is not None else '-'} | "
                     f"{(a['summary'] or '').replace('|', '/')} |")
    lines += ["", "### MCP tool calls (from the servers' own trace)", ""]
    lines += [f"{i}. {c['server']}.{c['tool']}" for i, c in enumerate(turn["mcp_calls"], 1)] or ["_(none)_"]
    if turn["scenario_check"] is not None:
        lines += ["", "### Scenario check", "", "PASS" if not turn["scenario_check"] else "FAIL: " + "; ".join(turn["scenario_check"])]
    return "\n".join(line for line in lines if line is not None) + "\n"


def turn_json(turn):
    return json.dumps(turn, indent=2, ensure_ascii=False)


def chat_markdown(turns):
    return "# Nexus concierge - chat export\n\n" + "\n---\n\n".join(turn_markdown(t, i) for i, t in enumerate(turns, 1))


def chat_json(turns):
    return json.dumps({"turns": turns}, indent=2, ensure_ascii=False)
