#!/usr/bin/env python3
"""
Process ITU BR IFIC MDB file: convert to SQLite and create analytical views.

Usage:
    python process_itu_mdb.py [INPUT_MDB] [OUTPUT_DIR]

If no arguments provided, uses defaults:
    INPUT_MDB:  /home/niksu/Downloads/ific3074.mdb
    OUTPUT_DIR: /home/niksu/Documents/SinceAI-Traficom
"""

import os
import sys
import subprocess
import sqlite3
import re


def parse_args():
    """Parse command-line arguments with defaults."""
    default_mdb = "/home/niksu/Downloads/ific3074.mdb"
    default_out = "/home/niksu/Documents/SinceAI-Traficom"

    if len(sys.argv) >= 2:
        mdb_path = sys.argv[1]
    else:
        mdb_path = default_mdb

    if len(sys.argv) >= 3:
        output_dir = sys.argv[2]
    else:
        output_dir = default_out

    # Derive output SQLite filename from input MDB basename
    mdb_basename = os.path.splitext(os.path.basename(mdb_path))[0]
    output_db = os.path.join(output_dir, f"{mdb_basename}.sqlite")

    return mdb_path, output_dir, output_db, mdb_basename


def convert_mdb_to_sqlite(mdb_path, output_db):
    """Convert MDB to SQLite using mdbtools. Returns list of table names."""
    os.makedirs(os.path.dirname(output_db), exist_ok=True)

    # Remove old DB if present
    if os.path.exists(output_db):
        os.remove(output_db)

    # Get all table names
    result = subprocess.run(
        ["mdb-tables", "-1", mdb_path], capture_output=True, text=True, check=True
    )
    tables = [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]
    print(f"Found {len(tables)} tables in MDB.")

    conn = sqlite3.connect(output_db)
    cur = conn.cursor()

    # Apply performance tuning PRAGMAs for fast bulk loading
    cur.execute("PRAGMA synchronous = OFF;")
    cur.execute("PRAGMA journal_mode = MEMORY;")
    cur.execute("PRAGMA temp_store = MEMORY;")

    # Extract and apply DDL Schema
    print("\nExtracting schema...")
    schema_res = subprocess.run(
        ["mdb-schema", mdb_path, "sqlite"], capture_output=True, text=True, check=True
    )
    schema_sql = schema_res.stdout.strip()

    if schema_sql:
        try:
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

    # Export and Insert Data for Each Table
    print("\nImporting table data...")
    for i, table in enumerate(tables, 1):
        export_res = subprocess.run(
            ["mdb-export", "-I", "sqlite", mdb_path, table],
            capture_output=True,
            text=True,
            check=True,
        )
        sql_text = export_res.stdout.strip()

        if not sql_text:
            print(f"  [{i}/{len(tables)}] {table}: empty or no data, skipping")
            continue

        try:
            cur.execute("BEGIN TRANSACTION;")
            cur.executescript(sql_text)
            conn.commit()
            print(f"  [{i}/{len(tables)}] {table}: successfully imported")
        except Exception as e:
            conn.rollback()
            print(f"  [{i}/{len(tables)}] {table}: ERROR during bulk insert: {e}")

    # Re-enable safe journaling
    cur.execute("PRAGMA synchronous = NORMAL;")
    conn.commit()

    return conn, tables


def create_space_ific_view(conn, ific_number=3074):
    """Create the Space IFIC PFD inputs view for a specific IFIC number."""
    cur = conn.cursor()

    view_statement = f"""
CREATE VIEW IF NOT EXISTS vw_space_{ific_number}_pfd_inputs AS
SELECT 
    n.ntc_id,
    n.adm AS submitting_country,
    n.wic_no AS issue_no,
    n.ntf_rsn,
    n.st_cur,
    g.grp_id,
    g.beam_name,
    g.emi_rcp,
    f.freq_min,
    f.freq_max,
    f.freq_mhz,
    e.pwr_ds_max,
    sb.gain AS peak_antenna_gain,
    geo.long_nom AS gso_longitude,
    t.coord_prov AS affected_country,
    t.agree_st
FROM notice n
JOIN grp g ON n.ntc_id = g.ntc_id
JOIN freq f ON g.grp_id = f.grp_id
LEFT JOIN emiss e ON g.grp_id = e.grp_id
LEFT JOIN s_beam sb ON n.ntc_id = sb.ntc_id AND g.beam_name = sb.beam_name
LEFT JOIN geo ON n.ntc_id = geo.ntc_id
LEFT JOIN tr_aff_ntw t ON n.ntc_id = t.ntc_id AND t.coord_prov = 'FIN'
WHERE n.wic_no = {ific_number}
    AND g.emi_rcp = 'E';
"""

    cur.execute(view_statement)
    conn.commit()
    print(f"\nCreated view: vw_space_{ific_number}_pfd_inputs")


def main():
    mdb_path, output_dir, output_db, mdb_basename = parse_args()

    # Extract IFIC number from filename (e.g., "ific3074" -> 3074)
    match = re.search(r'(\d+)', mdb_basename)
    ific_number = int(match.group(1)) if match else 3074

    print(f"Input MDB:  {mdb_path}")
    print(f"Output DB:  {output_db}")
    print(f"IFIC number: {ific_number}")
    print(f"Output dir: {output_dir}")

    if not os.path.exists(mdb_path):
        print(f"ERROR: MDB file not found: {mdb_path}")
        sys.exit(1)

    # Step 1: Convert MDB to SQLite
    conn, tables = convert_mdb_to_sqlite(mdb_path, output_db)

    # Step 2: Create analytical view (only for IFIC 3074 or if tables look like Space IFIC)
    # Check if required tables exist
    required_tables = {'notice', 'grp', 'freq', 'emiss', 's_beam', 'geo', 'tr_aff_ntw'}
    if required_tables.issubset(set(tables)):
        create_space_ific_view(conn, ific_number)
    else:
        print("\nRequired tables for Space IFIC view not found; skipping view creation.")
        print(f"Available tables: {', '.join(sorted(tables))}")

    # Close connection
    conn.close()
    print(f"\nProcessing finished successfully: {output_db}")


if __name__ == "__main__":
    main()