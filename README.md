# IFIC interference screening for Finland (SinceAI 2026 · Traficom)

Since AI Hackathon 2026 - Traficom challenge *"Saving Earth from Space Interferers Using Radio Frequencies"*.

An ETL pipeline that turns each new ITU BR IFIC (published every two weeks) into an expert-reviewable
analysis of cases relevant to Finland - satellite power flux-density over Finnish territory (Space IFIC)
and foreign land-mobile field strength at the Finnish border (Terrestrial IFIC) - with a cited reason
for every finding, a draft letter to the administration concerned and a self-contained review page.

## For the judges

**Start here: open [`report.html`](report.html) in a browser** (works offline, no installation).
Search any satellite, station or country in the header, click a finding to see why it was flagged,
the figures, the regulation, the affected Finnish services and stations, a map (terrestrial) and the
recommended letter; ◀ ▶ / arrow keys step through the findings.

### Evaluation results (separate files per IFIC)

| IFIC | Folder | Alerts | Concerns |
|---|---|---|---|
| **Space 3074** | [`runs/space_3074/`](runs/space_3074) | **34** - 31 exceed RR Table 21-4, 3 require coordination (RR No. 9.14); priority 18 high / 14 medium / 2 low | 48 |
| **Terrestrial 3076** | [`runs/terrestrial_3076/`](runs/terrestrial_3076) | **6** exceed ECC T/R 25-08 at the Finnish border; priority 2 high / 4 low | 2 |

Each folder holds `<kind>_<ific>_results.xlsx` (Summary, Alerts, Concerns, Method sheets),
`alerts.csv/.json`, `concerns.csv/.json`, `audit.txt` (inputs with SHA-256, every exclusion and why)
and `letters/` (one draft per alert).

The pipeline also processed newer and neighbouring issues downloaded directly from the ITU, to show that
any existing or forthcoming IFIC can be evaluated: **Space 3083** (34 alerts) and **Terrestrial 3075 / 3077**
(16 / 0 alerts; consecutive-issue comparison for the side quest).

### Independent checks of the results

* 36 automated tests, including hand calculations of real findings from the raw IFIC tables
  (CSN-SSO, PAZ_2-A, Kostomuksha) and the RR footnote rules.
