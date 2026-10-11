"""insurance_server logic. Data access: policies + policy_clauses + claims ONLY (no account/portfolio data)."""
import re
from typing import Any

from config import load_config
from database import db, fetch_one
from errors import fail
from guardrails import mask_identifier, redact_for_logging, validate_id
from logging_config import get_logger, trace
from tool_support import as_json_str, ok, require_owned, typed_errors

SERVER = "insurance_server"
logger = get_logger(SERVER)


def _r(data):
    return redact_for_logging(data)


@trace(logger, redact=_r)
def load_policy(policy_id):
    validate_id("policy", policy_id)
    with db(SERVER) as conn:
        row = fetch_one(conn, "SELECT * FROM policies WHERE policy_id = ?", (policy_id,))
    return require_owned(row, "Policy not found")


@typed_errors
@trace(logger, redact=_r)
def get_policy_details(policy_id: str) -> dict[str, Any]:
    """Single policy lookup: premium, payment method and terms, status (policy number masked)."""
    row = load_policy(policy_id)
    return ok({"policy": {
        "policy_number": mask_identifier(row["policy_id"]), "customer_id": row["customer_id"],
        "policy_type": row["policy_type"], "policy_status": row["policy_status"],
        "premium_amount": row["premium_amount"], "premium_frequency": row["premium_frequency"],
        "payment_method": row["payment_method"], "payment_terms": row["payment_terms"],
        "start_date": row["start_date"], "end_date": row["end_date"]}})


@typed_errors
@trace(logger, redact=_r)
def check_coverage_clause(policy_id: str, scenario_tag: str) -> dict[str, Any]:
    """Deterministic clause check against policy_clauses: covered | excluded | not_specified (never a guess)."""
    row = load_policy(policy_id)
    if not isinstance(scenario_tag, str) or not re.fullmatch(load_config()["scenario_tag_pattern"], scenario_tag):
        fail("INVALID_SCENARIO_TAG", "scenario_tag must be lower_snake_case")
    with db(SERVER) as conn:
        clause = fetch_one(conn, "SELECT clause_type, covered, clause_text FROM policy_clauses "
                                 "WHERE policy_id = ? AND scenario_tag = ?", (policy_id, scenario_tag))
    result = {"policy_number": mask_identifier(row["policy_id"]), "scenario_tag": scenario_tag,
              "result": "not_specified"}
    if clause:
        result.update(result="covered" if clause["covered"] else "excluded", clause_type=clause["clause_type"],
                      clause_text=clause["clause_text"])
    return ok(result)


@typed_errors
@trace(logger, redact=_r)
def get_claim_status(claim_id: str) -> dict[str, Any]:
    """Claim status only - no settlement authority; the claim amount is never returned."""
    validate_id("claim", claim_id)
    with db(SERVER) as conn:
        row = fetch_one(conn, "SELECT c.claim_id, c.policy_id, c.claim_type, c.claim_status, c.incident_date, "
                              "c.submitted_at, c.last_updated_at, p.customer_id FROM claims c "
                              "JOIN policies p ON p.policy_id = c.policy_id WHERE c.claim_id = ?", (claim_id,))
    require_owned(row, "Claim not found")
    return ok({"claim": {
        "claim_id": row["claim_id"], "policy_number": mask_identifier(row["policy_id"]), "customer_id": row["customer_id"],
        "claim_type": row["claim_type"], "claim_status": row["claim_status"], "incident_date": row["incident_date"],
        "submitted_at": row["submitted_at"], "last_updated_at": row["last_updated_at"],
        "note": "Status information only; settlements are decided by the claims team."}})


@as_json_str
@typed_errors
@trace(logger, redact=_r)
def policy_summary_resource(policy_id: str) -> str:
    """Resource policy://{policy_id}/summary - masked, no bulk data."""
    return get_policy_details(policy_id)


TOOLS = {f.__name__: f for f in (get_policy_details, check_coverage_clause, get_claim_status)}
RESOURCES = {"policy": policy_summary_resource}
RESOURCE_TEMPLATES = ["policy://{policy_id}/summary"]
PROMPTS = {}
