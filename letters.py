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
import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.request
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

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")   # override with --model or ANTHROPIC_MODEL
LOCAL_MODEL = os.environ.get("LETTER_AI_MODEL", "llama3.2:3b")  # local model served by Ollama
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
AI_NOTE = ("Note: the opening paragraph was drafted by a local language model ({model}) from the calculated "
           "figures and\nchecked automatically; every number in it matches the technical summary below.")


def key_figures(f: pd.Series) -> list[str]:
    return [str(f["notice_id"]), f"{f['pfd_dbw_m2']:.1f}", f"{f['pfd_limit_dbw_m2']:.1f}",
            f"{f['margin_of_exceedance']:.1f}", str(f["frequency_mhz"])]


def template_letter(f: pd.Series, ific: int, intro: str | None = None, ai_model: str = "") -> str:
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
    opening = (f"""The Administration of Finland has examined the {f['notice_type']} information for the satellite
network {f['network_name']} published in BR IFIC (Space Services) No. {ific}. Our analysis
indicates that the notified characteristics may produce a power flux-density (pfd) at the
Earth's surface over Finnish territory that exceeds {basis}.""" if not intro else _wrap(intro))
    return f"""DRAFT FOR EXPERT REVIEW - not for dispatch without technical verification
{chr(10) + AI_NOTE.format(model=ai_model) + chr(10) if intro else ""}
From: Finnish Transport and Communications Agency Traficom, Spectrum and Space
To:   Administration of {adm} ({f['country']})
Date: {date.today().isoformat()}
Subject: BR IFIC {ific} - satellite network {f['network_name']}, notice {f['notice_id']}{related}:
         power flux-density over Finnish territory - {subject}

Dear colleagues,

{opening}

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


def polish(draft: str, figures: list[str], model: str = MODEL) -> str | None:
    """Ask Claude to improve the prose; return None (with the reason printed) if unavailable or figures changed."""
    try:
        import anthropic
    except ImportError:
        print("  polish skipped: the anthropic package is not installed (pip install anthropic)", file=sys.stderr)
        return None
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=("You edit formal ITU coordination correspondence between spectrum administrations. "
                    "Improve clarity and diplomatic tone only. Keep every number, identifier, unit, "
                    "frequency and the 'DRAFT FOR EXPERT REVIEW' header exactly as given. Do not add "
                    "facts, limits or regulatory conclusions that are not in the draft. Return only the letter."),
            messages=[{"role": "user", "content": draft}],
        )
    except anthropic.AuthenticationError as exc:
        print(f"  polish skipped: authentication failed - check ANTHROPIC_API_KEY ({exc.message})", file=sys.stderr)
        return None
    except anthropic.NotFoundError as exc:
        print(f"  polish skipped: model {model!r} not found - use --model ({exc.message})", file=sys.stderr)
        return None
    except anthropic.RateLimitError as exc:
        print(f"  polish skipped: rate limited after the SDK's automatic retries ({exc.message})", file=sys.stderr)
        return None
    except anthropic.APIStatusError as exc:
        print(f"  polish skipped: API error {exc.status_code}: {exc.message}", file=sys.stderr)
        return None
    except anthropic.APIConnectionError as exc:
        print(f"  polish skipped: cannot reach the API ({exc})", file=sys.stderr)
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        print(f"  polish skipped: stop_reason {response.stop_reason}", file=sys.stderr)
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    missing = [fig for fig in figures if fig not in text]
    if missing:
        print(f"  polish rejected: figures changed or missing {missing}; template kept", file=sys.stderr)
        return None
    return text


def _wrap(text: str) -> str:
    return "\n".join(textwrap.wrap(" ".join(text.split()), width=95))


def letter_facts(f: pd.Series, kind: str, ific: int) -> dict:
    """The only facts the language model may use (all figures come from the calculation)."""
    adm = ADMINISTRATIONS.get(f["country"], f["country"])
    if kind == "space":
        return {"recipient": f"Administration of {adm}", "ific": f"BR IFIC (Space Services) No. {ific}",
                "network": f["network_name"], "notice": str(f["notice_id"]), "notice type": f["notice_type"],
                "frequency band MHz": f["frequency_mhz"], "finding": f["result_class"].lower(),
                "calculated pfd dB(W/m2)": f"{f['pfd_dbw_m2']:.1f}", "limit dB(W/m2)": f"{f['pfd_limit_dbw_m2']:.1f}",
                "reference bandwidth": f["reference_bandwidth"], "exceedance dB": f"{f['margin_of_exceedance']:.1f}",
                "regulation": f["rr_reference"], "affected Finnish services": f["m4_service"] or "n/a"}
    return {"recipient": f"Administration of {adm}", "ific": f"BR IFIC (Terrestrial Services) No. {ific}",
            "station": f["site_name"], "assignment": str(f["notice_id"]), "station class": f["station_class"],
            "frequency MHz": f["frequency_mhz"], "calculated field strength dB(uV/m)": f"{f['field_strength_dbuv_m']:.1f}",
            "trigger dB(uV/m)": f"{f['threshold_dbuv_m']:.1f}", "exceedance dB": f"{f['margin_db']:.1f}",
            "distance to Finnish territory km": f"{f['distance_km']:.1f}",
            "criterion": "CEPT Recommendation T/R 25-08, Annex 1, A1.2.3"}


def check_paragraph(text: str, facts: dict) -> str | None:
    """Reason to reject an AI paragraph, or None if it is acceptable: every number must come from the facts."""
    allowed = set()
    for value in [*facts.keys(), *facts.values()]:   # names carry units such as dB(W/m2)
        for n in NUMBER.findall(str(value)):
            allowed |= {n, n.replace(",", "."), n.split(".")[0]}
    invented = sorted({n for n in NUMBER.findall(text) if n.replace(",", ".") not in allowed})
    if invented:
        return f"numbers not in the calculation: {invented}"
    if not 150 <= len(text) <= 1200:
        return f"length {len(text)} outside 150-1200 characters"
    if any(mark in text for mark in ("[", "{", "<", "Dear", "Yours")):
        return "contains placeholders or letter framing"
    return None


def ai_intro(facts: dict, model: str = LOCAL_MODEL, url: str = OLLAMA_URL) -> tuple[str | None, str]:
    """Opening paragraph drafted by a local model via Ollama; (paragraph, status). Never raises."""
    prompt = ("Write the opening paragraph (3-4 sentences, formal and courteous) of a letter from the "
              "Administration of Finland to the recipient below about a frequency assignment published in the "
              "ITU BR IFIC. Explain in plain language what was found and why it concerns Finland. Use ONLY these "
              "facts; copy any number exactly as written and do not introduce any other number, date or claim. "
              "Do not write a greeting, signature, list or request - only the paragraph.\n\nFacts:\n"
              + "\n".join(f"- {k}: {v}" for k, v in facts.items()))
    body = json.dumps({"model": model, "stream": False, "options": {"temperature": 0.2, "num_predict": 300},
                       "messages": [{"role": "system", "content": "You draft formal correspondence between national "
                                     "radio spectrum administrations. You never invent figures."},
                                    {"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(f"{url}/api/chat", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            text = json.load(response)["message"]["content"].strip()
    except (OSError, ValueError, KeyError) as exc:  # server down, model missing, bad JSON
        return None, f"local AI unavailable ({type(exc).__name__}: {exc}) - template kept"
    reason = check_paragraph(text, facts)
    if reason:
        return None, f"AI paragraph rejected ({reason}) - template kept"
    return text, f"AI paragraph accepted ({model}, figures verified)"


def make_letter(f: pd.Series, kind: str, ific: int, ai: bool = False, model: str = LOCAL_MODEL) -> tuple[str, str]:
    """(letter text, how it was produced)."""
    build = template_letter if kind == "space" else terrestrial_letter
    if not ai:
        return build(f, ific), "template"
    intro, status = ai_intro(letter_facts(f, kind, ific), model)
    return build(f, ific, intro, model), status


def terrestrial_letter(f: pd.Series, ific: int, intro: str | None = None, ai_model: str = "") -> str:
    """Draft for a foreign land-mobile assignment exceeding the T/R 25-08 trigger at the Finnish border."""
    adm = ADMINISTRATIONS.get(f["country"], f["country"])
    coord = str(f.get("coordination_with_fin", "") or "")
    coord_text = (f"\nThe ITU records list the following coordination status with Finland for this assignment:\n"
                  f"  {coord}\nFinland will verify this status against its national frequency register.\n"
                  if coord and coord != "nan" else "")
    stations = str(f.get("finnish_station_id", "") or "")
    stations = stations if stations and stations != "nan" else "none in the special-interest list above the trigger"
    opening = (f"""The Administration of Finland has examined the {str(f['change_type']).lower()} land-mobile assignment
{f['notice_id']} (station class {f['station_class']}) published in BR IFIC (Terrestrial Services)
No. {ific}. Our calculation indicates that the field strength produced on Finnish territory
exceeds the coordination trigger of CEPT Recommendation T/R 25-08 (Annex 1, A1.2.3).""" if not intro else _wrap(intro))
    return f"""DRAFT FOR EXPERT REVIEW - not for dispatch without technical verification
{chr(10) + AI_NOTE.format(model=ai_model) + chr(10) if intro else ""}
From: Finnish Transport and Communications Agency Traficom, Spectrum and Space
To:   Administration of {adm} ({f['country']})
Date: {date.today().isoformat()}
Subject: BR IFIC (Terrestrial Services) No. {ific} - land-mobile assignment {f['notice_id']}
         ({f['site_name']}, {f['frequency_mhz']} MHz): field strength at the Finnish border

