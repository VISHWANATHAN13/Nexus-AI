"""Registration of tools / resource templates / prompts on FastMCP.
With the real `mcp` package this verifies the real @mcp.tool()/resource()/prompt() registration (spec section 6);
with the test-only stub it only verifies our wiring."""
import asyncio
import importlib

EXPECTED = {
    "banking_server": ({"get_account_summary", "get_transaction_history", "get_linked_accounts"}, {"account://{account_id}/summary"}, set()),
    "insurance_server": ({"get_policy_details", "check_coverage_clause", "get_claim_status"}, {"policy://{policy_id}/summary"}, set()),
    "wealth_server": ({"get_portfolio_summary", "get_investment_product_details", "check_product_suitability"}, set(), set()),
    "concierge_ops_server": ({"get_customer_kyc_status", "run_escalation_check", "send_customer_notification", "write_audit_log"},
                             {"customer://{customer_id}/profile", "knowledgebase://{document_id}/excerpt"},
                             {"request_classification_prompt", "response_drafting_prompt"}),
}


def listing(server_name):
    server = importlib.import_module(f"{server_name}.server").build_server()
    return ({t.name for t in asyncio.run(server.list_tools())},
            {t.uriTemplate for t in asyncio.run(server.list_resource_templates())},
            {p.name for p in asyncio.run(server.list_prompts())})


def test_exactly_13_tools_4_resource_templates_2_prompts_and_an_entrypoint_per_server():
    names = [n for server in EXPECTED for n in importlib.import_module(f"{server}.tools").TOOLS]
    assert len(names) == 13 and len(set(names)) == 13
    totals = [set(), set(), set()]
    for name, expected in EXPECTED.items():
        got = listing(name)
        assert got == expected, name
        for total, part in zip(totals, got):
            total |= part
        assert hasattr(importlib.import_module(f"{name}.__main__"), "build_server")
    assert (len(totals[0]), len(totals[1]), len(totals[2])) == (13, 4, 2)
