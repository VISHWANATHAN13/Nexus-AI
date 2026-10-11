"""Local demo (spec section 8). Starts the four MCP servers itself (no external MCP client needed) and runs real free-text
requests through the CrewAI pipeline with a real LLM. Needs OPENAI_API_KEY, the database and the knowledge index.

    python database.py && python seed_database.py && python build_index.py && python build_index.py --demo
    python run_local_demo.py

Scenarios are read from demo_scenarios.yaml. Results are judged on PROPERTIES (status, servers called, rule, no leaks), not
exact wording, because the agents are LLM-driven. Exit code 0 only if every check passes.
"""
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

from config import setup_environment

os.environ["NEXUS_TEST_MODE"] = "1"  # this script is a test harness: it uses NEXUS_OFFLINE / NEXUS_FORCE_CONFIDENCE (ignored by production)
setup_environment()
from logging_config import clean_old_trace_files, get_logger, log_files, trace  # noqa: E402

DEMO_START = datetime.now(timezone.utc).isoformat(timespec="seconds")
clean_old_trace_files()  # fresh traces (one file per process) for this demo run

logger = get_logger("run_local_demo")


def _r(data):
    return redact_for_logging(data)


import database  # noqa: E402
import scenario_runner  # noqa: E402
FIRST_AUDIT_ID = None  # set in main(): audit_log rows with a higher id were written by this demo
from guardrails import find_ids_in_text, redact_for_logging  # noqa: E402
from nexus_orchestrator.intake import build_intake, precheck_text  # noqa: E402

RESULTS = []
OFFLINE = False  # True: scripted agents + in-process tool calls (no OpenAI key, runs in seconds); see main()


@trace(logger, redact=_r)
def banner(text):
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


@trace(logger, redact=_r)
def check(label, condition):
    RESULTS.append((label, bool(condition)))
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")


@contextmanager
def offline_demo_index():
    """Offline demo only: build a small knowledge index (the poisoned FAQ + one clean FAQ) with fake embeddings and point the real
    knowledge module at it, so the prompt-injection-in-a-retrieved-document scenario runs for real without any network."""
    import tempfile
    from pathlib import Path

    import knowledge
    from langchain_core.embeddings import DeterministicFakeEmbedding
    from ui import scripted_agents
    root = Path(tempfile.mkdtemp(prefix="nexus_demo_kb_"))
    (root / "faqs").mkdir()
    poisoned = Path(__file__).parent / "demo_assets" / "poisoned_docs" / "faq_fee_waivers_poisoned.md"
    (root / "faqs" / poisoned.name).write_text(poisoned.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "faqs" / "faq_cards.md").write_text("Card replacement takes five working days.", encoding="utf-8")
    saved = (knowledge.knowledge_folders, knowledge.index_path, knowledge.make_embeddings)
    knowledge.knowledge_folders = lambda include_demo=False: [("faqs", root / "faqs")]
    knowledge.index_path = lambda include_demo=False: root / "chroma"
    knowledge.make_embeddings = lambda: DeterministicFakeEmbedding(size=64)
    scripted_agents.OPTIONS["use_knowledge"] = True
    try:
        knowledge.build_index()
        yield
    finally:
        scripted_agents.OPTIONS["use_knowledge"] = False
        knowledge.knowledge_folders, knowledge.index_path, knowledge.make_embeddings = saved


@trace(logger, redact=_r)
def run(name, show=True):
    scenario = scenario_runner.find_scenario(name)
    print(f"\n  >> {scenario['title']}\n     \"{scenario['text']}\"")
    if OFFLINE and scenario.get("needs_demo_index"):
        with offline_demo_index():  # a throw-away index over the poisoned demo document, built with fake embeddings (no network)
            result = scenario_runner.run_scenario(scenario)
        if show:
            scenario_runner.show(result)
        problems = scenario_runner.judge(scenario, result)
        check(f"{name}: " + ("expectations met" if not problems else "; ".join(problems)), not problems)
        return scenario, result
    if scenario_runner.demo_index_missing(scenario):
        check(f"{name}: demo index present (run: python build_index.py --demo)", False)
        return scenario, None
    result = scenario_runner.run_scenario(scenario)
    if show:
        scenario_runner.show(result)
    problems = scenario_runner.judge(scenario, result)
    check(f"{name}: " + ("expectations met" if not problems else "; ".join(problems)), not problems)
    return scenario, result


@trace(logger, redact=_r)
def main():
    """python run_local_demo.py            live agents if OPENAI_API_KEY is set, otherwise offline
       python run_local_demo.py --offline  scripted agents, no key, runs in seconds (the real pipeline, guardrails, MCP tool code)
       python run_local_demo.py --live     live agents only (exit 2 without a key)"""
    global OFFLINE
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    OFFLINE = "--offline" in sys.argv or (not has_key and "--live" not in sys.argv)
    if not OFFLINE and not has_key:
        print("OPENAI_API_KEY is not set - copy .env.example to .env and fill it in (or run with --offline).")
        return 2
    if not OFFLINE:
        return run_all()
    from ui import scripted_agents
    banner("OFFLINE MODE: scripted stand-in agents + in-process tool calls. Real: intake, KYC/ownership checks, fact verification,\n"
           "guardrails, deterministic escalation, delivery, audit. Not real: the LLM answers.")
    saved = scripted_agents.install()
    try:
        return run_all()
    finally:
        scripted_agents.uninstall(saved)


