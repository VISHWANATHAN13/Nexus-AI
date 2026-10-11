# MCP Inspector demo (live, no LLM needed)

MCP Inspector is the official visual tool for MCP servers. It starts a server as a child process and lets you list and call
its tools, read resources and render prompts - ideal for showing the four servers live. Needs **Node.js** (for `npx`).

Run from the project root with the virtual environment **activated** (so `python` is the venv's Python) and the database seeded
(`python database.py` then `python seed_database.py`). One terminal per server:

```powershell
npx @modelcontextprotocol/inspector python -m banking_server
npx @modelcontextprotocol/inspector python -m insurance_server
npx @modelcontextprotocol/inspector python -m wealth_server
npx @modelcontextprotocol/inspector python -m concierge_ops_server
```
The terminal prints the Inspector URL (usually http://localhost:6274, with a token) - open it, make sure the transport is
**STDIO**, and click **Connect**.

## 6-minute script

| # | Server | In Inspector | What to point out |
|---|---|---|---|
| 1 | each | **Tools** -> List Tools; **Resources** -> Resource Templates; **Prompts** -> List | 3 + 3 + 3 + 4 tools = 13, 4 resource templates, 2 prompts - the PDF inventory |
| 2 | banking_server | Tools -> `get_account_summary` with `account_id=bad`, `caller_scope=summary` | typed error `INVALID_ID_FORMAT` |
| 3 | banking_server | `get_account_summary` with `ACC-20077`, scope `summary`, then `service` | number masked to `****0077`; `service` adds balance and auto-debit flags - **scoped field visibility** |
| 4 | banking_server | Resources -> `account://ACC-20077/summary` | resource is masked and least-privilege |
| 5 | insurance_server | `check_coverage_clause` `POL-IN-30091` / `auto_debit` | deterministic answer from the `policy_clauses` table; `get_claim_status` never shows the claim amount |
| 6 | wealth_server | `check_product_suitability` for a product and each risk profile | read-only, nothing about the customer except the profile passed in |
| 7 | concierge_ops_server | `run_escalation_check` `CUS-10004`, `financial_hardship`, confidence `0.99` | `escalate: true, rule: hard_category` - **confidence is never consulted** |
| 8 | concierge_ops_server | the same tool with `none` and `0.5`, then `0.9` | `low_confidence` -> escalate; `0.9` -> no escalation |
| 9 | concierge_ops_server | Prompts -> `request_classification_prompt` with a message containing "ignore all previous instructions" | the argument is sanitised: the injection becomes `[REMOVED...]` |
| 10 | concierge_ops_server | `send_customer_notification` for a customer whose KYC is not `verified` | refused with `KYC_NOT_VERIFIED`, and the refusal is audited |

Then run the full pipeline (needs your OpenAI key): `python try_nexus.py --showcase` and show the ROUTING + GROUNDING sections.
