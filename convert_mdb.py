#!/usr/bin/env python3
"""Convert an ITU BR IFIC MDB (MS Access) file to SQLite using mdbtools and sqlite3.

    python convert_mdb.py ific3080.mdb [ific3080.sqlite]

Every table is copied; afterwards the row count of each table is compared with
mdb-count and a mismatch raises an error, so a conversion is never silently lossy.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def convert(mdb_path: Path, output_db: Path, log=print) -> dict[str, int]:
    """Convert mdb_path into output_db; return {table: rows}. Raises on a row-count mismatch."""
    mdb_path, output_db = Path(mdb_path), Path(output_db)
    tmp = output_db.with_suffix(".sqlite.partial")
    if tmp.exists():
        tmp.unlink()
    tables = [t.strip() for t in _run("mdb-tables", "-1", str(mdb_path)).splitlines() if t.strip()]
    log(f"  {mdb_path.name}: {len(tables)} tables")

    conn = sqlite3.connect(tmp)
    cur = conn.cursor()
    cur.execute("PRAGMA synchronous = OFF;")
    cur.execute("PRAGMA journal_mode = MEMORY;")
    cur.execute("PRAGMA temp_store = MEMORY;")

    schema_sql = _run("mdb-schema", str(mdb_path), "sqlite").strip()
    try:
        cur.executescript(schema_sql)
    except sqlite3.Error:
        # Fall back to statement-by-statement so one odd DDL statement does not stop the rest.
        for stmt in schema_sql.split(";"):
            if stmt.strip():
                try:
                    cur.execute(stmt + ";")
                except sqlite3.Error as exc:
                    log(f"    ignored schema statement: {stmt.strip()[:60]}... ({exc})")
    conn.commit()

    counts: dict[str, int] = {}
    for table in tables:
        sql_text = _run("mdb-export", "-I", "sqlite", str(mdb_path), table).strip()
        if sql_text:
            cur.execute("BEGIN TRANSACTION;")
            cur.executescript(sql_text)
            conn.commit()
        counts[table] = cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        expected = int(_run("mdb-count", str(mdb_path), table).strip() or 0)
        if counts[table] != expected:
            conn.close()
            raise RuntimeError(f"{mdb_path.name}: table {table} has {counts[table]} rows, MDB has {expected}")

    cur.execute("PRAGMA synchronous = NORMAL;")
    conn.commit()
    conn.close()
    os.replace(tmp, output_db)
    log(f"  converted {sum(counts.values())} rows in {len(counts)} tables, all row counts verified")
    return counts


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    source = Path(sys.argv[1])
    convert(source, Path(sys.argv[2]) if len(sys.argv) > 2 else source.with_suffix(".sqlite"))
