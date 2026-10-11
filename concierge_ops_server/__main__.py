Codeshare logo
 ShareSign UpLog In

 
1
"""banking_server logic. Data access: accounts + transactions tables ONLY (enforced by database.connect_for)."""
2
from typing import Any
3
​
4
from config import load_config
5
from database import db, fetch_all, fetch_one
6
from errors import fail
7
from guardrails import (check_scope, mask_identifier, minimize_account_fields, redact_for_logging, require_scope,
8
                        validate_id)
9
from logging_config import get_logger, trace
10
from tool_support import as_json_str, enforce_max_scope, enforce_owner, ok, require_owned, typed_errors
11
​
12
SERVER = "banking_server"
13
logger = get_logger(SERVER)
14
​
15
​
16
def _r(data):
17
    return redact_for_logging(data)
18
​
19
​
20
@typed_errors
21
@trace(logger, redact=_r)
22
def get_account_summary(account_id: str, caller_scope: str = "summary") -> dict[str, Any]:
23
    """Account lookup; returned fields are minimised per caller_scope (summary | service | full)."""
24
    validate_id("account", account_id)
25
    check_scope(caller_scope)
26
    enforce_max_scope(caller_scope)
27
    with db(SERVER) as conn:
28
        row = fetch_one(conn, "SELECT * FROM accounts WHERE account_id = ?", (account_id,))
29
    require_owned(row, "Account not found")
30
    return ok({"account": minimize_account_fields(row, caller_scope)})
31
​
32
​
33
@typed_errors
34
@trace(logger, redact=_r)
35
def get_transaction_history(account_id: str, limit: int = 10, caller_scope: str = "service") -> dict[str, Any]:
36
    """Recent transactions, most recent first (needs a caller_scope at or above the configured minimum)."""
37
    validate_id("account", account_id)
38
    require_scope(caller_scope, load_config()["scopes"]["transactions_min_scope"])
39
    enforce_max_scope(caller_scope)




Wegic
Modern, dynamic and visually stunning websites created from a simple business description.
px
Hide Ads
