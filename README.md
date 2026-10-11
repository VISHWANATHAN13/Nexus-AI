# Nexus - Unified Financial Services Concierge (Meridian Financial Group)

One free-text customer request flows live through four layers: **Pydantic intake -> LangChain + MCP hybrid retrieval ->
five CrewAI agents (tiered OpenAI models) -> four scoped MCP servers**, with guardrails, DEBUG JSON tracing, audit logging,
a demo script and an MCP Inspector walkthrough. Plain local Python processes (no containers). Python 3.11+.

## Status - read this first

What each kind of check has actually shown, and what still has to be run on YOUR machine (this README was last updated in an
environment without network access, so it cannot claim live results):

| Check | How to see it | State when this README was written |
|---|---|---|
| Guardrails, 13 tools, 4 resources, 2 prompts, DB isolation, tracing, server-side session binding, call ledger, escalation queue, persistent-session logic (fake sessions) | `pytest tests/ -q -m "not live"` | passes in a sandbox with a test stub for `mcp` and a stand-in for pydantic (41 passed, 0 failed, 9 skipped there) |
| Whole pipeline with scripted stand-in agents (every scenario, stress tests, hard escalation, ledger evidence) | `python run_local_demo.py --offline` | 28 / 28 checks in the same sandbox, ~1 s |
| Real `mcp` registration + all four servers and the orchestrator as separate processes | `python verify_live.py` | **run it yourself** |
| Real CrewAI agents + OpenAI + Chroma, including the independent model screen | `python run_local_demo.py --live`, `pytest -m live` | **run it yourself** |
| Opt-in persistent MCP sessions against the real stdio client | set `mcp.persistent_sessions: true`, then `verify_live.py` + the demo | **run it yourself** (only fake sessions were exercised) |

`python run_evidence.py` runs the first three rows and saves every output under `evidence/` (add `--live` for the paid ones). Commit
that folder: it is the proof the specification asks for ("tests actually executed", "verified live rather than through a stub").

## Setup (once)
```powershell
python -m venv .venv ; .venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt                          # crewai, crewai-tools[mcp], mcp<2, langchain*, chromadb, pydantic, faker, pytest
copy .env.example .env                                   # then put your OPENAI_API_KEY in .env
```
Open `config.yaml` -> `llm:` and **confirm the three model IDs** exist on your OpenAI account (`small` / `mid` / `top`, plus the
embedding model). They are defaults, not facts. You can also override them in `.env` (`NEXUS_MODEL_SMALL` ...).

Put your files in `knowledge/communication_templates|policy_docs|product_docs|faqs/` (`.md`, `.txt`, `.csv`).

```powershell
python database.py                 # create the schema (your database.py) -> data/meridian.db
python seed_database.py            # 10,000 customers etc. (your seeder, patched - see below)
python build_index.py              # LangChain: load -> RecursiveCharacterTextSplitter -> OpenAI embeddings -> Chroma
python build_index.py --demo       # a second index that also contains the deliberately poisoned demo document
python show_data.py                # row counts per table + knowledge files
```

## Run
| Goal | Command |
|---|---|
| Unit tests (no LLM needed for most) | `python -m pytest tests/` |
| Prove 4 servers + orchestrator start as separate processes and answer a live MCP client | `python verify_live.py` |
| One free-text request, with routing + grounding evidence | `python try_nexus.py --text "I am CUS-20077 and my account is ACC-20077. What is my balance?"` |
| The showcase with PASS/FAIL | `python try_nexus.py --showcase` |
| Every scenario in `demo_scenarios.yaml` | `python try_nexus.py --suite` |
| The full PDF section 8 demo (live agents if a key is set, otherwise offline) | `python run_local_demo.py` |
| The same demo with scripted agents, no key, about a second | `python run_local_demo.py --offline` |
| All evidence in one go, saved under `evidence/` | `python run_evidence.py` |
| Visual demo of the MCP servers | see `INSPECTOR_DEMO.md` (`npx @modelcontextprotocol/inspector python -m banking_server`) |
| The orchestrator as its own process | `python -m nexus_orchestrator --serve` (JSON lines) |

