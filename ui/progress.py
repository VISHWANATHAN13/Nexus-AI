
"""Live progress of the five agents while handle_request() runs, observed from OUTSIDE the backend.

Three read-only event sources, active only inside the UI process and only while a request runs:
1. pass-through wrappers around agents.classify / retrieve / draft / handoff / validate / escalate (agent start / end);
2. pass-through wrappers around mcp_client.call_tool / read_resource / get_prompt (orchestrator MCP calls);
3. CrewAI's event bus (tool calls, LLM calls and guardrail retries inside an agent, attributed by agent role).
Every wrapper calls the original function with the original arguments and returns its result unchanged; the originals
are restored when the request ends. The pipeline's own result (steps, routing, escalation) stays the source of truth.
"""
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from config import load_yaml
from guardrails import mask_identifiers_in_text, redact_for_logging

AGENTS = [  # pipeline order; functions = the agents.py entry points that run this agent; icon = the card's badge
    {"key": "classifier", "label": "Classifier", "icon": "🧭", "functions": ["classify"]},
    {"key": "retriever", "label": "Retriever", "icon": "🔎", "functions": ["retrieve"]},
    {"key": "drafter", "label": "Drafter", "icon": "✍️", "functions": ["draft", "handoff"]},
    {"key": "validator", "label": "Validator", "icon": "🛡️", "functions": ["validate"]},
    {"key": "escalation", "label": "Escalation", "icon": "🚨", "functions": ["escalate"]},
]
STEP_AGENT = {"Classifier": "classifier", "Retriever": "retriever", "Drafting": "drafter", "Validation": "validator",
              "Escalation": "escalation"}  # result["steps"][i]["agent"] -> card
MCP_FUNCTIONS = ["call_tool", "read_resource", "get_prompt"]
FINISHED = ("done", "failed", "skipped")
RUN_LOCK = threading.Lock()  # the backend keeps per-request module state (QUARANTINED): one request at a time per process
_SINK = {"emit": None, "current": None}
_LISTENERS = {"registered": False}


def agent_meta():
    """Role and model tier of every agent, read from agents.yaml (so the UI never drifts from the backend)."""
    cfg = load_yaml("agents.yaml")
    return {a["key"]: {"role": cfg[a["key"]]["role"], "tier": cfg[a["key"]].get("tier", "")} for a in AGENTS}


def role_to_key():
    return {meta["role"]: key for key, meta in agent_meta().items()}


def new_state():
    """Per-request view model: one card per agent plus the orchestrator's own MCP calls."""
    return {"agents": {a["key"]: {"status": "waiting", "started": None, "duration": None, "runs": 0, "summary": "",
                                  "activity": [], "output": []} for a in AGENTS},
            "orchestrator": [], "outcome": [], "queued": False, "finished": False}


def _short(value, limit=90):
    text = str(value)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _row(label, value):
    """One output line; identifiers are masked with the backend's own guardrail (the UI never shows a raw account number)."""
    return {"label": label, "value": mask_identifiers_in_text(str(value))}


def _yes_no(value):
    return "yes" if value else "no"


