"""Configuration loading: config.yaml, agents.yaml, tasks.yaml, signals/*.yaml, .env. Nothing is hardcoded elsewhere."""
import logging
import os
from pathlib import Path

import yaml

from logging_config import get_logger, log_json, trace

ROOT = Path(__file__).resolve().parent
logger = get_logger("config")
_CACHE = {}

ENV_OVERRIDES = {("llm", "small"): "NEXUS_MODEL_SMALL", ("llm", "mid"): "NEXUS_MODEL_MID",
                 ("llm", "top"): "NEXUS_MODEL_TOP", ("llm", "embedding"): "NEXUS_EMBEDDING_MODEL"}


@trace(logger)
def setup_environment():
    """Load .env, switch off framework telemetry (no outbound calls other than the LLM) and, if configured, trust the OS
    certificate store via truststore (config.yaml network.use_system_certs)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    if (load_config().get("network") or {}).get("use_system_certs"):
        try:
            import truststore
            truststore.inject_into_ssl()
        except ImportError:
            log_json(logger, logging.WARNING, "truststore_missing", detail="network.use_system_certs is true but truststore is not installed; using certifi defaults")


def load_yaml(relative_path):
    """Read and cache a YAML file relative to the project root."""
    if relative_path not in _CACHE:
        with open(ROOT / relative_path, encoding="utf-8") as handle:
            _CACHE[relative_path] = yaml.safe_load(handle)
    return _CACHE[relative_path]


def load_config():
    """config.yaml with environment-variable overrides for the model names."""
    cfg = load_yaml("config.yaml")
    for (section, key), env_name in ENV_OVERRIDES.items():
        if os.environ.get(env_name):
            cfg[section][key] = os.environ[env_name]
    return cfg


def testing_enabled():
    """Test switches (NEXUS_OFFLINE, NEXUS_FORCE_CONFIDENCE, ...) are honoured ONLY when NEXUS_TEST_MODE=1 and the process is
    not marked as production (NEXUS_ENV=production). Otherwise anyone who can set an environment variable could force a
    validator confidence of 1.0 or simulate an outage."""
    return os.environ.get("NEXUS_TEST_MODE", "") == "1" and os.environ.get("NEXUS_ENV", "").lower() != "production"


def env_switch(name, default=""):
    """Value of a test switch, or `default` when test mode is off (a switch that is set but ignored is logged once as a warning)."""
    value = os.environ.get(name, "")
    if value and not testing_enabled():
        if name not in _CACHE.setdefault("_ignored_switches", set()):
            _CACHE["_ignored_switches"].add(name)
            log_json(logger, logging.WARNING, "test_switch_ignored", switch=name,
                     detail="test switches need NEXUS_TEST_MODE=1 and NEXUS_ENV must not be production")
        return default
    return value or default


@trace(logger)
def resolve_path(relative):
    """Project-relative path -> absolute Path. MERIDIAN_DB / NEXUS_INDEX style overrides are handled by callers."""
    return ROOT / relative
