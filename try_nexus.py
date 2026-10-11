"""Try Nexus yourself: type free text (include your customer id) and see the answer, which MCP servers handled it,
and the facts it was grounded in. Needs OPENAI_API_KEY (agents) - set it in .env.

  python try_nexus.py                      interactive
  python try_nexus.py --text "I am CUS-20077 and my account is ACC-20077. What is my balance?"
  python try_nexus.py --showcase           the PDF's showcase scenario with PASS/FAIL
  python try_nexus.py --scenario hardship  one named scenario from demo_scenarios.yaml
  python try_nexus.py --suite              every scenario in demo_scenarios.yaml (several minutes: real LLM + real MCP processes)
  python try_nexus.py --list               list scenario names
"""
import argparse
import os
import sys

from config import setup_environment

setup_environment()

import scenario_runner  # noqa: E402


def check_ready():
    """Friendly pre-flight: API key, database, index."""
    from config import ROOT
    from knowledge import index_path
    missing = []
    if not os.environ.get("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY is not set (copy .env.example to .env and fill it in)")
    from database import DATABASE_PATH
    if not DATABASE_PATH.exists():
        missing.append("database missing: run  python database.py  then  python seed_database.py")
    if not index_path().exists():
        missing.append("knowledge index missing: run  python build_index.py")
    if missing:
        print("Not ready:\n  - " + "\n  - ".join(missing))
        sys.exit(2)


def run_and_report(scenario):
    print(f"\n{'=' * 78}\n{scenario['title']}\n  customer text: {scenario['text']}")
    if scenario_runner.demo_index_missing(scenario):
        print("  SKIPPED: build the demo index first:  python build_index.py --demo")
        return None
    result = scenario_runner.run_scenario(scenario)
    scenario_runner.show(result)
    problems = scenario_runner.judge(scenario, result)
    print(f"\n  RESULT: {'PASS' if not problems else 'FAIL'}" + "".join(f"\n    - {p}" for p in problems))
    return not problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text"), parser.add_argument("--scenario")
    parser.add_argument("--showcase", action="store_true"), parser.add_argument("--suite", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for scenario in scenario_runner.load_scenarios():
            print(f"  {scenario['name']:<22} {scenario['title']}")
        return 0
    check_ready()
    if args.showcase or args.scenario:
        scenario = scenario_runner.find_scenario("showcase" if args.showcase else args.scenario)
        if not scenario:
            print("unknown scenario; use --list")
            return 2
        return 0 if run_and_report(scenario) is not False else 1
    if args.suite:
        outcomes = [run_and_report(s) for s in scenario_runner.load_scenarios()]
        print(f"\n{'=' * 78}\n{sum(1 for o in outcomes if o)} passed, {sum(1 for o in outcomes if o is False)} failed, "
              f"{sum(1 for o in outcomes if o is None)} skipped")
        return 1 if any(o is False for o in outcomes) else 0
    if args.text:
        from nexus_orchestrator.pipeline import handle_request
        scenario_runner.show(handle_request(args.text))
        return 0
    print("Type one free-text request that includes your customer id, e.g.\n"
          "  I am CUS-20077, my account is ACC-20077 and my policy is POL-IN-30091. Can I set up auto-debit for my premium?")
    from nexus_orchestrator.pipeline import handle_request
    while True:
        text = input("\nyou (empty to quit): ").strip()
        if not text:
            return 0
        scenario_runner.show(handle_request(text))


if __name__ == "__main__":
    sys.exit(main())