The customer id is **read from the free text** ("I am CUS-20077 ..."), together with any account/policy/portfolio/claim/product ids.
No customer id -> a typed error and zero tool calls. (In production the id would come from the authenticated session.)
Each real request makes ~10 short-lived MCP server processes and ~6 LLM calls, so expect tens of seconds per request.

## Web UI (Streamlit)
```powershell
pip install -r requirements.txt          # includes streamlit
streamlit run streamlit_app.py           # opens http://localhost:8501
```
* **Live agent progress**: one card per agent (Classifier -> Retriever -> Drafter -> Validator -> Escalation) turns
  waiting -> running (timer, tool calls, LLM calls, guardrail retries) -> done/failed/skipped while the request runs; the card summary is
  the pipeline's own `result["steps"]` line. Below: the orchestrator's MCP calls (KYC, profile, ownership, delivery, audit).
* **Chat + downloads**: every answer has *Markdown* and *JSON* download buttons (query, response, status, classification, validation,
  escalation decision, agent timeline, MCP calls); the sidebar downloads the whole chat. Account / policy / portfolio numbers are masked.
* **Scenarios**: the sidebar runs any entry of `demo_scenarios.yaml` (with its test switches) and shows the scenario check PASS/FAIL.
* **Modes**: *Live* = real CrewAI agents + OpenAI + MCP server processes (needs `OPENAI_API_KEY` in `.env`); *Demo* = the scripted agents
  the offline tests use (`ui/scripted_agents.py`), in-process tool calls, no OpenAI.
* **No backend change**: `ui/progress.py` observes the run with pass-through wrappers around the `agents.*` / `mcp_client.*` entry
  points (installed only while a request runs, then restored) and CrewAI's event bus. Agents run with streaming off, so the UI streams
  per agent / per tool call, not token by token. One request runs at a time (the backend keeps per-request module state).

## Tests (50 test functions; 4 need an OpenAI key)
| File | Tests | What |
|---|---|---|
| `test_guardrails.py` | 2 | ids, masking, scoped fields; sanitising, injection, escalation phrases, output filter |
| `test_hardening.py` | 20 | test-mode gating, whole-string ids, injection variants (spaced / leet / base64 / paraphrase) with no false positives on the project's own documents, over-claim filter, escalation recall on a labelled corpus, the model screen (raise-only, fail-safe), server-side binding (other customers refused, scope cap), call ledger, figure verification, escalation flags, audited refusals, PII-safe tracebacks, persistent sessions with fake sessions, every `.py` file compiles |
| `test_database.py` | 1 | WAL, per-server SQLite isolation, append-only ops tables, typed ACCESS_DENIED |
| `test_tools.py` | 4 | the 13 tools, one test per server |
| `test_resources_prompts.py`, `test_mcp_registration.py` | 1 + 1 | 4 resources, 2 prompts (4 args each), 13/4/2 registered on FastMCP |
| `test_logging_config.py`, `test_intake.py`, `test_knowledge.py`, `test_live_mcp.py` | 1 each | tracing + redaction; Pydantic intake + agent guardrails; LangChain/Chroma + quarantine; real MCP stdio |
| `test_ui.py` | 2 | live progress of the five agents; masked downloads + the page (Streamlit AppTest) |
| `test_scenarios_offline.py` | 11 | **every** `demo_scenarios.yaml` scenario + stress test 3, real pipeline with scripted agents (no API key) |
| `test_scenarios_live.py` | 4 | the same scenarios with the real agents + OpenAI (`@pytest.mark.live`, skipped without a key) |

`pytest tests/ -q` runs everything; `pytest tests/ -q -m "not live"` skips the paid OpenAI runs; `pytest -m live` runs only them.

### How good is the hard-escalation detector? (read before quoting numbers)
The phrase scan in `signals/escalation.yaml` is a deterministic BACKSTOP, not the main defence. Measured on sentences the author wrote
(`tests/data/escalation_rephrasings.yaml`):

| Set | Original list | Before widening | Current rules |
|---|---|---|---|
| dev / holdout (used while writing the rules) | 24% / 13% | 100% | 100% |
| blind 1 (tuned on after the first look) | 0% | 84% | 98% |
| blind 2 (written afterwards, scored once) | 2% | 20% | 96% (tuned on after the first look) |
| **blind 3 (written after the last widening, scored once)** | - | - | **50% on first scoring, then tuned to 100%** |
| false positives on 98 ordinary requests | 0 | 0 | 0 |

