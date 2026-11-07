#!/usr/bin/env python3
"""Terrestrial IFIC land-mobile screening against ECC T/R 25-08 at the Finnish border.

For every foreign land-mobile assignment (BR IFIC Terrestrial Preface classes FB, FL,
ML) in the issue, the field strength produced on Finnish territory is calculated with
Recommendation ITU-R P.1546-6 (reference implementation Py1546 by ITU-R WP 3K) along
the Finnish land border and the outer limit of the territorial sea, and compared with
the T/R 25-08 A1.2.3 general land-mobile trigger field strength. Finnish stations of
the Traficom special-interest list on overlapping frequencies are flagged as well.

Parameters (Traficom guidance, SinceAI Discord 7.11.2026):
  * criterion: T/R 25-08 A1.2.3, 20 dB(uV/m) in 25 kHz, also above 470 MHz;
  * P.1546-6, 10 % time, 50 % locations, receiving antenna 10 m, Rural, no terrain
    profile; no 10 dB height correction;
  * evaluation along the land border and the territorial-sea outer limit;
  * worst case: the maximum notified e.r.p. is assumed toward Finland, a mobile
    station is placed at the edge of its notified circle nearest to Finland;
  * any calculated exceedance of the trigger is flagged ("if your calculations indicate
    that the coordination thresholds have been exceeded, the result should be flagged");
  * coordination recorded with Finland (fxm_coord) is reported but does not close the
    case: Traficom verifies it against its own frequency register.

If Py1546 is not installed the script falls back to free-space field strength, an
upper bound, and reports every case as a concern (REVIEW:P1546_UNAVAILABLE) only.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from inspection_engine import load_m4, load_stations, open_db, provenance, write_outputs

# Py1546 6.1 still calls np.mat, removed in NumPy 2.0; it was an alias of np.asmatrix.
if not hasattr(np, "mat"):
    np.mat = np.asmatrix

try:
    from Py1546 import P1546
except ImportError:  # pragma: no cover - optional dependency
    P1546 = None

LAND_MOBILE_CLASSES = ("FB", "FL", "ML")       # BR IFIC Terrestrial Preface, land mobile service
CHANGE_TYPES = {"ADD": "NEW", "MODIFY": "MODIFIED", "SUPPRESS": "WITHDRAWN", "RECORDED": "RECORDED"}

TRIGGER_DBUV_M = 20.0          # T/R 25-08 A1.2.3 general land-mobile trigger
TRIGGER_REF_BW_KHZ = 25.0      # ... in 25 kHz
TIME_PERCENT = 10.0
RX_HEIGHT_M = 10.0
MOBILE_TX_HEIGHT_M = 1.5       # fallback when neither fxm_ant nor the t_hgt_agl remark gives a height
MAX_DISTANCE_KM = 1000.0       # P.1546 validity
MIN_EXCEEDANCE_MARGIN_DB = 0.0  # Traficom: any calculated exceedance is flagged
NEAR_LIMIT_DB = 6.0
POINTS_PER_BOUNDARY = 12       # nearest boundary points evaluated per transmitter
EARTH_RADIUS_KM = 6371.0

GEODATA = Path(__file__).resolve().parent / "geodata"
DEFAULT_BORDER_FILE = GEODATA / "finland_boundaries.csv"   # built by build_boundaries.py (Natural Earth)
LAND_MASK_FILE = GEODATA / "land_mask.npz"

# Fallback only (used when geodata/ is missing): coarse hand-drawn polylines.
LAND_BORDER = [
    # Finland - Russia, Gulf of Finland to the Norwegian tripoint
    (60.47, 27.77), (60.58, 27.85), (60.73, 28.10), (60.86, 28.32), (61.04, 28.62), (61.17, 28.86),
    (61.30, 29.20), (61.50, 29.55), (61.73, 30.00), (61.95, 30.55), (62.20, 31.00), (62.50, 31.40),
    (62.90, 31.58), (63.20, 31.25), (63.60, 30.70), (64.00, 30.10), (64.48, 30.05), (64.90, 29.60),
    (65.40, 29.80), (65.90, 29.95), (66.30, 29.60), (66.83, 29.30), (67.50, 29.60), (68.00, 28.70),
    (68.60, 28.40), (68.90, 28.80), (69.05, 28.93),
    # Finland - Norway
    (69.35, 29.20), (69.70, 28.90), (70.09, 27.90), (69.90, 27.00), (69.45, 25.90), (68.95, 25.80),
    (68.90, 23.00), (69.06, 20.55),
    # Finland - Sweden
    (68.60, 21.60), (68.44, 22.48), (67.95, 23.60), (67.30, 23.80), (66.80, 23.85), (66.30, 23.65),
    (65.84, 24.15),
]
TERRITORIAL_SEA = [
    # Bothnian Bay -> Kvarken -> Bothnian Sea -> Aland -> Baltic -> Gulf of Finland
    (65.75, 24.05), (65.20, 24.55), (64.60, 23.90), (64.00, 22.40), (63.50, 21.00), (63.20, 20.60),
    (62.50, 20.60), (61.60, 20.90), (60.90, 20.60), (60.45, 19.10), (59.90, 19.60), (59.75, 20.60),
    (59.60, 21.60), (59.60, 22.90), (59.75, 24.00), (59.85, 25.00), (59.95, 26.00), (60.05, 26.70),
    (60.15, 27.20), (60.35, 27.60), (60.47, 27.77),
]
# Rough water polygon (Gulf of Finland, Gulf of Bothnia, northern Baltic) used to split a
# path into Land / Cold-sea zones; the Baltic is "cold sea" in P.1546.
BALTIC_WATER = [
    (60.47, 27.77), (60.55, 27.20), (60.40, 26.50), (60.30, 25.50), (60.15, 24.95), (60.05, 24.00),
    (59.82, 22.95), (60.20, 22.00), (60.40, 21.40), (60.90, 21.30), (61.48, 21.50), (62.20, 21.20),
    (63.10, 21.50), (63.80, 22.60), (64.68, 24.47), (65.00, 25.40), (65.60, 24.90), (65.84, 24.15),
    (65.58, 22.15), (64.75, 21.20), (63.80, 20.30), (63.20, 19.00), (62.60, 17.90), (61.70, 17.20),
    (60.70, 17.40), (60.20, 18.60), (59.80, 18.90), (59.00, 18.30), (58.00, 16.80), (57.50, 16.70),
    (57.50, 21.00), (58.30, 22.00), (59.00, 23.40), (59.45, 24.60), (59.55, 26.00), (59.45, 27.50),
    (59.70, 28.30), (59.90, 29.00), (59.90, 30.20), (60.20, 29.60), (60.70, 28.70), (60.55, 28.00),
]

TERRA_SQL = f"""
SELECT t.terrakey, t.adm, t.adm_ref_id, t.stn_cls, t.intent, t.notice_typ, t.freq_assgn,
       t.bdwdth, t.emi_cls, t.d_inuse,
       g.geo_type, g.site_name, g.ctry, g.lat_dec, g.long_dec, g.radius, g.site_alt
