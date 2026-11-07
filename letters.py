#!/usr/bin/env python3
"""Draft expert-review correspondence from space_<ific>_report.csv.

Every letter is first rendered from a deterministic template using only the
structured finding (no invented values). If the `anthropic` package and
credentials are available and --polish is given, Claude rewrites the prose; the
polished draft is kept only if every key figure from the finding survives
verbatim, otherwise the template draft is used.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

ADMINISTRATIONS = {
    "CHN": "China (People's Republic of)", "USA": "United States of America", "F": "France",
    "E": "Spain", "KAZ": "Kazakhstan (Republic of)", "IND": "India (Republic of)",
    "KOR": "Korea (Republic of)", "PHL": "Philippines (Republic of the)", "ISR": "Israel (State of)",
    "INS": "Indonesia (Republic of)", "G": "United Kingdom", "D": "Germany (Federal Republic of)",
    "J": "Japan", "RUS": "Russian Federation", "LUX": "Luxembourg", "NOR": "Norway",
    "I": "Italy", "POL": "Poland (Republic of)", "S": "Sweden", "QAT": "Qatar (State of)", "SVN": "Slovenia (Republic of)",
    "ARS": "Saudi Arabia (Kingdom of)",
    "CAN": "Canada",
    "PAK": "Pakistan (Islamic Republic of)",
    "UAE": "United Arab Emirates",
    "VTN": "Viet Nam (Socialist Republic of)",
    "POR": "Portugal",
    "AUS": "Australia",
    "B": "Brazil (Federative Republic of)",
    "HOL": "Netherlands (Kingdom of the)",
    "NZL": "New Zealand",
    "ARG": "Argentine Republic",
    "MEX": "Mexico",
    "EGY": "Egypt (Arab Republic of)",
    "UKR": "Ukraine",
    "IRN": "Iran (Islamic Republic of)",
    "THA": "Thailand",
    "MLA": "Malaysia",
    "SNG": "Singapore (Republic of)",
    "BEL": "Belgium",
    "SUI": "Switzerland (Confederation of)",
    "AUT": "Austria",
    "DNK": "Denmark",
    "EST": "Estonia (Republic of)",
    "LTU": "Lithuania (Republic of)",
    "LVA": "Latvia (Republic of)",
    "GRC": "Greece",
    "NIG": "Nigeria (Federal Republic of)",
    "ALG": "Algeria (People's Democratic Republic of)",
    "MRC": "Morocco (Kingdom of)",
    "TUR": "Türkiye",
    "AZE": "Azerbaijan (Republic of)",
    "BLR": "Belarus (Republic of)",
    "BGD": "Bangladesh (People's Republic of)",
    "MNG": "Mongolia",
    "CYP": "Cyprus (Republic of)",
    "PNG": "Papua New Guinea",
    "TON": "Tonga (Kingdom of)",
    "BOL": "Bolivia (Plurinational State of)",
    "PRU": "Peru",
    "CHL": "Chile",
    "COL": "Colombia (Republic of)",
    "VEN": "Venezuela (Bolivarian Republic of)",
    "ISL": "Iceland",
    "IRL": "Ireland",
    "CZE": "Czech Republic",
    "HNG": "Hungary",
    "ROU": "Romania",
    "BUL": "Bulgaria (Republic of)",
    "SRB": "Serbia (Republic of)",
    "HRV": "Croatia (Republic of)",
    "SVK": "Slovak Republic",
    "FIN": "Finland",
    "ETH": "Ethiopia",
    "KEN": "Kenya (Republic of)",
    "AFS": "South Africa (Republic of)",
    "OMA": "Oman (Sultanate of)",
    "KWT": "Kuwait (State of)",
    "BHR": "Bahrain (Kingdom of)",
    "JOR": "Jordan (Hashemite Kingdom of)",
    "UZB": "Uzbekistan (Republic of)",
    "TKM": "Turkmenistan",
    "ARM": "Armenia (Republic of)",
    "GEO": "Georgia",
    "MDA": "Moldova (Republic of)",
    "PNR": "Panama (Republic of)",
    "BRB": "Barbados",
    "LAO": "Lao People's Democratic Republic",
    "KRE": "Democratic People's Republic of Korea",
    "CBG": "Cambodia (Kingdom of)",
    "NPL": "Nepal",
    "AGL": "Angola (Republic of)",
    "ZWE": "Zimbabwe (Republic of)",
    "TUN": "Tunisia",
    "LBY": "Libya",
}

MODEL = "claude-opus-5"


def key_figures(f: pd.Series) -> list[str]:
    return [str(f["notice_id"]), f"{f['pfd_dbw_m2']:.1f}", f"{f['pfd_limit_dbw_m2']:.1f}",
            f"{f['margin_of_exceedance']:.1f}", str(f["frequency_mhz"])]


def template_letter(f: pd.Series, ific: int) -> str:
    adm = ADMINISTRATIONS.get(f["country"], f["country"])
    related = f" (and related notice {f['related_notice_id']})" if str(f["related_notice_id"]) not in ("", "nan") else ""
    orbit = (f"geostationary orbit at {f['gso_longitude']:.1f}°" if f["orbit"] == "GSO"
             else f"non-geostationary orbit, altitude {f['altitude_km']:.0f} km")
    ids = [x for x in str(f["finnish_station_id"]).split(";") if x and x != "nan"]
    stations = ("none in the special-interest list" if not ids else
                ", ".join(ids[:12]) + (f" (+{len(ids) - 12} more, see report)" if len(ids) > 12 else ""))
    coord = f["result_class"] == "COORDINATION REQUIRED"
    basis = ("the coordination threshold of RR Appendix 5, so that coordination with Finland is required\n"
             "under No. 9.14 of the Radio Regulations" if coord else
             "the limits of Article 21, Table 21-4 of the\nRadio Regulations")
    subject = "RR No. 9.14 coordination threshold exceeded" if coord else "exceeding RR Table 21-4"
    limit_label = "App. 5 threshold:" if coord else "Table 21-4 limit:"
    request = ("effect coordination with the Administration of Finland under RR No. 9.14 before the\n"
               "     assignments are notified" if coord else
               "take the necessary measures (e.g. pfd masks, beam pointing restrictions) to ensure\n"
               "     compliance with RR No. 21.16 and Table 21-4, so that Finnish services are protected")
    return f"""DRAFT FOR EXPERT REVIEW - not for dispatch without technical verification

