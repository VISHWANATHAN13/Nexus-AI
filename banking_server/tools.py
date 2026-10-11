"""banking_server logic. Data access: accounts + transactions tables ONLY (enforced by database.connect_for)."""
from typing import Any

from config import load_config
from database import db, fetch_all, fetch_one
from errors import fail
from guardrails import (check_scope, mask_identifier, minimize_account_fields, redact_for_logging, require_scope,
                        validate_id)
from logging_config import get_logger, trace
from tool_support import as_json_str, enforce_max_scope, enforce_owner, ok, require_owned, typed_errors

SERVER = "banking_server"
logger = get_logger(SERVER)


def _r(data):
    return redact_for_logging(data)


@typed_errors
@trace(logger, redact=_r)
def get_account_summary(account_id: str, caller_scope: str = "summary") -> dict[str, Any]:
    """Account lookup; returned fields are minimised per caller_scope (summary | service | full)."""
    validate_id("account", account_id)
    check_scope(caller_scope)
    enforce_max_scope(caller_scope)
    with db(SERVER) as conn:
        row = fetch_one(conn, "SELECT * FROM accounts WHERE account_id = ?", (account_id,))
    require_owned(row, "Account not found")
    return ok({"account": minimize_account_fields(row, caller_scope)})


@typed_errors
@trace(logger, redact=_r)
def get_transaction_history(account_id: str, limit: int = 10, caller_scope: str = "service") -> dict[str, Any]:
    """Recent transactions, most recent first (needs a caller_scope at or above the configured minimum)."""
    validate_id("account", account_id)
    require_scope(caller_scope, load_config()["scopes"]["transactions_min_scope"])
    enforce_max_scope(caller_scope)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        fail("INVALID_ARGUMENT", "limit must be an integer between 1 and 50")
    with db(SERVER) as conn:
        require_owned(fetch_one(conn, "SELECT customer_id FROM accounts WHERE account_id = ?", (account_id,)), "Account not found")
        rows = fetch_all(conn, "SELECT transaction_id, transaction_type, amount, currency, description, transaction_status, "
                               "transaction_date FROM transactions WHERE account_id = ? "
                               "ORDER BY transaction_date DESC, transaction_id DESC LIMIT ?", (account_id, limit))
    return ok({"account_number": mask_identifier(account_id), "transactions": rows})


@typed_errors
@trace(logger, redact=_r)
def get_linked_accounts(customer_id: str, caller_scope: str = "summary") -> dict[str, Any]:
    """All accounts linked to a customer, same field minimisation as get_account_summary."""
    validate_id("customer", customer_id)
    check_scope(caller_scope)
    enforce_max_scope(caller_scope)
    enforce_owner(customer_id)
    with db(SERVER) as conn:
        rows = fetch_all(conn, "SELECT * FROM accounts WHERE customer_id = ? ORDER BY account_id", (customer_id,))
    return ok({"count": len(rows), "accounts": [minimize_account_fields(r, caller_scope) for r in rows]})


@as_json_str
@typed_errors
@trace(logger, redact=_r)
def account_summary_resource(account_id: str) -> str:
    """Resource account://{account_id}/summary - least-privilege view, always masked."""
    return get_account_summary(account_id, "summary")


TOOLS = {f.__name__: f for f in (get_account_summary, get_transaction_history, get_linked_accounts)}
RESOURCES = {"account": account_summary_resource}  # URI scheme -> handler
RESOURCE_TEMPLATES = ["account://{account_id}/summary"]
PROMPTS = {}