FROM fxm_terra t
LEFT JOIN fxm_tx x ON x.terrakey = t.terrakey
LEFT JOIN fxm_geo g ON g.geo_key = COALESCE(x.geo_key, t.geo_key)
WHERE t.stn_cls IN ({",".join(f"'{c}'" for c in LAND_MOBILE_CLASSES)})
"""

ANT_SQL = """
SELECT a.terrakey, a.pwr_dbw, a.pwr_eiv, a.pwr_xyz, a.gain_max, a.gain_type, a.hgt_agl,
       a.eff_hgtmax, a.ant_dir, (SELECT MAX(h.eff_hgt) FROM fxm_ant_hgt h
                                 WHERE h.terrakey = a.terrakey AND h.ant_key = a.ant_key) AS eff_hgt
FROM fxm_ant a
"""

COORD_SQL = """
SELECT terrakey, coord_prov, coord_st, d_coord_st FROM fxm_coord WHERE adm = 'FIN'
ORDER BY terrakey, d_coord_st DESC
"""

RMKS_SQL = "SELECT terrakey, rmk_type, rmk_text FROM fxm_rmks"

OUTPUT_COLUMNS = [
    "notice_id", "adm_ref_id", "country", "station_class", "change_type", "site_name", "latitude",
    "longitude", "geo_type", "radius_km", "frequency_mhz", "bandwidth_khz", "emission", "erp_dbw",
    "tx_height_m", "worst_point_lat", "worst_point_lon", "worst_point_type", "distance_km", "path",
    "model", "field_strength_dbuv_m", "threshold_dbuv_m", "margin_db", "finnish_station_id",
    "m4_service", "coordination_with_fin", "itu_remarks", "result_class", "priority", "priority_reason", "reason",
]


# --------------------------------------------------------------------------- geometry

def densify(polyline, step_km: float = 2.0) -> np.ndarray:
    out = []
    for (a_lat, a_lon), (b_lat, b_lon) in zip(polyline[:-1], polyline[1:]):
        n = max(1, int(np.ceil(distance_km(a_lat, a_lon, np.array([b_lat]), np.array([b_lon]))[0] / step_km)))
        out.extend(zip(np.linspace(a_lat, b_lat, n, endpoint=False), np.linspace(a_lon, b_lon, n, endpoint=False)))
    out.append(polyline[-1])
    return np.array(out)


def distance_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def in_polygon(lat: np.ndarray, lon: np.ndarray, polygon) -> np.ndarray:
    poly = np.array(polygon)
    inside = np.zeros(lat.shape, dtype=bool)
    for (y1, x1), (y2, x2) in zip(poly, np.roll(poly, -1, axis=0)):
        crosses = (y1 > lat) != (y2 > lat)
        x_at = x1 + (lat - y1) * (x2 - x1) / np.where(y2 == y1, 1e-12, y2 - y1)
        inside ^= crosses & (lon < x_at)
    return inside


def load_land_mask(path: Path = LAND_MASK_FILE):
    if not path.exists():
        return None
    m = np.load(path)
    land = np.unpackbits(m["land"], axis=1)[:, :int(m["shape"][1])].astype(bool)
    return land, float(m["lat0"]), float(m["lon0"]), float(m["step"])


LAND_MASK = load_land_mask()


def is_sea(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    if LAND_MASK is None:
        return in_polygon(lat, lon, BALTIC_WATER)
    land, lat0, lon0, step = LAND_MASK
    r = np.clip(np.rint((lat - lat0) / step).astype(int), 0, land.shape[0] - 1)
    c = np.clip(np.rint((lon - lon0) / step).astype(int), 0, land.shape[1] - 1)
    return ~land[r, c]


def path_zones(lat1, lon1, lat2, lon2, total_km: float) -> tuple[list[float], list[str]]:
    """Split the path into consecutive Land / Cold-sea zones (P.1546 mixed path; the Baltic is cold sea)."""
    n = max(2, int(total_km))
    t = np.linspace(0, 1, n)
    sea = is_sea(lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t)
    lengths, zones, start = [], [], 0
    for i in range(1, n + 1):
        if i == n or sea[i] != sea[start]:
            lengths.append(total_km * (i - start) / n)
            zones.append("Cold" if sea[start] else "Land")
            start = i
    return lengths, zones


# --------------------------------------------------------------------------- propagation

def field_strength_1kw(f_mhz: float, h1: float, lengths: list[float], zones: list[str]) -> float:
    """dB(uV/m) for 1 kW e.r.p., P.1546-6 (or free-space upper bound if unavailable)."""
    d = max(sum(lengths), 1.0)
    if P1546 is None:
        return 106.9 - 20 * np.log10(d)
    with contextlib.redirect_stdout(io.StringIO()):  # Py1546 prints h1 warnings for 3-15 km paths
        E, _ = P1546.bt_loss(float(f_mhz), TIME_PERCENT, float(max(h1, 1.0)), RX_HEIGHT_M, 10.0, "Rural",
                             [max(x, 0.001) for x in lengths], zones, 0)
    return float(E)


def erp_dbw(ant: pd.DataFrame) -> pd.Series:
    """e.r.p. from the notified power: E = e.r.p., I = e.i.r.p., blank = power to antenna."""
    pwr = pd.to_numeric(ant["pwr_dbw"], errors="coerce")
    gain = pd.to_numeric(ant["gain_max"], errors="coerce").fillna(0.0)
    gain_dbd = np.where(ant["gain_type"].fillna("I").str.upper().eq("D"), gain, gain - 2.15)
    kind = ant["pwr_eiv"].fillna("").str.upper()
    return pd.Series(np.select([kind.eq("E"), kind.eq("I")], [pwr, pwr - 2.15], default=pwr + gain_dbd),
                     index=ant.index)


# --------------------------------------------------------------------------- scan

def terrestrial_report(database: Path, stations: pd.DataFrame, m4: pd.DataFrame, ific_number: int,
                       min_exceedance_db: float = MIN_EXCEEDANCE_MARGIN_DB,
                       near_limit_db: float = NEAR_LIMIT_DB, border: dict | None = None,
                       audit: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    audit = audit if audit is not None else []
    note = audit.append
    with open_db(database) as connection:
        terra = pd.read_sql_query(TERRA_SQL, connection)
        ant = pd.read_sql_query(ANT_SQL, connection)
        coord = pd.read_sql_query(COORD_SQL, connection)
        rmks = pd.read_sql_query(RMKS_SQL, connection)

    # Coordination status with Finland, newest first, e.g. "COORD COMPLETED (RR6.7, 2026-06-22)".
    coord_text = coord.groupby("terrakey").apply(
        lambda g: "; ".join(f"{r.coord_st} ({r.coord_prov}, {str(r.d_coord_st)[:10]})" for r in g.itertuples()),
        include_groups=False).rename("coordination_with_fin")
    remarks = rmks.groupby("terrakey")["rmk_text"].agg(lambda s: " | ".join(s.dropna().str.strip())).rename("itu_remarks")
    remark_height = pd.to_numeric(rmks["rmk_text"].str.extract(r"t_hgt_agl\s*[=:]\s*(\d+(?:\.\d+)?)")[0],
                                  errors="coerce").groupby(rmks["terrakey"]).max().rename("remark_height_m")

    terra["change_type"] = terra["intent"].map(CHANGE_TYPES).fillna(terra["intent"])
    note(f"Terrestrial IFIC {ific_number}: {len(terra)} land-mobile records ({', '.join(LAND_MOBILE_CLASSES)})")
    write_trends(terra, note)

    foreign = terra[terra["adm"] != "FIN"]
    note(f"Foreign administrations: {len(foreign)} records; Finnish records excluded: {len(terra) - len(foreign)}")
    withdrawn = foreign["change_type"].isin(["WITHDRAWN", "RECORDED"])
    note(f"Not evaluated: {int((foreign['change_type'] == 'WITHDRAWN').sum())} WITHDRAWN (CHANGE_WITHDRAWN), "
         f"{int((foreign['change_type'] == 'RECORDED').sum())} RECORDED (pre-modification state of a MODIFIED record)")
    foreign = foreign[~withdrawn]

    # Strongest notified antenna per assignment.
    ant["erp_dbw"] = erp_dbw(ant)
    ant["tx_height_m"] = pd.to_numeric(ant["eff_hgt"], errors="coerce").fillna(
        pd.to_numeric(ant["eff_hgtmax"], errors="coerce")).fillna(pd.to_numeric(ant["hgt_agl"], errors="coerce"))
    ant = ant.sort_values("erp_dbw", ascending=False).drop_duplicates("terrakey")
    rows = foreign.merge(ant[["terrakey", "erp_dbw", "tx_height_m"]], on="terrakey", how="left")
    rows = rows.join(coord_text, on="terrakey").join(remarks, on="terrakey").join(remark_height, on="terrakey")
    rows["tx_height_m"] = rows["tx_height_m"].fillna(rows["remark_height_m"]).fillna(
        pd.Series(np.where(rows["stn_cls"] == "ML", MOBILE_TX_HEIGHT_M, np.nan), index=rows.index))
    coordinated = rows["coordination_with_fin"].notna()
    note(f"Coordination with Finland recorded (fxm_coord, adm FIN): {int(coordinated.sum())} evaluated records - "
         f"reported in column coordination_with_fin, still flagged for verification against the Traficom register")

    no_coord = rows["lat_dec"].isna() | rows["long_dec"].isna()
    note(f"REVIEW (not exported): {int(no_coord.sum())} records without point coordinates "
         f"(zones: {sorted(rows.loc[no_coord, 'adm'].unique())})")
    rows = rows[~no_coord].copy()
    no_power = rows["erp_dbw"].isna() | rows["tx_height_m"].isna()
    note(f"REVIEW:P1546_INPUT_MISSING (not exported): {int(no_power.sum())} records without e.r.p. or antenna height")
    rows = rows[~no_power].copy()

    border = border or {"land border": densify(LAND_BORDER), "territorial sea limit": densify(TERRITORIAL_SEA)}
    all_points = np.vstack(list(border.values()))
    rows["radius_km"] = pd.to_numeric(rows["radius"], errors="coerce").fillna(0.0)
    nearest = np.array([distance_km(la, lo, all_points[:, 0], all_points[:, 1]).min()
                        for la, lo in zip(rows["lat_dec"], rows["long_dec"])])
    far = (nearest - rows["radius_km"].to_numpy()) > MAX_DISTANCE_KM
    note(f"Out of P.1546 range (> {MAX_DISTANCE_KM:g} km from Finland): {int(far.sum())} records "
         f"({', '.join(f'{a}: {n}' for a, n in rows.loc[far, 'adm'].value_counts().items())})")
    rows = rows[~far].copy()
    note(f"Evaluated with {'ITU-R P.1546-6 (Py1546 reference implementation)' if P1546 else 'FREE SPACE (Py1546 not installed)'}: "
         f"{len(rows)} records from {', '.join(sorted(rows['adm'].unique()))}")

    results = [evaluate(r, border, stations) for r in rows.itertuples(index=False)]
    rows = pd.concat([rows.reset_index(drop=True), pd.DataFrame(results)], axis=1)
    rows["threshold_dbuv_m"] = TRIGGER_DBUV_M + 10 * np.log10(np.maximum(rows["bdwdth"].fillna(25.0), TRIGGER_REF_BW_KHZ)
                                                              / TRIGGER_REF_BW_KHZ)
    rows["margin_db"] = rows["field_strength_dbuv_m"] - rows["threshold_dbuv_m"]
    rows["finnish_station_id"] = [
        ";".join(f"{sid}" for sid, e in hits if e >= thr) for hits, thr in zip(rows["station_hits"], rows["threshold_dbuv_m"])]

    m = rows["margin_db"]
    if P1546 is None:
        cls = np.where(m >= 0, "REVIEW:P1546_UNAVAILABLE", "")
    else:
        cls = np.select([m >= min_exceedance_db, (m >= 0), (m >= -near_limit_db)],
                        ["CRITICAL ERROR", "SUB_QA_EXCEEDANCE", "NEAR_LIMIT"], default="")
    rows["result_class"] = cls
    rows["reason"] = [reason(r, min_exceedance_db, near_limit_db) for r in rows.itertuples()]

    out = format_rows(rows, m4)
    alerts = prioritise_terrestrial(out[out["result_class"] == "CRITICAL ERROR"])
    concerns = out[out["result_class"].isin(["SUB_QA_EXCEEDANCE", "NEAR_LIMIT", "REVIEW:P1546_UNAVAILABLE"])]
    concerns = concerns.sort_values(["result_class", "margin_db"], ascending=[True, False])
    note(f"Alerts (margin >= {min_exceedance_db:g} dB): {len(alerts)}; concerns: {len(concerns)} "
         f"({', '.join(f'{c}: {n}' for c, n in concerns['result_class'].value_counts().items())})")
    return alerts.reindex(columns=OUTPUT_COLUMNS), concerns.reindex(columns=OUTPUT_COLUMNS)


def prioritise_terrestrial(alerts: pd.DataFrame) -> pd.DataFrame:
    """Deterministic review priority for terrestrial alerts.

    High:   margin >= 6 dB, or a Finnish special-interest station receives more than the trigger.
    Medium: margin 1-6 dB.
    Low:    margin below 1 dB (within P.1546 / boundary modelling uncertainty).
    """
    alerts = alerts.copy()
    margin = pd.to_numeric(alerts["margin_db"], errors="coerce").fillna(0)
    stations = alerts["finnish_station_id"].fillna("").astype(str).str.len() > 0
    alerts["priority"] = np.select([(margin >= 6) | stations, margin >= 1], ["High", "Medium"], default="Low")
    alerts["priority_reason"] = [
        f"{m:+.1f} dB" + ("; Finnish station above trigger" if s else "")
        + ("; within modelling uncertainty (< 1 dB)" if m < 1 and not s else "")
        for m, s in zip(margin, stations)]
    rank = alerts["priority"].map(["High", "Medium", "Low"].index)
    return alerts.assign(_r=rank).sort_values(["_r", "margin_db"], ascending=[True, False]).drop(columns="_r")


def evaluate(r, border: dict, stations: pd.DataFrame) -> dict:
    """Worst-case field strength on Finnish territory for one assignment."""
    best = None
    for kind, pts in border.items():
        d_all = distance_km(r.lat_dec, r.long_dec, pts[:, 0], pts[:, 1])
        for i in np.argsort(d_all)[:POINTS_PER_BOUNDARY]:
            d = max(d_all[i] - r.radius_km, 1.0)  # mobile at circle edge nearest to Finland
            lengths, zones = path_zones(r.lat_dec, r.long_dec, pts[i, 0], pts[i, 1], d)
            e = field_strength_1kw(r.freq_assgn, r.tx_height_m, lengths, zones) + (r.erp_dbw - 30.0)
            if best is None or e > best["field_strength_dbuv_m"]:
                best = {"field_strength_dbuv_m": e, "worst_point_lat": pts[i, 0], "worst_point_lon": pts[i, 1],
                        "worst_point_type": kind, "distance_km": d,
                        "path": " + ".join(f"{z} {x:.1f} km" for z, x in zip(zones, lengths))}
    # Finnish special-interest stations on overlapping frequencies (co-channel).
    half_bw = (r.bdwdth if pd.notna(r.bdwdth) else 25.0) / 2000.0
    near = stations[(stations["freq_min_mhz"] < r.freq_assgn + half_bw) &
                    (stations["freq_max_mhz"] > r.freq_assgn - half_bw)].drop_duplicates("station_id")
    hits = []
    for s in near.itertuples():
        d = max(distance_km(r.lat_dec, r.long_dec, np.array([s.lat]), np.array([s.lon]))[0] - r.radius_km, 1.0)
        if d <= MAX_DISTANCE_KM:
            lengths, zones = path_zones(r.lat_dec, r.long_dec, s.lat, s.lon, d)
            hits.append((s.station_id, field_strength_1kw(r.freq_assgn, r.tx_height_m, lengths, zones) + r.erp_dbw - 30.0))
    best["station_hits"] = hits
    return best


def reason(r, min_db: float, near_db: float) -> str:
    text = _reason(r, min_db, near_db)
    if isinstance(r.coordination_with_fin, str) and r.coordination_with_fin:
        text += (f"; ITU records coordination with Finland: {r.coordination_with_fin} - "
                 f"verify against the Traficom frequency register")
    return text


def _reason(r, min_db: float, near_db: float) -> str:
    base = (f"{r.field_strength_dbuv_m:.1f} dB(uV/m) at {r.worst_point_type} ({r.distance_km:.0f} km, "
            f"{r.path}) vs T/R 25-08 A1.2.3 trigger {r.threshold_dbuv_m:.1f} dB(uV/m)")
    if r.result_class == "CRITICAL ERROR":
        return f"{base}: exceeded by {r.margin_db:.1f} dB"
    if r.result_class == "SUB_QA_EXCEEDANCE":
        return f"{base}: exceeded by {r.margin_db:.1f} dB, below the {min_db:g} dB QA gate"
    if r.result_class == "NEAR_LIMIT":
        return f"{base}: {-r.margin_db:.1f} dB below the trigger (near-limit margin {near_db:g} dB)"
    if r.result_class.startswith("REVIEW"):
        return f"{base}: free-space upper bound only, P.1546 implementation not installed"
    return base


def format_rows(rows: pd.DataFrame, m4: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "notice_id": rows["terrakey"], "adm_ref_id": rows["adm_ref_id"], "country": rows["adm"],
        "station_class": rows["stn_cls"], "change_type": rows["change_type"], "site_name": rows["site_name"],
        "latitude": rows["lat_dec"].round(5), "longitude": rows["long_dec"].round(5),
        "geo_type": rows["geo_type"], "radius_km": rows["radius_km"], "frequency_mhz": rows["freq_assgn"],
        "bandwidth_khz": rows["bdwdth"], "emission": rows["emi_cls"], "erp_dbw": rows["erp_dbw"].round(2),
        "tx_height_m": rows["tx_height_m"], "worst_point_lat": rows["worst_point_lat"].round(4),
        "worst_point_lon": rows["worst_point_lon"].round(4), "worst_point_type": rows["worst_point_type"],
        "distance_km": rows["distance_km"].round(1), "path": rows["path"],
        "model": ("ITU-R P.1546-6 (Py1546), " if P1546 else "free space (upper bound), ")
                 + f"{TIME_PERCENT:g}% time, 50% locations, h2={RX_HEIGHT_M:g} m, Rural, no terrain",
        "field_strength_dbuv_m": rows["field_strength_dbuv_m"].round(2),
        "threshold_dbuv_m": rows["threshold_dbuv_m"].round(2), "margin_db": rows["margin_db"].round(2),
        "finnish_station_id": rows["finnish_station_id"],
        "coordination_with_fin": rows["coordination_with_fin"].fillna(""),
        "itu_remarks": rows["itu_remarks"].fillna(""), "result_class": rows["result_class"],
        "reason": rows["reason"],
    })
    out["m4_service"] = [
        "; ".join(m4.loc[(m4["freq_min_mhz"] <= f) & (m4["freq_max_mhz"] > f), "Services_in_Finland"]
                  .fillna("").astype(str).unique()) for f in out["frequency_mhz"]]
    return out


def write_trends(terra: pd.DataFrame, note) -> None:
    """Side quest: new activity by administration, station class and band."""
    t = terra.copy()
    t["band"] = pd.cut(t["freq_assgn"], [0, 174, 470, 694, 960, 1500, 2700, 1e6],
                       labels=["<174 MHz", "174-470", "470-694", "694-960", "960-1500", "1500-2700", ">2700"])
    pivot = t.pivot_table(index=["adm", "stn_cls"], columns="change_type", values="terrakey",
                          aggfunc="count", fill_value=0)
    note("Trends - land-mobile records by administration / class / change type:\n" + pivot.to_string())
    new = t[t["change_type"] == "NEW"]
    note("Trends - NEW land-mobile assignments by administration and band:\n"
         + new.pivot_table(index="adm", columns="band", values="terrakey", aggfunc="count",
                           fill_value=0, observed=False).to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--terrestrial-db", type=Path, default=Path("brific3076.sqlite"))
    parser.add_argument("--ific-number", type=int, default=3076)
    parser.add_argument("--m4", type=Path, default=Path("m4.json"))
    parser.add_argument("--stations", type=Path,
                        default=Path("Traficom materials") / "Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx")
    parser.add_argument("--border-file", type=Path,
                        default=DEFAULT_BORDER_FILE if DEFAULT_BORDER_FILE.exists() else None,
                        help="CSV with columns lat, lon, type ('land border' / 'territorial sea'); default "
                             "geodata/finland_boundaries.csv from build_boundaries.py (Natural Earth 1:10m)")
    parser.add_argument("--min-exceedance-db", type=float, default=MIN_EXCEEDANCE_MARGIN_DB)
    parser.add_argument("--near-limit-db", type=float, default=NEAR_LIMIT_DB)
    parser.add_argument("--allow-free-space", action="store_true",
                        help="Run without Py1546: free-space upper bound, every case REVIEW:P1546_UNAVAILABLE")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--concerns-output", type=Path, default=None)
    parser.add_argument("--audit-output", type=Path, default=None)
    args = parser.parse_args()
    if P1546 is None and not args.allow_free_space:
        raise SystemExit("Py1546 (ITU-R P.1546-6) is not installed in this interpreter. Use the project venv "
                         "(.venv/bin/python) or pip install -r requirements.txt; --allow-free-space runs a "
                         "free-space upper-bound screening that produces REVIEW concerns only.")
    if not args.terrestrial_db.is_file() or args.terrestrial_db.stat().st_size == 0:
        raise SystemExit(f"Terrestrial IFIC database not found or empty: {args.terrestrial_db} "
                         f"(convert it with convert_mdb.py, or run pipeline.py on the .zip/.mdb)")
    output = args.output or Path(f"terrestrial_{args.ific_number}_report.csv")
    concerns_output = args.concerns_output or Path(f"terrestrial_{args.ific_number}_concerns.csv")
    audit_output = args.audit_output or Path(f"terrestrial_{args.ific_number}_audit.txt")

    border = None
    if args.border_file:
        b = pd.read_csv(args.border_file)
        border = {kind: g[["lat", "lon"]].to_numpy() for kind, g in b.groupby("type")}
    audit = [f"TERRESTRIAL INTERFERENCE AUDIT - Terrestrial IFIC {args.ific_number}",
             f"Database: {args.terrestrial_db}", f"Stations: {args.stations}",
             f"Criterion: ECC T/R 25-08 A1.2.3, {TRIGGER_DBUV_M:g} dB(uV/m) in {TRIGGER_REF_BW_KHZ:g} kHz "
             f"(+10log(BW/25 kHz) for wider channels), {TIME_PERCENT:g}% time, 50% locations, h2 {RX_HEIGHT_M:g} m",
             f"Boundaries: {args.border_file or 'built-in coarse fallback polylines (~10 km accuracy)'}; "
             f"land/sea path split: {'geodata/land_mask.npz (Natural Earth 1:10m)' if LAND_MASK else 'coarse Baltic polygon'}",
             f"QA gate: margin >= {args.min_exceedance_db:g} dB; near-limit concern margin {args.near_limit_db:g} dB"]
    audit += provenance([args.terrestrial_db, args.m4, args.stations, args.border_file or "", LAND_MASK_FILE])
    if P1546 is not None:
        import importlib.metadata as md
        audit.append(f"Propagation: Py1546 {md.version('Py1546')} (ITU-R P.1546-6 reference implementation, ITU-R WP 3K)")
    alerts, concerns = terrestrial_report(args.terrestrial_db, load_stations(args.stations), load_m4(args.m4),
                                          args.ific_number, args.min_exceedance_db, args.near_limit_db, border, audit)
    write_outputs(alerts, output)
    write_outputs(concerns, concerns_output)
    audit.append(f"Exported {len(alerts)} alerts to {output} and {len(concerns)} concerns to {concerns_output}")
    audit_output.write_text("\n".join(audit) + "\n", encoding="utf-8")
    print("\n".join(audit))


if __name__ == "__main__":
    main()
