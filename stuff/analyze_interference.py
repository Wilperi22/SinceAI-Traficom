#!/usr/bin/env python3
"""Find ITU notice frequency/PFD violations against a Finnish Excel file.

The Excel file is loaded first. Its frequency intervals are then used to build
a parameterized SQLite query, so the database does not need to be loaded into
pandas or memory in full.

The comparison is deliberately conservative: a record is an Error only when
the intervals overlap and both the ITU PFD and a verified PFD limit are present.
The two PFD values must use the same unit and reference bandwidth.

This tool does not calculate satellite PFD for every Finnish station. It uses
the notified ITU PFD and records a review item when the applicable limit is
missing. Finnish station calculations belong to the separate terrestrial
field-strength workflow.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook


# These are the notice codes present in the supplied converted IFIC database.
# Override with --notice-types after confirming the mapping for a new extract.
DEFAULT_NOTICE_TYPES = ("N", "S", "G", "R")
FREQUENCY_UNITS_TO_MHZ = {"hz": 1e-6, "khz": 1e-3, "mhz": 1.0, "ghz": 1e3}


@dataclass(frozen=True)
class InterestBand:
    row_number: int
    station_number: str
    frequency_min_mhz: float
    frequency_max_mhz: float
    pfd_limit: float
    pfd_unit: str


def normalise_header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def parse_measurement(value: Any, default_unit: str = "mhz") -> float:
    """Parse a number optionally followed by Hz/kHz/MHz/GHz."""
    if value is None or str(value).strip() == "":
        raise ValueError("empty measurement")
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?", str(value))
    if not match:
        raise ValueError(f"cannot parse measurement {value!r}")
    number = float(match.group(0).replace(",", "."))
    unit_match = re.search(r"(ghz|mhz|khz|hz)\b", str(value), re.I)
    unit = unit_match.group(1).lower() if unit_match else default_unit.lower()
    if unit not in FREQUENCY_UNITS_TO_MHZ:
        raise ValueError(f"unsupported unit {unit!r} in {value!r}")
    return number * FREQUENCY_UNITS_TO_MHZ[unit]


def find_column(headers: dict[str, int], aliases: Iterable[str]) -> int | None:
    for alias in aliases:
        if normalise_header(alias) in headers:
            return headers[normalise_header(alias)]
    return None


def load_interest_bands(
    path: Path,
    pfd_limit_column: str,
    pfd_unit: str,
    default_frequency_unit: str,
) -> list[InterestBand]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    rows = sheet.iter_rows(values_only=True)
    header_row: tuple[Any, ...] | None = None
    header_number = 0
    buffered: list[tuple[int, tuple[Any, ...]]] = []

    for row_number, row in enumerate(rows, 1):
        buffered.append((row_number, row))
        headers = {normalise_header(value): index for index, value in enumerate(row)}
        if "station number" in headers or "asemanro" in headers:
            header_row, header_number = row, row_number
            break
    if header_row is None:
        raise ValueError("could not find the Excel header row")

    headers = {normalise_header(value): index for index, value in enumerate(header_row)}
    station_col = find_column(headers, ("Station number", "Asemanro"))
    min_col = find_column(headers, ("Frequency min", "Frequency minimum", "From"))
    max_col = find_column(headers, ("Frequency max", "Frequency maximum", "To"))
    tx_col = find_column(headers, ("TX frequency", "TX-taajuus"))
    rx_col = find_column(headers, ("RX frequency", "RX-taajuus"))
    bandwidth_col = find_column(headers, ("Channel bandwidth", "Kan.lev."))
    limit_col = find_column(headers, (pfd_limit_column,))

    if limit_col is None:
        raise ValueError(
            f"Excel column {pfd_limit_column!r} was not found. The supplied "
            "workbook has no explicit PFD protection-limit column; add one or "
            "pass the correct column name. No Errors were generated."
        )
    if min_col is None or max_col is None:
        if tx_col is None and rx_col is None:
            raise ValueError("Excel needs min/max frequency or TX/RX frequency columns")
        if bandwidth_col is None:
            raise ValueError("center-frequency input requires a bandwidth column")

    bands: list[InterestBand] = []
    pending_rows = buffered + list(enumerate(rows, header_number + 1))
    for row_number, row in pending_rows:
        if row_number <= header_number:
            continue
        def cell(column: int | None) -> Any:
            return row[column] if column is not None and column < len(row) else None

        try:
            limit = float(str(cell(limit_col)).replace(",", "."))
        except (TypeError, ValueError):
            continue
        station = str(cell(station_col) or row_number)
        intervals: list[tuple[float, float]] = []
        if min_col is not None and max_col is not None and cell(min_col) not in (None, ""):
            intervals.append((parse_measurement(cell(min_col), default_frequency_unit),
                              parse_measurement(cell(max_col), default_frequency_unit)))
        else:
            bandwidth = parse_measurement(cell(bandwidth_col), default_frequency_unit)
            for center in (cell(tx_col), cell(rx_col)):
                if center not in (None, ""):
                    center_mhz = parse_measurement(center, default_frequency_unit)
                    intervals.append((center_mhz - bandwidth / 2, center_mhz + bandwidth / 2))
        for lower, upper in intervals:
            if lower > upper:
                lower, upper = upper, lower
            bands.append(InterestBand(row_number, station, lower, upper, limit, pfd_unit))
    if not bands:
        raise ValueError("no usable Excel frequency rows were found")
    return bands


def ensure_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_freq_range ON freq(freq_min, freq_max);
        CREATE INDEX IF NOT EXISTS idx_freq_ntc_id ON freq(ntc_id);
        CREATE INDEX IF NOT EXISTS idx_notice_type_ntc_id ON notice(ntc_type, ntc_id);
        CREATE INDEX IF NOT EXISTS idx_c_pfd_range ON c_pfd(ntc_id, freq_min, freq_max);
        """
    )
    connection.commit()


