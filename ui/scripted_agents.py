"""A SCRIPTED stand-in for the LLM (no OpenAI, no CrewAI, no subprocesses), shared by the offline tests and the UI's demo mode.

The pipeline's single seam to the model is agents.run_task. install() replaces it with a small script that behaves like
sensible agents (it really calls the tool functions, so the routing evidence in the trace is genuine) and serves MCP calls
in-process. Everything else is the real code: intake validation, KYC/ownership checks, fact verification, guardrail
functions, escalation decision, delivery, audit. uninstall() restores the originals.
"""
import importlib
import json
import types
from contextlib import contextmanager

SERVERS = {"banking_server": "banking_server.tools", "insurance_server": "insurance_server.tools",
           "wealth_server": "wealth_server.tools", "concierge_ops_server": "concierge_ops_server.tools"}
OPTIONS = {"use_knowledge": False}  # True: the scripted Retriever also runs the real knowledge search (quarantine path)


def module_for(server):
    return importlib.import_module(SERVERS[server])


def fake_call_tool(server, tool, arguments):
    from nexus_orchestrator import mcp_client
    if server in mcp_client.offline_servers():
        return {"ok": False, "error": {"code": "SERVER_UNAVAILABLE", "message": f"{server} is unavailable", "details": {"server": server}}}
    return json.loads(json.dumps(module_for(server).TOOLS[tool](**arguments)))


def fake_read_resource(server, uri):
    scheme, rest = uri.split("://")
    return json.loads(module_for(server).RESOURCES[scheme](rest.split("/")[0]))


def fake_get_prompt(server, name, arguments):
    return {"ok": True, "data": module_for(server).PROMPTS[name](**arguments)}


def fake_list_primitives(server):
    from nexus_orchestrator import mcp_client
    if server in mcp_client.offline_servers():
        return fake_call_tool(server, "x", {})
    return {"ok": True, "data": {}}


def call(server, tool, **arguments):
    """A scripted agent's tool call; reported to the UI the same way CrewAI reports a real agent's tool use."""
    from ui import progress
    progress.on_tool_started(None, types.SimpleNamespace(agent_role=None, tool_name=tool, tool_args=arguments))
    value = module_for(server).TOOLS[tool](**arguments)
    progress.on_tool_finished(None, types.SimpleNamespace(agent_role=None, tool_name=tool))
    return value


def scripted_run_task(agent_name, task_name, values, tools=None, output_pydantic=None, guardrail=None):
    handlers = {"classify": scripted_classify, "retrieve": scripted_retrieve, "draft": scripted_draft, "handoff": scripted_handoff,
                "validate": scripted_validate, "escalate": scripted_escalate, "screen": scripted_screen}
    raw = handlers[task_name](values)
    output = types.SimpleNamespace(raw=raw, pydantic=None)
    if guardrail is not None:  # the real guardrail functions judge the scripted output, exactly as CrewAI would
        accepted, detail = guardrail(output)
        if not accepted:
            raise AssertionError(f"{task_name} guardrail rejected the scripted output: {detail}")
    return output


def scripted_classify(values):
    text = values["customer_text"].lower()
    line = "insurance" if ("policy" in text or "claim" in text) else "wealth" if "portfolio" in text else "banking"  # a weak model: never says cross_product
    kind = "auto_debit_setup" if "auto-debit" in text else "claim_status" if "claim" in text else "balance_inquiry"
    return json.dumps({"product_line": line, "request_type": kind, "urgency": "routine",
                       "customer_stated_intent": "a customer request", "escalation_category": "none"})  # the model 'misses' hardship


def scripted_screen(values):
    return json.dumps({"escalation_category": "none", "reason": "scripted screener: sees nothing"})  # also 'misses' hardship: the phrase scan must catch it