def output_rows(key, value):
    """What an agent produced, as [{label, value}] rows for its card. `value` is the agent function's return value (live, per agent);
    outputs_from_result() later replaces it with the finished pipeline result, which is authoritative."""
    rows = []
    if key == "classifier" and isinstance(value, dict):
        rows = [_row("Product line", value.get("product_line", "")), _row("Request type", value.get("request_type", "")),
                _row("Urgency", value.get("urgency", "")), _row("Escalation category", value.get("escalation_category", "")),
                _row("Customer intent", value.get("customer_stated_intent", ""))]
    elif key == "retriever" and isinstance(value, dict):
        rows = [_row("Grounded facts", len(value.get("facts", [])))]
        rows += [_row(f"fact · {f['source'].replace('_server', '')}", f["text"]) for f in value.get("facts", [])[:6]]
        rows += [_row("⚠ conflict", f"{c.get('topic')}: live data says '{c.get('live_data')}', documents say '{c.get('document_claim')}' "
                                      "→ live data wins" if isinstance(c, dict) else c) for c in value.get("conflicts", [])]
        rows += [_row("gap", g) for g in value.get("gaps", [])[:3]]
        rows += [_row("quarantined", q["doc_id"]) for q in value.get("quarantined", [])]
    elif key == "drafter" and isinstance(value, str):
        rows = [_row("Draft", value)]
    elif key == "validator" and isinstance(value, dict):
        rows = [_row("Confidence", f"{value.get('confidence')}"), _row("Answers the question", _yes_no(value.get("answers_question"))),
                _row("Conflicts seen", ", ".join(map(str, value.get("conflicts_seen", []))) or "none"),
                _row("Issues", "; ".join([str(i) for i in value.get("issues", [])][:3]) or "none")]
    elif key == "escalation" and isinstance(value, dict):
        rows = [_row("Decision", "ESCALATE to a human" if value.get("escalate") else "No escalation"), _row("Rule", value.get("rule", "")),
                _row("Route", value.get("route_to") or "-"), _row("Agent agreed", value.get("agent_agreed", ""))]
    return rows


def outputs_from_result(result):
    """Authoritative per-agent output rows from the finished pipeline result (replaces the live ones where available)."""
    result = result or {}
    intake, context, validation = result.get("intake"), result.get("context"), result.get("validation")
    escalation, outputs = result.get("escalation"), {}
    if intake:
        outputs["classifier"] = output_rows("classifier", intake)
    elif result.get("error"):
        outputs["classifier"] = [_row("Rejected", f"{result['error'].get('code')}: {result['error'].get('message')}")]
    if context:
        outputs["retriever"] = output_rows("retriever", context)
    if result.get("draft"):
        unsent = result.get("status") == "escalated" and (escalation or {}).get("rule") != "hard_category"
        outputs["drafter"] = [_row("Draft (not sent)" if unsent else "Draft", result["draft"])]
    elif escalation and escalation.get("early_exit"):
        outputs["drafter"] = [_row("Handoff note", result.get("customer_message", ""))]
    if validation:
        outputs["validator"] = output_rows("validator", validation)
    if escalation:
        outputs["escalation"] = output_rows("escalation", escalation)
        if escalation.get("early_exit"):
            outputs["escalation"].append(_row("Early exit", escalation["early_exit"]))
    return outputs


def outcome_rows(result):
    """The orchestrator's final outcome rows."""
    result = result or {}
    delivery, rows = result.get("delivery"), [_row("Status", str(result.get("status", "")).upper())]
    if result.get("request_id"):
        rows.append(_row("Request", result["request_id"]))
    if delivery is not None:
        rows.append(_row("Delivery", "sent" if delivery.get("sent") else f"not sent ({delivery.get('reason') or (delivery.get('error') or {}).get('code', '')})"))
    return rows


def apply_event(state, event):
    """Fold one event into the view model (pure function: no Streamlit, no I/O)."""
    kind, key = event["type"], event.get("agent")
    card = state["agents"].get(key)
    if kind == "queued":
        state["queued"] = True
    elif kind == "run_start":
        state["queued"] = False
    elif kind == "agent_start" and card:
        card.update(status="running", started=event["t"], duration=None)
        card["runs"] += 1
        if card["runs"] > 1:
            card["activity"].append(f"▶ run {card['runs']}: {event.get('function', '')}")
    elif kind == "agent_end" and card:
        if event.get("output"):
            card["output"] = event["output"]
        card["status"] = "done" if event["ok"] else "failed"
        card["duration"] = round((card["duration"] or 0) + event["t"] - (card["started"] or event["t"]), 2)
        if not event["ok"]:
            card["activity"].append(f"✗ {_short(event.get('error', 'failed'))}")
    elif kind == "mcp":
        mark = "✓" if event["ok"] else f"✗ {event.get('code', '')}"
        line = f"🔌 {event['server'].replace('_server', '')}.{event['name']} {mark}"
        (card["activity"] if card else state["orchestrator"]).append(line)
    elif kind == "tool" and card:
        if event["phase"] == "start":
            card["activity"].append(f"🔧 {event['tool']}({_short(event.get('args', ''), 70)})")
        elif event["phase"] == "error":
            card["activity"].append(f"✗ {event['tool']}: {_short(event.get('error', ''))}")
        else:
            card["activity"].append(f"✓ {event['tool']} returned")
    elif kind == "llm" and card:
        card["activity"].append(f"🧠 LLM call ({event.get('model', '')})" if event["phase"] == "start"
                                else f"🧠 LLM replied{' (' + str(event['tokens']) + ' tokens)' if event.get('tokens') else ''}")
    elif kind == "guardrail" and card:
        card["activity"].append("🛡 guardrail passed" if event["success"]
                                else f"🛡 guardrail rejected the output -> retry {event.get('retry_count', '')}: {_short(event.get('error', ''))}")
    elif kind == "done":
        state["finished"] = True
    return state


