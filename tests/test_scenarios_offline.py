"""Every scenario in demo_scenarios.yaml (+ stress test 3) through the REAL pipeline with the SCRIPTED agents
(ui/scripted_agents.py: no OpenAI, no CrewAI, in-process tool calls). The scenario's own `expect` block is judged by the
real scenario_runner.judge, then each test adds the stricter property checks. Always runs - no API key needed."""
import tempfile
from pathlib import Path

import conftest  # noqa: F401  (test database + environment)
import database
from helpers import need


def run(name, **switches):
    """Run one named scenario; switches = env test switches (e.g. NEXUS_FORCE_CONFIDENCE). Returns (result, judge problems)."""
    need("pydantic")
    import os
    import scenario_runner
    from ui import scripted_agents
    scenario = scenario_runner.find_scenario(name)
    assert scenario, f"scenario {name} missing from demo_scenarios.yaml"
    saved = scripted_agents.install()
    for key, value in switches.items():
        os.environ[key] = value
    try:
        result = scenario_runner.run_scenario(scenario)
    finally:
        for key in switches:
            os.environ.pop(key, None)
        scripted_agents.uninstall(saved)
    return result, scenario_runner.judge(scenario, result)


def passes(name, **switches):
    result, problems = run(name, **switches)
    assert problems == [], f"{name}: {problems}"
    return result


def audit_actions():
    with database.db("concierge_ops_server") as conn:
        return [r["action_type"] for r in database.fetch_all(conn, "SELECT action_type FROM audit_log ORDER BY audit_id")]


def called(result):
    return {c["server"] for c in result["routing"]}


def test_showcase_flows_through_all_layers_and_resolves_without_a_human():
    result = passes("showcase")
    assert result["status"] == "resolved" and result["intake"]["product_line"] == "cross_product"  # settled by the ids, not the model
    assert {"banking_server", "insurance_server", "concierge_ops_server"} <= called(result)
    assert result["escalation"]["escalate"] is False and result["validation"]["confidence"] >= 0.9
    assert [s["agent"] for s in result["steps"]][:1] == ["Classifier"]
    assert {"Classifier", "Retriever", "Drafting", "Validation", "Escalation"} <= {s["agent"] for s in result["steps"]}
    assert result["customer_message"].startswith("Hello Arjun") and "ACC-20077" not in result["customer_message"]
    assert result["delivery"]["sent"] is True and {"notification_sent", "request_resolved"} <= set(audit_actions())
    assert result["intake"]["request_id"].startswith("REQ-")


def test_routine_balance_claim_and_portfolio_route_to_the_right_server():
    for name, server, unwanted in [("routine_balance", "banking_server", "wealth_server"),
                                   ("claim_status", "insurance_server", "banking_server"),
                                   ("portfolio", "wealth_server", "insurance_server")]:
        result = passes(name)
        assert result["status"] == "resolved" and server in called(result) and unwanted not in called(result), name
        assert all(f["source"] == server for f in result["context"]["facts"]), name


def test_low_confidence_sends_a_handoff_not_the_unsupported_answer():
    result = passes("low_confidence")
    assert result["validation"]["answers_question"] is False and result["validation"]["confidence"] <= 0.5  # capped although the model said 0.95
    assert result["customer_message"].startswith("Hello Arjun") and "specialist" in result["customer_message"]
    assert result["draft"] and "specialist" not in result["draft"]  # the unsupported draft is kept as evidence but never sent
    assert "request_escalated" in audit_actions()


def test_financial_hardship_escalates_even_rephrased_and_at_forced_high_confidence():
    for name, switches in [("hardship", {}), ("hardship_rephrased", {}), ("hardship_rephrased", {"NEXUS_FORCE_CONFIDENCE": "0.99"})]:
        result = passes(name, **switches)
        assert result["intake"]["escalation_category"] == "financial_hardship", name  # the scripted model said 'none'
        assert result["escalation"]["route_to"] == "financial_support_team" and "specialist" in result["customer_message"]
    assert result["validation"]["confidence"] == 0.99  # stress test 3: the hard rule ignores confidence