def candidate_rows(
    connection: sqlite3.Connection,
    bands: list[InterestBand],
    notice_types: tuple[str, ...],
    batch_size: int = 500,
) -> Iterable[sqlite3.Row]:
    type_placeholders = ",".join("?" for _ in notice_types)
    overlap_sql = " OR ".join("(f.freq_min < ? AND f.freq_max > ?)" for _ in bands)
    sql = f"""
        SELECT f.ntc_id, n.ntc_type, n.adm, n.prov, f.emi_rcp, f.grp_id,
               f.seq_no, f.freq_min, f.freq_max, f.bdwdth,
               c.freq_min AS pfd_freq_min, c.freq_max AS pfd_freq_max, c.pfd
        FROM freq AS f
        JOIN notice AS n ON n.ntc_id = f.ntc_id
                LEFT JOIN c_pfd AS c
                    ON c.ntc_id = f.ntc_id
                 AND c.freq_min < f.freq_max
                 AND c.freq_max > f.freq_min
        WHERE n.ntc_type IN ({type_placeholders})
          AND ({overlap_sql})
        ORDER BY f.ntc_id, f.seq_no
    """
    params: list[Any] = list(notice_types)
    for band in bands:
        params.extend((band.frequency_max_mhz, band.frequency_min_mhz))
    connection.row_factory = sqlite3.Row
    cursor = connection.execute(sql, params)
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        yield from rows


