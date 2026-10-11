"""Test environment: temp database (built from the project's own schema), temp log, and the test-only `mcp` stub if the real
package is absent. No LLM, no network. Tests that need frameworks (pydantic, langchain, crewai) skip themselves when missing."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TMP = Path(tempfile.mkdtemp(prefix="nexus_tests_"))
os.environ["MERIDIAN_DB"] = str(TMP / "meridian.db")
os.environ["NEXUS_LOG_FILE"] = str(TMP / "tests_trace.jsonl")
os.environ["LOG_LEVEL"] = "DEBUG"
os.environ["NEXUS_TEST_MODE"] = "1"  # the suite deliberately uses the test switches (NEXUS_OFFLINE, NEXUS_FORCE_CONFIDENCE); production ignores them

try:
    import mcp  # noqa: F401
except ImportError:  # development fallback allowed by the spec (section 6) - NOT a substitute for real verification
    sys.path.insert(0, str(Path(__file__).resolve().parent / "stubs"))
    USING_STUB = True
else:
    try:
        import mcp.server.fastmcp  # noqa: F401
    except ImportError as exc:
        raise RuntimeError('An incompatible `mcp` version is installed (2.x?). Run: pip install "mcp>=1.12,<2"') from exc
    USING_STUB = False

import fixtures  # noqa: E402

fixtures.seed_minimal()


def pytest_configure(config):
    config.addinivalue_line("markers", "live: real CrewAI agents + OpenAI (needs OPENAI_API_KEY); run only these with  pytest -m live")
