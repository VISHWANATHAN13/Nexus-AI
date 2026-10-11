"""Tiny test database built from the project's own schema (database.SCHEMA_SQL) - no Faker, no 10,000-row seed.
Mirrors the showcase / stress fixtures of seed_database.py (same IDs, same meaning)."""
import sqlite3

import database

NOW = "2026-10-01T10:00:00"


def seed_minimal():
    """Create meridian.db (path from MERIDIAN_DB) and insert the showcase + stress fixtures."""
    path = database.DATABASE_PATH
    for suffix in ("", "-wal", "-shm"):
        if path.with_name(path.name + suffix).exists():
            path.with_name(path.name + suffix).unlink()
    database.create_database()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)", [
        ("CUS-20077", "Arjun Mehta", "arjun.mehta@example.com", "+919876543210", "premium", "verified", NOW, NOW),
        ("CUS-10002", "Stress Test Customer", "stress.customer@example.com", "+919800000002", "standard", "verified", NOW, NOW),
        ("CUS-10004", "Hard Escalation Customer", "hard.escalation@example.com", "+919800000004", "premium", "verified", NOW, NOW),
        ("CUS-00005", "Pending Kyc Customer", "pending@example.com", "+919800000005", "standard", "pending", NOW, NOW),
    ])
    conn.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?)", [
        ("ACC-20077", "CUS-20077", "savings", "INR", 125000.0, "active", 1, None, NOW),
        ("ACC-10002", "CUS-10002", "savings", "INR", 50000.0, "restricted", 0,
         "Temporary compliance restriction: third-party debit mandates blocked", NOW),
    ])
    conn.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)", [
        ("TXN-1", "ACC-20077", "credit", 45000.0, "INR", "Salary credit", "completed", "2026-09-30"),
        ("TXN-2", "ACC-20077", "debit", 3120.4, "INR", "Grocery store", "completed", "2026-09-28"),
        ("TXN-3", "ACC-20077", "payment", 2210.0, "INR", "Electricity bill", "completed", "2026-09-25"),
    ])
    conn.executemany("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("POL-IN-30091", "CUS-20077", "motor", "active", 3200.0, "monthly", "auto_debit",
         "Premium may be paid by auto-debit from a Meridian savings account in good standing.", "2026-01-01", "2026-12-31"),
        ("POL-IN-10002", "CUS-10002", "motor", "active", 3200.0, "monthly", "auto_debit",
         "Premium must be paid according to the agreed payment schedule.", "2026-01-01", "2026-12-31"),
    ])
    conn.executemany("INSERT INTO policy_clauses(policy_id, scenario_tag, clause_type, covered, clause_text) VALUES (?,?,?,?,?)", [
        ("POL-IN-30091", "auto_debit", "payment", 1, "Premium payment by auto-debit is permitted."),
        ("POL-IN-30091", "theft", "cover", 1, "Theft of the insured vehicle is covered."),
        ("POL-IN-30091", "flood_damage", "exclusion", 0, "Flood damage is excluded."),
    ])
    conn.execute("INSERT INTO claims VALUES ('CLM-30091','POL-IN-30091','accident','under_review',85000.0,'2026-09-10',?,?)", (NOW, NOW))
    conn.execute("INSERT INTO portfolios VALUES ('PORT-20077','CUS-20077','Arjun Long-Term Portfolio','moderate',850000.0,'INR','active',?)", (NOW,))
    conn.executemany("INSERT INTO investment_products VALUES (?,?,?,?,?,?,?,?)", [
        ("PROD-01-01", "Secure Bond Fund", "debt", "low", 1000.0, "Steady income", "daily", "active"),
        ("PROD-02-02", "Growth Equity Fund", "equity", "high", 5000.0, "Long-term growth", "T+3", "active"),
        ("PROD-03-03", "Balanced Advantage Fund", "hybrid", "medium", 2500.0, "Mixed growth and income", "T+2", "active"),
    ])
    conn.commit()
    conn.close()
