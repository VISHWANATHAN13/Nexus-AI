"""Submission evidence for 'System startup & process evidence' (spec 6 and 9): all four MCP servers and the orchestrator
start as independent local processes and answer a live MCP client. Needs the real `mcp` package and the seeded database.
No LLM is needed for this script.

    python verify_live.py          # exit code 0 = everything verified
"""
import json
import subprocess
import sys

from config import ROOT, setup_environment

setup_environment()

try:
    import mcp  # noqa: F401
    from mcp.server.fastmcp import FastMCP  # noqa: F401
except ImportError:
    sys.exit('Compatible `mcp` (1.x) not found. Run: pip install -r requirements.txt')

import scenario_runner  # noqa: E402
from guardrails import find_ids_in_text  # noqa: E402
from nexus_orchestrator import mcp_client  # noqa: E402

results = []


def check(label, condition):
    results.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")


print("1. Each MCP server starts as its own process and answers a live MCP client (stdio):")
totals = {"tools": 0, "resource_templates": 0, "prompts": 0}
for server in ("banking_server", "insurance_server", "wealth_server", "concierge_ops_server"):
    reply = mcp_client.list_primitives(server)
    if reply["ok"]:
        for key in totals:
            totals[key] += len(reply["data"][key])
        check(f"{server}: tools {reply['data']['tools']}, resources {reply['data']['resource_templates']}, prompts {reply['data']['prompts']}", True)
    else:
        check(f"{server} failed to start: {reply['error']['message']}", False)
check(f"13 tools / 4 resource templates / 2 prompts registered on the real FastMCP (got {totals})",
      totals == {"tools": 13, "resource_templates": 4, "prompts": 2})

print("2. Live tool / resource / prompt calls (ids taken from the showcase scenario in demo_scenarios.yaml):")
ids = find_ids_in_text(scenario_runner.find_scenario("showcase")["text"])
account, policy, customer = ids["account"][0], ids["policy"][0], ids["customer"][0]
reply = mcp_client.call_tool("banking_server", "get_account_summary", {"account_id": account, "caller_scope": "summary"})
check("banking_server.get_account_summary returns a masked number", reply["ok"] and reply["data"]["account"]["account_number"] == "****" + account[-4:])
reply = mcp_client.call_tool("insurance_server", "check_coverage_clause", {"policy_id": policy, "scenario_tag": "auto_debit"})
check("insurance_server.check_coverage_clause answers from policy_clauses", reply["ok"] and reply["data"]["result"] in ("covered", "excluded", "not_specified"))
reply = mcp_client.call_tool("concierge_ops_server", "run_escalation_check", {"customer_id": customer, "request_category": "financial_hardship", "draft_confidence": 0.99})
check("concierge_ops_server.run_escalation_check escalates a hard category at 0.99", reply["ok"] and reply["data"]["escalate"] is True)
reply = mcp_client.read_resource("concierge_ops_server", f"customer://{customer}/profile")
check("resource customer://.../profile is readable and masks contact details", reply["ok"] and "***" in reply["data"]["email"])
reply = mcp_client.get_prompt("concierge_ops_server", "response_drafting_prompt",
                              {"classified_intent": "balance_inquiry", "retrieved_context_summary": "x", "customer_name": "Test", "escalation_flag": "false"})
check("prompt response_drafting_prompt renders", reply["ok"] and "Test" in reply["data"])
reply = mcp_client.call_tool("banking_server", "get_account_summary", {"account_id": "bad", "caller_scope": "summary"})
check("typed error crosses the MCP boundary", not reply["ok"] and reply["error"]["code"] == "INVALID_ID_FORMAT")

print("3. Orchestrator as an independent local process:")
done = subprocess.run([sys.executable, "-m", "nexus_orchestrator", "--ping"], cwd=ROOT, capture_output=True, text=True, timeout=120)
check("orchestrator process starts and answers", done.returncode == 0 and json.loads(done.stdout.strip().splitlines()[-1]).get("service") == "nexus_orchestrator")

print(f"\n{sum(results)}/{len(results)} live checks passed")
sys.exit(0 if all(results) else 1)
