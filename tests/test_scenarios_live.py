"""End-to-end through the real CrewAI agents + real MCP server processes + OpenAI: every scenario in demo_scenarios.yaml
(+ stress test 3). Marked `live`; skipped without OPENAI_API_KEY. Run only these:  pytest -m live
Agents are LLM-driven, so these judge PROPERTIES (status, servers called, rule, no leaks), never exact wording. Each test runs
a group of scenarios and reports every failing scenario at once."""
import os
import unittest

import pytest

import conftest
from helpers import need

pytestmark = pytest.mark.live


def runner():
    need("crewai", "crewai_tools", "pydantic", "langchain_chroma", "mcp.client.stdio")
    from config import setup_environment
    setup_environment()  # loads OPENAI_API_KEY from .env, exactly as the app does
    if conftest.USING_STUB or not os.environ.get("OPENAI_API_KEY"):
        raise unittest.SkipTest("needs OPENAI_API_KEY and the real mcp package")
    import scenario_runner
    return scenario_runner


def check_all(*names):
    scenario_runner = runner()
    failures = {}
    for name in names:
        scenario = scenario_runner.find_scenario(name)
        if scenario_runner.demo_index_missing(scenario):
            failures[name] = ["demo index missing: run  python build_index.py --demo"]
            continue
        problems = scenario_runner.judge(scenario, scenario_runner.run_scenario(scenario))
        if problems:
            failures[name] = problems
    assert not failures, failures


def test_live_showcase_and_routine_requests_resolve_without_a_human():
    check_all("showcase", "routine_balance", "claim_status", "portfolio")


def test_live_confidence_and_hard_escalations_including_forced_high_confidence():
    check_all("low_confidence", "hardship", "hardship_rephrased", "safeguarding", "compliance_override")
    scenario_runner = runner()
    os.environ["NEXUS_FORCE_CONFIDENCE"] = "0.99"  # stress test 3
    try:
        result = scenario_runner.run_scenario(scenario_runner.find_scenario("hardship_rephrased"))
    finally:
        os.environ.pop("NEXUS_FORCE_CONFIDENCE")
    assert result["status"] == "escalated" and result["validation"]["confidence"] == 0.99 and result["escalation"]["rule"] == "hard_category"


def test_live_stress_tests_identity_conflict_offline_server_and_ownership():
    check_all("missing_customer", "malformed_customer", "unknown_customer", "retrieval_conflict", "server_offline", "ownership")


def test_live_prompt_injection_in_customer_text_and_in_a_retrieved_document():
    check_all("injection_in_text", "injection_in_document")
