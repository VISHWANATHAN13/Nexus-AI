"""Show everything Nexus knows: every database table (row counts) and the knowledge-base files.

    python show_data.py             counts
    python show_data.py --rows 3    plus the first 3 rows of every table
"""
import sqlite3
import sys

from config import ROOT, load_config
from database import DATABASE_PATH
from knowledge import knowledge_folders

rows_to_show = int(sys.argv[sys.argv.index("--rows") + 1]) if "--rows" in sys.argv else 0
if not DATABASE_PATH.exists():
    sys.exit("database missing: run  python database.py  then  python seed_database.py")
conn = sqlite3.connect(DATABASE_PATH)  # admin view: unrestricted on purpose; the MCP servers use restricted connections
owner = {t: s for s, spec in load_config()["database"]["server_tables"].items() for t in spec["read"]}
print(f"\nSTRUCTURED DATA -> {DATABASE_PATH}\n\n{'table':<22}{'rows':>9}   owned by")
total = 0
for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
    count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    total += count
    print(f"{name:<22}{count:>9,}   {owner.get(name, '')}")
    if rows_to_show:
        for row in conn.execute(f"SELECT * FROM {name} LIMIT {rows_to_show}").fetchall():
            print("     ", " | ".join(str(v)[:28] for v in row))
print(f"{'TOTAL':<22}{total:>9,}")
print(f"\nUNSTRUCTURED DATA -> {ROOT / load_config()['knowledge']['root']}")
files_total = 0
for category, folder in knowledge_folders():
    files = [p for p in folder.rglob("*") if p.is_file()]
    files_total += len(files)
    print(f"  {category:<26}{len(files):>4} files   e.g. {', '.join(p.name for p in files[:3])}")
print(f"  {'TOTAL':<26}{files_total:>4} files")