@trace(logger, redact=_r)
def run_all():
    global FIRST_AUDIT_ID
    FIRST_AUDIT_ID = database_rows("SELECT COALESCE(MAX(audit_id), 0) AS last FROM audit_log")[0]["last"]

    banner("1. Structured intake: one valid and one deliberately invalid extraction (Pydantic, no LLM needed)")
    pre = precheck_text("I am CUS-20077. What is my balance on ACC-20077?")
    valid = build_intake(pre["customer_id"], pre["references"], {"product_line": "banking", "request_type": "balance_inquiry",
                         "urgency": "routine", "customer_stated_intent": "wants the balance", "escalation_category": "none"})
    check("valid extraction becomes a typed Intake with a server-generated request_id", valid["ok"] and valid["intake"].request_id.startswith("REQ-"))
    invalid = build_intake("CUS-1", {}, {"product_line": "mortgage", "request_type": "x", "urgency": "whenever",
                           "customer_stated_intent": " ", "escalation_category": "maybe"})
    check("invalid extraction is rejected with a typed error (does not proceed)", not invalid["ok"] and invalid["error"]["code"] == "INTAKE_REJECTED")
    print("     problems:", [p["field"] for p in invalid["error"]["details"]["errors"]])

    banner("2-3. Retrieval reconciling live MCP data with documents, and the full agent chain on a routine request")
    run("routine_balance")

    banner("4. Confidence-based escalation")
    run("low_confidence")

    banner("5. Hard, category-based escalation - including a rephrased attempt that still triggers it")
    # for name in ("hardship", "hardship_rephrased", "safeguarding", "compliance_override"):
    #     run(name, show=name in ("hardship_rephrased", "safeguarding"))
    for name in ("hardship", "hardship_rephrased", "safeguarding", "compliance_override", "hardship_kyc_pending", "hardship_foreign_account"):

        run(name, show=name in ("hardship_rephrased", "safeguarding", "hardship_kyc_pending", "hardship_foreign_account"))
 
    banner("6. Prompt injection arriving via a RETRIEVED DOCUMENT is blocked")
    run("injection_in_document")
    if not OFFLINE:
        audit = database_audit()
        check("the block was written to audit_log", any(a["action_type"] == "injection_blocked" for a in audit))
    run("injection_in_text", show=False)

    banner("7. SHOWCASE SCENARIO - Arjun Mehta (CUS-20077)")
    run("showcase")

    banner("8. STRESS TESTS")
    run("missing_customer", show=False)
    run("malformed_customer", show=False)
    run("unknown_customer", show=False)
    run("retrieval_conflict")
    print("\n  Stress 3 - hard-escalation override: the validator is forced to report 0.99 confidence")
    os.environ["NEXUS_FORCE_CONFIDENCE"] = "0.99"
    _, forced = run("hardship_rephrased")
    os.environ.pop("NEXUS_FORCE_CONFIDENCE")
    check("escalated although the validator reported 0.99", forced and forced["validation"]["confidence"] == 0.99 and forced["status"] == "escalated")
    run("server_offline")
    run("ownership", show=False)

    banner("9. Evidence: isolation, audit trail, tracing and PII-safe logs")
    for label, refused in database.check_isolation().items():
        check(f"DB isolation enforced: {label}", refused)
    audit = database_audit()
    kinds = sorted({a["action_type"] for a in audit})
    print(f"  audit_log: {len(audit)} entries written during this demo, action types: {kinds}")
    check("writes were audited (notification_sent and request_* entries)", {"notification_sent", "request_escalated"} <= set(kinds))
    good, bad = [], []
    for trace_file in log_files():
        for line in trace_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    good.append(json.loads(line))
                except ValueError:
                    bad.append(line)
    events = [entry.get("event") for entry in good]
    print(f"  traces ({len(log_files())} files, one per process): {len(good)} JSON lines "
          f"(ENTER={events.count('ENTER')}, EXIT={events.count('EXIT')}, FAILED={events.count('FAILED')}), {len(bad)} unreadable")
    for line in bad[:3]:
        print("    unreadable line:", line[:140])
    check("every trace line is valid JSON, with ENTER/EXIT pairs", not bad and events.count("ENTER") >= events.count("EXIT") > 0)
    log = "\n".join(trace_file.read_text(encoding="utf-8", errors="replace") for trace_file in log_files())
    used_ids = {i for s in scenario_runner.load_scenarios() for ids in find_ids_in_text(s["text"]).values() for i in ids}
    leaked = sorted(i for i in used_ids if re.search(re.escape(i), log))
    check(f"no raw customer/account/policy/portfolio ids in the trace (leaked: {leaked or 'none'})", not leaked)

    failed = [label for label, ok in RESULTS if not ok]
    banner(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for label in failed:
        print("  FAILED:", label)
    return 1 if failed else 0


@trace(logger, redact=_r)
def database_audit():
    """audit_log rows written since this demo started (the seeded history is excluded)."""
    return database_rows("SELECT action_type FROM audit_log WHERE audit_id > ? ORDER BY audit_id DESC", (FIRST_AUDIT_ID,))


@trace(logger, redact=_r)
def database_rows(sql, params=()):
    with database.db("concierge_ops_server") as conn:
        return database.fetch_all(conn, sql, params)


if __name__ == "__main__":
    sys.exit(main())
