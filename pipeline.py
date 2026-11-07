#!/usr/bin/env python3
"""IFIC screening ETL pipeline: new BR IFIC in -> analysis, letters and report out.

    python pipeline.py                                   # process every new file in inbox/
    python pipeline.py ific3080.zip brific3082.mdb       # process given files
    python pipeline.py 3080                              # download Space + Terrestrial IFIC 3080 from the ITU
    python pipeline.py s3080 t3079                       # only the Space / only the Terrestrial part
    python pipeline.py --watch                           # fetch + process any newly published issue (cron)
    python pipeline.py --watch --every 6                 # keep watching, checking every 6 hours
    python pipeline.py --refresh-m4 inbox/*.zip          # also refresh M4 from the Traficom API
    python pipeline.py --report-only                     # rebuild report.html from stored runs

EXTRACT   .zip (ITU download) / .mdb / .sqlite. MDB is converted to SQLite with every
          table's row count verified, cached by SHA-256 in data/sqlite/. The dataset type
          (Space vs Terrestrial IFIC) and the issue number are detected from the data.
          Optional: refresh the M4 table from opendata.traficom.fi.
TRANSFORM Space: inspection_engine.space_report (RR Table 21-4 + App. 5 over Finland).
          Terrestrial: terrestrial.terrestrial_report (P.1546-6 vs T/R 25-08 at the border).
          Draft letters for every alert.
LOAD      runs/<kind>_<ific>/  alerts.csv/.json, concerns.csv/.json, audit.txt, letters/
          results.sqlite       cumulative runs + findings of every processed issue
          report.html          review page with an issue selector and history

A file whose SHA-256 was already processed is skipped (use --force to redo it), so the
pipeline can run unattended every two weeks, e.g. from cron:
    0 6 * * 1  cd /path/to/repo && .venv/bin/python pipeline.py >> pipeline.log 2>&1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "inbox"
DATA = ROOT / "data"
RUNS = ROOT / "runs"
RESULTS_DB = ROOT / "results.sqlite"
M4_URL = "https://opendata.traficom.fi/api/v13/Taajuusjakotaulukko"
ITU_SPACE_IFIC_URL = "https://www.itu.int/sns/ific10/ific{num}.zip"   # ITU BR Space IFIC download
ITU_TERRESTRIAL_IFIC_URL = "https://www.itu.int/en/ITU-R/terrestrial/brific/BRIFIC/MDB/brific{num}.zip"
URLS = {"space": ITU_SPACE_IFIC_URL, "terrestrial": ITU_TERRESTRIAL_IFIC_URL}
DEFAULTS = {
    "m4": ROOT / "m4.json",
    "limits": ROOT / "pfd.json",
    "stations": ROOT / "Traficom materials" / "Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx",
    "border": ROOT / "geodata" / "finland_boundaries.csv",
}


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- extract

def to_sqlite(source: Path, digest: str) -> Path:
    """Return the SQLite path for source (sha256 digest), converting/unzipping as needed."""
    suffix = source.suffix.lower()
    if suffix in (".sqlite", ".db", ".sqlite3"):
        return source
    cached = DATA / "sqlite" / f"{source.stem}_{digest[:12]}.sqlite"
    if cached.exists():
        log(f"  cached conversion {cached.relative_to(ROOT)}")
        return cached
    cached.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".zip":
        with zipfile.ZipFile(source) as archive:
            members = [m for m in archive.infolist() if m.filename.lower().endswith((".mdb", ".accdb"))]
            if not members:
                raise ValueError(f"{source.name}: no .mdb inside the zip")
            member = max(members, key=lambda m: m.file_size)
            raw = DATA / "raw" / Path(member.filename).name
            raw.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, raw.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            log(f"  unzipped {member.filename} ({member.file_size >> 20} MB)")
        mdb = raw
    elif suffix in (".mdb", ".accdb"):
        mdb = source
    else:
        raise ValueError(f"{source.name}: unsupported file type (expected .zip, .mdb or .sqlite)")
    from convert_mdb import convert
    started = time.time()
    convert(mdb, cached, log=lambda m: log("  " + m.strip()))
    log(f"  MDB -> SQLite in {time.time() - started:.0f} s")
    return cached


def published(url: str) -> bool:
    """True if the ITU already serves this file (HEAD 200); False on 404 (issue not published yet)."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def download(url: str, attempts: int = 5) -> Path:
    """Download url into inbox/, resuming a partial file (HTTP Range) after interruptions.

    Large BR IFIC files take long to download; the watcher fetches them as soon as they are
    published, so nobody has to wait for the download (Traficom feedback)."""
    target = INBOX / Path(url.split("?")[0]).name
    if target.exists():
        log(f"  already downloaded {target.relative_to(ROOT)}")
        return target
    INBOX.mkdir(exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    for attempt in range(1, attempts + 1):
        have = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                resumed = response.status == 206
                total = int(response.headers.get("Content-Length", 0)) + (have if resumed else 0)
                log(f"  downloading {url} ({total >> 20} MB" + (f", resuming at {have >> 20} MB)" if resumed else ")"))
                with partial.open("ab" if resumed else "wb") as out:
                    done, next_report = (have if resumed else 0), 0.1
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        if total and done / total >= next_report:
                            log(f"    {done / total:.0%} ({done >> 20}/{total >> 20} MB)")
                            next_report += 0.1
            if total and partial.stat().st_size != total:
                raise IOError(f"incomplete download: {partial.stat().st_size} of {total} bytes")
            partial.rename(target)
            log(f"  saved {target.relative_to(ROOT)} ({target.stat().st_size >> 20} MB)")
            return target
        except (OSError, urllib.error.URLError) as exc:
            if attempt == attempts:
                raise
            log(f"  download interrupted ({type(exc).__name__}: {exc}); retry {attempt}/{attempts - 1} in {10 * attempt} s")
            time.sleep(10 * attempt)


def resolve_spec(spec: str) -> list[str]:
    """'3084' -> Space + Terrestrial URLs, 's3084' / 't3084' -> one of them, http(s) URL -> itself."""
    if spec.startswith(("http://", "https://")):
        return [spec]
    kinds = {"s": ["space"], "t": ["terrestrial"]}.get(spec[0].lower(), ["space", "terrestrial"])
    number = spec[1:] if spec[0].lower() in "st" else spec
    return [URLS[k].format(num=int(number)) for k in kinds]


def is_issue_spec(spec: str) -> bool:
    return spec.startswith(("http://", "https://")) or spec.isdigit() or (
        len(spec) > 1 and spec[0].lower() in "st" and spec[1:].isdigit())


def watch_once(lookahead: int = 3) -> list[Path]:
    """Download every Space/Terrestrial issue published after the latest one processed."""
    latest = 0
    if RESULTS_DB.exists():
        with sqlite3.connect(RESULTS_DB) as con:
            latest = con.execute("SELECT COALESCE(MAX(ific), 0) FROM runs").fetchone()[0]
    if not latest:
        raise RuntimeError("no processed issue yet - run e.g. 'pipeline.py 3083' once to set the starting point")
    found = []
    for number in range(latest + 1, latest + 1 + lookahead):
        for kind, template in URLS.items():
            url = template.format(num=number)
            if published(url):
                log(f"new {kind} IFIC {number} published")
                found.append(download(url))
    if not found:
        log(f"no new IFIC after {latest} published yet (checked {latest + 1}-{latest + lookahead})")
    return found


def detect(db: Path) -> tuple[str, int]:
    """('space'|'terrestrial', IFIC number) from the database content."""
    from inspection_engine import open_db
    with open_db(db) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if {"notice", "grp", "emiss"} <= tables:
            ific = con.execute("SELECT wic_no, COUNT(*) FROM notice GROUP BY wic_no ORDER BY 2 DESC").fetchone()
            return "space", int(ific[0])
        if "fxm_terra" in tables:
            row = (con.execute("SELECT MAX(circ_no) FROM pub_current_circ").fetchone() if "pub_current_circ" in tables
                   else con.execute("SELECT MAX(circ_no) FROM circ").fetchone())
            return "terrestrial", int(row[0])
    raise ValueError(f"{db.name}: neither a Space IFIC (notice/grp/emiss) nor a Terrestrial IFIC (fxm_terra)")


def refresh_m4(path: Path) -> None:
    """Download the M4 frequency allocation table (OData, follows nextLink paging)."""
    rows, url = [], M4_URL
    while url:
        with urllib.request.urlopen(url, timeout=60) as response:
            page = json.load(response)
        rows += page.get("value", [])
        url = page.get("@odata.nextLink")
    if len(rows) < 1000:
        raise RuntimeError(f"M4 download returned only {len(rows)} rows; keeping {path.name}")
    path.write_text(json.dumps({"@odata.context": M4_URL, "value": rows}, ensure_ascii=False), encoding="utf-8")
    log(f"M4 refreshed from {M4_URL}: {len(rows)} rows")


# --------------------------------------------------------------------------- transform + load

def process(db: Path, digest: str, source: Path, kind: str, ific: int, cfg) -> dict:
    import inspection_engine as ie
    import letters as lt
    import terrestrial as tr

    # Check prerequisites before the previous results for this issue are replaced.
    if kind == "terrestrial" and tr.P1546 is None and not cfg.allow_free_space:
        raise RuntimeError("Py1546 (ITU-R P.1546-6) is not installed - terrestrial results would be free-space "
                           "upper bounds only. Install it (pip install -r requirements.txt) or pass "
                           "--allow-free-space to accept REVIEW-only output.")
    started = time.time()
    out = RUNS / f"{kind}_{ific}"
    if out.exists():
        shutil.rmtree(out)
    (out / "letters").mkdir(parents=True)
    m4, stations = ie.load_m4(cfg.m4), ie.load_stations(cfg.stations)
    audit = [f"{kind.upper()} IFIC {ific} - pipeline run", f"Source: {source.name} (sha256 {digest})",
             f"Database: {db.relative_to(ROOT) if db.is_relative_to(ROOT) else db}"]
    if kind == "space":
        audit.append(f"Alert gate: margin >= {cfg.min_exceedance_db:g} dB; near-limit {ie.NEAR_LIMIT_DB:g} dB")
        audit += ie.provenance([db, cfg.limits, cfg.m4, cfg.stations])
        notice_types = ("A", "C") if cfg.exclude_notifications else ("A", "C", "N")
        alerts, concerns = ie.space_report(db, ie.load_limits(cfg.limits), m4, stations, ific, notice_types,
                                           cfg.min_exceedance_db, audit)
        for _, f in alerts.iterrows():
            name = f"{f['country']}_{f['notice_id']}_{str(f['network_name']).replace('/', '-')}.txt"
            (out / "letters" / name).write_text(lt.template_letter(f, ific), encoding="utf-8")
    else:
        border = None
        if cfg.border.exists():
            b = pd.read_csv(cfg.border)
            border = {k: g[["lat", "lon"]].to_numpy() for k, g in b.groupby("type")}
        audit.append(f"Criterion: ECC T/R 25-08 A1.2.3, {tr.TRIGGER_DBUV_M:g} dB(uV/m) in 25 kHz; "
                     f"P.1546-6 {tr.TIME_PERCENT:g}% time; alert gate {cfg.min_exceedance_db:g} dB")
        audit += ie.provenance([db, cfg.m4, cfg.stations, cfg.border, tr.LAND_MASK_FILE])
        if tr.P1546 is None:
            log("  WARNING: Py1546 not installed - terrestrial results are free-space REVIEW only (--allow-free-space)")
        alerts, concerns = tr.terrestrial_report(db, stations, m4, ific, cfg.min_exceedance_db,
                                                 tr.NEAR_LIMIT_DB, border, audit, previous_databases(ific))
        for _, f in alerts.iterrows():
            (out / "letters" / f"T_{f['country']}_{f['notice_id']}.txt").write_text(
                lt.terrestrial_letter(f, ific), encoding="utf-8")

    ie.write_outputs(alerts, out / "alerts.csv")
    ie.write_outputs(concerns, out / "concerns.csv")
    seconds = time.time() - started
    audit.append(f"Exported {len(alerts)} alerts and {len(concerns)} concerns in {seconds:.1f} s")
    (out / "audit.txt").write_text("\n".join(audit) + "\n", encoding="utf-8")
    run = {"kind": kind, "ific": ific, "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "source": source.name, "source_sha256": digest, "n_alerts": len(alerts), "n_concerns": len(concerns),
           "seconds": round(seconds, 1), "sqlite_path": str(db)}
    write_workbook(out / f"{kind}_{ific}_results.xlsx", kind, ific, run, alerts, concerns)
    store(run, alerts, concerns)
    log(f"  {kind} IFIC {ific}: {len(alerts)} alerts, {len(concerns)} concerns -> {out.relative_to(ROOT)}/ "
        f"({seconds:.1f} s)")
    return run


SPACE_FIRST = ["priority", "network_name", "country", "notice_id", "related_notice_id", "notice_type", "orbit",
               "frequency_mhz", "result_class", "margin_of_exceedance", "pfd_dbw_m2", "pfd_limit_dbw_m2",
               "reference_bandwidth", "elevation_deg", "rr_reference", "br_finding", "priority_reason", "reason"]
TERRA_FIRST = ["priority", "site_name", "country", "notice_id", "station_class", "change_type", "frequency_mhz",
               "result_class", "margin_db", "field_strength_dbuv_m", "threshold_dbuv_m", "distance_km",
               "worst_point_type", "coordination_with_fin", "change_vs_previous_issues", "issue_history",
               "priority_reason", "reason"]
METHOD = {
    "space": [
        ("Criterion", "ITU RR Ed. 2024 Vol. 1 Art. 21 No. 21.16, Table 21-4 (Rev.WRC-23), angle-of-arrival dependent, "
                      "incl. footnotes 11 (<= 99 satellites), 18 (EESS active mean pfd), 24/25 (HEO)"),
        ("Criterion", "ITU RR Ed. 2024 Vol. 2 Appendix 5 Annex 1 coordination thresholds (No. 9.14), Table 5-2 NOTE 9 for Finland"),
        ("Scope", "API/A, CR/C, AP30/30A/30B and Art. 11 notifications of Earth-orbiting space stations"),
        ("Geometry", "GSO from orbital longitude; non-GSO at every elevation reachable from Finland (perigee/apogee/inclination); "
                     "evaluated at the Traficom station sites and Finnish border/sea extremes"),
        ("Worst case", "Peak antenna gain toward Finland (Traficom guidance)"),
        ("Alert", "Calculated pfd exceeds the cited limit/threshold (any margin > 0 dB)"),
        ("Priority High", ">= 10 dB, or ITU BR finding X/21.16, or Finnish stations in band and >= 3 dB"),
        ("Priority Medium", ">= 3 dB, or Finnish stations in band, or RR 9.14 coordination required"),
        ("Priority Low", "0-3 dB without Finnish stations in band"),
        ("Concerns", "near limit (6 dB), limit not applicable (> 99 satellites), worst-case inter-satellite beams, "
                     "high pfd in bands without a limit, ITU-unfavourable networks not visible from Finland"),
    ],
    "terrestrial": [
        ("Criterion", "CEPT ECC T/R 25-08 A1.2.3: 20 dB(uV/m) in 25 kHz (+10log(BW/25 kHz) for wider channels)"),
        ("Model", "ITU-R P.1546-6 (ITU-R WP 3K reference implementation Py1546), 10% time, 50% locations, h2 = 10 m, "
                  "land / cold-sea mixed paths, no terrain profile"),
        ("Scope", "Foreign land-mobile assignments (FB, FL, ML), NEW and MODIFIED; within 1000 km of Finland"),
        ("Evaluation points", "Finnish land border and territorial sea (Natural Earth 1:10m); Traficom stations on overlapping frequencies"),
        ("Worst case", "Maximum notified e.r.p. toward Finland; mobile stations at the circle edge nearest Finland"),
        ("Priority High", ">= 6 dB, or a Finnish station above the trigger"),
        ("Priority Medium", "1-6 dB"),
        ("Priority Low", "< 1 dB (within modelling uncertainty)"),
        ("Coordination", "Coordination recorded with Finland in the IFIC is reported, not treated as closed"),
        ("Earlier issues", "Each assignment (administration + reference + station class) is looked up in the earlier "
                           "Terrestrial IFICs processed: NEW, REPUBLISHED (same frequency and site) or MODIFIED"),
    ],
}


def write_workbook(path: Path, kind: str, ific: int, run: dict, alerts: pd.DataFrame, concerns: pd.DataFrame) -> None:
    """Excel submission file (Traficom: xls/csv preferred for quick evaluation)."""
    from openpyxl.styles import Alignment, Font
    first = SPACE_FIRST if kind == "space" else TERRA_FIRST
    order = lambda df: df[[c for c in first if c in df] + [c for c in df.columns if c not in first]]  # noqa: E731
    summary = [("Dataset", f"{'Space' if kind == 'space' else 'Terrestrial'} BR IFIC {ific}"),
               ("Source file", run["source"]), ("Source SHA-256", run["source_sha256"]), ("Processed (UTC)", run["run_utc"]),
               ("Alerts", len(alerts))]
    summary += [(f"  priority {p}", int((alerts.get("priority") == p).sum())) for p in ("High", "Medium", "Low")]
    summary += [(f"  {c}", n) for c, n in alerts["result_class"].value_counts().items()]
    summary += [("Concerns", len(concerns))] + [(f"  {c}", n) for c, n in concerns["result_class"].value_counts().items()]
    summary += [("", ""), ("How to read", "Alerts: calculated exceedance of a cited criterion, sorted by priority. "
                                          "Concerns: screening cases below alert level. Draft letters: letters/ folder.")]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(summary, columns=["Item", "Value"]).to_excel(xw, sheet_name="Summary", index=False)
        order(alerts).to_excel(xw, sheet_name="Alerts", index=False)
        order(concerns).to_excel(xw, sheet_name="Concerns", index=False)
        pd.DataFrame(METHOD[kind], columns=["Topic", "Description"]).to_excel(xw, sheet_name="Method", index=False)
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for col in ws.columns:
                width = max(len(str(c.value or "")) for c in list(col)[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 70)
                if width > 70:
                    for c in col:
                        c.alignment = Alignment(wrap_text=True, vertical="top")


def store(run: dict, alerts: pd.DataFrame, concerns: pd.DataFrame) -> None:
    """Upsert one run and its findings into the cumulative results database."""
    with sqlite3.connect(RESULTS_DB) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS runs (kind TEXT, ific INTEGER, run_utc TEXT, source TEXT,
                source_sha256 TEXT, n_alerts INTEGER, n_concerns INTEGER, seconds REAL, PRIMARY KEY (kind, ific));
            CREATE TABLE IF NOT EXISTS findings (kind TEXT, ific INTEGER, tier TEXT, notice_id TEXT, country TEXT,
                name TEXT, result_class TEXT, margin_db REAL, data TEXT);
            CREATE INDEX IF NOT EXISTS findings_issue ON findings (kind, ific);""")
        con.execute("DELETE FROM findings WHERE kind = ? AND ific = ?", (run["kind"], run["ific"]))
        if "sqlite_path" not in {r[1] for r in con.execute("PRAGMA table_info(runs)")}:
            con.execute("ALTER TABLE runs ADD COLUMN sqlite_path TEXT")
        con.execute("INSERT OR REPLACE INTO runs (kind, ific, run_utc, source, source_sha256, n_alerts, n_concerns, "
                    "seconds, sqlite_path) VALUES (:kind, :ific, :run_utc, :source, :source_sha256, :n_alerts, "
                    ":n_concerns, :seconds, :sqlite_path)", run)
        for tier, frame in (("alert", alerts), ("concern", concerns)):
            margin = "margin_of_exceedance" if run["kind"] == "space" else "margin_db"
            name = "network_name" if run["kind"] == "space" else "site_name"
            for rec in json.loads(frame.to_json(orient="records")):
                con.execute("INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?)",
                            (run["kind"], run["ific"], tier, str(rec.get("notice_id")), rec.get("country"),
                             rec.get(name), rec.get("result_class"), rec.get(margin), json.dumps(rec, ensure_ascii=False)))


def previous_databases(ific: int) -> dict[int, Path]:
    """Databases of earlier Terrestrial IFICs processed before, for the consecutive-issue comparison."""
    if not RESULTS_DB.exists():
        return {}
    with sqlite3.connect(RESULTS_DB) as con:
        if "sqlite_path" not in {r[1] for r in con.execute("PRAGMA table_info(runs)")}:
            return {}
        rows = con.execute("SELECT ific, sqlite_path FROM runs WHERE kind = 'terrestrial' AND ific < ? "
                           "AND sqlite_path IS NOT NULL", (ific,)).fetchall()
    return {n: Path(p) for n, p in rows if Path(p).is_file()}


def already_processed(digest: str) -> bool:
    if not RESULTS_DB.exists():
        return False
    with sqlite3.connect(RESULTS_DB) as con:
        try:
            return con.execute("SELECT 1 FROM runs WHERE source_sha256 = ?", (digest,)).fetchone() is not None
        except sqlite3.OperationalError:
            return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="*", help="IFIC files (.zip/.mdb/.sqlite), Space IFIC numbers to download "
                                                  "from the ITU (e.g. 3080) or https URLs; default: inbox/*")
    parser.add_argument("--refresh-m4", action="store_true", help="Download the current M4 table first")
    parser.add_argument("--force", action="store_true", help="Re-process files already in results.sqlite")
    parser.add_argument("--report-only", action="store_true", help="Only rebuild report.html")
    parser.add_argument("--watch", action="store_true",
                        help="Download and process every IFIC published after the latest processed one")
    parser.add_argument("--every", type=float, default=0,
                        help="With --watch: keep running and check again every N hours")
    parser.add_argument("--min-exceedance-db", type=float, default=0.0)
    parser.add_argument("--exclude-notifications", action="store_true")
    parser.add_argument("--m4", type=Path, default=DEFAULTS["m4"])
    parser.add_argument("--limits", type=Path, default=DEFAULTS["limits"])
    parser.add_argument("--stations", type=Path, default=DEFAULTS["stations"])
    parser.add_argument("--border", type=Path, default=DEFAULTS["border"])
    parser.add_argument("--allow-free-space", action="store_true",
                        help="Run terrestrial screening without Py1546 (free-space upper bound, REVIEW only)")
    parser.add_argument("--report-issues", type=int, default=12, help="Issues per kind kept in report.html")
    return parser.parse_args()


def run_once(cfg: argparse.Namespace) -> None:
    if cfg.refresh_m4:
        try:
            refresh_m4(cfg.m4)
        except (OSError, ValueError, RuntimeError) as exc:  # network/HTTP/JSON problems (URLError is an OSError)
            log(f"M4 refresh failed ({type(exc).__name__}: {exc}); using existing {cfg.m4.name}")

    if not cfg.report_only:
        INBOX.mkdir(exist_ok=True)
        inputs = []
        for spec in cfg.inputs:
            if is_issue_spec(spec):
                for url in resolve_spec(spec):
                    try:
                        if published(url):
                            inputs.append(download(url))
                        else:
                            log(f"{url}: not published (404) - skipped")
                    except (OSError, ValueError) as exc:
                        log(f"{url}: download FAILED: {type(exc).__name__}: {exc}")
            else:
                inputs.append(Path(spec))
        if cfg.watch:
            inputs += watch_once()
        if not cfg.inputs and not cfg.watch:
            inputs = sorted(p for p in INBOX.iterdir() if p.suffix.lower() in
                            (".zip", ".mdb", ".accdb", ".sqlite", ".db"))
        if not inputs:
            log(f"No input files (put new IFIC downloads in {INBOX.relative_to(ROOT)}/)")
        failures = 0
        for source in inputs:
            log(f"{source.name}")
            try:
                digest = sha256(source)
                if not cfg.force and already_processed(digest):
                    log("  already processed (same SHA-256) - skipped; use --force to redo")
                    continue
                db = to_sqlite(source, digest)
                kind, ific = detect(db)
                log(f"  detected {kind} IFIC {ific}")
                process(db, digest, source, kind, ific, cfg)
            except Exception as exc:  # one bad input must not stop the others; keep the traceback
                failures += 1
                log(f"  FAILED: {type(exc).__name__}: {exc}")
                log("  " + traceback.format_exc().strip().replace("\n", "\n  "))
        if failures:
            log(f"{failures} input(s) failed")

    import build_report
    build_report.build(ROOT / "report.html", max_issues=cfg.report_issues)


def main() -> None:
    cfg = parse_args()
    run_once(cfg)
    while cfg.watch and cfg.every > 0:
        log(f"next check in {cfg.every:g} h")
        time.sleep(cfg.every * 3600)
        run_once(cfg)


if __name__ == "__main__":
    main()
