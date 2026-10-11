"""Live verification over real MCP stdio (spec 6): real server processes, no LLM. Skipped unless the real `mcp` package is installed."""
import os
import unittest

import conftest
from helpers import need


def test_four_server_processes_answer_a_real_mcp_client_with_typed_errors_and_offline_handling():
    need("mcp.client.stdio")
    if conftest.USING_STUB:
        raise unittest.SkipTest("real `mcp` package not installed")
    from nexus_orchestrator import mcp_client
    totals = {"tools": 0, "resource_templates": 0, "prompts": 0}
    for server in ("banking_server", "insurance_server", "wealth_server", "concierge_ops_server"):
        reply = mcp_client.list_primitives(server)
        assert reply["ok"], reply
        for key in totals:
            totals[key] += len(reply["data"][key])
    assert totals == {"tools": 13, "resource_templates": 4, "prompts": 2}

    reply = mcp_client.call_tool("banking_server", "get_account_summary", {"account_id": "ACC-20077", "caller_scope": "summary"})
    assert reply["ok"] and reply["data"]["account"]["account_number"] == "****0077"
    assert mcp_client.call_tool("banking_server", "get_account_summary", {"account_id": "bad", "caller_scope": "summary"})["error"]["code"] == "INVALID_ID_FORMAT"
    assert mcp_client.read_resource("concierge_ops_server", "customer://CUS-20077/profile")["data"]["display_name"] == "Arjun"
    assert "Arjun" in mcp_client.get_prompt("concierge_ops_server", "response_drafting_prompt",
                                            {"classified_intent": "x", "retrieved_context_summary": "y", "customer_name": "Arjun", "escalation_flag": "false"})["data"]

    os.environ["NEXUS_OFFLINE"] = "insurance_server"
    try:
        reply = mcp_client.call_tool("insurance_server", "get_policy_details", {"policy_id": "POL-IN-30091"})
    finally:
        os.environ.pop("NEXUS_OFFLINE")
    assert reply["ok"] is False and reply["error"]["code"] == "SERVER_UNAVAILABLE"
