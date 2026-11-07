# IFIC interference screening for Finland (SinceAI 2026 · Traficom)

An ETL pipeline that turns each new ITU BR IFIC (published every two weeks) into an
expert-reviewable analysis of cases relevant to Finland: Space IFIC PFD over Finnish
territory, and Terrestrial IFIC land-mobile field strength at the Finnish border, with
draft correspondence and a self-contained HTML report.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # needs mdbtools for .mdb input
cp ~/Downloads/<new IFIC>.zip inbox/                                 # Space or Terrestrial, .zip/.mdb/.sqlite
.venv/bin/python pipeline.py                                         # extract -> analyse -> load -> report
xdg-open report.html
```

Unattended every two weeks (cron):

```
0 6 * * 1  cd /path/to/repo && .venv/bin/python pipeline.py --refresh-m4 >> pipeline.log 2>&1
```

## Pipeline (`pipeline.py`)

| Stage | What happens |
|---|---|
| **Extract** | `.zip` (ITU download) is unpacked, `.mdb` converted to SQLite (`convert_mdb.py`, every table's row count verified against the MDB), cached by SHA-256 in `data/sqlite/`. Dataset type and IFIC number are detected from the data. `--refresh-m4` downloads the current M4 table from opendata.traficom.fi. |
| **Transform** | Space: `inspection_engine.py`. Terrestrial: `terrestrial.py`. Draft letters for every alert: `letters.py`. |
| **Load** | `runs/<kind>_<ific>/` (alerts.csv/.json, concerns.csv/.json, audit.txt, letters/), cumulative `results.sqlite` (runs + findings of every issue), `report.html` (issue selector, history). |

Files already processed (same SHA-256) are skipped; `--force` re-runs them, `--report-only` rebuilds the page.

## What is checked

**Space IFIC** - API/A, CR/C, AP30/30A/30B and Art. 11 notifications of Earth-orbiting space stations:

* **Exceeds limit**: pfd over Finland vs ITU RR Ed. 2024 Art. 21 No. 21.16 **Table 21-4** (Rev.WRC-23),
  angle-of-arrival dependent, including footnotes 11 (≤ 99 satellites), 18 (EESS active mean pfd) and 24/25 (HEO).
* **Coordination required**: RR No. 9.14 thresholds of **Appendix 5** Annex 1 (Table 5-2 NOTE 9 values for Finland).
* Geometry from Finland: GSO from the orbital longitude, non-GSO at every elevation reachable given
  perigee/apogee/inclination; evaluated at the Excel station sites and Finnish border/sea extremes.
* Worst case as instructed by Traficom: peak antenna gain toward Finland.
* ITU BR examination findings (A-/N-, X/21.16) are shown and cross-checked.

**Terrestrial IFIC** - foreign land-mobile assignments (FB, FL, ML), NEW and MODIFIED:

* Field strength with **ITU-R P.1546-6** (ITU-R WP 3K reference implementation `Py1546`), 10 % time,
  50 % locations, 10 m receiver, land/cold-sea mixed paths.
* Compared with **ECC T/R 25-08 A1.2.3** (20 dB(µV/m) in 25 kHz) along the Finnish land border and
  territorial sea (`geodata/`, built from Natural Earth 1:10m by `build_boundaries.py`; replace with the
  official dataset via `terrestrial.py --border-file`).
* Coordination with Finland recorded in the IFIC is reported, not treated as closed; trends by
  administration/class/band are in the audit.

**Alerts** = calculated exceedance of a cited criterion. **Concerns** = near-limit, limit not applicable,
worst-case inter-satellite beams, high pfd in bands without a limit, BR-unfavourable networks not visible
from Finland. Margins are command-line options.

## Inputs

| File | Source |
|---|---|
| Space / Terrestrial IFIC | ITU BR IFIC downloads (.zip/.mdb) |
| `m4.json` | Traficom open data API v13 `Taajuusjakotaulukko` (M4) |
| `Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx` | Traficom special-interest stations |
| `pfd.json` | RR Table 21-4 + App. 5 thresholds (transcribed from RR Ed. 2024 Vol. 1/2) |
| `geodata/` | Finnish land border, territorial sea, land mask (`build_boundaries.py`) |

## Verification

```bash
.venv/bin/python -m unittest -v tests.test_pipeline
```

27 tests: physics against reference values, independent hand calculations of real findings from the raw
IFIC tables (CSN-SSO, PAZ_2-A, Kostomuksha), RR footnote rules, BR cross-check, and invariants on the
outputs. Each audit file records input SHA-256 hashes and model versions.