From: Finnish Transport and Communications Agency Traficom, Spectrum and Space
To:   Administration of {adm} ({f['country']})
Date: {date.today().isoformat()}
Subject: BR IFIC {ific} - satellite network {f['network_name']}, notice {f['notice_id']}{related}:
         power flux-density over Finnish territory - {subject}

Dear colleagues,

The Administration of Finland has examined the {f['notice_type']} information for the satellite
network {f['network_name']} published in BR IFIC (Space Services) No. {ific}. Our analysis
indicates that the notified characteristics may produce a power flux-density (pfd) at the
Earth's surface over Finnish territory that exceeds {basis}.

Technical summary
  Notice / network:           {f['notice_id']} / {f['network_name']}
  Orbit:                      {orbit}
  Assignment:                 {f['frequency_mhz']} MHz, beam {f['beam_name']}, emission {f['emission']}
  Service (criterion row):    {f['service']} ({f['table21_4_row']})
  Regulation:                 {f['rr_reference']}
  {('e.i.r.p. in ' + f['reference_bandwidth'] + ':'):<28}{f['eirp_ref_dbw']:.1f} dBW ({f['power_source']})
  Worst-case geometry:        elevation {f['elevation_deg']:.1f}°, slant range {f['distance_km']:.0f} km
  Calculated pfd:             {f['pfd_dbw_m2']:.1f} dB(W/m²) in {f['reference_bandwidth']}
  {limit_label:<28}{f['pfd_limit_dbw_m2']:.1f} dB(W/m²) in {f['reference_bandwidth']}
  Exceedance:                 {f['margin_of_exceedance']:.1f} dB
  Finnish services (M4):      {f['m4_service'] or 'n/a'}
  Finnish stations in band:   {stations}

Method: pfd = e.i.r.p. - 10·log10(4πd²), evaluated over Finnish territory at every reachable
angle of arrival; the limit is the angle-dependent value of the cited regulation. The calculation assumes
peak antenna gain toward Finland and is therefore a worst case.

In view of the above, the Administration of Finland kindly requests that you
  1. confirm the pfd values produced by {f['network_name']} over the territory of Finland, and
  2. {request}.

The Administration of Finland reserves its rights with respect to protection of its
terrestrial and space services from harmful interference.