* ANANTHSAT-89E-A (Space 3083): +18.93 dB here vs +18.9 dB found independently by another team.
* Every network the ITU Bureau itself found unfavourable under No. 21.16 (finding X/21.16) is either an
  alert or listed with the reason it cannot affect Finland (orbit never visible from Finland).

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # installs Py1546 (ITU-R P.1546-6) from GitHub
sudo pacman -S mdbtools        # or: sudo apt install mdbtools  (needed to read .mdb files)
.venv/bin/python pipeline.py 3083             # download Space + Terrestrial IFIC 3083 from the ITU and analyse it
xdg-open report.html
```

Other inputs: `.venv/bin/python pipeline.py s3074 t3076` (one part each), local files
(`pipeline.py ific3074.zip brific3076.mdb`) or anything dropped into `inbox/` (`pipeline.py`).

### Every two weeks, automatically

```bash
.venv/bin/python pipeline.py --watch             # download + process every issue published since the last one
.venv/bin/python pipeline.py --watch --every 6   # keep running, check every 6 hours
```

Cron: `0 */6 * * *  cd /path/to/repo && .venv/bin/python pipeline.py --watch --refresh-m4 >> pipeline.log 2>&1`

The issue is downloaded as soon as the ITU publishes it (resumable downloads), so the analysis is ready
before anyone opens it.

## Pipeline (`pipeline.py`)

| Stage | What happens |
|---|---|
| **Extract** | Space/Terrestrial IFIC by number or URL from the ITU (`ific10/ific<N>.zip`, `brific/BRIFIC/MDB/brific<N>.zip`), or local `.zip`/`.mdb`/`.sqlite`. MDB is converted to SQLite (`convert_mdb.py`, every table's row count verified), cached by SHA-256 in `data/sqlite/`. Dataset type and issue number are detected from the data. `--refresh-m4` downloads the current M4 table from opendata.traficom.fi. |
| **Transform** | Space: `inspection_engine.py`. Terrestrial: `terrestrial.py`. Draft letters for every alert: `letters.py`. |
| **Load** | `runs/<kind>_<ific>/` (Excel workbook, CSV/JSON, audit, letters), cumulative `results.sqlite` (all issues), `report.html` (issue selector, history). |

Already-processed files (same SHA-256) are skipped; `--force` re-runs them, `--report-only` rebuilds the page.
Terrestrial screening needs Py1546 and stops with an error without it (unless `--allow-free-space`).

## What is checked

**Space IFIC** - API/A, CR/C, AP30/30A/30B and Art. 11 notifications of Earth-orbiting space stations:

* **Exceeds limit**: pfd over Finland vs ITU RR Ed. 2024 Vol. 1 Art. 21 No. 21.16 **Table 21-4**
  (Rev.WRC-23), angle-of-arrival dependent, with footnotes 11 (≤ 99 satellites), 18 (EESS active mean pfd)
  and 24/25 (highly-inclined orbits).
* **Coordination required**: RR No. 9.14 thresholds, ITU RR Vol. 2 **Appendix 5** Annex 1 (Table 5-2,
  NOTE 9 values for Finland).
* Geometry from Finland: GSO from the orbital longitude; non-GSO at every elevation reachable given
  perigee/apogee/inclination; evaluated at the Traficom station sites and the Finnish border/sea extremes.
* Worst case as instructed by Traficom: peak antenna gain toward Finland.
* ITU BR examination findings (A-/N-, X/21.16) are shown on every row and cross-checked.

**Terrestrial IFIC** - foreign land-mobile assignments (FB, FL, ML), NEW and MODIFIED:

* Field strength with **ITU-R P.1546-6** (ITU-R WP 3K reference implementation `Py1546`), 10 % time,
  50 % locations, 10 m receiver, land / cold-sea mixed paths.
* Compared with **ECC T/R 25-08 A1.2.3** (20 dB(µV/m) in 25 kHz) along the Finnish land border and the
  outer limit of the territorial sea, and at the Traficom stations on overlapping frequencies.
* **Consecutive issues**: each assignment is looked up in the earlier Terrestrial IFICs processed and marked
  NEW, REPUBLISHED or MODIFIED with its publication history.
* Coordination with Finland recorded in the IFIC is reported, not treated as closed. Trends by
  administration / station class / band are in the audit.

**Alerts** = calculated exceedance of a cited criterion, ordered by review **priority**: High (space:
≥ 10 dB, ITU BR finding X/21.16, or Finnish stations in band and ≥ 3 dB; terrestrial: ≥ 6 dB or a Finnish
station above the trigger), Medium (space: ≥ 3 dB, Finnish stations in band or RR 9.14 coordination;
terrestrial: 1-6 dB), Low (small margins; terrestrial < 1 dB is within modelling uncertainty).
**Concerns** = near the limit (6 dB), limit not applicable (> 99 satellites), worst-case inter-satellite
beams, high pfd in bands without a limit, ITU-unfavourable networks not visible from Finland.
All margins are command-line options.

## Letters and AI

Every alert gets a draft letter to the administration concerned (`runs/<kind>_<ific>/letters/`), built
from the calculated figures only and marked *DRAFT FOR EXPERT REVIEW*.

Optional AI: with `--ai-letters` a **local language model** (Ollama, default `llama3.2:3b`, no cloud,
no API key) writes the opening paragraph in plain language. Every number in that paragraph is checked
against the calculation; if the model invents or changes a figure, the template paragraph is kept. The
letter and the report state which was used.

```bash
# install Ollama (https://ollama.com), then once:
ollama serve &  ollama pull llama3.2:3b
.venv/bin/python pipeline.py --force --ai-letters s3074
```

(`letters.py --polish` does the same with the Claude API if an Anthropic key and `pip install anthropic`
are available.)

## Known limitations

* Worst case by design: peak antenna gain toward Finland; real beams usually point elsewhere.
* Table 21-4 terms that need constellation data not in the IFIC (X, Y) use the less strict value.
* Station class E5 (12 space notices) is not mapped to a service.
* Terrestrial: no terrain profile; boundaries from Natural Earth 1:10m, territorial sea measured from the
  coastline - replace with the official dataset via `terrestrial.py --border-file`.
* Finnish satellite networks (ITU Space Explorer) are not part of the IFIC data, so new filings are not yet
  checked against Finnish satellites; the BR affected-network list of 3074 names no Finnish network.

## Inputs

| File | Source |
|---|---|
| Space / Terrestrial IFIC | ITU BR IFIC downloads (.zip/.mdb) |
| `m4.json` | Traficom open data API v13 `Taajuusjakotaulukko` (M4) |
| `Traficom materials/` | Challenge brief and the special-interest station Excel (SHA-256 verified against `SHA256SUMS.txt`) |
| `pfd.json` | RR Table 21-4 + Appendix 5 thresholds, transcribed from ITU RR Ed. 2024 Vol. 1/2 |
| `geodata/` | Finnish land border, territorial sea, land mask, map outline (`build_boundaries.py`, Natural Earth 1:10m) |

## Verification

```bash
.venv/bin/python -m unittest -v tests.test_pipeline
```

36 tests: physics against reference values, independent hand calculations of real findings, RR footnote
rules, ITU Bureau cross-check, the letter AI's number check, pipeline I/O, and invariants on the outputs.

## Files

| File | Description |
|---|---|
| `pipeline.py` | ETL entry point (extract / transform / load, watch mode) |
| `inspection_engine.py` | Space IFIC screening |
| `terrestrial.py` | Terrestrial IFIC screening |
| `letters.py` | Draft letters (template; optional local AI opening paragraph) |
| `build_report.py` | Builds `report.html` from all processed issues |
| `convert_mdb.py` | MDB -> SQLite with per-table row-count verification |
| `build_boundaries.py` | Builds `geodata/` from Natural Earth 1:10m |
| `Table 21-4.odt`, `interference_check_criteria.txt` | Working notes |

## Contacts

Traficom: Pekka Pussinen, Otto Julkunen (Since AI Discord). Challenge materials © Traficom; code for hackathon purposes.