def finalize(state, result):
    """After the run: summaries come verbatim from result['steps']; agents that never ran are marked skipped.
    A deterministic step without its agent (e.g. KYC not verified -> escalation) is shown as done by the pipeline."""
    for key, rows in outputs_from_result(result).items():
        if rows:
            state["agents"][key]["output"] = rows
    state["outcome"] = outcome_rows(result)
    for step in (result or {}).get("steps", []):
        key = STEP_AGENT.get(step["agent"])
        if key:
            card = state["agents"][key]
            card["summary"] = step["detail"] if not card["summary"] else f"{card['summary']} | {step['detail']}"
            if card["status"] == "waiting":
                card["status"] = "done"
                card["activity"].append("⚙ decided by the pipeline (no agent call needed)")
    for card in state["agents"].values():
        if card["status"] == "running":
            card["status"] = "failed"
        elif card["status"] == "waiting":
            card["status"] = "skipped"
    state["finished"] = True
    return state


def _emit(event):
    sink = _SINK["emit"]
    if sink is not None:
        sink(event)


def _wrap_agent(key, name, original):
    def wrapper(*args, **kwargs):
        _SINK["current"] = key
        _emit({"type": "agent_start", "agent": key, "function": name})
        try:
            value = original(*args, **kwargs)
        except Exception as exc:
            _emit({"type": "agent_end", "agent": key, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            raise
        finally:
            _SINK["current"] = None
        _emit({"type": "agent_end", "agent": key, "ok": True, "output": output_rows(key, value)})
        return value
    wrapper.__wrapped__ = original
    return wrapper


def _wrap_mcp(function, original):
    def wrapper(server, name, *args, **kwargs):
        value = original(server, name, *args, **kwargs)
        owner = "escalation" if name == "run_escalation_check" else _SINK["current"]  # the binding escalation check belongs on that card
        ok = bool(isinstance(value, dict) and value.get("ok"))
        code = "" if ok or not isinstance(value, dict) else (value.get("error") or {}).get("code", "")
        label = name.split("://")[0] + "://" if function == "read_resource" else name
        _emit({"type": "mcp", "agent": owner, "server": server, "name": label, "function": function, "ok": ok, "code": code})
        return value
    wrapper.__wrapped__ = original
    return wrapper


@contextmanager
def instrument(emit):
    """Install the pass-through wrappers for the duration of one request; always restores the originals."""
    from nexus_orchestrator import agents, mcp_client
    saved = []
    for spec in AGENTS:
        for name in spec["functions"]:
            saved.append((agents, name, getattr(agents, name)))
            setattr(agents, name, _wrap_agent(spec["key"], name, getattr(agents, name)))
    for name in MCP_FUNCTIONS:
        saved.append((mcp_client, name, getattr(mcp_client, name)))
        setattr(mcp_client, name, _wrap_mcp(name, getattr(mcp_client, name)))
    _SINK["emit"], _SINK["current"] = emit, None
    try:
        yield
    finally:
        _SINK["emit"], _SINK["current"] = None, None
        for module, name, original in reversed(saved):
            setattr(module, name, original)


def _agent_of(event):
    return role_to_key().get(getattr(event, "agent_role", None) or "") or _SINK["current"]


def on_tool_started(source, event):
    _emit({"type": "tool", "phase": "start", "agent": _agent_of(event), "tool": event.tool_name,
           "args": redact_for_logging(getattr(event, "tool_args", "") or "")})


def on_tool_finished(source, event):
    _emit({"type": "tool", "phase": "end", "agent": _agent_of(event), "tool": event.tool_name})


def on_tool_error(source, event):
    _emit({"type": "tool", "phase": "error", "agent": _agent_of(event), "tool": event.tool_name,
           "error": redact_for_logging(str(getattr(event, "error", "")))})


def on_llm_started(source, event):
    _emit({"type": "llm", "phase": "start", "agent": _agent_of(event), "model": getattr(event, "model", "") or ""})


def on_llm_completed(source, event):
    usage = getattr(event, "usage", None) or {}
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else getattr(usage, "total_tokens", None)
    _emit({"type": "llm", "phase": "end", "agent": _agent_of(event), "tokens": tokens})


def on_guardrail_completed(source, event):
    _emit({"type": "guardrail", "agent": _agent_of(event), "success": bool(getattr(event, "success", False)),
           "retry_count": getattr(event, "retry_count", ""), "error": redact_for_logging(str(getattr(event, "error", "") or ""))})


def register_crewai_listeners():
    """Subscribe once per process to CrewAI's event bus. Without crewai (demo/tests) this is a no-op."""
    if _LISTENERS["registered"]:
        return True
    try:
        from crewai.events import crewai_event_bus
        from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallStartedEvent
        from crewai.events.types.llm_guardrail_events import LLMGuardrailCompletedEvent
        from crewai.events.types.tool_usage_events import ToolUsageErrorEvent, ToolUsageFinishedEvent, ToolUsageStartedEvent
    except ImportError:
        return False
    for event_type, handler in [(ToolUsageStartedEvent, on_tool_started), (ToolUsageFinishedEvent, on_tool_finished),
                                (ToolUsageErrorEvent, on_tool_error), (LLMCallStartedEvent, on_llm_started),
                                (LLMCallCompletedEvent, on_llm_completed), (LLMGuardrailCompletedEvent, on_guardrail_completed)]:
        crewai_event_bus.on(event_type)(handler)
    _LISTENERS["registered"] = True
    return True


def new_job(text, scenario=None, mode="live"):
    """One request. Events are appended by the worker thread and read by the page (list.append is thread-safe)."""
    return {"id": uuid.uuid4().hex[:8], "text": text, "scenario": scenario, "mode": mode, "events": [], "done": False,
            "result": None, "error": None, "t0": time.perf_counter(),
            "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def run_job(job):
    """Run the request (blocking) with instrumentation; used by the worker thread and directly by tests."""
    def emit(event):
        job["events"].append({**event, "t": round(time.perf_counter() - job["t0"], 2)})

    if not RUN_LOCK.acquire(blocking=False):
        emit({"type": "queued"})
        RUN_LOCK.acquire()
    saved = None
    try:
        if job["mode"] == "demo":
            from ui import scripted_agents
            saved = scripted_agents.install()
        else:
            register_crewai_listeners()
        with instrument(emit):
            emit({"type": "run_start"})
            if job["scenario"]:
                import scenario_runner
                job["result"] = scenario_runner.run_scenario(job["scenario"])
            else:
                from nexus_orchestrator.pipeline import handle_request
                job["result"] = handle_request(job["text"])
    except Exception as exc:  # handle_request already turns pipeline failures into typed errors; this is a last resort
        job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if saved is not None:
            from ui import scripted_agents
            scripted_agents.uninstall(saved)
        RUN_LOCK.release()
        emit({"type": "done"})
        job["done"] = True
    return job


def start_job(job):
    threading.Thread(target=run_job, args=(job,), name=f"nexus-{job['id']}", daemon=True).start()
    return job


def state_of(job):
    """Replay all events received so far into a fresh view model (cheap: a request produces at most a few hundred)."""
    state = new_state()
    for event in list(job["events"]):
        apply_event(state, event)
    if job["done"]:
        finalize(state, job["result"])
    return state