def scripted_retrieve(values):
    refs, facts, conflicts, gaps = json.loads(values["references"]), [], [], []
    if OPTIONS["use_knowledge"]:
        from nexus_orchestrator.tools import knowledge_passages
        for passage in knowledge_passages(values["customer_text"])["passages"][:1]:
            facts.append({"source": passage["source"], "text": passage["text"][:200]})
    if "account" in refs:
        a = call("banking_server", "get_account_summary", account_id=refs["account"], caller_scope="service")["data"]["account"]
        facts.append({"source": "banking_server", "text": f"Account ending {a['account_number'][-4:]} is {a['account_status']}; "
                      f"auto-debit {'allowed' if a['auto_debit_enabled'] else 'not allowed'}; balance {a['balance']}"})
    if "policy" in refs:
        p = call("insurance_server", "get_policy_details", policy_id=refs["policy"])["data"]["policy"]
        c = call("insurance_server", "check_coverage_clause", policy_id=refs["policy"], scenario_tag="auto_debit")["data"]
        facts.append({"source": "insurance_server", "text": f"Policy ending {p['policy_number'][-4:]} auto_debit clause: {c['result']}"})
    if "claim" in refs:
        c = call("insurance_server", "get_claim_status", claim_id=refs["claim"])["data"]["claim"]
        facts.append({"source": "insurance_server", "text": f"Claim status {c['claim_status']}"})
    if "portfolio" in refs:
        p = call("wealth_server", "get_portfolio_summary", portfolio_id=refs["portfolio"], caller_scope="service")["data"]["portfolio"]
        facts.append({"source": "wealth_server", "text": f"Portfolio value {p['total_value']} {p['currency']}"})
    if "auto-debit" in values["customer_text"].lower():
        facts.append({"source": "kb:payment_terms", "text": "Documents say auto-debit is permitted for insurance premiums."})
        if any("not allowed" in f["text"] for f in facts):
            conflicts.append({"topic": "auto-debit", "live_data": "account does not allow auto-debit", "document_claim": "permitted"})
    if not facts:
        gaps.append("no reference was provided and the knowledge base has nothing relevant")
    return json.dumps({"facts": facts, "conflicts": conflicts, "gaps": gaps})


def scripted_draft(values):
    facts = json.loads(values["facts"])
    live = [f["text"] for f in facts if not f["source"].startswith("kb:")]
    return f"Hello {values['customer_name']}, here is what we found: " + "; ".join(live) + "."


def scripted_handoff(values):
    return f"Hello {values['customer_name']}, a specialist will review your request and contact you."


def scripted_validate(values):
    """An OVER-CONFIDENT validator: always 0.95, but honest about whether the draft answers the question."""
    facts, conflicts = json.loads(values["facts"]), json.loads(values["conflicts"])
    answers = any(not f["source"].startswith("kb:") for f in facts)
    return json.dumps({"confidence": 0.95, "answers_question": answers, "issues": [], "conflicts_seen": [c["topic"] for c in conflicts]})


def scripted_escalate(values):
    return json.dumps(call("concierge_ops_server", "run_escalation_check", customer_id=values["customer_id"],
                           request_category=values["category"], draft_confidence=float(values["confidence"]))["data"])


@contextmanager
def no_live_tools(*args, **kwargs):
    yield []


def install():
    """Swap in the scripted agents and in-process MCP calls. Returns what uninstall() needs to restore."""
    from nexus_orchestrator import agents, mcp_client, pipeline
    saved = (agents.run_task, mcp_client.call_tool, mcp_client.read_resource, mcp_client.get_prompt, mcp_client.list_primitives,
             pipeline.mcp_tools, pipeline.make_search_tool)
    agents.run_task = scripted_run_task
    mcp_client.call_tool, mcp_client.read_resource = fake_call_tool, fake_read_resource
    mcp_client.get_prompt, mcp_client.list_primitives = fake_get_prompt, fake_list_primitives
    pipeline.mcp_tools, pipeline.make_search_tool = no_live_tools, lambda: None
    return saved


def uninstall(saved):
    from nexus_orchestrator import agents, mcp_client, pipeline
    (agents.run_task, mcp_client.call_tool, mcp_client.read_resource, mcp_client.get_prompt, mcp_client.list_primitives,
     pipeline.mcp_tools, pipeline.make_search_tool) = saved
