# SinceAI-Traficom

Since AI Hackathon 2026 – Traficom Challenge: *"Saving Earth from Space Interferers Using Radio Frequencies"*

---

## Project Overview

This repository contains the data processing pipeline and analysis tools for the Traficom challenge at the Since AI Hackathon 2026. The goal is to analyze satellite frequency coordination data from ITU BR IFIC (International Frequency Information Circular) to identify and mitigate space-based radio frequency interference affecting Finland.

---

## Repository Structure

| File / Directory | Description |
|------------------|-------------|
| `process_itu_mdb.py` | **Unified script**: converts any ITU BR IFIC MDB to SQLite and creates analytical views. Accepts CLI arguments for input/output paths. |
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

## Data Pipeline (`process_itu_mdb.py`)

Single script that handles download, conversion, and view creation:

```bash
# Auto-download IFIC #3074 from ITU (default)
python process_itu_mdb.py

# Download specific IFIC by number
python process_itu_mdb.py 3083

# Download from custom ITU zip URL
python process_itu_mdb.py https://www.itu.int/sns/ific10/ific3083.zip

# Use local .mdb file
python process_itu_mdb.py /path/to/ific3083.mdb

# Use local .zip file
python process_itu_mdb.py /path/to/ific3083.zip

# Custom output directory
python process_itu_mdb.py 3083 /path/to/output_dir
```

### What it does:

0. **Downloads & extracts** (if input is IFIC number, URL, or .zip)
   - Fetches `https://www.itu.int/sns/ific10/ific<NUM>.zip`
   - Extracts `.mdb` to temporary directory
   - Cleans up temp files automatically

1. **Converts MDB → SQLite** using `mdb-tools` (`mdb-tables`, `mdb-schema`, `mdb-export`)
   - Output filename derived from input (e.g., `ific3083.mdb` → `ific3083.sqlite`)
   - Performance-tuned PRAGMAs for fast bulk loading
   - Transaction-wrapped imports with progress logging

2. **Creates analytical view** (conditional)
   - Only runs if required Space IFIC tables exist: `notice`, `grp`, `freq`, `emiss`, `s_beam`, `geo`, `tr_aff_ntw`
   - IFIC number auto-extracted from filename (e.g., `ific3074` → 3074)
   - Creates view `vw_space_<NUM>_pfd_inputs` joining all relevant tables
   - Filters: `wic_no = <IFIC_NUM>` AND `emi_rcp = 'E'` (Emission/Reception)

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

# 2. Run unified processing script (auto-downloads IFIC #3074 from ITU)
python process_itu_mdb.py
```

**Default behavior:**
- Input: IFIC #3074 (auto-downloaded from `https://www.itu.int/sns/ific10/ific3074.zip`)
- Output: `/home/niksu/Documents/SinceAI-Traficom/ific3074.sqlite`

**Override input:** `python process_itu_mdb.py <IFIC_NUM|URL|.mdb|.zip> [output_dir]`

---

## Contacts

- **Traficom**: Pekka Pussinen, Otto Julkunen (via Since AI Discord)
- **Challenge**: Since AI Hackathon 2026

---

## License

Challenge materials © Traficom. Code in this repository is for hackathon purposes.