def test_safeguarding_and_compliance_override_escalate_and_safeguarding_sends_nothing():
    result = passes("safeguarding")
    assert result["escalation"]["route_to"] == "safeguarding_team" and result["delivery"]["sent"] is False
    result = passes("compliance_override")
    assert result["intake"]["escalation_category"] == "compliance_override_request"
    assert result["escalation"]["route_to"] == "compliance_team" and result["delivery"]["sent"] is True


def test_prompt_injection_in_a_retrieved_document_is_quarantined_and_audited():
    need("langchain_community", "langchain_text_splitters", "langchain_chroma", "langchain_core", "chromadb")
    import knowledge
    from langchain_core.embeddings import DeterministicFakeEmbedding
    from ui import scripted_agents
    root = Path(tempfile.mkdtemp())
    (root / "faqs").mkdir()
    poisoned = Path(conftest.ROOT, "demo_assets", "poisoned_docs", "faq_fee_waivers_poisoned.md")  # the project's own demo document
    (root / "faqs" / poisoned.name).write_text(poisoned.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "faqs" / "faq_cards.md").write_text("Card replacement takes five working days.", encoding="utf-8")
    saved = (knowledge.knowledge_folders, knowledge.index_path, knowledge.make_embeddings)
    knowledge.knowledge_folders = lambda include_demo=False: [("faqs", root / "faqs")]
    knowledge.index_path = lambda include_demo=False: root / "chroma"
    knowledge.make_embeddings = lambda: DeterministicFakeEmbedding(size=64)
    scripted_agents.OPTIONS["use_knowledge"] = True
    try:
        knowledge.build_index()
        result = passes("injection_in_document")
    finally:
        scripted_agents.OPTIONS["use_knowledge"] = False
        knowledge.knowledge_folders, knowledge.index_path, knowledge.make_embeddings = saved
    assert [q["doc_id"] for q in result["context"]["quarantined"]] == [poisoned.stem]
    assert all("ignore" not in f["text"].lower() for f in result["context"]["facts"])
    assert result["status"] in ("resolved", "escalated") and "injection_blocked" in audit_actions()  # the request carries on


def test_prompt_injection_typed_by_the_customer_is_blocked_at_intake():
    result = passes("injection_in_text")
    assert result["routing"] == [] and result["intake"] is None


def test_stress_1_missing_malformed_unknown_customer_and_unverified_kyc():
    for name in ("missing_customer", "malformed_customer"):
        result = passes(name)
        assert result["routing"] == [] and "CUS-12345" in result["customer_message"], name  # told WHAT is missing / wrong
    result = passes("unknown_customer")
    assert [c["tool"] for c in result["routing"]] == ["get_customer_kyc_status"]  # stops after the single lookup
    need("pydantic")
    from ui import scripted_agents
    from nexus_orchestrator.pipeline import handle_request
    saved = scripted_agents.install()
    try:
        result = handle_request("I am CUS-00005. What is my balance?")  # verified customer record, KYC still pending
    finally:
        scripted_agents.uninstall(saved)
    assert result["status"] == "escalated" and result["escalation"]["rule"] == "kyc_not_verified" and "banking_server" not in called(result)


def test_stress_2_retrieval_conflict_is_surfaced_and_live_data_is_used():
    import scenario_runner
    result = passes("retrieval_conflict")
    assert len(result["context"]["conflicts"]) == 1 and result["validation"]["conflicts_seen"] == ["auto-debit"]
    assert any("not allowed" in f["text"] for f in result["context"]["facts"])
    assert scenario_runner.judge({"expect": {"status_in": ["resolved"]}}, {**result, "status": "escalated"})  # status_in is enforced


def test_stress_4_a_server_that_is_down_gives_a_typed_error_and_no_answer():
    result = passes("server_offline")
    assert result["error"]["details"]["server"] == "insurance_server" and result["validation"] is None and result["draft"] is None
    assert "theft" not in result["customer_message"].lower()


def test_another_customers_account_is_refused():
    result = passes("ownership")
    assert "restricted" not in result["customer_message"] and result["context"] is None
