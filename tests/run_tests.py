"""Fallback runner used ONLY when pytest is not installed:  python tests/run_tests.py
(The suite itself is plain pytest-style functions; `pytest tests/` is the intended command.)"""
import importlib
import sys
import time
import traceback
import unittest
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
import conftest  # noqa: E402,F401

passed = failed = skipped = 0
for path in sorted(here.glob("test_*.py")):
    try:
        mod = importlib.import_module(path.stem)
    except ImportError as exc:  # a test file needing a framework that is not installed (e.g. pytest, streamlit)
        skipped += 1
        print(f"SKIP  {path.stem}  (cannot import: {exc})")
        continue
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        fn = getattr(mod, name)
        start = time.perf_counter()
        try:
            fn()
            passed += 1
            print(f"PASS  {path.stem}::{name}  ({(time.perf_counter() - start) * 1000:.0f} ms)")
        except unittest.SkipTest as exc:
            skipped += 1
            print(f"SKIP  {path.stem}::{name}  ({exc})")
        except Exception:
            failed += 1
            print(f"FAIL  {path.stem}::{name}")
            traceback.print_exc()
print(f"\n{passed} passed, {failed} failed, {skipped} skipped  (stub mcp in use: {conftest.USING_STUB})")
sys.exit(1 if failed else 0)
