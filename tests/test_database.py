import sqlite3

import database as d


def refused(conn, sql):
    try:
        conn.execute(sql)
    except sqlite3.DatabaseError as exc:
        return str(exc)
    raise AssertionError(f"not refused: {sql}")


def test_schema_wal_and_per_server_isolation_with_append_only_ops_tables():
    with d.db("concierge_ops_server") as conn:
        assert d.fetch_one(conn, "SELECT full_name FROM customers WHERE customer_id = 'CUS-20077'")["full_name"] == "Arjun Mehta"
    raw = sqlite3.connect(d.DATABASE_PATH)
    assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    raw.close()

    for server, own, foreign in [("banking_server", "accounts", "policies"), ("insurance_server", "policies", "accounts"),
                                 ("wealth_server", "portfolios", "claims"), ("concierge_ops_server", "customers", "transactions")]:
        conn = d.connect_for(server)
        conn.execute(f"SELECT * FROM {own} LIMIT 1")
        for blocked in (foreign, "sqlite_master", "sqlite_sequence"):
            message = refused(conn, f"SELECT * FROM {blocked}")
            assert "not authorized" in message or "prohibited" in message
        conn.close()

    conn = d.connect_for("insurance_server")  # owns policy_clauses + claims, but a join / sub-select into accounts is refused
    conn.execute("SELECT * FROM policy_clauses LIMIT 1")
    conn.execute("SELECT c.claim_id FROM claims c JOIN policies p ON p.policy_id = c.policy_id LIMIT 1")
    refused(conn, "SELECT p.policy_id FROM policies p JOIN accounts a ON a.customer_id = p.customer_id")
    refused(conn, "SELECT (SELECT balance FROM accounts LIMIT 1)")
    conn.close()

    conn = d.connect_for("banking_server")
    for sql in ("UPDATE accounts SET balance = 0", "DELETE FROM accounts", "DROP TABLE accounts", "ATTACH DATABASE ':memory:' AS x"):
        refused(conn, sql)
    conn.close()
    ops = d.connect_for("concierge_ops_server")
    ops.execute("INSERT INTO audit_log(timestamp, action_type, performed_by, customer_id, outcome) VALUES ('t','a','b','CUS-20077','o')")
    for sql in ("UPDATE audit_log SET outcome = 'x'", "DELETE FROM audit_log", "DELETE FROM interaction_log", "UPDATE customers SET kyc_status = 'failed'"):
        refused(ops, sql)
    ops.rollback()
    ops.close()

    try:
        d.connect_for("rogue_server")
        raise AssertionError("unknown server accepted")
    except ValueError:
        pass
    assert all(d.check_isolation().values())

    from helpers import code_of
    from tool_support import typed_errors

    @typed_errors
    def rogue():
        with d.db("insurance_server") as rogue_conn:
            rogue_conn.execute("SELECT balance FROM accounts")

    assert code_of(rogue()) == "ACCESS_DENIED"  # a database denial reaches the caller as a typed error
