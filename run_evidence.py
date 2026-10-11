"""Produce the submission evidence in ONE command and save every output under evidence/ (spec section 6: tests must be written AND
executed; section 9: servers and orchestrator verified live, not through a stub).

    python run_evidence.py            # unit + offline-scenario tests, verify_live.py, offline demo
    python run_evidence.py --live     # also the paid live-agent tests and the live demo (needs OPENAI_API_KEY)

Each step's full output goes to evidence/<step>.txt with a header (time, Python, exit code); a summary table is printed and written
to evidence/SUMMARY.txt. Exit code 0 only if every step passed. Nothing here changes any code or data of the project."""
import importlib.util
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "evidence"


def steps(live):
    has_pytest = importlib.util.find_spec("pytest.__main__") is not None
    tests = ([sys.executable, "-m", "pytest", "tests/", "-q", "-m", "live" if live else "not live"] if has_pytest
             else [sys.executable, "tests/run_tests.py"])
    plan = [("1_tests", tests, 900), ("2_verify_live_servers_and_orchestrator", [sys.executable, "verify_live.py"], 600),
            ("3_demo_offline", [sys.executable, "run_local_demo.py", "--offline"], 600)]
    if live:
        plan.append(("4_demo_live", [sys.executable, "run_local_demo.py", "--live"], 3600))
    return plan


def run_step(name, command, timeout):
    started = time.perf_counter()
    try:
        done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        code, text = done.returncode, done.stdout + (("\n--- stderr ---\n" + done.stderr) if done.stderr.strip() else "")
    except subprocess.TimeoutExpired as exc:
        code, text = 124, f"TIMEOUT after {timeout} s\n{exc.stdout or ''}"
    seconds = time.perf_counter() - started
    header = (f"step: {name}\ncommand: {' '.join(command)}\nwhen: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
              f"python: {platform.python_version()} on {platform.system()}\nexit code: {code}\nseconds: {seconds:.1f}\n{'=' * 78}\n")
    (OUT / f"{name}.txt").write_text(header + text, encoding="utf-8")
    return code, seconds


def main():
    OUT.mkdir(exist_ok=True)
    rows = []
    for name, command, timeout in steps("--live" in sys.argv):
        print(f"running {name} ...", flush=True)
        code, seconds = run_step(name, command, timeout)
        rows.append((name, code, seconds))
    lines = [f"{'step':45} {'result':8} seconds"] + [f"{n:45} {'PASS' if c == 0 else 'FAIL(' + str(c) + ')':8} {s:7.1f}" for n, c, s in rows]
    summary = "\n".join(lines)
    (OUT / "SUMMARY.txt").write_text(summary + "\n", encoding="utf-8")
    print("\n" + summary + f"\n\nfull outputs: {OUT}")
    return 0 if all(code == 0 for _, code, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
