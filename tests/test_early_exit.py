"""Hard escalation categories survive every early exit, and the rule-based conflict detector works without the Retriever LLM.

Real pipeline + scripted agents (no OpenAI). Needs pydantic (skips itself otherwise)."""

import json
 
import conftest  # noqa: F401  (test database + environment)

import database

from helpers import need
 
HARDSHIP = "I lost my job and can't pay my insurance premium any more."
 
 
def run_text(text, **env):

    need("pydantic")

    import os

    from nexus_orchestrator.pipeline import handle_request

    from ui import scripted_agents

    saved = scripted_agents.install()

    os.environ.update(env)

    try:

        return handle_request(text)

    finally:

        for key in env:

            os.environ.pop(key, None)

        scripted_agents.uninstall(saved)
 
 
def flags():

    with database.db("concierge_ops_server") as conn:

        return database.fetch_all(conn, "SELECT customer_id, request_category, hard_escalation, status FROM escalation_flags")
 
 
def called(result):

    return {c["server"] for c in result["routing"]}
 
 
def test_hard_category_with_kyc_pending_escalates_to_the_category_team():

    before = len(flags())

    result = run_text(f"I am CUS-00005. {HARDSHIP}")  # verified record, KYC pending

    assert result["status"] == "escalated" and result["escalation"]["rule"] == "hard_category"

    assert result["escalation"]["route_to"] == "financial_support_team" and result["escalation"]["early_exit"] == "kyc_not_verified"

    assert "banking_server" not in called(result) and "insurance_server" not in called(result)  # nothing retrieved

    assert result["delivery"]["sent"] is False and "verifying your identity" in result["customer_message"]

    new = flags()[before:]

    assert len(new) == 1 and new[0]["request_category"] == "financial_hardship" and new[0]["hard_escalation"] == 1
 
 
def test_hard_category_citing_another_customers_account_still_escalates_and_leaks_nothing():

    result = run_text(f"I am CUS-10004. {HARDSHIP} My account is ACC-20077.")

    assert result["status"] == "escalated" and result["escalation"]["rule"] == "hard_category"

    assert result["escalation"]["early_exit"] == "reference_check_ownership_mismatch" and result["context"] is None

    assert "20077" not in result["customer_message"] and "Arjun" not in result["customer_message"]
 
 
def test_safeguarding_early_exit_sends_no_message_to_the_customer():

    result = run_text("I am CUS-10004. My son is forcing me to hand over my pension. My account is ACC-20077.")

    assert result["status"] == "escalated" and result["escalation"]["route_to"] == "safeguarding_team" and result["delivery"]["sent"] is False
 
 
def test_hard_category_survives_a_server_outage_and_an_agent_failure():

    result = run_text(f"I am CUS-10004. {HARDSHIP} My policy is POL-IN-10002.", NEXUS_OFFLINE="insurance_server")  # caught by the ownership check

    assert result["status"] == "escalated" and result["escalation"]["early_exit"] == "reference_check_server_unavailable"

    result = run_text(f"I am CUS-10004. {HARDSHIP} It is my policy premium.", NEXUS_OFFLINE="insurance_server")  # caught when the servers start

    assert result["status"] == "escalated" and result["escalation"]["early_exit"] == "server_unavailable"

    need("pydantic")

    from nexus_orchestrator import agents

    from nexus_orchestrator.pipeline import handle_request

    from ui import scripted_agents

    saved = scripted_agents.install()

    original = agents.retrieve

    agents.retrieve = lambda values, tools: (_ for _ in ()).throw(RuntimeError("model API down"))

    try:

        result = handle_request(f"I am CUS-10004. {HARDSHIP}")

        routine = handle_request("I am CUS-20077 and my account is ACC-20077. What is my current balance?")

    finally:

        agents.retrieve = original

        scripted_agents.uninstall(saved)

    assert result["status"] == "escalated" and result["escalation"]["early_exit"] == "agent_failure"

    assert routine["status"] == "error" and routine["error"]["code"] == "AGENT_FAILURE"  # a non-hard request still fails closed
 
 
def test_unidentified_and_unknown_customers_stay_typed_errors_with_no_tool_call():

    missing = run_text(f"{HARDSHIP}")

    assert missing["status"] == "rejected" and missing["error"]["code"] == "MISSING_CUSTOMER_ID" and missing["routing"] == []

    assert "customer support team" in missing["customer_message"] and "CUS-12345" in missing["customer_message"]

    ordinary = run_text("What is my balance?")

    assert "customer support team" not in ordinary["customer_message"]  # only hard-category wording gets the extra sentence

    unknown = run_text(f"I am CUS-99999. {HARDSHIP}")

    assert unknown["status"] == "rejected" and unknown["error"]["code"] == "CUSTOMER_NOT_FOUND" and "customer support team" in unknown["customer_message"]

    assert [c["tool"] for c in unknown["routing"]] == ["get_customer_kyc_status"]
 
 
def test_rule_based_conflict_detector_needs_no_retriever_llm():

    need("pydantic")

    from nexus_orchestrator.pipeline import detect_conflicts, handle_request

    from ui import scripted_agents

    restricted = {"account_status": "restricted", "auto_debit_enabled": False, "auto_debit_restriction_reason": "Temporary compliance restriction"}

    permit = ["Yes, auto-debit is available for insurance premiums."]

    found = detect_conflicts({"account": restricted}, permit, [])

    assert len(found) == 1 and found[0]["topic"] == "auto-debit" and found[0]["detected_by"] == "rule" and "restricted" in found[0]["live_data"]

    assert found[0]["source"] == "banking_server"

    assert detect_conflicts({"account": restricted}, permit, [{"topic": "auto debit"}]) == []  # the Retriever already reported it

    active = {"account_status": "active", "auto_debit_enabled": True, "auto_debit_restriction_reason": None}

    switched_off = {"account_status": "active", "auto_debit_enabled": False, "auto_debit_restriction_reason": None}

    assert detect_conflicts({"account": active}, permit, []) == []  # showcase: no conflict

    assert detect_conflicts({"account": switched_off}, permit, []) == []  # merely not set up is not a restriction

    assert detect_conflicts({"account": restricted}, ["Auto-debit is not permitted for restricted accounts."], []) == []  # document agrees

    assert detect_conflicts({"account": restricted}, ["Card replacement takes five working days."], []) == []
 
    original = scripted_agents.scripted_retrieve  # an LLM Retriever that reports NO conflict at all

    scripted_agents.scripted_retrieve = lambda values: json.dumps({**json.loads(original(values)), "conflicts": []})

    saved = scripted_agents.install()

    try:

        result = handle_request("I am CUS-10002. My account is ACC-10002 and my policy is POL-IN-10002. Can I set up auto-debit for my premium?")

    finally:

        scripted_agents.uninstall(saved)

        scripted_agents.scripted_retrieve = original

    assert [c["detected_by"] for c in result["context"]["conflicts"]] == ["rule"] and result["validation"]["conflicts_seen"] == ["auto-debit"]
 