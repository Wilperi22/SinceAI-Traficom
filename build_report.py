#!/usr/bin/env python3
"""Build report.html: a single self-contained, offline review page for the IFIC screening.

Reads every processed issue (results.sqlite + runs/<kind>_<ific>/: alerts, concerns, audit,
letters) and the test-suite result, and embeds everything into one HTML file that opens in
any browser without a server. Normally called by pipeline.py.

    .venv/bin/python build_report.py            # runs the tests, writes report.html
    .venv/bin/python build_report.py --no-tests
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent

ADMINISTRATIONS = {
    "CHN": "China", "USA": "United States", "F": "France", "E": "Spain", "KAZ": "Kazakhstan", "IND": "India",
    "KOR": "Korea (Rep. of)", "PHL": "Philippines", "ISR": "Israel", "INS": "Indonesia", "G": "United Kingdom",
    "D": "Germany", "J": "Japan", "RUS": "Russian Federation", "LUX": "Luxembourg", "NOR": "Norway", "I": "Italy",
    "POL": "Poland", "S": "Sweden", "QAT": "Qatar", "SVN": "Slovenia", "TUR": "Türkiye",
    "ARS": "Saudi Arabia",
    "CAN": "Canada",
    "PAK": "Pakistan",
    "UAE": "United Arab Emirates",
    "VTN": "Viet Nam",
    "POR": "Portugal",
    "AUS": "Australia",
    "B": "Brazil",
    "HOL": "Netherlands",
    "NZL": "New Zealand",
    "ARG": "Argentine Republic",
    "MEX": "Mexico",
    "EGY": "Egypt",
    "UKR": "Ukraine",
    "IRN": "Iran",
    "THA": "Thailand",
    "MLA": "Malaysia",
    "SNG": "Singapore",
    "BEL": "Belgium",
    "SUI": "Switzerland",
    "AUT": "Austria",
    "DNK": "Denmark",
    "EST": "Estonia",
    "LTU": "Lithuania",
    "LVA": "Latvia",
    "GRC": "Greece",
    "NIG": "Nigeria",
    "ALG": "Algeria",
    "MRC": "Morocco",
    "AZE": "Azerbaijan",
    "BLR": "Belarus",
    "BGD": "Bangladesh",
    "MNG": "Mongolia",
    "CYP": "Cyprus",
    "PNG": "Papua New Guinea",
    "TON": "Tonga",
    "BOL": "Bolivia",
    "PRU": "Peru",
    "CHL": "Chile",
    "COL": "Colombia",
    "VEN": "Venezuela",
    "ISL": "Iceland",
    "IRL": "Ireland",
    "CZE": "Czech Republic",
    "HNG": "Hungary",
    "ROU": "Romania",
    "BUL": "Bulgaria",
    "SRB": "Serbia",
    "HRV": "Croatia",
    "SVK": "Slovak Republic",
    "FIN": "Finland",
    "ETH": "Ethiopia",
    "KEN": "Kenya",
    "AFS": "South Africa",
    "OMA": "Oman",
    "KWT": "Kuwait",
    "BHR": "Bahrain",
    "JOR": "Jordan",
    "UZB": "Uzbekistan",
    "TKM": "Turkmenistan",
    "ARM": "Armenia",
    "GEO": "Georgia",
    "MDA": "Moldova",
    "PNR": "Panama",
    "BRB": "Barbados",
    "LAO": "Lao People's Democratic Republic",
    "KRE": "Democratic People's Republic of Korea",
    "CBG": "Cambodia",
    "NPL": "Nepal",
    "AGL": "Angola",
    "ZWE": "Zimbabwe",
    "TUN": "Tunisia",
    "LBY": "Libya",
}


def records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    frame = pd.read_csv(path, dtype={"notice_id": str, "related_notice_id": str, "adm_ref_id": str})
    return json.loads(frame.to_json(orient="records"))


def letters(directory: Path) -> dict[str, str]:
    out = {}
    for path in sorted(directory.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        if path.name.startswith("T_"):
            out["T:" + path.stem.split("_")[2]] = text
        else:
            out["S:" + path.stem.split("_")[1]] = text
    return out


def run_tests() -> dict:
    proc = subprocess.run([sys.executable, "-m", "unittest", "tests.test_pipeline"], cwd=ROOT,
                          capture_output=True, text=True)
    tail = proc.stderr.strip().splitlines()
    ran = next((l for l in tail if l.startswith("Ran ")), "")
    return {"ok": proc.returncode == 0, "summary": f"{ran} - {'OK' if proc.returncode == 0 else tail[-1]}",
            "names": re.findall(r"^(test_\w+)", proc.stderr, flags=re.M)}


def load_runs(max_issues: int) -> tuple[dict, dict, list]:
    """Latest max_issues runs per kind from results.sqlite + runs/<kind>_<ific>/."""
    db = ROOT / "results.sqlite"
    if not db.exists():
        raise SystemExit("results.sqlite not found - run pipeline.py first")
    with sqlite3.connect(db) as con:
        meta = pd.read_sql_query("SELECT * FROM runs ORDER BY ific DESC", con)
    issues, runs = {"space": [], "terra": []}, {"space": {}, "terra": {}}
    for row in meta.itertuples():
        key = "space" if row.kind == "space" else "terra"
        directory = ROOT / "runs" / f"{row.kind}_{row.ific}"
        if len(issues[key]) >= max_issues or not directory.exists():
            continue
        issues[key].append(int(row.ific))
        audit = (directory / "audit.txt").read_text(encoding="utf-8") if (directory / "audit.txt").exists() else ""
        runs[key][int(row.ific)] = {
            "alerts": records(directory / "alerts.csv"), "concerns": records(directory / "concerns.csv"),
            "letters": letters(directory / "letters"), "audit": audit,
            "inputs": re.findall(r"^Input (.+?): sha256 ([0-9a-f]{64}) \((\d+) bytes\)", audit, flags=re.M),
            "meta": {"run_utc": row.run_utc, "source": row.source, "sha256": row.source_sha256,
                     "seconds": row.seconds},
        }
    history = [{"kind": "space" if r.kind == "space" else "terra", "ific": int(r.ific), "alerts": int(r.n_alerts),
                "concerns": int(r.n_concerns)} for r in meta.sort_values("ific").itertuples()]
    return issues, runs, history


def build(output: Path, max_issues: int = 12, tests: dict | None = None) -> None:
    issues, runs, history = load_runs(max_issues)
    data = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "issues": issues, "runs": runs,
            "history": history, "tests": tests or {"ok": None, "summary": "not run", "names": []},
            "adm": ADMINISTRATIONS}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    output.write_text(TEMPLATE.replace("/*__DATA__*/", payload), encoding="utf-8")
    print(f"Wrote {output} ({output.stat().st_size // 1024} KB): space issues {issues['space']}, "
          f"terrestrial issues {issues['terra']}; tests: {data['tests']['summary']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / "report.html")
    parser.add_argument("--issues", type=int, default=12, help="Issues per kind to include")
    parser.add_argument("--no-tests", action="store_true")
    args = parser.parse_args()
    build(args.output, args.issues, None if args.no_tests else run_tests())


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IFIC Interference Screening - Finland</title>
<style>
:root{
  --bg:#f4f3f0; --surface:#fcfcfb; --surface-2:#f0efec; --line:#e2e1dc;
  --ink:#0b0b0b; --ink-2:#52514e; --ink-3:#77766f; --accent:#2a78d6; --accent-ink:#1c5cab;
  --critical:#d03b3b; --serious:#ec835a; --warning:#fab219; --good:#0ca30c; --neutral:#a8a79f;
  --shadow:0 1px 2px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.06);
  color-scheme: light;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#121211; --surface:#1a1a19; --surface-2:#242422; --line:#33332f;
    --ink:#ffffff; --ink-2:#c3c2b7; --ink-3:#97968c; --accent:#3987e5; --accent-ink:#86b6ef;
    --neutral:#6c6b64; --shadow:0 1px 2px rgba(0,0,0,.4); color-scheme: dark; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{background:var(--surface);border-bottom:1px solid var(--line);padding:18px 24px 0}
.wrap{max-width:1320px;margin:0 auto}
h1{font-size:20px;margin:0 0 2px} h2{font-size:16px;margin:24px 0 10px} h3{font-size:13px;margin:18px 0 6px;color:var(--ink-2);text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--ink-2);font-size:13px}
.draft{display:inline-block;margin-left:8px;padding:1px 8px;border-radius:10px;background:var(--surface-2);color:var(--ink-2);font-size:11px;vertical-align:middle}
nav{display:flex;gap:2px;margin-top:14px;flex-wrap:wrap}
nav button{border:0;background:none;color:var(--ink-2);padding:10px 14px;font:inherit;cursor:pointer;border-bottom:2px solid transparent}
nav button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--accent);font-weight:600}
main{padding:20px 24px 60px}
section[hidden]{display:none!important}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;box-shadow:var(--shadow);cursor:pointer}
.tile .k{color:var(--ink-2);font-size:12px} .tile .v{font-size:30px;font-weight:650;line-height:1.2;font-variant-numeric:tabular-nums}
.tile .d{color:var(--ink-3);font-size:12px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px;box-shadow:var(--shadow)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px} @media(max-width:900px){.grid2{grid-template-columns:1fr}}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px 0 12px}
input[type=search],select{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
input[type=search]{min-width:240px;flex:1 1 240px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{font:inherit;border:0;background:var(--surface);color:var(--ink-2);padding:7px 12px;cursor:pointer}
.seg button[aria-pressed=true]{background:var(--accent);color:#fff}
.tablewrap{overflow:auto;max-height:calc(100vh - 230px);min-height:240px;background:var(--surface);border:1px solid var(--line);border-radius:10px;
  scrollbar-width:auto;scrollbar-color:var(--ink-3) var(--surface-2);overscroll-behavior-x:contain}
.tablewrap::-webkit-scrollbar{height:14px;width:12px}
.tablewrap::-webkit-scrollbar-track{background:var(--surface-2)}
.tablewrap::-webkit-scrollbar-thumb{background:var(--ink-3);border-radius:7px;border:3px solid var(--surface-2)}
.tablewrap::-webkit-scrollbar-thumb:hover{background:var(--ink-2)}
.tablewrap td{background:var(--surface)}
.tablewrap th:nth-child(1),.tablewrap td:nth-child(1){position:sticky;left:0;z-index:1;width:44px;min-width:44px;max-width:44px}
.tablewrap th:nth-child(2),.tablewrap td:nth-child(2){position:sticky;left:44px;z-index:1;box-shadow:inset -1px 0 0 var(--line)}
.tablewrap th{z-index:2} .tablewrap th:nth-child(1),.tablewrap th:nth-child(2){z-index:3}
.tablewrap tbody tr:hover td,.tablewrap tbody tr.sel td{background:var(--surface-2)}
.slide{display:inline-flex;gap:4px} .slide button{font:inherit;border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:8px;padding:6px 10px;cursor:pointer}
.issuesel{display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin-top:8px;font-size:13px;color:var(--ink-2)}
.issuesel select{padding:4px 8px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:12px;color:var(--ink-2);font-weight:600;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--surface)}
th.num,td.num{text-align:right}
tbody tr{cursor:pointer} tbody tr.sel td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
td.name{font-weight:600;white-space:normal;min-width:140px}
td,th{padding:8px 8px}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink);white-space:nowrap}
.badge i{width:10px;height:10px;border-radius:2px;display:inline-block;flex:none}
.mbar{display:flex;align-items:center;gap:8px;justify-content:flex-end}
.mbar span.b{height:6px;border-radius:0 3px 3px 0;background:var(--accent);display:inline-block}
.mbar span.b.neg{background:var(--neutral);border-radius:3px 0 0 3px}
.empty{padding:28px;text-align:center;color:var(--ink-2)}
/* drawer */
#drawer{position:fixed;top:0;right:0;height:100%;width:min(720px,100%);background:var(--surface);border-left:1px solid var(--line);
  transform:translateX(100%);visibility:hidden;transition:transform .18s ease,visibility 0s .18s;z-index:20;display:flex;flex-direction:column}
#drawer.open{transform:none;visibility:visible;box-shadow:-8px 0 24px rgba(0,0,0,.15);transition:transform .18s ease}
#drawer .head{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:flex-start}
#drawer .head h2{margin:0;font-size:18px} #drawer .body{padding:4px 20px 30px;overflow:auto}
#close{margin-left:auto;font:inherit;font-size:20px;line-height:1;border:0;background:none;color:var(--ink-2);cursor:pointer;padding:4px 8px}
.why{background:var(--surface-2);border-radius:8px;padding:12px 14px;margin-top:12px}
.why .big{font-size:24px;font-weight:650}
dl.kv{display:grid;grid-template-columns:200px 1fr;gap:4px 14px;margin:0} dl.kv dt{color:var(--ink-2)} dl.kv dd{margin:0;word-break:break-word}
@media(max-width:600px){dl.kv{grid-template-columns:1fr} dl.kv dt{margin-top:6px}}
pre.letter{white-space:pre-wrap;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:14px;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0}
.btns{display:flex;gap:8px;margin:8px 0}
.btn{font:inherit;border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:8px;padding:6px 12px;cursor:pointer}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.chips{display:flex;flex-wrap:wrap;gap:4px} .chip{background:var(--surface-2);border-radius:10px;padding:1px 8px;font-size:12px}
.note{color:var(--ink-2);font-size:13px}
.callout{border-left:3px solid var(--warning);background:var(--surface-2);padding:8px 12px;border-radius:0 8px 8px 0;margin:10px 0}
/* chart */
.bars{display:grid;grid-template-columns:150px 1fr;gap:6px 10px;align-items:center}
.bars .lab{color:var(--ink-2);font-size:13px;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bars .row{display:flex;align-items:center;gap:6px;position:relative}
.bars .seg2{height:14px} .bars .seg2.s{background:var(--accent)} .bars .seg2.t{background:var(--accent-ink)}
.bars .stack{display:flex;gap:2px} .bars .stack .seg2:last-child{border-radius:0 4px 4px 0}
.bars .val{font-size:12px;color:var(--ink-2)}
.legend{display:flex;gap:14px;font-size:12px;color:var(--ink-2);margin:6px 0 10px} .legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);padding:6px 9px;border-radius:6px;font-size:12px;z-index:30;display:none;max-width:280px}
ol.steps{padding-left:20px} ol.steps li{margin:4px 0}
table.plain td,table.plain th{white-space:normal;cursor:auto}
table.plain tbody tr{cursor:auto} table.plain tbody tr:hover{background:none}
code{font:12px ui-monospace,Menlo,monospace;background:var(--surface-2);padding:1px 4px;border-radius:4px}
.hash{font:11px ui-monospace,Menlo,monospace;color:var(--ink-2);word-break:break-all}
</style>
</head>
<body>
<header><div class="wrap">
  <h1>IFIC Interference Screening for Finland <span class="draft">Draft for expert review</span></h1>
  <div class="sub" id="subtitle"></div>
  <div class="issuesel"><label>Space IFIC <select id="selS"></select></label><label>Terrestrial IFIC <select id="selT"></select></label><span id="runinfo"></span></div>
  <nav role="tablist">
    <button role="tab" data-tab="overview" aria-selected="true">Overview</button>
    <button role="tab" data-tab="space">Space IFIC <span class="sifc"></span></button>
    <button role="tab" data-tab="terra">Terrestrial IFIC <span class="tifc"></span></button>
    <button role="tab" data-tab="method">Method &amp; criteria</button>
    <button role="tab" data-tab="sources">Data &amp; audit trail</button>
  </nav>
</div></header>

<main class="wrap">
<section id="tab-overview">
  <div class="tiles" id="tiles"></div>
  <div class="grid2" style="margin-top:16px">
    <div class="card">
      <h3 style="margin-top:0">Alerts by administration</h3>
      <div class="legend"><span><i style="background:var(--accent)"></i>Space</span><span><i style="background:var(--accent-ink)"></i>Terrestrial</span></div>
      <div class="bars" id="admchart"></div>
    </div>
    <div class="card">
      <h3 style="margin-top:0">Top findings (largest exceedance)</h3>
      <div id="top"></div>
    </div>
  </div>
  <div class="card" style="margin-top:16px" id="historycard">
    <h3 style="margin-top:0">Alerts per IFIC issue (all processed issues)</h3>
    <div class="legend"><span><i style="background:var(--accent)"></i>Space</span><span><i style="background:var(--accent-ink)"></i>Terrestrial</span></div>
    <div class="bars" id="history"></div>
  </div>
  <div class="card" style="margin-top:16px">
    <h3 style="margin-top:0">How to read this report</h3>
    <ul class="note" style="margin:0;padding-left:18px">
      <li><b>Alerts</b> are cases where a calculated value exceeds a cited regulatory limit: <span class="badge"><i style="background:var(--critical)"></i>Exceeds limit</span> (RR Table 21-4 hard limit, or T/R 25-08 trigger for terrestrial) or <span class="badge"><i style="background:var(--serious)"></i>Coordination required</span> (RR No. 9.14, Appendix 5 threshold).</li>
      <li><b>Concerns</b> are documented screening cases below alert level (near the limit, limit not applicable, worst-case inter-satellite beams, high pfd in bands without a limit). Margins are adjustable on the command line.</li>
      <li>Click any row to see <b>why</b> it was flagged, the figures, the regulation, affected Finnish services and stations, and the <b>recommended letter</b> to the administration.</li>
      <li><b>Priority</b> orders alerts for review: <span style="color:var(--critical)">▲</span> High (space: ≥10 dB, ITU BR finding X/21.16, or Finnish stations in band and ≥3 dB; terrestrial: ≥6 dB or a Finnish station above the trigger), <span style="color:var(--serious)">●</span> Medium, <span style="color:var(--ink-3)">○</span> Low (small margins, within modelling uncertainty for terrestrial &lt;1 dB).</li>
      <li>All calculations are worst case as instructed by Traficom (peak gain toward Finland; maximum e.r.p. toward the border).</li>
    </ul>
  </div>
</section>

<section id="tab-space" hidden>
  <div class="toolbar">
    <div class="seg" data-for="space"><button aria-pressed="true" data-v="alerts">Alerts</button><button aria-pressed="false" data-v="concerns">Concerns</button><button aria-pressed="false" data-v="all">All</button></div>
    <input type="search" placeholder="Search network, notice, administration, band..." data-search="space">
    <select data-adm="space"><option value="">All administrations</option></select>
    <select data-cls="space"><option value="">All result classes</option></select>
    <select data-prio="space"><option value="">All priorities</option><option>High</option><option>Medium</option><option>Low</option></select>
    <span class="slide"><button data-slide="space" data-d="-1" title="Scroll table left">&#9664;</button><button data-slide="space" data-d="1" title="Scroll table right">&#9654;</button></span>
    <span class="note" data-count="space"></span>
  </div>
  <div class="tablewrap"><table id="tspace"></table></div>
</section>

<section id="tab-terra" hidden>
  <div class="toolbar">
    <div class="seg" data-for="terra"><button aria-pressed="true" data-v="alerts">Alerts</button><button aria-pressed="false" data-v="concerns">Concerns</button><button aria-pressed="false" data-v="all">All</button></div>
    <input type="search" placeholder="Search site, notice, administration..." data-search="terra">
    <select data-adm="terra"><option value="">All administrations</option></select>
    <select data-cls="terra"><option value="">All result classes</option></select>
    <select data-prio="terra"><option value="">All priorities</option><option>High</option><option>Medium</option><option>Low</option></select>
    <span class="slide"><button data-slide="terra" data-d="-1" title="Scroll table left">&#9664;</button><button data-slide="terra" data-d="1" title="Scroll table right">&#9654;</button></span>
    <span class="note" data-count="terra"></span>
  </div>
  <div class="tablewrap"><table id="tterra"></table></div>
  <div class="card" style="margin-top:16px"><h3 style="margin-top:0">Trends in neighbouring administrations (side quest)</h3><pre class="letter" id="trends"></pre></div>
</section>

<section id="tab-method" hidden>
  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">Pipeline</h3>
      <ol class="steps">
        <li><b>Ingest</b> the BR IFIC databases (MDB converted losslessly to SQLite), the M4 frequency allocation table (Traficom open-data API) and the Traficom list of Finnish stations of special interest.</li>
        <li><b>Scope</b>: Space - API/A, CR/C, AP30/30A/30B and Article 11 notifications of space stations orbiting the Earth. Terrestrial - foreign land-mobile assignments (classes FB, FL, ML; NEW and MODIFIED).</li>
        <li><b>Geometry from Finland</b>: GSO satellites from their orbital longitude, non-GSO from perigee/apogee/inclination (every reachable elevation); terrestrial paths to the Finnish land border and territorial sea with land/sea segmentation.</li>
        <li><b>Calculate</b>: space pfd = e.i.r.p. in the reference bandwidth - 10log(4&pi;d&sup2;); terrestrial field strength with ITU-R P.1546-6 (ITU-R WP 3K reference implementation).</li>
        <li><b>Compare</b> with the cited criterion at the actual angle of arrival / point, classify as alert or concern, one row per network or assignment.</li>
        <li><b>Explain</b>: reason text, regulation citation, Finnish services (M4) and stations (Excel) affected, draft letter to the administration, audit trail with input hashes.</li>
      </ol>
    </div>
    <div class="card">
      <h3 style="margin-top:0">Criteria applied</h3>
      <table class="plain"><thead><tr><th>Scan</th><th>Criterion</th><th>Result</th></tr></thead><tbody>
        <tr><td>Space</td><td>ITU RR Ed. 2024 Vol. 1 Art. 21 No. 21.16, <b>Table 21-4</b> (Rev.WRC-23), angle-dependent, incl. footnotes 11 (&le;99 satellites), 18 (EESS active mean pfd), 24/25 (HEO rows)</td><td>Exceeds limit</td></tr>
        <tr><td>Space</td><td>ITU RR Ed. 2024 Vol. 2 <b>Appendix 5</b> Annex 1 (No. 9.14) coordination thresholds, Table 5-2 NOTE 9 values for Finland</td><td>Coordination required</td></tr>
        <tr><td>Terrestrial</td><td>CEPT <b>T/R 25-08</b> A1.2.3: 20 dB(&micro;V/m) in 25 kHz (+10log(BW/25 kHz) for wider channels), 10% time, 50% locations, 10 m receiver</td><td>Exceeds trigger</td></tr>
      </tbody></table>
      <h3>Traficom guidance applied (SinceAI Discord)</h3>
      <ul class="note" style="padding-left:18px;margin:0">
        <li>Worst case always: satellite peak gain toward Finland; protected = RX and TX frequencies of Finnish stations, whole territory.</li>
        <li>Any calculated exceedance is flagged; near-limit cases are concerns with documented, adjustable margins.</li>
        <li>Terrestrial: land-mobile classes only, T/R 25-08 A1.2.3 (also above 470 MHz), P.1546-6 without the 10 dB height correction, land border and territorial sea; coordination recorded with Finland is reported, not treated as closed.</li>
        <li>Excel: smaller of channel/transmission bandwidth; missing licence class = MUUT.</li>
      </ul>
    </div>
  </div>
  <div class="card" style="margin-top:16px">
    <h3 style="margin-top:0">Assumptions and known limitations</h3>
    <ul class="note" style="padding-left:18px;margin:0">
      <li>Where Table 21-4 depends on constellation data not in the IFIC (functions X, Y), the less strict value is used - this can only under-report.</li>
      <li>Inconsistent power data (integrated pwr_ds_max above pep_max) is bounded by pep_max over the emission bandwidth and labelled in the power source.</li>
      <li>Terrestrial boundaries from Natural Earth 1:10m (public domain); territorial sea measured from the coastline, not the official baselines. Replace with the official dataset via <code>terrestrial.py --border-file</code>.</li>
      <li>P.1546 without terrain profile; large lakes treated as land. Station class E5 (12 space notices) is not mapped to a service. Finnish satellite networks (ITU Space Explorer) are not in the IFIC data; the BR affected-network list for this issue names no Finnish network.</li>
    </ul>
  </div>
</section>

<section id="tab-sources" hidden>
  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">Input data (SHA-256)</h3>
      <table class="plain"><thead><tr><th>File</th><th>Hash / size</th></tr></thead><tbody id="inputs"></tbody></table>
      <h3>Verification</h3>
      <div id="tests" class="note"></div>
    </div>
    <div class="card">
      <h3 style="margin-top:0">Reproduce</h3>
<pre class="letter">.venv/bin/python inspection_engine.py      # Space IFIC 3074
.venv/bin/python terrestrial.py           # Terrestrial IFIC 3076
.venv/bin/python letters.py               # draft letters
.venv/bin/python -m unittest tests.test_pipeline
.venv/bin/python build_report.py          # this page</pre>
      <h3>Output files</h3>
      <p class="note">Per issue in runs/&lt;kind&gt;_&lt;ific&gt;/: <b>&lt;kind&gt;_&lt;ific&gt;_results.xlsx</b> (Summary, Alerts, Concerns, Method), alerts.csv/.json, concerns.csv/.json, audit.txt, letters/*.txt. All issues: results.sqlite.</p>
    </div>
  </div>
  <div class="card" style="margin-top:16px"><h3 style="margin-top:0">Space audit trail</h3><pre class="letter" id="auditS"></pre></div>
  <div class="card" style="margin-top:16px"><h3 style="margin-top:0">Terrestrial audit trail</h3><pre class="letter" id="auditT"></pre></div>
</section>
</main>

<aside id="drawer" aria-hidden="true"><div class="head"><div id="dhead"></div><button id="close" aria-label="Close">&times;</button></div><div class="body" id="dbody"></div></aside>
<div id="tip"></div>

<script>
const D = /*__DATA__*/;
const $ = (s, el=document) => el.querySelector(s), $$ = (s, el=document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const num = (v, d=1) => (v === null || v === undefined || v === "" || isNaN(v)) ? "" : Number(v).toFixed(d);
const sgn = (v, d=1) => (v === null || v === undefined || isNaN(v)) ? "" : (v >= 0 ? "+" : "") + num(v, d);
const admName = c => D.adm[c] ? `${D.adm[c]} (${c})` : c;

const CLASS = {
  "EXCEEDS LIMIT": ["var(--critical)", "Exceeds limit"],
  "COORDINATION REQUIRED": ["var(--serious)", "Coordination required"],
  "SUB_QA_EXCEEDANCE": ["var(--warning)", "Small exceedance"],
  "LIMIT_NOT_APPLICABLE": ["var(--warning)", "Limit not applicable (>99 sats)"],
  "WORST_CASE_ISS": ["var(--warning)", "Worst case: inter-satellite beam"],
  "NO_LIMIT_HIGH_PFD": ["var(--warning)", "High pfd, no limit in band"],
  "BR_UNFAVOURABLE_21_16": ["var(--warning)", "ITU BR found 21.16 exceeded"],
  "NEAR_LIMIT": ["var(--neutral)", "Near limit"],
  "REVIEW:P1546_UNAVAILABLE": ["var(--neutral)", "Review"],
};
const PRIO = {High: ["▲", "var(--critical)"], Medium: ["●", "var(--serious)"], Low: ["○", "var(--ink-3)"]};
const prioRank = r => ({High: 0, Medium: 1, Low: 2}[r.priority] ?? 3);
const prioBadge = p => p ? `<span class="badge"><span style="color:${PRIO[p][1]}">${PRIO[p][0]}</span>${esc(p)}</span>` : `<span class="note">-</span>`;
const badge = c => { const [col, lab] = CLASS[c] || ["var(--neutral)", c]; return `<span class="badge"><i style="background:${col}"></i>${esc(lab)}</span>`; };

// ---------- issue selection (?space=3074&terra=3076, default latest) ----------
const P = new URLSearchParams(location.search);
const pick = k => { const want = +P.get(k); return D.issues[k].includes(want) ? want : D.issues[k][0]; };
D.spaceIfic = pick("space"); D.terraIfic = pick("terra");
const EMPTY = {alerts:[], concerns:[], letters:{}, audit:"", inputs:[], meta:{}};
const RS = D.runs.space[D.spaceIfic] || EMPTY, RT = D.runs.terra[D.terraIfic] || EMPTY;
D.space = {alerts: RS.alerts, concerns: RS.concerns}; D.terra = {alerts: RT.alerts, concerns: RT.concerns};
D.letters = {...RS.letters, ...RT.letters}; D.audit = {space: RS.audit, terra: RT.audit}; D.inputs = [...RS.inputs, ...RT.inputs];
[["selS", "space"], ["selT", "terra"]].forEach(([id, k]) => {
  const sel = $("#" + id);
  sel.innerHTML = D.issues[k].map(i => `<option value="${i}">${i}${i === D.issues[k][0] ? " (latest)" : ""}</option>`).join("") || "<option>none processed</option>";
  sel.value = k === "space" ? D.spaceIfic : D.terraIfic;
  sel.onchange = () => { P.set(k, sel.value); location.search = P.toString(); };
});
$("#runinfo").textContent = [RS.meta.source && `space source ${RS.meta.source}`, RT.meta.source && `terrestrial source ${RT.meta.source}`].filter(Boolean).join(" · ");

// ---------- rows ----------
D.space.alerts.forEach(r => r._k = "alert"); D.space.concerns.forEach(r => r._k = "concern");
D.terra.alerts.forEach(r => r._k = "alert"); D.terra.concerns.forEach(r => r._k = "concern");
const SPACE = [...D.space.alerts, ...D.space.concerns], TERRA = [...D.terra.alerts, ...D.terra.concerns];

// ---------- header / tabs ----------
$("#subtitle").textContent = `Space IFIC ${D.spaceIfic} and Terrestrial IFIC ${D.terraIfic} screened against Finnish frequency use · generated ${D.generated}`;
$(".sifc").textContent = D.spaceIfic; $(".tifc").textContent = D.terraIfic;
function showTab(name, keepHash){ if (!keepHash) history.replaceState(null, "", "#" + name); $$("nav button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === name)); $$("main > section").forEach(s => s.hidden = s.id !== "tab-" + name); closeDrawer(); }
$$("nav button").forEach(b => b.onclick = () => showTab(b.dataset.tab));

// ---------- overview ----------
const count = (rows, c) => rows.filter(r => r.result_class === c).length;
const tiles = [
  {k:`Space ${D.spaceIfic} alerts`, v:D.space.alerts.length, d:`${D.space.alerts.filter(r => r.priority === "High").length} high priority · ${count(D.space.alerts,"EXCEEDS LIMIT")} exceed Table 21-4 · ${count(D.space.alerts,"COORDINATION REQUIRED")} need coordination (RR 9.14)`, go:["space","alerts"]},
  {k:`Space ${D.spaceIfic} concerns`, v:D.space.concerns.length, d:"near limit, limit not applicable, worst-case", go:["space","concerns"]},
  {k:`Terrestrial ${D.terraIfic} alerts`, v:D.terra.alerts.length, d:`${D.terra.alerts.filter(r => r.priority === "High").length} high priority · exceed T/R 25-08 trigger at the Finnish border`, go:["terra","alerts"]},
  {k:`Terrestrial ${D.terraIfic} concerns`, v:D.terra.concerns.length, d:"within the near-limit margin", go:["terra","concerns"]},
];
$("#tiles").innerHTML = tiles.map((t,i) => `<div class="tile" data-i="${i}" role="button" tabindex="0"><div class="k">${esc(t.k)}</div><div class="v">${t.v}</div><div class="d">${esc(t.d)}</div></div>`).join("");
$$(".tile").forEach(el => el.onclick = () => { const [tab, mode] = tiles[el.dataset.i].go; showTab(tab); setMode(tab, mode); });

const adm = {};
D.space.alerts.forEach(r => (adm[r.country] ??= {s:0,t:0}).s++);
D.terra.alerts.forEach(r => (adm[r.country] ??= {s:0,t:0}).t++);
const admRows = Object.entries(adm).sort((a,b) => (b[1].s+b[1].t) - (a[1].s+a[1].t));
const maxA = Math.max(1, ...admRows.map(([,v]) => v.s+v.t));
$("#admchart").innerHTML = admRows.map(([c,v]) => `<div class="lab" title="${esc(admName(c))}">${esc(D.adm[c] || c)}</div>
  <div class="row" data-tip="${esc(admName(c))}: ${v.s} space, ${v.t} terrestrial alert(s)">
   <span class="stack" style="width:${(v.s+v.t)/maxA*85}%">${v.s ? `<span class="seg2 s" style="flex:${v.s}"></span>` : ""}${v.t ? `<span class="seg2 t" style="flex:${v.t}"></span>` : ""}</span>
   <span class="val">${v.s+v.t}</span></div>`).join("");
$$("#admchart .row").forEach(el => { el.onmousemove = e => tip(e, el.dataset.tip); el.onmouseleave = () => tip(); el.style.cursor = "pointer";
  el.onclick = () => { const c = admRows[$$("#admchart .row").indexOf(el)][0]; const tab = adm[c].s ? "space" : "terra"; showTab(tab); $(`[data-adm=${tab}]`).value = c; setMode(tab, "alerts"); }; });

const topRows = [...D.space.alerts.map(r => ({r, k:"space", m:r.margin_of_exceedance, name:r.network_name})),
             ...D.terra.alerts.map(r => ({r, k:"terra", m:r.margin_db, name:r.site_name}))].sort((a,b) => prioRank(a.r) - prioRank(b.r) || b.m-a.m).slice(0,8);
$("#top").innerHTML = `<table class="plain"><tbody>${topRows.map((x,i) => `<tr data-i="${i}" style="cursor:pointer"><td><b>${esc(x.name)}</b><div class="note">${esc(admName(x.r.country))} · ${x.k === "space" ? "Space" : "Terrestrial"} · ${esc(x.r.frequency_mhz)} MHz</div></td><td>${badge(x.r.result_class)}</td><td class="num"><b>${sgn(x.m)} dB</b></td></tr>`).join("")}</tbody></table>`;
$$("#top tr").forEach(el => el.onclick = () => { const x = topRows[el.dataset.i]; openDetail(x.k, x.r); });

// ---------- history ----------
const maxH = Math.max(1, ...D.history.map(h => h.alerts));
$("#history").innerHTML = D.history.map(h => `<div class="lab">${h.kind === "space" ? "Space" : "Terrestrial"} ${h.ific}</div>
  <div class="row" data-tip="${h.kind === "space" ? "Space" : "Terrestrial"} IFIC ${h.ific}: ${h.alerts} alerts, ${h.concerns} concerns">
   <span class="stack" style="width:${h.alerts/maxH*85}%"><span class="seg2 ${h.kind === "space" ? "s" : "t"}" style="flex:1"></span></span><span class="val">${h.alerts}</span></div>`).join("");
$$("#history .row").forEach((el, i) => { el.onmousemove = e => tip(e, el.dataset.tip); el.onmouseleave = () => tip(); el.style.cursor = "pointer";
  el.onclick = () => { const h = D.history[i]; P.set(h.kind, h.ific); location.search = P.toString(); }; });
$$("[data-slide]").forEach(b => b.onclick = () => $(`#t${b.dataset.slide}`).parentElement.scrollBy({left: +b.dataset.d * 400, behavior: "smooth"}));

// ---------- tables ----------
const COLS = {
  space: [
    ["#", r => r._rank, "num"], ["Network", r => r.network_name, "name"], ["Priority", r => r.priority, "prio", prioRank], ["Admin.", r => r.country], ["Notice", r => r.notice_id],
    ["Type", r => r.notice_type], ["Orbit", r => r.orbit === "GSO" ? `GSO ${num(r.gso_longitude)}°` : `NGSO ${num(r.altitude_km,0)} km`],
    ["Band (MHz)", r => r.frequency_mhz], ["Service", r => r.service], ["Result", r => r.result_class, "badge"],
    ["Margin (dB)", r => r.margin_of_exceedance, "margin"], ["FI stations", r => (r.finnish_station_id||"").split(";").filter(Boolean).length, "num"],
  ],
  terra: [
    ["#", r => r._rank, "num"], ["Site", r => r.site_name, "name"], ["Priority", r => r.priority, "prio", prioRank], ["Admin.", r => r.country], ["Notice", r => r.notice_id],
    ["Class", r => r.station_class], ["Change", r => r.change_type], ["Freq (MHz)", r => r.frequency_mhz, "num"],
    ["Distance (km)", r => r.distance_km, "num"], ["Field (dBµV/m)", r => r.field_strength_dbuv_m, "num1"], ["Result", r => r.result_class, "badge"],
    ["Margin (dB)", r => r.margin_db, "margin"], ["Coordinated w/ FIN", r => r.coordination_with_fin ? "recorded" : ""],
    ["vs earlier issues", r => r.change_vs_previous_issues || ""],
  ],
};
const STATE = {space:{mode:"alerts", sort:null, dir:1}, terra:{mode:"alerts", sort:null, dir:1}};
const rowsOf = k => k === "space" ? SPACE : TERRA;
const marginOf = (k, r) => k === "space" ? r.margin_of_exceedance : r.margin_db;
[["space", SPACE], ["terra", TERRA]].forEach(([k, rows]) => {
  [...new Set(rows.map(r => r.country))].sort().forEach(c => $(`[data-adm=${k}]`).insertAdjacentHTML("beforeend", `<option value="${esc(c)}">${esc(admName(c))}</option>`));
  [...new Set(rows.map(r => r.result_class))].forEach(c => $(`[data-cls=${k}]`).insertAdjacentHTML("beforeend", `<option value="${esc(c)}">${esc((CLASS[c]||[,c])[1])}</option>`));
  $(`[data-search=${k}]`).oninput = () => render(k); $(`[data-adm=${k}]`).onchange = () => render(k); $(`[data-cls=${k}]`).onchange = () => render(k); $(`[data-prio=${k}]`).onchange = () => render(k);
  $$(`.seg[data-for=${k}] button`).forEach(b => b.onclick = () => setMode(k, b.dataset.v));
  render(k);
});
function setMode(k, mode){ STATE[k].mode = mode; $$(`.seg[data-for=${k}] button`).forEach(b => b.setAttribute("aria-pressed", b.dataset.v === mode)); render(k); }
function render(k){
  const st = STATE[k], q = $(`[data-search=${k}]`).value.toLowerCase().trim(), a = $(`[data-adm=${k}]`).value, c = $(`[data-cls=${k}]`).value, pr = $(`[data-prio=${k}]`).value;
  let rows = rowsOf(k).filter(r => st.mode === "all" || (st.mode === "alerts") === (r._k === "alert"));
  rows.forEach((r,i) => r._rank = i+1);
  rows = rows.filter(r => (!a || r.country === a) && (!c || r.result_class === c) && (!pr || r.priority === pr) && (!q || JSON.stringify(r).toLowerCase().includes(q)
         || (D.adm[r.country]||"").toLowerCase().includes(q)));
  const cols = COLS[k];
  if (st.sort !== null) { const f = cols[st.sort][3] || cols[st.sort][1]; rows = [...rows].sort((x,y) => { const p = f(x), q2 = f(y); return (typeof p === "number" && typeof q2 === "number" ? p-q2 : String(p).localeCompare(String(q2), undefined, {numeric:true})) * st.dir; }); }
  const maxM = Math.max(1, ...rowsOf(k).map(r => Math.abs(marginOf(k,r)||0)));
  const cell = (col, r) => { const v = col[1](r), t = col[2];
    if (t === "badge") return `<td>${badge(v)}</td>`;
    if (t === "prio") return `<td>${prioBadge(v)}</td>`;
    if (t === "margin") return v === null || v === undefined ? `<td class="num note">n/a</td>` : `<td class="num"><div class="mbar"><b>${sgn(v)}</b><span class="b ${v<0?"neg":""}" style="width:${Math.max(2,Math.abs(v)/maxM*90)}px"></span></div></td>`;
    if (t === "name") return `<td class="name">${esc(v)}</td>`;
    if (t === "num1") return `<td class="num">${num(v)}</td>`;
    if (t === "num") return `<td class="num">${esc(v)}</td>`;
    return `<td>${esc(v)}</td>`; };
  $(`#t${k}`).innerHTML = `<thead><tr>${cols.map((col,i) => `<th class="${["num","num1","margin"].includes(col[2])?"num":""}" data-i="${i}">${esc(col[0])}${st.sort===i?(st.dir>0?" ▲":" ▼"):""}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r,i) => `<tr data-i="${i}">${cols.map(col => cell(col, r)).join("")}</tr>`).join("") || `<tr><td colspan="${cols.length}" class="empty">No rows match the filters.</td></tr>`}</tbody>`;
  $$(`#t${k} th`).forEach(th => th.onclick = () => { const i = +th.dataset.i; st.dir = st.sort === i ? -st.dir : (["num","num1","margin"].includes(cols[i][2]) ? -1 : 1); st.sort = i; render(k); });
  $$(`#t${k} tbody tr[data-i]`).forEach(tr => tr.onclick = () => { $$(`#t${k} tr.sel`).forEach(x => x.classList.remove("sel")); tr.classList.add("sel"); openDetail(k, rows[tr.dataset.i]); });
  $(`[data-count=${k}]`).textContent = `${rows.length} row(s)`;
}

// ---------- detail drawer ----------
const kv = pairs => `<dl class="kv">${pairs.filter(p => p[1] !== "" && p[1] !== null && p[1] !== undefined).map(([k,v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
function stationsBlock(ids){
  const list = (ids||"").split(";").filter(Boolean);
  if (!list.length) return `<span class="note">None of the Traficom special-interest stations overlap this frequency.</span>`;
  const shown = list.slice(0, 40).map(s => `<span class="chip">${esc(s)}</span>`).join("");
  return `<div class="note">${list.length} station(s) of the special-interest list use this frequency range (RX/TX):</div><div class="chips" style="margin-top:6px">${shown}${list.length>40?`<span class="chip">+${list.length-40} more</span>`:""}</div>`;
}
function letterBlock(key, title){
  const text = D.letters[key];
  if (!text) return `<p class="note">No letter is drafted for concerns; letters are prepared for alerts only.</p>`;
  return `<div class="btns"><button class="btn primary" data-copy>Copy letter</button><button class="btn" data-dl="${esc(title)}">Download .txt</button></div><pre class="letter">${esc(text)}</pre>`;
}
function openDetail(k, r){
  const m = marginOf(k, r);
  let head, body;
  if (k === "space") {
    head = `<h2>${esc(r.network_name)}</h2><div class="note">${esc(admName(r.country))} · notice ${esc(r.notice_id)}${r.related_notice_id ? ` (related ${esc(r.related_notice_id)})` : ""} · ${esc(r.notice_type)}</div><div style="margin-top:6px;display:flex;gap:14px">${badge(r.result_class)}${r.priority ? prioBadge(r.priority) + ` <span class="note">${esc(r.priority_reason)}</span>` : ""}</div>`;
    body = `<div class="why"><div class="big">${m === null || m === undefined ? "ITU Bureau finding - checked against Finland" : `${sgn(m)} dB ${m>=0 ? "over" : "under"} the ${r.result_class==="COORDINATION REQUIRED" ? "coordination threshold" : "limit"}`}</div><div style="margin-top:6px">${esc(r.reason)}</div></div>
      ${r.pfd_dbw_m2 === null || r.pfd_dbw_m2 === undefined ? "" : `<h3>Key figures</h3>${kv([
        ["Calculated pfd", `${num(r.pfd_dbw_m2,2)} dB(W/m²) in ${esc(r.reference_bandwidth)}`],
        ["Limit / threshold", `${num(r.pfd_limit_dbw_m2,2)} dB(W/m²) in ${esc(r.reference_bandwidth)}`],
        ["Margin", `<b>${sgn(m,2)} dB</b>`],
        ["e.i.r.p. in ref. bandwidth", `${num(r.eirp_ref_dbw,2)} dBW`],
        ["Power source", esc(r.power_source)],
        ["Worst-case geometry", `elevation ${num(r.elevation_deg)}°, slant range ${num(r.distance_km,0)} km`]])}`}
      <h3>Satellite</h3>${kv([
        ["Network", esc(r.network_name)], ["Orbit", r.orbit === "GSO" ? `Geostationary, ${num(r.gso_longitude,2)}°` : `Non-geostationary${r.altitude_km ? `, altitude ${num(r.altitude_km,0)} km` : ""}`],
        ["Assignment", `${esc(r.frequency_mhz)} MHz` + (r.beam_name ? `, beam ${esc(r.beam_name)}, emission ${esc(r.emission)}` : "")],
        ["Station class / service", r.station_class ? `${esc(r.station_class)} / ${esc(r.service)}` : ""]])}
      <h3>Regulation</h3>${kv([["Criterion", esc(r.rr_reference)], ["Register row", r.table21_4_row ? `<code>${esc(r.table21_4_row)}</code>` : ""],
        ["ITU BR examination finding", r.br_finding ? esc(r.br_finding) + ` <span class="note">(A- favourable, N- unfavourable; X/21.16 = Bureau found the pfd limit exceeded)</span>` : `<span class="note">none recorded (API/A notices are not examined by the Bureau)</span>`]])}
      <h3>Affected in Finland</h3>${kv([["M4 services in band", esc(r.m4_service) || "-"]])}<div style="margin-top:8px">${stationsBlock(r.finnish_station_id)}</div>
      <h3>Recommended letter</h3>${letterBlock("S:" + r.notice_id, `${r.country}_${r.notice_id}_${r.network_name}`)}`;
  } else {
    head = `<h2>${esc(r.site_name)}</h2><div class="note">${esc(admName(r.country))} · assignment ${esc(r.notice_id)} · class ${esc(r.station_class)} · ${esc(r.change_type)}</div><div style="margin-top:6px;display:flex;gap:14px">${badge(r.result_class)}${r.priority ? prioBadge(r.priority) + ` <span class="note">${esc(r.priority_reason)}</span>` : ""}</div>`;
    body = `<div class="why"><div class="big">${sgn(m)} dB ${m>=0 ? "over" : "under"} the T/R 25-08 trigger</div><div style="margin-top:6px">${esc(r.reason)}</div></div>
      ${r.coordination_with_fin ? `<div class="callout"><b>Coordination with Finland recorded in the ITU data:</b> ${esc(r.coordination_with_fin)}<br><span class="note">Verify against the Traficom frequency register - the case remains flagged.</span></div>` : ""}
      <h3>Key figures</h3>${kv([
        ["Field strength", `${num(r.field_strength_dbuv_m,2)} dB(µV/m)`], ["T/R 25-08 trigger", `${num(r.threshold_dbuv_m,2)} dB(µV/m)`],
        ["Margin", `<b>${sgn(m,2)} dB</b>`], ["Worst point in Finland", `${esc(r.worst_point_type)} at ${num(r.worst_point_lat,4)} N ${num(r.worst_point_lon,4)} E`],
        ["Distance / path", `${num(r.distance_km)} km - ${esc(r.path)}`], ["Model", esc(r.model)]])}
      <h3>Transmitter</h3>${kv([
        ["Site", `${esc(r.site_name)}, ${num(r.latitude,4)} N ${num(r.longitude,4)} E (${esc(r.geo_type)}${r.radius_km ? `, radius ${num(r.radius_km,0)} km` : ""})`],
        ["Frequency / bandwidth", `${esc(r.frequency_mhz)} MHz / ${esc(r.bandwidth_khz)} kHz, ${esc(r.emission)}`],
        ["e.r.p. / antenna height", `${num(r.erp_dbw)} dBW / ${num(r.tx_height_m,0)} m`], ["ITU remarks", esc(r.itu_remarks)]])}
      <h3>Compared with earlier IFIC issues</h3>${kv([["Status", esc(r.change_vs_previous_issues || "-")],
        ["Earlier publications", esc(r.issue_history) || "<span class=\"note\">none in the processed issues</span>"]])}
      <h3>Affected in Finland</h3>${kv([["M4 services at this frequency", esc(r.m4_service) || "-"]])}<div style="margin-top:8px">${stationsBlock(r.finnish_station_id)}</div>
      <h3>Recommended letter</h3>${letterBlock("T:" + r.notice_id, `T_${r.country}_${r.notice_id}`)}`;
  }
  history.replaceState(null, "", `#${k}/${r.notice_id}`);
  $("#dhead").innerHTML = head; $("#dbody").innerHTML = body; $("#dbody").scrollTop = 0;
  const d = $("#drawer"); d.classList.add("open"); d.setAttribute("aria-hidden", "false");
  const copy = $("[data-copy]", d), dl = $("[data-dl]", d), text = $("pre.letter", d)?.textContent;
  if (copy) copy.onclick = async () => { try { await navigator.clipboard.writeText(text); copy.textContent = "Copied"; } catch { const rng = document.createRange(); rng.selectNodeContents($("pre.letter", d)); getSelection().removeAllRanges(); getSelection().addRange(rng); copy.textContent = "Selected - press Ctrl+C"; } };
  if (dl) dl.onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], {type:"text/plain"})); a.download = dl.dataset.dl.replace(/[^\w.-]+/g, "_") + ".txt"; a.click(); };
}
function closeDrawer(){ if (location.hash.includes("/")) history.replaceState(null, "", "#" + location.hash.slice(1).split("/")[0]); const d = $("#drawer"); d.classList.remove("open"); d.setAttribute("aria-hidden", "true"); }
$("#close").onclick = closeDrawer; document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

// ---------- deep links: #space, #terra/93082383 ----------
(function route(){
  const [tab, id] = decodeURIComponent(location.hash.slice(1)).split("/");
  if (!["overview","space","terra","method","sources"].includes(tab)) return;
  showTab(tab, true);
  if (id && (tab === "space" || tab === "terra")) {
    const r = rowsOf(tab).find(x => String(x.notice_id) === id);
    if (r) { setMode(tab, r._k === "alert" ? "alerts" : "concerns"); openDetail(tab, r); }
  }
})();

// ---------- tooltip ----------
function tip(e, text){ const t = $("#tip"); if (!text) { t.style.display = "none"; return; } t.textContent = text; t.style.display = "block"; t.style.left = (e.clientX + 12) + "px"; t.style.top = (e.clientY + 12) + "px"; }

// ---------- sources ----------
$("#inputs").innerHTML = [...new Map(D.inputs.map(x => [x[0], x])).values()].map(([f,h,b]) => `<tr><td>${esc(f)}</td><td><div class="hash">${esc(h)}</div><div class="note">${(b/1048576).toFixed(1)} MB</div></td></tr>`).join("");
$("#tests").innerHTML = D.tests.ok === null ? "Test suite not run for this build." :
  `${D.tests.ok ? "&#10003;" : "&#10007;"} <b>${esc(D.tests.summary)}</b> - unit tests against reference values, independent hand calculations of real findings from the raw IFIC tables, and invariants on the published files (<code>tests/test_pipeline.py</code>).`;
$("#auditS").textContent = D.audit.space; $("#auditT").textContent = D.audit.terra;
$("#trends").textContent = (D.audit.terra.match(/Trends[\s\S]*?(?=\n(?:Foreign|Not evaluated))/) || ["(trends not available)"])[0];
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