The honest generalisation number for brand-new wording is therefore about **50%** (blind 3, first scoring); the 96-100% figures are
regression guards on sentences the rules were tuned on. So phrase lists generalise poorly to new wording. That is why the Classifier is backed by an independent model screen
(`agents.screen`, `escalation.llm_screen.enabled`) and why `run_escalation_check` then treats the category as binding. To improve
the numbers: add real misses to the corpus first, then to the rules, and re-score on a fresh set.

## What changed in your files
* `database.py`: **schema SQL untouched.** Added: `MERIDIAN_DB` path override, `connect_for(server)` (SQLite authorizer isolation),
  `db()`, `fetch_*`, `check_isolation()`, tracing.
* `seed_database.py` patches (each marked `# PATCH`): claim ids `CLM-30091` / `CLM-NNNNN` (were `CLM 30091`, which fails the PDF's `CLM-XXXXX`
  format); random rows no longer land on the reserved showcase/stress customers; the stress account `ACC-10002` is now `restricted` with a reason
  (the PDF's conflict case); `MERIDIAN_DB` override.
* ID validators accept letters **or digits** in the policy/product middle segment, because your seeder generates `POL-01-00001` / `PROD-01-01`
  while the PDF's showcase id is `POL-IN-30091`.

## Layout
```
config.yaml agents.yaml tasks.yaml demo_scenarios.yaml signals/*.yaml     all behaviour, wording, patterns, thresholds, scenarios
database.py seed_database.py data/                                       your schema + seeder; data/meridian.db
knowledge/ knowledge.py build_index.py                                   LangChain loaders + splitter + Chroma
guardrails.py logging_config.py errors.py tool_support.py config.py      shared plain functions
banking_server/ insurance_server/ wealth_server/ concierge_ops_server/   tools.py (logic) + server.py (FastMCP) + __main__.py
nexus_orchestrator/  intake.py (Pydantic)  agents.py (CrewAI)  tools.py  mcp_client.py  pipeline.py  __main__.py
scenario_runner.py try_nexus.py run_local_demo.py verify_live.py show_data.py INSPECTOR_DEMO.md
tests/   ARCHITECTURE.md
```

## Spec -> implementation
| PDF | Where |
|---|---|
| 3.1 / 7: 13 tools, 4 resources, 2 prompts | `*/tools.py`; `verify_live.py` counts them over real MCP; prompts are used by the pipeline (classification + drafting) |
| 3.2 intake | `intake.py`: Pydantic `ClassifierOutput` / `Intake`; ids and request_id are never model-supplied |
| 3.3 hybrid retrieval, structured wins | `knowledge.py` (LangChain) + the Retriever agent's MCP tools; conflicts are returned and passed to the Validator |
| 3.4 five agents, hard + confidence escalation | `agents.py`, `pipeline.py`, `run_escalation_check` (deterministic; the pipeline's own call is binding) |
| 4 guardrails | `guardrails.py`; sanitising happens in `ok()` for tool output, in `knowledge_passages()` for documents, at intake for customer text |
| 5 tracing | `@trace` on every function (exemptions below) |
| 6 tests, live processes | `tests/`, `verify_live.py` |
| 8 demo, showcase, stress tests | `run_local_demo.py` + `demo_scenarios.yaml` |

## Safety switches and hardening
* **Test switches need test mode.** `NEXUS_OFFLINE`, `NEXUS_FORCE_CONFIDENCE` are ignored unless `NEXUS_TEST_MODE=1` and `NEXUS_ENV` is not
  `production` (a warning is logged once if they are set but ignored). The test suite, `scenario_runner` (offline scenarios) and
  `run_local_demo.py` enable it themselves.
* **Agents cannot read other customers' data, enforced in the server.** The MCP servers started for the Retriever / Escalation agents get
  `NEXUS_BOUND_CUSTOMER` and `NEXUS_MAX_SCOPE`; they return `ACCESS_DENIED` for any other customer's record (and for unknown ids, so ids
  cannot be enumerated) or a `caller_scope` above the cap (`routing.bind_agent_tools`, `routing.retriever_scope`). Unbound servers
  (the orchestrator's own deterministic calls) behave as before.
* **Grounding evidence no longer depends on log levels.** Every tool call is appended to a call ledger (`logs/ledger/calls.<pid>.jsonl`:
  server, tool, ok, and short hashes of the figures returned - never the values). The pipeline drops a Retriever fact whose server was not
  called, or that quotes a figure of 4+ digits no tool of that server returned (`retrieval.verify_figures`). The trace logs are only a fallback.
* **Escalation queue.** Every escalated request raises one open row in `escalation_flags` in the same transaction as its audit entry
  (`write_audit_log` with `escalation_reason`). The table is insert-only for the ops server.
* **Refused writes are audited** (`notification_blocked`: invalid channel, injection, too long, KYC not verified).
* **Output filter** also blocks absolute wording ('guaranteed', 'never fails', 'no risk' ...; `output_filter.overclaim_patterns`) that the grounded
  facts do not contain. **FAILED log lines** have identifiers scrubbed from the traceback.
* **Persistent MCP sessions (off by default).** `mcp.persistent_sessions: true` (or `NEXUS_PERSISTENT_MCP=1`) keeps one stdio session per
  server instead of starting a process per call (~6 s each). A session that cannot be opened falls back to one-process-per-call; a call that
  fails on an open session is reported as `SERVER_UNAVAILABLE`, the session is dropped, and the call is never retried (a retried write could repeat a send).
  Turn it on only after `verify_live.py` passes; it has so far been exercised with fake sessions only.
* **Known limits.** Customer identity is still read from the message text and `caller_scope` is still supplied by the caller (production: take both
  from an authenticated session). `performed_by` in the audit log is caller-supplied. One request runs at a time per process. The model screen adds
  one small-model call to the Classifier step when the Classifier says `none`.

## Design decisions to defend in the debrief
* **The LLM never decides the hard rule.** Intake takes the stricter of the model's `escalation_category` and a phrase scan
  (`signals/escalation.yaml`), so rephrasing cannot hide it; `run_escalation_check` ignores confidence for hard categories; and the
  pipeline makes its own binding call to that tool even though the Escalation agent also calls it (`agent_agreed` is recorded).
* **Ownership is verified in code, not by the model:** every referenced account/policy/portfolio/claim must belong to the customer.
* **Facts are verified:** a fact attributed to a server the Retriever never actually called (read from the servers' own trace) is dropped.
* **Sanitise at the source:** CrewAI gives no hook between a tool and the model, so MCP servers sanitise their own output and the knowledge
  search tool sanitises passages before the agent sees them. Poisoned documents are quarantined, audited, and the request carries on.
* **No exception classes / no model classes of our own** except the two Pydantic models you asked for. Typed errors are JSON payloads in a `ValueError`
  at tool level and `{"ok": false, "error": {...}}` everywhere else.
* **Model tiers:** small = Classifier; mid = Retriever, Drafter, Escalation; top = Validator.
* **Test switches (env, honoured only in test mode, see above):** `NEXUS_OFFLINE=insurance_server` (stress test 4), `NEXUS_FORCE_CONFIDENCE=0.99` (stress test 3), `NEXUS_DEMO=1` (also search the poisoned demo document).
  `NEXUS_DEMO=1` (also search the poisoned demo document).
* **Tracing exemptions:** the tracer and redaction primitives it calls (would recurse), config getters on the hot path, context managers,
  dunder methods, nested closures, the Streamlit UI helpers, the database seed script, helper CLI scripts and tests. PII-safe logs: ids -> last four characters, free text -> length + hash.
* Two things that need judgement: the Classifier/Retriever/Validator are LLMs, so results vary run to run - the tests judge **properties**;
  the keyword phrase list is not exhaustive (human review is the backstop).

## Dependencies
`requirements.txt` pins the direct dependencies to the versions the team's clean environment resolved. If pip reports a conflict
(`crewai` constrains `chromadb` / `pydantic`), relax the pin, run `python run_evidence.py`, and re-pin what passes.
