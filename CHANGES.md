# Final submission fixes (latest)

| Problem found | Fix |
|---|---|
| `ui/scripted_agents.py` contained a copy of `progress.py`; the scripted stand-in agents were commented out, so 23 tests failed | restored the scripted agents module |
| `ui/progress.py`, `streamlit_app.py`, `guardrails.py`, `pipeline.py`, `logging_config.py`, `nexus_orchestrator/tools.py`, `errors.py` began with a large commented-out old copy of themselves | removed the dead commented code (active code unchanged) |
| `config.yaml`: `hard_handoff` / `hard_unidentified` sat outside `messages:` (KeyError -> every hard escalation became an AGENT_FAILURE); blank lines after `|-` put a newline at the start of the conflict-rule regexes so the rule-based conflict detector never fired | indented the keys under `messages:`; removed the blank lines after block scalars |
| `test_every_python_file_compiles` scanned the virtualenv folder and failed | skips `.venv*`, `venv*`, `.nexus*` folders |
| `@trace` coverage ~54% | added to the remaining non-trivial functions in intake, mcp_client, tool_support, knowledge, database, guardrails and the demo script (exemptions stay as listed in the README) |
| hard-escalation phrase list generalised poorly (20% on a fresh set) | widened `signals/escalation.yaml` with general vocabulary cues; new fresh set `blind3` scored 50% on first look (README table), 0 false positives on 98 ordinary requests; recall floors raised to 90% |
| offline demo skipped the "prompt injection inside a retrieved document" scenario | `run_local_demo.py --offline` now builds a throw-away index with fake embeddings and runs it for real (31/31 checks) |

---

# Nexus fixes - what changed and how to apply it

Apply: unzip over the project root (paths mirror the project), then run `python run_evidence.py` and commit the `evidence/` folder.
Only the files listed here differ from `nexus_crewai_streamlit.zip`; every other file is untouched (the shipped database is unchanged).

| File | Change |
|---|---|
| `config.py` | `env_switch()` / `testing_enabled()`: test switches honoured only with `NEXUS_TEST_MODE=1` and not `NEXUS_ENV=production`; `truststore` actually injected (`network.use_system_certs`); warnings are JSON |
| `guardrails.py` | whole-string id match; injection scan also reads spaced / leet / base64 variants; escalation detector normalises text and supports co-occurrence rules; output filter blocks unsupported absolute wording |
| `signals/escalation.yaml` | widened strong phrases + co-occurrence `pairs` (recall: see README table; blind-set recall is only 20%) |
| `signals/injection.yaml` | paraphrase, "new instructions", approval-manipulation and exfiltration signatures |
| `tool_support.py` | call ledger (`NEXUS_CALL_LEDGER_DIR`), server-side session binding (`require_owned`, `enforce_owner`, `enforce_max_scope`) |
| `banking_server/tools.py`, `insurance_server/tools.py`, `wealth_server/tools.py` | bound sessions refuse other customers' records and scopes above the cap; scenario-tag check is whole-string |
| `concierge_ops_server/tools.py` | `write_audit_log` can raise an `escalation_flags` row in the same transaction; refused notifications are audited; `run_escalation_check` honours the binding |
| `nexus_orchestrator/mcp_client.py` | test-mode gate; agent-bound `server_params`; ledger dir; opt-in persistent sessions (default off) |
| `nexus_orchestrator/tools.py` | `mcp_tools(..., bound_customer, max_scope)` |
| `nexus_orchestrator/agents.py` | LLM objects cached; `NEXUS_FORCE_CONFIDENCE` gated; independent model screen (`screen`, raise-only, fail-safe) |
| `nexus_orchestrator/pipeline.py` | ledger-based routing evidence (trace logs only as fallback); agent servers bound to the request's customer; facts verified by provenance AND figures; one open `escalation_flags` row per escalation; ledger pruning |
| `nexus_orchestrator/intake.py`, `demo_scenarios.yaml`, `tasks.yaml`, `config.yaml`, `logging_config.py`, `scenario_runner.py`, `run_local_demo.py` | commented-out legacy code removed (checked: parsed YAML identical / AST identical); new settings and the `screen` task added where relevant; `FAILED` tracebacks scrubbed of identifiers; demo gets `--offline` / `--live` |
| `agents.yaml` | `screener` agent |
| `ui/scripted_agents.py` | scripted `screen` task for Demo mode / offline tests |
| `streamlit_app.py` | HTML-escapes text rendered with `unsafe_allow_html` |
| `tests/conftest.py`, `tests/run_tests.py` | test mode on for the suite; fallback runner skips files whose framework is missing |
| `tests/test_hardening.py`, `tests/data/escalation_rephrasings.yaml` | 20 new tests and the labelled escalation corpus |
| `run_evidence.py` | one command that runs tests, `verify_live.py` and the offline demo and saves outputs under `evidence/` |
| `requirements.txt`, `README.md` | pinned versions (+ streamlit); honest status, hardening and detector-quality sections |

## Verified (sandbox, no network, `mcp` test stub, stand-in for pydantic)
Full suite 41 passed / 0 failed / 9 skipped (baseline before my changes: 22 / 0 / 8); `python run_local_demo.py --offline` 28 / 28.

## NOT verified - run these yourself
* `verify_live.py` and the live agents (`run_local_demo.py --live`, `pytest -m live`): real crewai / mcp / OpenAI were not available.
* The independent model screen (`escalation.llm_screen.enabled`, default true) has never called a real model. Set it to false to switch it off.
* Persistent MCP sessions (`mcp.persistent_sessions`, default false) were exercised with fake sessions only.
* `test_ui.py` (needs streamlit) and the skipped LangChain/Chroma tests.
* `requirements.txt` pins were taken from your earlier clean-environment branch and could not be checked offline.

## Not changed on purpose
`performed_by` in the audit log is still caller-supplied (existing tests rely on it); identity is still read from the message text; the
function-level `@trace` coverage is unchanged (~54% of core functions).
