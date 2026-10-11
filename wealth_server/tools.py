"""wealth_server logic. Data access: portfolios + investment_products ONLY (no account or policy data)."""
from typing import Any

from config import load_config
from database import db, fetch_one
from errors import fail
from guardrails import check_scope, minimize_portfolio_fields, minimize_product_fields, redact_for_logging, validate_id
from logging_config import get_logger, trace
from tool_support import enforce_max_scope, ok, require_owned, typed_errors

SERVER = "wealth_server"
logger = get_logger(SERVER)


def _r(data):
    return redact_for_logging(data)


@trace(logger, redact=_r)
def load_product(product_id):
    validate_id("product", product_id)
    with db(SERVER) as conn:
        row = fetch_one(conn, "SELECT * FROM investment_products WHERE product_id = ?", (product_id,))
    if row is None:
        fail("NOT_FOUND", "Investment product not found")
    return row


@typed_errors
@trace(logger, redact=_r)
def get_portfolio_summary(portfolio_id: str, caller_scope: str = "summary") -> dict[str, Any]:
    """Portfolio lookup, fields minimised per caller_scope; portfolio number masked."""
    validate_id("portfolio", portfolio_id)
    check_scope(caller_scope)
    enforce_max_scope(caller_scope)
    with db(SERVER) as conn:
        row = fetch_one(conn, "SELECT * FROM portfolios WHERE portfolio_id = ?", (portfolio_id,))
    require_owned(row, "Portfolio not found")
    return ok({"portfolio": minimize_portfolio_fields(row, caller_scope)})


@typed_errors
@trace(logger, redact=_r)
def get_investment_product_details(product_id: str, caller_scope: str = "service") -> dict[str, Any]:
    """Single investment product lookup (catalogue data, fields per caller_scope)."""
    check_scope(caller_scope)
    enforce_max_scope(caller_scope)
    return ok({"product": minimize_product_fields(load_product(product_id), caller_scope)})


@typed_errors
@trace(logger, redact=_r)
def check_product_suitability(product_id: str, risk_profile: str, caller_scope: str = "summary") -> dict[str, Any]:
    """Suitability check only: read-only, uses nothing about the customer except the risk profile passed in."""
    check_scope(caller_scope)
    allowed = load_config()["suitability"]
    if risk_profile not in allowed:
        fail("INVALID_RISK_PROFILE", "Unknown risk_profile", {"allowed": list(allowed)})
    product = load_product(product_id)
    suitable = product["risk_level"] in allowed[risk_profile] and product["product_status"] == "active"
    reason = (f"{product['product_name']} (risk level {product['risk_level']}, status {product['product_status']}) is "
              f"{'suitable' if suitable else 'not suitable'} for a {risk_profile} risk profile.")
    return ok({"product_id": product["product_id"], "risk_profile": risk_profile, "risk_level": product["risk_level"],
               "suitable": suitable, "reason": reason})


TOOLS = {f.__name__: f for f in (get_portfolio_summary, get_investment_product_details, check_product_suitability)}
RESOURCES = {}
RESOURCE_TEMPLATES = []
PROMPTS = {}