Yours faithfully,
Traficom - Spectrum and Space
"""


def polish(draft: str, figures: list[str]) -> str | None:
    """Ask Claude to improve the prose; return None if unavailable or figures changed."""
    try:
        import anthropic
    except ImportError:
        return None
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=("You edit formal ITU coordination correspondence between spectrum administrations. "
                    "Improve clarity and diplomatic tone only. Keep every number, identifier, unit, "
                    "frequency and the 'DRAFT FOR EXPERT REVIEW' header exactly as given. Do not add "
                    "facts, limits or regulatory conclusions that are not in the draft. Return only the letter."),
            messages=[{"role": "user", "content": draft}],
        )
    except anthropic.APIError as exc:
        print(f"  polish skipped: {type(exc).__name__}")
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text if all(fig in text for fig in figures) else None


def terrestrial_letter(f: pd.Series, ific: int) -> str:
    """Draft for a foreign land-mobile assignment exceeding the T/R 25-08 trigger at the Finnish border."""
    adm = ADMINISTRATIONS.get(f["country"], f["country"])
    coord = str(f.get("coordination_with_fin", "") or "")
    coord_text = (f"\nThe ITU records list the following coordination status with Finland for this assignment:\n"
                  f"  {coord}\nFinland will verify this status against its national frequency register.\n"
                  if coord and coord != "nan" else "")
    stations = str(f.get("finnish_station_id", "") or "")
    stations = stations if stations and stations != "nan" else "none in the special-interest list above the trigger"
    return f"""DRAFT FOR EXPERT REVIEW - not for dispatch without technical verification

From: Finnish Transport and Communications Agency Traficom, Spectrum and Space
To:   Administration of {adm} ({f['country']})
Date: {date.today().isoformat()}
Subject: BR IFIC (Terrestrial Services) No. {ific} - land-mobile assignment {f['notice_id']}
         ({f['site_name']}, {f['frequency_mhz']} MHz): field strength at the Finnish border

Dear colleagues,

The Administration of Finland has examined the {str(f['change_type']).lower()} land-mobile assignment
{f['notice_id']} (station class {f['station_class']}) published in BR IFIC (Terrestrial Services)
No. {ific}. Our calculation indicates that the field strength produced on Finnish territory
exceeds the coordination trigger of CEPT Recommendation T/R 25-08 (Annex 1, A1.2.3).

Technical summary
  Assignment:                 {f['notice_id']} (adm. ref. {f['adm_ref_id']}), {f['frequency_mhz']} MHz, {f['bandwidth_khz']} kHz, {f['emission']}
  Transmitter site:           {f['site_name']}, {f['latitude']:.4f} N {f['longitude']:.4f} E
  e.r.p. / antenna height:    {f['erp_dbw']:.1f} dBW / {f['tx_height_m']:.0f} m
  Worst point in Finland:     {f['worst_point_type']}, {f['worst_point_lat']:.4f} N {f['worst_point_lon']:.4f} E ({f['distance_km']:.1f} km)
  Path:                       {f['path']}
  Model:                      {f['model']}
  Calculated field strength:  {f['field_strength_dbuv_m']:.1f} dB(uV/m)
  T/R 25-08 trigger:          {f['threshold_dbuv_m']:.1f} dB(uV/m)
  Exceedance:                 {f['margin_db']:.1f} dB
  Finnish stations affected:  {stations}
{coord_text}
In view of the above, the Administration of Finland kindly requests that you
  1. confirm the technical characteristics of the assignment, and
  2. coordinate the assignment with the Administration of Finland before bringing it into use,
     or reduce the field strength at the border below the T/R 25-08 trigger.

The Administration of Finland reserves its rights with respect to protection of its
radiocommunication services from harmful interference.

Yours faithfully,
Traficom - Spectrum and Space
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, default=Path("space_3074_report.csv"))
    parser.add_argument("--ific-number", type=int, default=3074)
    parser.add_argument("--out-dir", type=Path, default=Path("letters"))
    parser.add_argument("--terrestrial-report", type=Path, default=Path("terrestrial_3076_report.csv"))
    parser.add_argument("--terrestrial-ific", type=int, default=3076)
    parser.add_argument("--polish", action="store_true", help="Polish prose with Claude (needs credentials)")
    args = parser.parse_args()

    findings = pd.read_csv(args.report, dtype={"notice_id": str, "related_notice_id": str})
    args.out_dir.mkdir(exist_ok=True)
    for _, f in findings.iterrows():
        draft = template_letter(f, args.ific_number)
        source = "template"
        if args.polish:
            polished = polish(draft, key_figures(f))
            if polished:
                draft, source = polished, "template + Claude polish (figures verified)"
        path = args.out_dir / f"{f['country']}_{f['notice_id']}_{str(f['network_name']).replace('/', '-')}.txt"
        path.write_text(draft, encoding="utf-8")
        print(f"{path}  [{source}]")
    print(f"{len(findings)} draft letters in {args.out_dir}/")

    if args.terrestrial_report.exists():
        terra = pd.read_csv(args.terrestrial_report, dtype={"notice_id": str, "adm_ref_id": str})
        for _, f in terra.iterrows():
            path = args.out_dir / f"T_{f['country']}_{f['notice_id']}.txt"
            path.write_text(terrestrial_letter(f, args.terrestrial_ific), encoding="utf-8")
        print(f"{len(terra)} terrestrial draft letters in {args.out_dir}/")


if __name__ == "__main__":
    main()
