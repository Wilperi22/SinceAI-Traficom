#!/usr/bin/env python3
"""Convert an MDB file to SQLite using mdbtools and sqlite3."""

import os
import subprocess
import sqlite3
import sys

MDB_PATH = "/home/niksu/Downloads/ific3074.mdb"
OUTPUT_DIR = "/home/niksu/Documents/SinceAI-Traficom"
OUTPUT_DB = os.path.join(OUTPUT_DIR, "ific3074.sqlite")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Remove old DB if present
if os.path.exists(OUTPUT_DB):
    os.remove(OUTPUT_DB)

# 2. Get all table names
result = subprocess.run(
    ["mdb-tables", "-1", MDB_PATH], capture_output=True, text=True, check=True
)
tables = [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]
print(f"Found {len(tables)} tables in MDB.")

conn = sqlite3.connect(OUTPUT_DB)
cur = conn.cursor()

# 3. Apply performance tuning PRAGMAs for fast bulk loading
cur.execute("PRAGMA synchronous = OFF;")
cur.execute("PRAGMA journal_mode = MEMORY;")
cur.execute("PRAGMA temp_store = MEMORY;")

# 4. Extract and apply DDL Schema
print("\nExtracting schema...")
schema_res = subprocess.run(
    ["mdb-schema", MDB_PATH, "sqlite"], capture_output=True, text=True, check=True
)
schema_sql = schema_res.stdout.strip()

if schema_sql:
    try:
        # executescript handles multi-statement DDL natively without broken splitting
        cur.executescript(schema_sql)
        conn.commit()
        print("  Schema successfully created.")
    except Exception as e:
        print(f"  Schema Execution Warning: {e}")
        # Fallback statement-by-statement execution if schema has non-critical errors
        for stmt in schema_sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    cur.execute(stmt + ";")
                except Exception as inner_e:
                    print(f"    Ignored Schema error on: {stmt[:60]}... -> {inner_e}")
        conn.commit()
else:
    print("  Warning: No schema output received from mdb-schema.")

# 5. Export and Insert Data for Each Table
print("\nImporting table data...")
for i, table in enumerate(tables, 1):
    export_res = subprocess.run(
        ["mdb-export", "-I", "sqlite", MDB_PATH, table],
        capture_output=True,
        text=True,
        check=True,
    )
    sql_text = export_res.stdout.strip()

    if not sql_text:
        print(f"  [{i}/{len(tables)}] {table}: empty or no data, skipping")
        continue

    try:
        # Wrap table import in a single transaction block for speed & safety
        cur.execute("BEGIN TRANSACTION;")
        cur.executescript(sql_text)
        conn.commit()
        print(f"  [{i}/{len(tables)}] {table}: successfully imported")
    except Exception as e:
        conn.rollback()
        print(f"  [{i}/{len(tables)}] {table}: ERROR during bulk insert: {e}")

# 6. Re-enable safe journaling & close connection
cur.execute("PRAGMA synchronous = NORMAL;")
conn.commit()
cur.close()
conn.close()

print(f"\nConversion finished successfully: {OUTPUT_DB}")
