#!/usr/bin/env python3
"""Space IFIC PFD inspection against ITU RR Table 21-4 as seen from Finland.

For every in-scope satellite notice (API/A, CR/C, AP30*) the engine takes each
space-station transmitting group, derives the worst-case e.i.r.p. in the Table
21-4 reference bandwidth, places the satellite in its real geometry relative to
Finnish territory (GSO longitude or non-GSO orbit) and compares the resulting
PFD with the angle-of-arrival dependent limit. Only exceedances of at least
MIN_EXCEEDANCE_MARGIN_DB are reported, one row per notice (ntc_id).

Worst-case assumptions (all recorded in the output and audit):
  * the transmitting beam's peak gain points at Finland;
  * non-GSO satellites are evaluated at perigee and apogee altitude for every
    elevation angle reachable from Finland given the orbital inclination;
  * where Table 21-4 offers alternative limits, the less strict one is used.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


OUTPUT_COLUMNS = [
    "notice_id", "related_notice_id", "network_name", "country", "notice_type", "orbit",
    "gso_longitude", "altitude_km", "frequency_mhz", "beam_name", "station_class", "service",
    "emission", "power_source", "calculated_power_dbw", "eirp_ref_dbw", "elevation_deg", "distance_km",
    "pfd_dbw_m2", "pfd_limit_dbw_m2", "margin_of_exceedance", "reference_bandwidth",
    "table21_4_row", "rr_reference", "br_finding", "finnish_station_id", "m4_service", "result_class",
    "priority", "priority_reason", "reason",
]
CONCERN_COLUMNS = OUTPUT_COLUMNS + ["concern_types"]

# Alert gate. Traficom (Discord, 7.11.2026): "if your calculations indicate that the
# thresholds have been exceeded, the result should be flagged" -> any exceedance is an
# alert. Raise with --min-exceedance-db to move small exceedances to the concerns file.
MIN_EXCEEDANCE_MARGIN_DB = 0.0

# Concern screening (team-defined, not regulatory; Traficom: own margins are fine if
# documented and easily modifiable). Concerns go to a separate CSV.
NEAR_LIMIT_DB = 6.0              # Table 21-4 margin in [-NEAR_LIMIT_DB, 0) -> NEAR_LIMIT
SCREEN_PFD_DBW_M2_4KHZ = -150.0  # bands without a Table 21-4 limit -> NO_LIMIT_HIGH_PFD
SCREEN_ROW = "SCREEN_NO_LIMIT"
CONCERN_PRIORITY = ["SUB_QA_EXCEEDANCE", "LIMIT_NOT_APPLICABLE", "WORST_CASE_ISS", "BR_UNFAVOURABLE_21_16",
                    "NEAR_LIMIT", "NO_LIMIT_HIGH_PFD"]
# M4 services that are terrestrial (receivers on Finnish territory), for the no-limit screen.
TERRESTRIAL_M4 = r"FIXED|MOBILE|BROADCASTING|AERONAUTICAL|MARITIME|RADIOLOCATION|RADIONAVIGATION|AMATEUR|METEOROLOGICAL AIDS"


def open_db(path: Path) -> sqlite3.Connection:
    """Open an IFIC SQLite database read-only. sqlite3.connect() would silently create an
    empty file for a missing path (GitHub issue #6)."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"IFIC database not found or empty: {path} (run pipeline.py or convert_mdb.py first)")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def overlaps_any(lo: np.ndarray, hi: np.ndarray, int_min: np.ndarray, int_max: np.ndarray) -> np.ndarray:
    """For each half-open [lo, hi): does any interval [int_min, int_max) overlap it? O(n log n), no n x m matrix."""
    order = np.argsort(int_min)
    mins, run_max = int_min[order], np.maximum.accumulate(int_max[order])
    idx = np.searchsorted(mins, hi, side="left")            # intervals starting before hi: [0, idx)
    best = np.where(idx > 0, run_max[np.maximum(idx - 1, 0)], -np.inf)
    return best > lo


def is_terrestrial_m4(services: pd.Series) -> pd.Series:
    """True for M4 rows of a terrestrial service. Each M4 row holds one service; satellite
    services ('MOBILE-SATELLITE', 'FIXED SATELLITE', ...) contain a terrestrial service name
    as a substring and must be excluded explicitly (GitHub issue #1)."""
    text = services.fillna("").str.upper()
    return text.str.contains(TERRESTRIAL_M4) & ~text.str.contains("SATELLITE")

BR_SQL = """
SELECT g.ntc_id, g.grp_id, g.fdg_reg, g.freq_min, g.freq_max, f.fdg_prov
FROM grp g JOIN notice n ON n.ntc_id = g.ntc_id LEFT JOIN fdg_ref f ON f.grp_id = g.grp_id
WHERE n.wic_no = ? AND g.emi_rcp = 'E'
"""
RR_REFERENCE = "RR Ed. 2024 Vol. 1 Art. 21 No. 21.16, Table 21-4 (Rev.WRC-23)"
HEO_MIN_APOGEE_KM = 18000.0

EARTH_RADIUS_KM = 6378.137
GSO_RADIUS_KM = 42164.0

NOTICE_TYPES = {"A": "API/A", "C": "CR/C", "N": "Notification (11.2)", "U": "Res.49"}

# Appendix 4 space-station class -> Table 21-4 service families.
STATION_CLASS_SERVICES = {
    "EC": {"FSS"}, "EB": {"BSS"}, "EV": {"BSS"},
    "EI": {"MSS"}, "EG": {"MSS"}, "EJ": {"MSS"}, "EU": {"MSS"},
    "EF": {"RDSS"}, "EH": {"SR"}, "EW": {"EESS"}, "EM": {"METSAT"},
    "E3": {"EESS"},  # every E3 group in IFIC 3074 is an active sensor (design_emi "SNSOR", SAR beams)
    "ES": {"ISS"}, "ED": {"SO"}, "EK": {"SO"}, "ER": {"SO"}, "ET": {"SO", "EESS"},
}

# Finnish territory reference points used when no station coordinates exist.
# Finnish territory evaluation points, added to the Excel station sites so that the
# extremes of the land border, Aland and the territorial sea are always covered
# (Traficom: every TX is received in Finland; an evaluation grid is acceptable).
FINLAND_POINTS = [
    (69.06, 20.55), (70.09, 27.90), (69.90, 27.00), (68.40, 23.60), (66.80, 29.50),
    (66.00, 29.60), (62.90, 31.58), (61.50, 29.50), (60.50, 27.80), (60.20, 24.90),
    (59.70, 24.90), (59.75, 22.90), (60.10, 19.90), (60.30, 19.13), (63.10, 21.30),
    (65.00, 25.50), (65.80, 24.10), (61.50, 23.80), (62.90, 27.70), (66.50, 25.70),
]

SPACE_SQL = """
SELECT n.ntc_id, n.adm, n.ntf_rsn, n.ntc_type, n.prov AS notice_prov, n.tgt_ntc_id,
       COALESCE(geo.sat_name, ng.sat_name) AS network_name, geo.long_nom, ng.ref_body,
       g.grp_id, g.beam_name, g.prov AS grp_prov,
       e.design_emi, e.pwr_ds_max, e.pep_max, e.pulse_length, e.pulse_rep,
       sb.gain, sb.pwr_max_4k, sb.pwr_max_1m
FROM notice n
JOIN grp g ON g.ntc_id = n.ntc_id AND g.emi_rcp = 'E'
JOIN emiss e ON e.grp_id = g.grp_id
LEFT JOIN s_beam sb ON sb.ntc_id = n.ntc_id AND sb.emi_rcp = 'E' AND sb.beam_name = g.beam_name
LEFT JOIN geo ON geo.ntc_id = n.ntc_id
LEFT JOIN non_geo ng ON ng.ntc_id = n.ntc_id
WHERE n.wic_no = ? AND n.ntc_type IN ('G', 'N')
"""

FREQ_SQL = """
SELECT f.grp_id, MIN(f.freq_min) AS freq_min, MAX(f.freq_max) AS freq_max
FROM freq f JOIN notice n ON n.ntc_id = f.ntc_id
WHERE n.wic_no = ? AND f.emi_rcp = 'E' AND f.freq_min < ? AND f.freq_max > ?
GROUP BY f.grp_id, ROUND(f.freq_min), ROUND(f.freq_max)
"""

ORBIT_SQL = """
SELECT ntc_id, MIN(COALESCE(perig_km, op_ht_km)) AS perig_km,
       MAX(COALESCE(apog_km, op_ht_km)) AS apog_km, MAX(inclin_ang) AS inclin_ang,
       SUM(nbr_sat_pl) AS n_satellites
FROM orbit GROUP BY ntc_id
"""


# --------------------------------------------------------------------------- provenance

def provenance(paths) -> list[str]:
    """SHA-256, size and timestamp of every input, for the audit trail."""
    import hashlib
    from datetime import datetime, timezone
    lines = [f"Run (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}"]
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        lines.append(f"Input {path.name}: sha256 {digest.hexdigest()} ({path.stat().st_size} bytes)")
    return lines


def write_outputs(frame: pd.DataFrame, csv_path: Path) -> None:
    """CSV for quick evaluation plus a machine-readable JSON twin (records)."""
    frame.to_csv(csv_path, index=False)
    frame.to_json(csv_path.with_suffix(".json"), orient="records", indent=1, force_ascii=False)


# --------------------------------------------------------------------------- inputs

def load_limits(path: Path) -> pd.DataFrame:
    """Load the Table 21-4 register (pfd.json)."""
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    rows = [dict(row, row_id=key) for key, row in data.items() if not key.startswith("_")]
    limits = pd.DataFrame(rows)
    if "rr_reference" in limits:
        limits = limits.rename(columns={"rr_reference": "rr_reference_row"})
    limits["angles"] = limits["curve"].map(lambda c: np.array([p[0] for p in c], dtype=float))
    limits["values"] = limits["curve"].map(lambda c: np.array([p[1] for p in c], dtype=float))
    return limits


def load_m4(path: Path) -> pd.DataFrame:
    with path.open(encoding="utf-8") as handle:
        m4 = pd.DataFrame(json.load(handle)["value"])
    m4["freq_min_mhz"] = pd.to_numeric(m4["Sub_band_lower_limit__Hz_"], errors="coerce") / 1e6
    m4["freq_max_mhz"] = pd.to_numeric(m4["Sub_band_upper_limit__Hz_"], errors="coerce") / 1e6
    return m4.dropna(subset=["freq_min_mhz", "freq_max_mhz"])


def parse_quantity_mhz(value) -> float:
    """'87.900 MHz' / '300.000 kHz' / '12 GHz' -> MHz."""
    match = re.match(r"\s*([\d.,]+)\s*([kMG]?Hz)", str(value))
    if not match:
        return np.nan
    number = float(match.group(1).replace(",", "."))
    return number * {"kHz": 1e-3, "MHz": 1.0, "GHz": 1e3, "Hz": 1e-6}[match.group(2)]


def parse_bandwidth_mhz(value) -> float:
    """Excel bandwidths: '300.000 kHz', '5 MHz', '5000k', '7,6k', '8M00', '118600k' -> MHz."""
    text = str(value).strip().replace(",", ".")
    if re.search(r"[kMG]?Hz", text):
        return parse_quantity_mhz(text)
    match = re.fullmatch(r"(\d*\.?\d*)([kKMG])(\d*)", text)
    if not match or not (match.group(1) or match.group(3)):
        return np.nan
    whole, frac = match.group(1), match.group(3)
    number = float(f"{whole or 0}.{frac}" if frac and "." not in whole else whole or 0)
    return number * {"k": 1e-3, "K": 1e-3, "M": 1.0, "G": 1e3}[match.group(2)]


def load_stations(path: Path) -> pd.DataFrame:
    """Finnish special-interest stations from the Traficom Excel, one row per protected interval.

    Per Traficom: RX frequencies are protected, and TX frequencies too (every TX is
    received somewhere in Finland); where channel and transmission bandwidth disagree
    the smaller one is used (the larger values are dataset typos); a missing licence
    class is treated as MUUT.
    """
    raw = pd.read_excel(path, header=10).iloc[1:]  # row 11 holds the Finnish headers
    base = pd.DataFrame({
        "station_id": raw["Station number"].astype(str),
        "licence": raw["Class of licence"].fillna("MUUT").astype(str),
        "lon": pd.to_numeric(raw["Longitude"], errors="coerce"),
        "lat": pd.to_numeric(raw["Latitude"], errors="coerce"),
        "bw_mhz": np.fmin(raw["Channel bandwidth"].map(parse_bandwidth_mhz),
                          raw["Transmission bandwidth"].map(parse_bandwidth_mhz)).fillna(0.0),
        "tx_mhz": raw["TX frequency"].map(parse_quantity_mhz),
        "rx_mhz": raw["RX frequency"].map(parse_quantity_mhz),
    })
    parts = []
    for direction, column in (("RX", "rx_mhz"), ("TX", "tx_mhz")):
        part = base.dropna(subset=[column]).copy()
        part["direction"] = direction
        part["freq_min_mhz"] = part[column] - part["bw_mhz"] / 2
        part["freq_max_mhz"] = part[column] + part["bw_mhz"] / 2
        parts.append(part)
    stations = pd.concat(parts, ignore_index=True)
    stations = stations.drop_duplicates(subset=["station_id", "freq_min_mhz", "freq_max_mhz"])
    return stations.dropna(subset=["lat", "lon"])


def emission_bandwidth_hz(design_emi) -> float:
    """ITU necessary-bandwidth designator ('100KN0N--', '2K70J3E', '36M0G7W') -> Hz."""
    match = re.match(r"(\d*)([HKMG])(\d*)", str(design_emi or ""))
    if not match or not (match.group(1) or match.group(3)):
        return np.nan
    number = float(f"{match.group(1) or 0}.{match.group(3) or 0}")
    return number * {"H": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}[match.group(2)]


# --------------------------------------------------------------------------- geometry

def spreading_loss_db(distance_km):
    distance_m = np.asarray(distance_km, dtype=float) * 1000.0
    return 10.0 * np.log10(4.0 * np.pi * distance_m ** 2)


def gso_geometry(sat_lon: float, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Elevation (deg) and slant range (km) from each (lat, lon) point to a GSO satellite."""
    lat = np.radians(points[:, 0])
    dlon = np.radians(points[:, 1] - sat_lon)
    cos_psi = np.cos(lat) * np.cos(dlon)
    sin_psi = np.sqrt(np.clip(1 - cos_psi ** 2, 0, None))
    distance = np.sqrt(EARTH_RADIUS_KM ** 2 + GSO_RADIUS_KM ** 2
                       - 2 * EARTH_RADIUS_KM * GSO_RADIUS_KM * cos_psi)
    elevation = np.degrees(np.arctan2(cos_psi - EARTH_RADIUS_KM / GSO_RADIUS_KM, sin_psi))
    return elevation, distance


def ngso_max_elevation(altitude_km: float, inclination_deg: float, min_lat: float) -> float:
    """Highest elevation at which a satellite of this orbit can be seen from Finland."""
    inc = inclination_deg if inclination_deg <= 90 else 180 - inclination_deg
    psi = np.radians(max(0.0, min_lat - inc))
    radius = EARTH_RADIUS_KM + altitude_km
    return float(np.degrees(np.arctan2(np.cos(psi) - EARTH_RADIUS_KM / radius, np.sin(psi))))


def ngso_range_km(altitude_km: float, elevation_deg: np.ndarray) -> np.ndarray:
    radius = EARTH_RADIUS_KM + altitude_km
    el = np.radians(elevation_deg)
    return np.sqrt(radius ** 2 - (EARTH_RADIUS_KM * np.cos(el)) ** 2) - EARTH_RADIUS_KM * np.sin(el)


def geometry_term(limit_row, orbit: str, gso_lon, perig, apog, incl, points) -> dict | None:
    """max over Finland of (-spreading_loss - limit); PFD margin = e.i.r.p._ref + this term."""
    angles, values = limit_row["angles"], limit_row["values"]
    if orbit == "GSO":
        elevation, distance = gso_geometry(gso_lon, points)
        visible = elevation >= 0
        if not visible.any():
            return None
        elevation, distance = elevation[visible], distance[visible]
        term = -spreading_loss_db(distance) - np.interp(elevation, angles, values)
        best = int(np.argmax(term))
        return {"term": term[best], "elevation": elevation[best], "distance": distance[best],
                "altitude": GSO_RADIUS_KM - EARTH_RADIUS_KM}
    best = None
    for altitude in {perig, apog}:
        max_el = ngso_max_elevation(altitude, incl, points[:, 0].min())
        if max_el < 0:
            continue
        elevation = np.linspace(0.0, max_el, 181)
        distance = ngso_range_km(altitude, elevation)
        term = -spreading_loss_db(distance) - np.interp(elevation, angles, values)
        i = int(np.argmax(term))
        if best is None or term[i] > best["term"]:
            best = {"term": term[i], "elevation": elevation[i], "distance": distance[i],
                    "altitude": altitude}
    return best


# --------------------------------------------------------------------------- scan

def screening_row(screen_pfd: float) -> dict:
    """Pseudo limit row for bands with no Table 21-4 limit (flat screening level, 4 kHz)."""
    return {"row_id": SCREEN_ROW, "freq_min_mhz": 0.0, "freq_max_mhz": 1e7, "orbit": "ANY",
            "services": [], "reference_bandwidth_hz": 4000,
            "curve": [[0, screen_pfd], [90, screen_pfd]],
            "angles": np.array([0.0, 90.0]), "values": np.array([screen_pfd, screen_pfd])}


def space_report(database: Path, limits: pd.DataFrame, m4: pd.DataFrame, stations: pd.DataFrame,
                 ific_number: int = 3074, notice_types=("A", "C"),
                 min_exceedance_db: float = MIN_EXCEEDANCE_MARGIN_DB,
                 audit: list[str] | None = None, near_limit_db: float = NEAR_LIMIT_DB,
                 screen_pfd: float = SCREEN_PFD_DBW_M2_4KHZ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (alerts, concerns): one row per satellite network in each."""
    audit = audit if audit is not None else []
    note = audit.append
    empty = (pd.DataFrame(columns=OUTPUT_COLUMNS), pd.DataFrame(columns=CONCERN_COLUMNS))
    limits = pd.concat([limits, pd.DataFrame([screening_row(screen_pfd)])], ignore_index=True)
    with open_db(database) as connection:
        itu = pd.read_sql_query(SPACE_SQL, connection, params=(ific_number,))
        freqs = pd.read_sql_query(FREQ_SQL, connection, params=(ific_number, 1e7, 0.0))
        orbits = pd.read_sql_query(ORBIT_SQL, connection)
        classes = pd.read_sql_query("SELECT grp_id, stn_cls FROM srv_cls", connection)
        br = pd.read_sql_query(BR_SQL, connection, params=(ific_number,))
        names = pd.read_sql_query("""SELECT n.ntc_id, n.adm, n.ntf_rsn, n.tgt_ntc_id, geo.long_nom,
            COALESCE(geo.sat_name, ng.sat_name) AS network_name FROM notice n
            LEFT JOIN geo ON geo.ntc_id = n.ntc_id LEFT JOIN non_geo ng ON ng.ntc_id = n.ntc_id
            WHERE n.wic_no = ?""", connection, params=(ific_number,))

    note(f"Space IFIC {ific_number}: {itu['ntc_id'].nunique()} space-station notices with transmitting groups")

    # S1 notice scope: API/A, CR/C (+ optional types) and anything under Appendix 30/30A/30B.
    ap30 = itu["notice_prov"].fillna("").str.contains("30") | itu["grp_prov"].fillna("").isin(["30", "30A", "30B"])
    in_scope = itu["ntf_rsn"].isin(notice_types) | ap30
    note(f"Notice scope {sorted(notice_types)} + AP30: kept {itu.loc[in_scope, 'ntc_id'].nunique()}, "
         f"excluded {itu.loc[~in_scope, 'ntc_id'].nunique()} notices")
    itu = itu[in_scope]

    # Deep-space / lunar / planetary missions are not Earth-orbiting: no Table 21-4 case.
    deep = itu["long_nom"].isna() & itu["ref_body"].fillna("") .ne("T")
    note(f"Excluded {itu.loc[deep, 'ntc_id'].nunique()} notices not orbiting the Earth "
         f"(ref_body != T): {sorted(itu.loc[deep, 'network_name'].dropna().unique())}")
    itu = itu[~deep]

    itu = itu.drop_duplicates(subset=["grp_id", "design_emi", "pwr_ds_max", "gain"])
    itu["orbit"] = np.where(itu["long_nom"].notna(), "GSO", "NGSO")

    # Service family per group from the Appendix 4 station class.
    classes = classes[classes["stn_cls"].str.startswith("E", na=False)]
    classes["services"] = classes["stn_cls"].map(lambda c: STATION_CLASS_SERVICES.get(c, set()))
    grp_services = classes.groupby("grp_id").agg(
        services=("services", lambda s: set().union(*s)),
        station_class=("stn_cls", lambda s: ",".join(sorted(set(s)))))
    itu = itu.merge(grp_services, left_on="grp_id", right_index=True, how="left")
    itu["services"] = itu["services"].map(lambda s: s if isinstance(s, set) else set())

    # e.i.r.p. needs a gain and either power in the reference bandwidth or a power density.
    itu["pwr_ds_max"] = pd.to_numeric(itu["pwr_ds_max"], errors="coerce")
    itu["gain"] = pd.to_numeric(itu["gain"], errors="coerce")
    missing = itu["gain"].isna() | (itu["pwr_ds_max"].isna() & itu["pwr_max_4k"].isna() & itu["pwr_max_1m"].isna())
    note(f"REVIEW (not exported): {itu.loc[missing, 'grp_id'].nunique()} groups lack antenna gain or power data")
    itu = itu[~missing]
    itu["emission_bw_hz"] = itu["design_emi"].map(emission_bandwidth_hz)

    # Frequency overlap with Table 21-4 rows (half-open intervals).
    # Pair each assignment only with the limit rows its frequency overlaps (a full cross join
    # built ~15 M rows and ~10 GB peak memory for IFIC 3074; GitHub issue #2).
    base = itu.merge(freqs, on="grp_id")
    parts = []
    for i in range(len(limits)):
        row = limits.iloc[[i]]
        hit = base[(base["freq_min"] < row["freq_max_mhz"].iat[0]) & (base["freq_max"] > row["freq_min_mhz"].iat[0])]
        if not hit.empty:
            parts.append(hit.merge(row, how="cross"))
    cand = pd.concat(parts, ignore_index=True) if parts else base.iloc[0:0].merge(limits.iloc[0:0], how="cross")
    cand = cand[(cand["orbit_y"] == "ANY") | (cand["orbit_y"] == cand["orbit_x"])] if "orbit_y" in cand else cand
    cand = cand.rename(columns={"orbit_x": "orbit"})
    screen = cand["row_id"] == SCREEN_ROW
    service_ok = screen | pd.Series([bool(s & set(r)) for s, r in zip(cand["services_x"], cand["services_y"])],
                                    index=cand.index)
    note(f"Frequency/orbit overlaps with Table 21-4: {int((~screen).sum())}; service mismatch dropped: "
         f"{int((~service_ok).sum())}")
    cand = cand[service_ok].copy()

    # Footnotes 24/25 (21.16.17/18): separate 10.7-12.5 GHz NGSO rows for highly-inclined
    # high-apogee orbits (inclination 35-145 deg, apogee > 18 000 km) and for all others.
    orb = orbits.set_index("ntc_id")
    heo_ntc = set(orb.index[(orb["apog_km"] > HEO_MIN_APOGEE_KM) & orb["inclin_ang"].between(35, 145)])
    if "heo" in cand:
        wants = cand["heo"]
        is_heo = cand["ntc_id"].isin(heo_ntc)
        cand = cand[wants.isna() | (wants.astype(object) == is_heo)]
    cand["n_satellites"] = cand["ntc_id"].map(orb["n_satellites"])

    # Screening applies only where no Table 21-4 row covers the assignment at all and the
    # assignment overlaps a Finnish station's RX/TX interval.
    real_keys = set(map(tuple, cand.loc[cand["row_id"] != SCREEN_ROW, ["grp_id", "freq_min", "freq_max"]].to_numpy()))
    screen = cand["row_id"] == SCREEN_ROW
    no_limit = pd.Series([tuple(k) not in real_keys for k in cand[["grp_id", "freq_min", "freq_max"]].to_numpy()],
                         index=cand.index)
    # Traficom: real Finnish use = M4 (incl. licence-exempt) and the special-interest stations.
    terr_m4 = m4[is_terrestrial_m4(m4["Services_in_Finland"])]
    use_min = np.concatenate([stations["freq_min_mhz"].to_numpy(), terr_m4["freq_min_mhz"].to_numpy()])
    use_max = np.concatenate([stations["freq_max_mhz"].to_numpy(), terr_m4["freq_max_mhz"].to_numpy()])
    in_finnish_use = pd.Series(overlaps_any(cand["freq_min"].to_numpy(), cand["freq_max"].to_numpy(), use_min, use_max),
                               index=cand.index)
    cand = cand[~screen | (no_limit & in_finnish_use)]
    note(f"Bands without a Table 21-4 limit but overlapping Finnish stations: "
         f"{cand.loc[cand['row_id'] == SCREEN_ROW, 'ntc_id'].nunique()} notices screened at "
         f"{screen_pfd:g} dB(W/m2) in 4 kHz")
    # Inter-satellite-only beams point at other spacecraft, not at the Earth: peak gain
    # toward Finland would be meaningless without the antenna pattern.
    cand["iss_only"] = cand["services_x"].map(lambda s: s == {"ISS"})
    note(f"Inter-satellite-only groups in {cand.loc[cand['iss_only'], 'ntc_id'].nunique()} notices: evaluated "
         f"worst case (peak gain toward Earth) and reported as WORST_CASE_ISS concerns, not alerts")
    if cand.empty:
        return empty

    # e.i.r.p. in the reference bandwidth. Prefer the notified max power in 4 kHz / 1 MHz;
    # otherwise integrate the max power density (averaged over the worst 4 kHz below
    # 15 GHz, 1 MHz above) over min(reference bandwidth, emission bandwidth).
    def occupied_hz(frame):
        avg_bw = pd.Series(np.where(frame["freq_min"] < 15000, 4e3, 1e6), index=frame.index)
        return np.maximum(frame["emission_bw_hz"].fillna(frame["reference_bandwidth_hz"]), avg_bw)

    # Data-quality gate: the integrated density cannot exceed the peak envelope power.
    pep = pd.to_numeric(cand["pep_max"], errors="coerce")
    total = cand["pwr_ds_max"] + 10 * np.log10(cand["emission_bw_hz"].fillna(cand["reference_bandwidth_hz"]))
    inconsistent = pep.notna() & (total > pep + 3.0) & cand["pwr_max_4k"].isna() & cand["pwr_max_1m"].isna()
    if inconsistent.any():
        bad = cand.loc[inconsistent, ["ntc_id", "network_name", "design_emi", "pwr_ds_max", "pep_max"]].drop_duplicates()
        note(f"DATA_QUALITY: {len(bad)} emissions where pwr_ds_max x bandwidth exceeds pep_max by >3 dB; their "
             f"density is derived from pep_max spread over the emission bandwidth instead, e.g. " + "; ".join(
                 f"{r.network_name} ({r.ntc_id}) {r.design_emi} pwr_ds_max={r.pwr_ds_max} pep_max={r.pep_max}"
                 for r in bad.head(5).itertuples()))
        emi_bw = cand["emission_bw_hz"].fillna(cand["reference_bandwidth_hz"])
        cand.loc[inconsistent, "pwr_ds_max"] = (pep - 10 * np.log10(emi_bw))[inconsistent]
    cand["dq_density_from_pep"] = inconsistent

    ref_bw = cand["reference_bandwidth_hz"].astype(float)
    from_density = cand["pwr_ds_max"] + 10 * np.log10(np.minimum(ref_bw, occupied_hz(cand)))
    pep = pd.to_numeric(cand["pep_max"], errors="coerce")
    from_density = from_density.where(pep.isna() | (from_density <= pep), pep)
    notified = pd.Series(np.where(ref_bw <= 4e3, cand["pwr_max_4k"],
                                  np.where(ref_bw == 1e6, cand["pwr_max_1m"], np.nan)), index=cand.index)
    notified = pd.to_numeric(notified, errors="coerce")
    # s_beam.pwr_max_4k / pwr_max_1m are peak e.i.r.p. in 4 kHz / 1 MHz (they equal
    # pwr_ds_max + 10log(BW) + gain in the data), so the gain is not added again.
    cand["eirp_ref_dbw"] = notified.fillna(from_density + cand["gain"])
    cand["power_source"] = np.where(notified.notna(), "s_beam notified peak e.i.r.p. in ref. BW",
                                    np.where(cand["dq_density_from_pep"],
                                             "pep_max over emission BW (pwr_ds_max inconsistent) + peak gain",
                                             "emiss.pwr_ds_max x min(ref BW, emission BW) + peak gain"))
    # Footnote 18 (21.16.8): EESS (active) limits are MEAN pfd: peak power x duty cycle
    # (pulse length [us] x PRF [kHz] x 1e-3) spread over the SAR bandwidth Bc.
    if "mean_pfd" in cand:
        tau, prf = pd.to_numeric(cand["pulse_length"], errors="coerce"), pd.to_numeric(cand["pulse_rep"], errors="coerce")
        duty = tau * prf * 1e-3
        bc_hz = (cand["freq_max"] - cand["freq_min"]) * 1e6
        mean_rows = cand["mean_pfd"].eq(True)
        usable = mean_rows & pep.notna() & (duty > 0) & (duty <= 1) & (bc_hz > 0)
        mean_eirp = pep + 10 * np.log10(duty.where(usable)) + 10 * np.log10(np.minimum(1.0, ref_bw / bc_hz)) + cand["gain"]
        cand.loc[usable, "eirp_ref_dbw"] = mean_eirp[usable]
        cand.loc[usable, "power_source"] = [
            f"RR 21.16.8 mean pfd: pep_max {p:.1f} dBW x duty {d:.3g} (tau {t:g} us x PRF {f:g} kHz) over Bc {b / 1e6:g} MHz + peak gain"
            for p, d, t, f, b in zip(pep[usable], duty[usable], tau[usable], prf[usable], bc_hz[usable])]
        cand.loc[mean_rows & ~usable, "power_source"] += " (peak: no pulse data for 21.16.8 mean)"
    cand["calculated_power_dbw"] = cand["eirp_ref_dbw"] - cand["gain"]
    cand = cand.dropna(subset=["eirp_ref_dbw"])

    # Geometry from Finland: one evaluation per (orbit, Table 21-4 row).
    points = np.vstack([stations[["lat", "lon"]].drop_duplicates().to_numpy().reshape(-1, 2),
                        np.array(FINLAND_POINTS)])
    orbits = orbits.set_index("ntc_id")
    limits_by_id = limits.set_index("row_id")
    cache: dict = {}
    geo_rows = []
    for ntc_id, orbit, gso_lon, row_id in cand[["ntc_id", "orbit", "long_nom", "row_id"]].drop_duplicates().itertuples(index=False):
        perig = apog = incl = None
        if orbit == "NGSO":
            if ntc_id not in orbits.index or pd.isna(orbits.at[ntc_id, "perig_km"]):
                geo_rows.append((ntc_id, row_id, None))
                continue
            perig, apog, incl = (float(orbits.at[ntc_id, c]) if pd.notna(orbits.at[ntc_id, c]) else None
                                 for c in ("perig_km", "apog_km", "inclin_ang"))
            apog = apog or perig
            incl = 90.0 if incl is None else incl
        key = (orbit, gso_lon, perig, apog, incl, row_id)
        if key not in cache:
            cache[key] = geometry_term(limits_by_id.loc[row_id], orbit, gso_lon, perig, apog, incl, points)
        geo_rows.append((ntc_id, row_id, cache[key]))
    geometry = pd.DataFrame(
        [(n, r, g["term"], g["elevation"], g["distance"], g["altitude"]) for n, r, g in geo_rows if g],
        columns=["ntc_id", "row_id", "geo_term", "elevation_deg", "distance_km", "altitude_km"])
    no_orbit = {n for n, _, g in geo_rows if g is None}
    note(f"Not visible from Finland or no orbit data: {len(no_orbit)} notices dropped")
    cand = cand.merge(geometry, on=["ntc_id", "row_id"])

    cand["margin_of_exceedance"] = cand["eirp_ref_dbw"] + cand["geo_term"]
    cand["pfd_dbw_m2"] = cand["eirp_ref_dbw"] - spreading_loss_db(cand["distance_km"])
    cand["pfd_limit_dbw_m2"] = cand["pfd_dbw_m2"] - cand["margin_of_exceedance"]

    target = pd.to_numeric(cand["tgt_ntc_id"], errors="coerce")
    cand["network_key"] = np.where(target.isin(set(cand["ntc_id"])), target, cand["ntc_id"]).astype("int64")
    # Footnote 11 (21.16.4): these NGSO limits apply to systems of 99 or fewer satellites;
    # larger constellations are "subject to further study" -> concern, not an alert.
    max_sats = pd.to_numeric(cand["max_satellites"], errors="coerce") if "max_satellites" in cand else np.nan
    not_applicable = (cand["n_satellites"] > max_sats).fillna(False) if "max_satellites" in cand else False
    iss = cand["iss_only"]
    real = (cand["row_id"] != SCREEN_ROW) & ~not_applicable & ~iss
    margin = cand["margin_of_exceedance"].where(real)
    note(f"Evaluated {len(cand)} group/emission/row combinations over {cand['ntc_id'].nunique()} notices; "
         f"exceedances >0 dB: {int((margin > 0).sum())}, >= {min_exceedance_db:g} dB: {int((margin >= min_exceedance_db).sum())} "
         f"in {cand.loc[margin >= min_exceedance_db, 'ntc_id'].nunique()} notices")

    # Alerts: Table 21-4 hard-limit exceedances (EXCEEDS LIMIT) and RR No. 9.14 Appendix 5
    # coordination-threshold exceedances (COORDINATION REQUIRED); one row per network, hard limits first.
    alerts_rows = cand[real & (cand["margin_of_exceedance"] >= min_exceedance_db)].copy()
    is_ap5 = alerts_rows["criterion"].eq("AP5") if "criterion" in alerts_rows else False
    alerts_rows["alert_class"] = np.where(is_ap5, "COORDINATION REQUIRED", "EXCEEDS LIMIT")
    alerts_rows["_priority"] = np.where(is_ap5, 1, 0)
    alerts = attach_victims(one_per_network(alerts_rows, "ALERT"), m4, stations)

    m = cand["margin_of_exceedance"]
    screen = cand["row_id"] == SCREEN_ROW
    cand["concern_class"] = np.select(
        [real & (m >= 0) & (m < min_exceedance_db), not_applicable & ~iss & (m >= 0),
         iss & ~screen & (m >= 0), real & (m >= -near_limit_db) & (m < 0), screen & ~iss & (m >= 0)],
        ["SUB_QA_EXCEEDANCE", "LIMIT_NOT_APPLICABLE", "WORST_CASE_ISS", "NEAR_LIMIT", "NO_LIMIT_HIGH_PFD"], default="")
    concern_rows = cand[(cand["concern_class"] != "") & ~cand["network_key"].isin(set(alerts_rows["network_key"]))].copy()
    concern_rows["_priority"] = concern_rows["concern_class"].map(CONCERN_PRIORITY.index)
    concerns = attach_victims(one_per_network(concern_rows, None, min_exceedance_db, near_limit_db), m4, stations,
                              CONCERN_COLUMNS)
    # ITU BR examination findings (fdg_reg A-/N-, fdg_prov e.g. X/21.16) on every row, and every
    # network the Bureau found unfavourable under No. 21.16 that has no alert here.
    finding = br.groupby("ntc_id").apply(lambda g: _br_text(g), include_groups=False)
    for frame in (alerts, concerns):
        frame["br_finding"] = pd.to_numeric(frame["notice_id"], errors="coerce").map(finding).fillna("")
    x2116 = br[br["fdg_prov"].fillna("").str.contains("X/21.16", regex=False)]
    flagged = set(pd.to_numeric(alerts["notice_id"], errors="coerce")) | {
        int(float(x)) for v in alerts["related_notice_id"].dropna() for x in str(v).split(";") if x}
    extra = []
    for ntc, g in x2116.groupby("ntc_id"):
        if ntc in flagged:
            continue
        n = names.set_index("ntc_id").loc[ntc]
        why = _finland_visibility(ntc, n, orb)
        extra.append({"notice_id": ntc, "network_name": n["network_name"], "country": n["adm"],
                      "notice_type": NOTICE_TYPES.get(n["ntf_rsn"], n["ntf_rsn"]),
                      "orbit": "GSO" if pd.notna(n["long_nom"]) else "NGSO", "gso_longitude": n["long_nom"],
                      "frequency_mhz": f"{g['freq_min'].min():g}-{g['freq_max'].max():g}",
                      "result_class": "BR_UNFAVOURABLE_21_16", "concern_types": "BR_UNFAVOURABLE_21_16",
                      "br_finding": finding.get(ntc, ""), "rr_reference": RR_REFERENCE + " (ITU BR finding X/21.16)",
                      "reason": f"The ITU Radiocommunication Bureau found the No. 21.16 pfd limit exceeded "
                                f"(finding X/21.16 on {g['grp_id'].nunique()} group(s)). The Bureau checks the whole "
                                f"Earth's surface; over Finland: {why}"})
    if extra:
        concerns = pd.concat([concerns, pd.DataFrame(extra)], ignore_index=True).reindex(columns=CONCERN_COLUMNS)
    note(f"ITU BR findings X/21.16 in this issue: {x2116['ntc_id'].nunique()} notices; alerts here: "
         f"{len(set(x2116['ntc_id']) & flagged)}; listed as BR_UNFAVOURABLE_21_16 concerns: {len(extra)}")
    note(f"Concerns (separate CSV, networks without an alert): {len(concerns)} - "
         + ", ".join(f"{c}: {int((concerns['result_class'] == c).sum())}" for c in CONCERN_PRIORITY))
    alerts = prioritise_space(alerts)
    note("Alert priority: " + ", ".join(f"{p}: {int((alerts['priority'] == p).sum())}" for p in PRIORITIES))
    return alerts, concerns


PRIORITIES = ["High", "Medium", "Low"]


def prioritise_space(alerts: pd.DataFrame) -> pd.DataFrame:
    """Deterministic review priority for space alerts (documented in README and report).

    High:   margin >= 10 dB, or the ITU BR itself found No. 21.16 exceeded (X/21.16),
            or Finnish special-interest stations use the band and margin >= 3 dB.
    Medium: margin >= 3 dB, or Finnish stations use the band, or RR No. 9.14 coordination required.
    Low:    everything else (0-3 dB, no Finnish station in the band).
    """
    alerts = alerts.copy()
    margin = pd.to_numeric(alerts["margin_of_exceedance"], errors="coerce").fillna(0)
    br = alerts["br_finding"].fillna("").astype(str).str.contains("X/21.16", regex=False)
    stations = alerts["finnish_station_id"].fillna("").astype(str).str.len() > 0
    coord = alerts["result_class"].eq("COORDINATION REQUIRED")
    high = (margin >= 10) | br | (stations & (margin >= 3))
    medium = ~high & ((margin >= 3) | stations | coord)
    alerts["priority"] = np.select([high, medium], ["High", "Medium"], default="Low")
    alerts["priority_reason"] = [
        "; ".join(t for t, on in ((f"{m:+.1f} dB", True), ("ITU BR finding X/21.16", b),
                                  ("Finnish stations in band", s), ("RR 9.14 coordination", c)) if on)
        for m, b, s, c in zip(margin, br, stations, coord)]
    rank = alerts["priority"].map(PRIORITIES.index)
    return alerts.assign(_r=rank).sort_values(["_r", "margin_of_exceedance"], ascending=[True, False]).drop(columns="_r")


def _br_text(g: pd.DataFrame) -> str:
    regs = g.drop_duplicates("grp_id")["fdg_reg"].fillna("").value_counts()
    provs = sorted({p for v in g["fdg_prov"].dropna() for p in str(v).replace(";", ",").split(",")
                    if p.startswith(("X/", "Q/"))})
    text = ", ".join(f"{r} x{n}" for r, n in regs.items() if r)
    return text + (f" ({', '.join(provs)})" if provs else "")


def _finland_visibility(ntc, n, orb: pd.DataFrame) -> str:
    if pd.notna(n["long_nom"]):
        el, _ = gso_geometry(float(n["long_nom"]), np.array(FINLAND_POINTS))
        return (f"GSO at {n['long_nom']:.1f} deg reaches at most {el.max():.1f} deg elevation"
                + (" - not visible" if el.max() < 0 else " - visible, but no calculated exceedance in a band with a limit"))
    if ntc not in orb.index or pd.isna(orb.at[ntc, "perig_km"]):
        return "no orbit data to evaluate"
    o = orb.loc[ntc]
    inc = 90.0 if pd.isna(o["inclin_ang"]) else float(o["inclin_ang"])
    best = max(ngso_max_elevation(float(h), inc, min(p[0] for p in FINLAND_POINTS))
               for h in (o["perig_km"], o["apog_km"]) if pd.notna(h))
    eff = inc if inc <= 90 else 180 - inc
    return (f"never visible (inclination {inc:g} deg = {eff:g} deg latitude reach, altitude "
            f"{o['perig_km']:g}-{o['apog_km']:g} km)" if best < 0 else
            f"visible (max elevation {best:.0f} deg) but no calculated exceedance over Finland")


def one_per_network(rows: pd.DataFrame, result_class: str | None,
                    min_margin_db: float = MIN_EXCEEDANCE_MARGIN_DB,
                    near_limit_db: float = NEAR_LIMIT_DB) -> pd.DataFrame:
    """Worst-case row per satellite network (a notice and the notice it modifies count once).

    Alerts (result_class given) rank by margin; concerns rank by concern class, then margin.
    """
    if rows.empty:
        return pd.DataFrame(columns=CONCERN_COLUMNS + ["freq_min", "freq_max"])
    keys = (["_priority", "margin_of_exceedance", "freq_min"], [True, False, True])
    rows = rows.sort_values(keys[0], ascending=keys[1], kind="mergesort")
    hits = rows.groupby("network_key").agg(
        n_hits=("grp_id", "size"),
        rows=("row_id", lambda s: ", ".join(sorted(set(s)))),
        notices=("ntc_id", lambda s: sorted(set(s))),
        concern_types=("concern_class", lambda s: ", ".join(c for c in CONCERN_PRIORITY if c in set(s)))
        if "concern_class" in rows else ("row_id", lambda s: ""))
    out = rows.drop_duplicates(subset=["network_key"]).copy().join(hits, on="network_key")

    out["notice_id"] = out["ntc_id"]
    out["related_notice_id"] = [";".join(str(n) for n in ns if n != own) for ns, own in zip(out["notices"], out["ntc_id"])]
    out["country"] = out["adm"]
    out["notice_type"] = out["ntf_rsn"].map(NOTICE_TYPES).fillna(out["ntf_rsn"])
    out["gso_longitude"] = out["long_nom"]
    out["frequency_mhz"] = out["freq_min"].round(4).astype(str) + "-" + out["freq_max"].round(4).astype(str)
    out["service"] = [("/".join(s) if r != SCREEN_ROW else ("/".join(sorted(x)) or c))
                      for s, r, x, c in zip(out["services_y"], out["row_id"], out["services_x"], out["station_class"].fillna(""))]
    out["emission"] = out["design_emi"]
    out["reference_bandwidth"] = out["reference_bandwidth_hz"].map(
        lambda hz: f"{hz / 1e6:g} MHz" if hz >= 1e6 else f"{hz / 1e3:g} kHz")
    out["table21_4_row"] = out["row_id"].where(out["row_id"] != SCREEN_ROW, "none (no Table 21-4 limit)")
    notes = out["footnotes"] if "footnotes" in out else pd.Series("", index=out.index)
    own = out["rr_reference_row"] if "rr_reference_row" in out else pd.Series(np.nan, index=out.index)
    out["rr_reference"] = [o if isinstance(o, str) else
                           (RR_REFERENCE + (f", footnote {n}" if isinstance(n, str) and n else "")
                            if r != SCREEN_ROW else "RR Art. 21 / App. 5: no limit or threshold for this band/service")
                           for r, n, o in zip(out["row_id"], notes, own)]
    out["result_class"] = out["alert_class"] if result_class == "ALERT" else (result_class or out["concern_class"])
    worst = "worst case per Traficom guidance: peak gain {g:.1f} dBi toward Finland"
    reasons = []
    for r in out.itertuples():
        geo = f"PFD {r.pfd_dbw_m2:.1f} dB(W/m2) in {r.reference_bandwidth} at {r.elevation_deg:.1f} deg elevation"
        ap5 = getattr(r, "criterion", "") == "AP5"
        label = "RR Appendix 5 coordination threshold" if ap5 else "RR Table 21-4 limit"
        if r.result_class == "EXCEEDS LIMIT":
            text = f"{geo} exceeds RR Table 21-4 limit {r.pfd_limit_dbw_m2:.1f} by {r.margin_of_exceedance:.1f} dB"
        elif r.result_class == "COORDINATION REQUIRED":
            text = (f"{geo} exceeds the RR Appendix 5 coordination threshold {r.pfd_limit_dbw_m2:.1f} by "
                    f"{r.margin_of_exceedance:.1f} dB: coordination with Finland is required under RR No. 9.14")
        elif r.result_class == "SUB_QA_EXCEEDANCE":
            text = (f"{geo} exceeds RR Table 21-4 limit {r.pfd_limit_dbw_m2:.1f} by {r.margin_of_exceedance:.1f} dB, "
                    f"below the {min_margin_db:g} dB QA gate")
        elif r.result_class == "LIMIT_NOT_APPLICABLE":
            text = (f"{geo} would exceed RR Table 21-4 limit {r.pfd_limit_dbw_m2:.1f} by {r.margin_of_exceedance:.1f} dB, "
                    f"but the limit applies only to systems of 99 or fewer satellites (RR 21.16.4); this system "
                    f"notifies {r.n_satellites:.0f} satellites - limit subject to further study")
        elif r.result_class == "WORST_CASE_ISS":
            text = (f"{geo} would exceed {label} {r.pfd_limit_dbw_m2:.1f} by {r.margin_of_exceedance:.1f} dB if the "
                    f"inter-satellite beam's peak gain pointed at Finland (worst case; beam normally points at "
                    f"another spacecraft)")
        elif r.result_class == "NEAR_LIMIT":
            text = (f"{geo} is {-r.margin_of_exceedance:.1f} dB below {label} {r.pfd_limit_dbw_m2:.1f} "
                    f"(near-limit screening margin {near_limit_db:g} dB)")
        else:
            text = (f"No RR Table 21-4 limit for this band; {geo} is {r.margin_of_exceedance:.1f} dB above the "
                    f"team screening level {r.pfd_limit_dbw_m2:.1f} dB(W/m2) in 4 kHz, in a band used by Finnish stations")
        reasons.append(f"{text} ({worst.format(g=r.gain)}); {r.n_hits} combination(s) in rows {r.rows}")
    out["reason"] = reasons
    for column in ("pfd_dbw_m2", "pfd_limit_dbw_m2", "margin_of_exceedance", "calculated_power_dbw", "eirp_ref_dbw",
                   "elevation_deg", "distance_km", "altitude_km"):
        out[column] = out[column].astype(float).round(2)
    out["finnish_station_id"] = ""
    out["m4_service"] = ""
    return out.reindex(columns=CONCERN_COLUMNS + ["freq_min", "freq_max"])


def attach_victims(findings: pd.DataFrame, m4: pd.DataFrame, stations: pd.DataFrame,
                   columns: list[str] = OUTPUT_COLUMNS) -> pd.DataFrame:
    """Finnish stations and M4 services whose frequency range overlaps the exceeding assignment."""
    for index, row in findings.iterrows():
        lo, hi = row["freq_min"], row["freq_max"]
        hit = stations[(stations["freq_min_mhz"] < hi) & (stations["freq_max_mhz"] > lo)]
        findings.at[index, "finnish_station_id"] = ";".join(
            f"{sid}({'/'.join(sorted(set(d)))})" for sid, d in hit.groupby("station_id", sort=False)["direction"])
        bands = m4[(m4["freq_min_mhz"] < hi) & (m4["freq_max_mhz"] > lo)]
        findings.at[index, "m4_service"] = "; ".join(bands["Services_in_Finland"].fillna("").astype(str).unique())
    if "concern_types" in columns:
        rank = findings["result_class"].map(lambda c: CONCERN_PRIORITY.index(c) if c in CONCERN_PRIORITY else 9)
        return findings.assign(_r=rank).sort_values(["_r", "margin_of_exceedance"], ascending=[True, False]).reindex(columns=columns)
    return findings.reindex(columns=columns).sort_values("margin_of_exceedance", ascending=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--space-db", type=Path, default=Path("ific3074.sqlite"))
    parser.add_argument("--ific-number", type=int, default=3074)
    parser.add_argument("--shield", type=Path, default=Path("pfd.json"), help="Table 21-4 limit register")
    parser.add_argument("--m4", type=Path, default=Path("m4.json"))
    parser.add_argument("--stations", type=Path,
                        default=Path("Traficom materials") / "Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx")
    parser.add_argument("--exclude-notifications", action="store_true",
                        help="Scan only API/A, CR/C and AP30 (drop Article 11 notifications, ntf_rsn N)")
    parser.add_argument("--min-exceedance-db", type=float, default=MIN_EXCEEDANCE_MARGIN_DB)
    parser.add_argument("--near-limit-db", type=float, default=NEAR_LIMIT_DB,
                        help="Concern: Table 21-4 margin within this many dB below the limit")
    parser.add_argument("--screen-pfd", type=float, default=SCREEN_PFD_DBW_M2_4KHZ,
                        help="Concern: PFD screening level dB(W/m2) in 4 kHz for bands without a Table 21-4 limit")
    parser.add_argument("--space-output", type=Path, default=None)
    parser.add_argument("--concerns-output", type=Path, default=None)
    parser.add_argument("--audit-output", type=Path, default=None)
    args = parser.parse_args()
    if not args.space_db.is_file() or args.space_db.stat().st_size == 0:
        raise SystemExit(f"Space IFIC database not found or empty: {args.space_db} "
                         f"(convert it with convert_mdb.py, or run pipeline.py on the .zip/.mdb)")
    output = args.space_output or Path(f"space_{args.ific_number}_report.csv")
    args.audit_output = args.audit_output or Path(f"space_{args.ific_number}_audit.txt")
    concerns_output = args.concerns_output or Path(f"space_{args.ific_number}_concerns.csv")

    audit = [f"INTERFERENCE ANALYSIS AUDIT - Space IFIC {args.ific_number}",
             f"Database: {args.space_db}", f"Limits: {args.shield} (RR Table 21-4)",
             f"Stations: {args.stations}", f"QA gate: margin >= {args.min_exceedance_db:g} dB, one row per network",
             f"Concern screening: near-limit {args.near_limit_db:g} dB, no-limit bands {args.screen_pfd:g} dB(W/m2)/4 kHz"]
    audit += provenance([args.space_db, args.shield, args.m4, args.stations])
    notice_types = ("A", "C") if args.exclude_notifications else ("A", "C", "N")
    report, concerns = space_report(args.space_db, load_limits(args.shield), load_m4(args.m4),
                                    load_stations(args.stations), args.ific_number, notice_types,
                                    args.min_exceedance_db, audit, args.near_limit_db, args.screen_pfd)
    write_outputs(report, output)
    write_outputs(concerns, concerns_output)
    audit.append(f"Exported {len(report)} alerts to {output}: " + ", ".join(f"{k}: {v}" for k, v in report["result_class"].value_counts().items()))
    audit.append(f"Exported {len(concerns)} concerns to {concerns_output}")
    args.audit_output.write_text("\n".join(audit) + "\n", encoding="utf-8")
    print("\n".join(audit))


if __name__ == "__main__":
    main()
