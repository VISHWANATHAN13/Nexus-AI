"""The Streamlit UI: live agent progress (ui/progress.py), downloads (ui/export.py) and the page itself (AppTest).
Runs in demo mode (scripted agents), so no API key is needed."""
import json
import time
import types

import conftest
from helpers import need

ORDER = ["classifier", "retriever", "drafter", "validator", "escalation"]
SHOWCASE = ("I am CUS-20077. My savings account is ACC-20077 and my policy is POL-IN-30091. "
            "Can I set up auto-debit for my insurance premium from my savings account?")
BALANCE = "I am CUS-20077 and my account is ACC-20077. What is my current balance?"


def demo_job(text):
    from ui import progress
    return progress.run_job(progress.new_job(text, None, "demo"))


def test_ui_streams_all_five_agents_in_order_and_leaves_the_backend_untouched():
    need("pydantic")
    from nexus_orchestrator import agents, mcp_client
    from ui import progress
    originals = {name: getattr(agents, name) for name in ("classify", "retrieve", "draft", "handoff", "validate", "escalate")}
    original_call = mcp_client.call_tool

    job = progress.start_job(progress.new_job(SHOWCASE, None, "demo"))  # worker thread, exactly as the page uses it
    deadline = time.time() + 120
    while not job["done"] and time.time() < deadline:
        time.sleep(0.05)
    assert job["done"] and job["error"] is None and job["result"]["status"] == "resolved"
    events = job["events"]
    starts = [e["agent"] for e in events if e["type"] == "agent_start"]
    assert starts == ORDER
    for key in ORDER:
        start = next(i for i, e in enumerate(events) if e["type"] == "agent_start" and e["agent"] == key)
        end = next(i for i, e in enumerate(events) if e["type"] == "agent_end" and e["agent"] == key)
        assert start < end and events[end]["ok"] and events[end]["t"] >= events[start]["t"]
    mcp = [(e["agent"], e["name"]) for e in events if e["type"] == "mcp"]
    assert (None, "get_customer_kyc_status") in mcp and ("escalation", "run_escalation_check") in mcp  # the binding check
    state = progress.state_of(job)
    details = {s["agent"]: s["detail"] for s in job["result"]["steps"]}
    for key, step in [("classifier", "Classifier"), ("retriever", "Retriever"), ("drafter", "Drafting"),
                      ("validator", "Validation"), ("escalation", "Escalation")]:
        card = state["agents"][key]
        assert card["status"] == "done" and card["duration"] is not None and details[step] in card["summary"]
    assert any("get_customer_kyc_status" in line for line in state["orchestrator"])
    assert all(getattr(agents, n) is f for n, f in originals.items()) and mcp_client.call_tool is original_call  # restored

    rejected = progress.state_of(demo_job("What is my balance?"))  # stopped at intake: no agent ran
    assert {card["status"] for card in rejected["agents"].values()} == {"skipped"}
    pending = progress.state_of(demo_job("I am CUS-00005. What is my balance?"))  # KYC not verified
    assert pending["agents"]["classifier"]["status"] == "done" and pending["agents"]["retriever"]["status"] == "skipped"
    assert pending["agents"]["escalation"]["status"] == "done" and "KYC not verified" in pending["agents"]["escalation"]["summary"]

    seen = []  # CrewAI event-bus handlers (live mode): attributed by agent role, arguments masked
    progress._SINK["emit"] = seen.append
    try:
        progress.on_tool_started(None, types.SimpleNamespace(agent_role="Grounding Retriever", tool_name="get_account_summary",
                                                             tool_args={"account_id": "ACC-20077", "caller_scope": "service"}))
        progress.on_guardrail_completed(None, types.SimpleNamespace(agent_role="Compliance Validator", success=False,
                                                                    retry_count=1, error="confidence missing"))
        progress.on_llm_completed(None, types.SimpleNamespace(agent_role="Reply Drafter", usage={"total_tokens": 321}))
    finally:
        progress._SINK["emit"] = None
    assert [e["agent"] for e in seen] == ["retriever", "validator", "drafter"] and "ACC-20077" not in json.dumps(seen)
    state = progress.new_state()
    for event in seen:
        progress.apply_event(state, {**event, "t": 0})
    assert "get_account_summary" in state["agents"]["retriever"]["activity"][0]
    assert "retry 1" in state["agents"]["validator"]["activity"][0] and "321 tokens" in state["agents"]["drafter"]["activity"][0]


def test_ui_downloads_are_masked_and_the_page_streams_a_request_into_the_chat():
    need("pydantic", "streamlit")
    from ui import export, progress
    job = demo_job(BALANCE)
    turn = export.build_turn(job, progress.state_of(job), [])
    md, js = export.turn_markdown(turn, 1), export.turn_json(turn)
    for blob in (md, js):
        assert "ACC-20077" not in blob and "****0077" in blob and "CUS-20077" in blob
    assert "### Query" in md and "### Response" in md and "Scenario check" in md and "PASS" in md
    assert all(f"| {label} |" in md for label in ("Classifier", "Retriever", "Drafter", "Validator", "Escalation"))
    assert "banking_server.get_account_summary" in md
    data = json.loads(js)
    assert data["status"] == "resolved" and data["response"].startswith("Hello Arjun") and len(data["agents"]) == 5
    assert data["mcp_calls"] and data["escalation"]["escalate"] is False and data["duration_s"] is not None
    chat_md, chat_js = export.chat_markdown([turn, turn]), json.loads(export.chat_json([turn, turn]))
    assert "## Request 2" in chat_md and len(chat_js["turns"]) == 2

    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(conftest.ROOT / "streamlit_app.py"), default_timeout=180)
    app.run()
    assert not app.exception
    app.sidebar.radio[0].set_value("Demo (scripted agents, no OpenAI)").run()
    app.chat_input[0].set_value(BALANCE).run()
    assert not app.exception
    page = " ".join(m.value for m in app.markdown)
    assert "RESOLVED" in page and "Hello Arjun" in page and "ACC-20077" not in page.replace(BALANCE, "")
    labels = [b.label for b in app.get("download_button")]
    assert labels.count("⬇ Markdown") == 1 and labels.count("⬇ JSON") == 1 and "⬇ Whole chat (Markdown)" in labels