Dear colleagues,

{opening}

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
    parser.add_argument("--report", type=Path, default=Path("runs/space_3074/alerts.csv"))
    parser.add_argument("--ific-number", type=int, default=3074)
    parser.add_argument("--out-dir", type=Path, default=Path("letters"), help="Where to write the drafts (the pipeline writes them to runs/<kind>_<ific>/letters/)")
    parser.add_argument("--terrestrial-report", type=Path, default=Path("runs/terrestrial_3076/alerts.csv"))
    parser.add_argument("--terrestrial-ific", type=int, default=3076)
    parser.add_argument("--model", default=MODEL, help="Claude model for --polish (default %(default)s)")
    parser.add_argument("--polish", action="store_true", help="Polish prose with Claude (needs credentials)")
    parser.add_argument("--ai", action="store_true",
                        help="Opening paragraph by a local model via Ollama (figures verified; template on failure)")
    parser.add_argument("--ai-model", default=LOCAL_MODEL, help="Ollama model for --ai (default %(default)s)")
    args = parser.parse_args()

    findings = pd.read_csv(args.report, dtype={"notice_id": str, "related_notice_id": str})
    args.out_dir.mkdir(exist_ok=True)
    for _, f in findings.iterrows():
        draft, source = make_letter(f, "space", args.ific_number, args.ai, args.ai_model)
        if args.polish:
            polished = polish(draft, key_figures(f), args.model)
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
            text, source = make_letter(f, "terrestrial", args.terrestrial_ific, args.ai, args.ai_model)
            path.write_text(text, encoding="utf-8")
            print(f"{path}  [{source}]")
        print(f"{len(terra)} terrestrial draft letters in {args.out_dir}/")


if __name__ == "__main__":
    main()
