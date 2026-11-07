#!/usr/bin/env python3
"""
Process ITU BR IFIC MDB file: convert to SQLite and create analytical views.

Usage:
    python process_itu_mdb.py [INPUT] [OUTPUT_DIR]

INPUT can be:
    - Path to local .mdb file
    - IFIC number (e.g., 3074) → downloads from https://www.itu.int/sns/ific10/ific3074.zip
    - Full ITU zip URL

If no arguments provided, uses defaults:
    INPUT:      3074 (downloads ific3074.zip from ITU)
    OUTPUT_DIR: /home/niksu/Documents/SinceAI-Traficom
"""

import os
import sys
import subprocess
import sqlite3
import re
import urllib.request
import zipfile
import tempfile
import shutil


ITU_BASE_URL = "https://www.itu.int/sns/ific10/ific{num}.zip"


def parse_args():
    """Parse command-line arguments with defaults."""
    default_input = "3074"  # IFIC number -> auto-download
    default_out = "/home/niksu/Documents/SinceAI-Traficom"

    if len(sys.argv) >= 2:
        input_arg = sys.argv[1]
    else:
        input_arg = default_input

    if len(sys.argv) >= 3:
        output_dir = sys.argv[2]
    else:
        output_dir = default_out

    return input_arg, output_dir


def resolve_input(input_arg, work_dir):
    """
    Resolve input argument to a local MDB path.
    Returns (mdb_path, ific_number, mdb_basename)
    """
    # Case 1: It's a URL
    if input_arg.startswith("http://") or input_arg.startswith("https://"):
        return download_and_extract(input_arg, work_dir)

    # Case 2: It's a local .mdb file
    if input_arg.endswith(".mdb") and os.path.exists(input_arg):
        mdb_basename = os.path.splitext(os.path.basename(input_arg))[0]
        match = re.search(r"(\d+)", mdb_basename)
        ific_number = int(match.group(1)) if match else 0
        return input_arg, ific_number, mdb_basename

    # Case 3: It's an IFIC number (digits only)
    if input_arg.isdigit():
        ific_number = int(input_arg)
        url = ITU_BASE_URL.format(num=ific_number)
        return download_and_extract(url, work_dir)

    # Case 4: It's a local .zip file
    if input_arg.endswith(".zip") and os.path.exists(input_arg):
        return extract_zip(input_arg, work_dir)

    print(f"ERROR: Cannot resolve input: {input_arg}")
    print("  Expected: local .mdb path, IFIC number (e.g., 3074), ITU zip URL, or local .zip path")
    sys.exit(1)


def download_and_extract(url, work_dir):
    """Download zip from URL and extract MDB. Returns (mdb_path, ific_number, mdb_basename)."""
    print(f"Downloading: {url}")
    zip_path = os.path.join(work_dir, os.path.basename(url))

    try:
        urllib.request.urlretrieve(url, zip_path)
        print(f"  Downloaded to: {zip_path}")
    except urllib.error.HTTPError as e:
        print(f"ERROR: Failed to download (HTTP {e.code}): {url}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: Network error: {e}")
        sys.exit(1)

    return extract_zip(zip_path, work_dir)


def extract_zip(zip_path, work_dir):
    """Extract MDB from zip file. Returns (mdb_path, ific_number, mdb_basename)."""
    print(f"Extracting: {zip_path}")
    mdb_files = []

    with zipfile.ZipFile(zip_path, 'r') as zf:
        # Find .mdb files in zip
        for name in zf.namelist():
            if name.lower().endswith(".mdb"):
                mdb_files.append(name)

        if not mdb_files:
            print("ERROR: No .mdb file found in zip")
            sys.exit(1)

        # Extract the first (or only) .mdb file
        mdb_name = mdb_files[0]
        zf.extract(mdb_name, work_dir)
        mdb_path = os.path.join(work_dir, mdb_name)
        print(f"  Extracted: {mdb_path}")

    mdb_basename = os.path.splitext(os.path.basename(mdb_path))[0]
    match = re.search(r"(\d+)", mdb_basename)
    ific_number = int(match.group(1)) if match else 0

    return mdb_path, ific_number, mdb_basename


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
                        print(
                            f"    Ignored Schema error on: {stmt[:60]}... -> {inner_e}"
                        )
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
    input_arg, output_dir = parse_args()

    # Create temp directory for downloads/extractions
    with tempfile.TemporaryDirectory(prefix="ific_") as work_dir:
        mdb_path, ific_number, mdb_basename = resolve_input(input_arg, work_dir)

        # Derive output SQLite filename from input MDB basename
        output_db = os.path.join(output_dir, f"{mdb_basename}.sqlite")

        print(f"Input MDB:  {mdb_path}")
        print(f"Output DB:  {output_db}")
        print(f"IFIC number: {ific_number}")
        print(f"Output dir: {output_dir}")

        if not os.path.exists(mdb_path):
            print(f"ERROR: MDB file not found: {mdb_path}")
            sys.exit(1)

        # Step 1: Convert MDB to SQLite
        conn, tables = convert_mdb_to_sqlite(mdb_path, output_db)

        # Step 2: Create analytical view (only if tables look like Space IFIC)
        required_tables = {"notice", "grp", "freq", "emiss", "s_beam", "geo", "tr_aff_ntw"}
        if required_tables.issubset(set(tables)):
            create_space_ific_view(conn, ific_number)
        else:
            print(
                "\nRequired tables for Space IFIC view not found; skipping view creation."
            )
            print(f"Available tables: {', '.join(sorted(tables))}")

        # Close connection
        conn.close()
        print(f"\nProcessing finished successfully: {output_db}")


if __name__ == "__main__":
    main()