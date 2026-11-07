# SinceAI-Traficom

Since AI Hackathon 2026 – Traficom Challenge: *"Saving Earth from Space Interferers Using Radio Frequencies"*

---

## Project Overview

This repository contains the data processing pipeline and analysis tools for the Traficom challenge at the Since AI Hackathon 2026. The goal is to analyze satellite frequency coordination data from ITU BR IFIC (International Frequency Information Circular) to identify and mitigate space-based radio frequency interference affecting Finland.

---

## Repository Structure

| File / Directory | Description |
|------------------|-------------|
| `convert_mdb.py` | Python script to convert ITU BR IFIC MDB (Microsoft Access) database to SQLite. Updated to process **IFIC #3074**. |
| `create_views.py` | Creates analytical SQL views on the SQLite database for Space IFIC #3074 PFD (Power Flux Density) input analysis. |
| `schema.txt` | Full database schema export (DDL) from the converted IFIC #3074 SQLite database. |
| `.gitignore` | Excludes large binary artifacts (`ific3074.sqlite` ~356 MB) from version control. |
| `Traficom materials/` | Original challenge brief, frequency dataset, and checksums provided by Traficom. |

---

## Traficom Materials

Located in `Traficom materials/`:

| File | Description |
|------|-------------|
| `READ_ME_FIRST.txt` | Quick-start guide with links to all resources. |
| `Since_AI_Traficom_Challenge_Brief_FINAL_04112026.pdf` / `.docx` | Official challenge brief: problem statement, side quest, data sources, evaluation criteria, contacts. |
| `Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx` | Synthetic frequency usage dataset for Finland (frequencies of special interest to be protected). Provided by Traficom on 5 Nov 2026. |
| `SHA256SUMS.txt` | SHA-256 checksums for all files in this folder. |

**External data sources** (linked in the brief):
- ITU BR IFIC databases
- Regulation M4 / Traficom open-data API
- ITU tools
- CEPT databases
- ITU Radio Regulations

---

## Data Pipeline

### 1. Convert MDB → SQLite (`convert_mdb.py`)

```bash
python convert_mdb.py
```

- Input: `/home/niksu/Downloads/ific3074.mdb` (ITU BR IFIC #3074 Microsoft Access database)
- Output: `ific3074.sqlite` (SQLite database, **~356 MB**, git-ignored)
- Uses `mdb-tools` (`mdb-tables`, `mdb-schema`, `mdb-export`) for conversion
- Configurable via `MDB_PATH` and `OUTPUT_DB` constants at top of script

### 2. Create Analytical Views (`create_views.py`)

```bash
python create_views.py
```

- Connects to `ific3074.sqlite`
- Creates view `vw_space_3074_pfd_inputs` joining:
  - `notice` (ntc_id, adm, wic_no, ntf_rsn, st_cur)
  - `grp` (grp_id, beam_name, emi_rcp)
  - `freq` (freq_min, freq_max, freq_mhz)
  - `emiss` (pwr_ds_max)
  - `s_beam` (gain → peak_antenna_gain)
  - `geo` (long_nom → gso_longitude)
  - `tr_aff_ntw` (coord_prov → affected_country, agree_st)
- Filters: `wic_no = 3074` AND `emi_rcp = 'E'` (Emission/Reception)
- Outputs sample query results via pandas

---

## Database Schema (`schema.txt`)

Complete DDL export including all tables and indexes. Key tables:

- `notice` – Satellite network notices
- `grp` – Beam groups
- `freq` – Frequency assignments
- `emiss` – Emission parameters
- `s_beam` – Satellite beam characteristics
- `geo` – Geostationary orbit positions
- `tr_aff_ntw` – Affected networks (coordination)
- `adm_assoc`, `aesim_param`, `all_aff_ntw`, … and many more

---

## Quick Start

```bash
# 1. Install mdb-tools (Ubuntu/Debian)
sudo apt-get install mdb-tools

# 2. Download IFIC #3074 MDB from ITU BR (place in ~/Downloads/ific3074.mdb)

# 3. Convert to SQLite
python convert_mdb.py

# 4. Create analytical views
python create_views.py
```

---

## Contacts

- **Traficom**: Pekka Pussinen, Otto Julkunen (via Since AI Discord)
- **Challenge**: Since AI Hackathon 2026

---

## License

Challenge materials © Traficom. Code in this repository is for hackathon purposes.