def analyse(
    db_path: Path,
    excel_path: Path,
    notice_types: tuple[str, ...],
    audit_path: Path,
    **excel_options: Any,
) -> list[dict[str, Any]]:
    bands = load_interest_bands(excel_path, **excel_options)
    audit_lines = [
        "INTERFERENCE ANALYSIS AUDIT",
        f"Database: {db_path}",
        f"Excel: {excel_path}",
        f"Notice types: {', '.join(notice_types)}",
        "PFD method: use notified c_pfd.pfd; do not calculate one per Finnish station",
        "",
        f"Loaded Finnish frequency bands: {len(bands)}",
    ]
    with sqlite3.connect(db_path) as connection:
        ensure_indexes(connection)
        audit_lines.append("SQLite indexes: verified/created on freq, notice, and c_pfd")
        errors: list[dict[str, Any]] = []
        candidate_count = 0
        for row in candidate_rows(connection, bands, notice_types):
            candidate_count += 1
            for band in bands:
                overlap_min = max(row["freq_min"], band.frequency_min_mhz)
                overlap_max = min(row["freq_max"], band.frequency_max_mhz)
                check_id = f"ntc_id={row['ntc_id']} excel_row={band.row_number}"
                if overlap_min >= overlap_max:
                    audit_lines.append(f"CHECK {check_id}: NO_OVERLAP")
                    continue
                pfd_overlap = (
                    row["pfd_freq_min"] is not None
                    and row["pfd_freq_max"] is not None
                    and row["pfd_freq_min"] < band.frequency_max_mhz
                    and row["pfd_freq_max"] > band.frequency_min_mhz
                )
                if not pfd_overlap:
                    audit_lines.append(
                        f"REVIEW {check_id}: PFD_FREQUENCY_RANGE_DOES_NOT_OVERLAP "
                        f"notice={row['freq_min']}-{row['freq_max']}MHz"
                    )
                    continue
                if row["pfd"] is None:
                    audit_lines.append(f"REVIEW {check_id}: MISSING_ITU_PFD_VALUE")
                    continue
                if row["pfd"] <= band.pfd_limit:
                    audit_lines.append(
                        f"PASS {check_id}: PFD_WITHIN_LIMIT "
                        f"pfd={row['pfd']} limit={band.pfd_limit} {band.pfd_unit}"
                    )
                    continue
                reason = (
                    "Frequency intervals overlap and the ITU PFD exceeds the "
                    "configured Finnish protection limit"
                )
                audit_lines.append(
                    f"ERROR {check_id}: {reason}; pfd={row['pfd']} "
                    f"limit={band.pfd_limit} {band.pfd_unit}"
                )
                errors.append({
                    "severity": "Error",
                    "reason": reason,
                    "notice": {"ntc_id": row["ntc_id"], "type": row["ntc_type"], "adm": row["adm"], "prov": row["prov"]},
                    "notice_frequency_mhz": [row["freq_min"], row["freq_max"]],
                    "finnish_frequency_mhz": [band.frequency_min_mhz, band.frequency_max_mhz],
                    "overlap_mhz": [overlap_min, overlap_max],
                    "itu_pfd": row["pfd"],
                    "itu_pfd_frequency_mhz": [row["pfd_freq_min"], row["pfd_freq_max"]],
                    "finnish_pfd_limit": band.pfd_limit,
                    "pfd_unit": band.pfd_unit,
                    "finnish_excel_row": band.row_number,
                    "station_number": band.station_number,
                    "source": {"database": str(db_path), "excel": str(excel_path)},
                })
                audit_lines.extend(("", f"Database candidates checked: {candidate_count}", f"Errors: {len(errors)}"))
                audit_path.write_text("\n".join(audit_lines) + "\n", encoding="utf-8")
        return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("ific3083.sqlite"))
    parser.add_argument("--excel", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("interference_errors.json"))
    parser.add_argument("--audit", type=Path, default=Path("interference_audit.txt"),
                        help="text file containing every check and decision")
    parser.add_argument("--notice-types", default=",".join(DEFAULT_NOTICE_TYPES),
                        help="comma-separated values from notice.ntc_type")
    parser.add_argument("--pfd-limit-column", default="PFD limit",
                        help="Excel header containing the protection limit")
    parser.add_argument("--pfd-unit", default="dBW/m2/4kHz")
    parser.add_argument("--default-frequency-unit", default="mhz",
                        choices=tuple(FREQUENCY_UNITS_TO_MHZ))
    args = parser.parse_args()
    notice_types = tuple(value.strip() for value in args.notice_types.split(",") if value.strip())
    try:
        errors = analyse(
            args.db,
            args.excel,
            notice_types,
            audit_path=args.audit,
            pfd_limit_column=args.pfd_limit_column,
            pfd_unit=args.pfd_unit,
            default_frequency_unit=args.default_frequency_unit,
        )
    except Exception as exc:
        args.audit.write_text(
            "INTERFERENCE ANALYSIS AUDIT\n"
            f"Database: {args.db}\nExcel: {args.excel}\n"
            f"FATAL_ERROR: {type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        raise
    args.output.write_text(json.dumps(errors, indent=2), encoding="utf-8")
    print(f"Wrote {len(errors)} Errors to {args.output}")
    print(f"Wrote audit checks to {args.audit}")


if __name__ == "__main__":
    main()