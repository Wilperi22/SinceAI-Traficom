"""Verification suite for the space and terrestrial scans.

Run from the repository root:  .venv/bin/python -m unittest -v tests.test_pipeline

Three kinds of checks:
  1. physics/unit tests against textbook reference values;
  2. independent re-calculation of real findings straight from the IFIC databases;
  3. invariants on the published CSVs that catch known failure modes (deep-space
     probes, invisible satellites, data typos, duplicate rows, double-counted gain).
"""

from __future__ import annotations

import json
import math
import sqlite3
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

import sys  # noqa: E402
sys.path.insert(0, str(ROOT))

import inspection_engine as ie  # noqa: E402
import terrestrial as tr  # noqa: E402

SPACE_DB = ROOT / "ific3074.sqlite"
TERRA_DB = ROOT / "brific3076.sqlite"
SPACE_RUN = ROOT / "runs" / "space_3074"          # written by pipeline.py
TERRA_RUN = ROOT / "runs" / "terrestrial_3076"
SPACE_REPORT = SPACE_RUN / "alerts.csv"
SPACE_CONCERNS = SPACE_RUN / "concerns.csv"
TERRA_REPORT = TERRA_RUN / "alerts.csv"
EXCEL = ROOT / "Traficom materials" / "Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx"


class SpacePhysics(unittest.TestCase):
    def test_spreading_loss_reference(self):
        # 10log10(4*pi*(1000 m)^2) = 70.99 dB; 35786 km -> 162.07 dB
        self.assertAlmostEqual(ie.spreading_loss_db(1.0), 70.99, places=2)
        self.assertAlmostEqual(ie.spreading_loss_db(35786.0), 162.07, places=2)

    def test_gso_subsatellite_point(self):
        el, d = ie.gso_geometry(10.0, np.array([[0.0, 10.0]]))
        self.assertAlmostEqual(el[0], 90.0, places=6)
        self.assertAlmostEqual(d[0], ie.GSO_RADIUS_KM - ie.EARTH_RADIUS_KM, places=3)

    def test_gso_visibility_limit_81_3_deg(self):
        # A GSO satellite at the same longitude sets at ~81.3 deg latitude.
        el, _ = ie.gso_geometry(0.0, np.array([[81.3, 0.0], [82.0, 0.0]]))
        self.assertAlmostEqual(el[0], 0.0, delta=0.1)
        self.assertLess(el[1], 0.0)

    def test_gso_helsinki_elevation(self):
        # Helsinki to a satellite on its own meridian: textbook ~21.8 deg, ~39,300 km.
        el, d = ie.gso_geometry(24.94, np.array([[60.17, 24.94]]))
        self.assertAlmostEqual(el[0], 21.8, delta=0.1)
        self.assertAlmostEqual(d[0], 39300, delta=100)

    def test_ngso_slant_range(self):
        h = 550.0
        self.assertAlmostEqual(ie.ngso_range_km(h, np.array([90.0]))[0], h, places=6)
        horizon = math.sqrt((ie.EARTH_RADIUS_KM + h) ** 2 - ie.EARTH_RADIUS_KM ** 2)
        self.assertAlmostEqual(ie.ngso_range_km(h, np.array([0.0]))[0], horizon, places=3)

    def test_ngso_never_visible_equatorial(self):
        # 4 deg inclination at 700 km (NUSANTARA-like) never rises over Finland (>= 59.8 N).
        self.assertLess(ie.ngso_max_elevation(700.0, 4.0, 59.8), 0.0)
        self.assertAlmostEqual(ie.ngso_max_elevation(550.0, 97.5, 59.8), 90.0, places=6)

    def test_table21_4_interpolation(self):
        limits = ie.load_limits(ROOT / "pfd.json").set_index("row_id")
        row = limits.loc["3400_4200_FSS_GSO"]
        f = lambda a: float(np.interp(a, row["angles"], row["values"]))  # noqa: E731
        self.assertEqual(f(3), -152.0)
        self.assertAlmostEqual(f(15), -152 + 0.5 * (15 - 5))  # RR formula -152 + 0.5(d-5)
        self.assertEqual(f(40), -142.0)

    def test_limit_register_sane(self):
        data = {k: v for k, v in json.loads((ROOT / "pfd.json").read_text()).items() if not k.startswith("_")}
        for key, row in data.items():
            angles = [p[0] for p in row["curve"]]
            self.assertEqual(angles, sorted(angles), key)
            self.assertEqual((angles[0], angles[-1]), (0, 90), key)
            self.assertLess(row["freq_min_mhz"], row["freq_max_mhz"], key)
            self.assertIn(row["reference_bandwidth_hz"], (4000, 1000000, 1500000), key)
        self.assertEqual(data["10700_12500_FSS_NGSO_HEO"]["heo"], True)          # RR fn 24 (21.16.17)
        self.assertEqual(data["37500_40000_FSS_MSS_NGSO"]["max_satellites"], 99)  # RR fn 11 (21.16.4)
        self.assertEqual(data["AP5_2483.5_2500_MSS_NGSO"]["curve"][0][1], -124.5)  # App. 5 NOTE 9 lists Finland

    def test_overlaps_any_matches_brute_force(self):
        rng = np.random.default_rng(1)
        lo = rng.uniform(0, 100, 500); hi = lo + rng.uniform(0, 5, 500)
        a = rng.uniform(0, 100, 80); b = a + rng.uniform(0, 3, 80)
        brute = ((a[None, :] < hi[:, None]) & (b[None, :] > lo[:, None])).any(axis=1)
        self.assertTrue((ie.overlaps_any(lo, hi, a, b) == brute).all())
        self.assertFalse(ie.overlaps_any(np.array([10.0]), np.array([20.0]), np.array([20.0]), np.array([30.0]))[0])  # touching

    def test_terrestrial_m4_excludes_satellite_services(self):
        """GitHub issue #1: 'MOBILE-SATELLITE' etc. must not count as terrestrial Finnish use."""
        services = pd.Series(["MOBILE-SATELLITE (SPACE-TO-EARTH)", "FIXED SATELLITE (EARTH-TO-SPACE, SPACE-TO-EARTH)",
                              "AMATEUR-SATELLITE", "RADIONAVIGATION-SATELLITE (SPACE-TO-EARTH)",
                              "AERONAUTICAL MOBILE-SATELLITE (R)", "FIXED", "MOBILE EXCEPT AERONAUTICAL MOBILE",
                              "BROADCASTING", "METEOROLOGICAL AIDS", "RADIO ASTRONOMY"])
        self.assertEqual(list(ie.is_terrestrial_m4(services)),
                         [False, False, False, False, False, True, True, True, True, False])
        m4 = ie.load_m4(ROOT / "m4.json")
        terr = m4.loc[ie.is_terrestrial_m4(m4["Services_in_Finland"]), "Services_in_Finland"]
        self.assertFalse(terr.str.upper().str.contains("SATELLITE").any())

    def test_bandwidth_parsers(self):
        self.assertEqual(ie.emission_bandwidth_hz("2K70J3E"), 2700.0)
        self.assertEqual(ie.emission_bandwidth_hz("36M0G7W"), 36e6)
        self.assertEqual(ie.emission_bandwidth_hz("100KN0N--"), 100e3)
        self.assertAlmostEqual(ie.parse_bandwidth_mhz("7,6k"), 0.0076)
        self.assertEqual(ie.parse_bandwidth_mhz("8M00"), 8.0)
        self.assertAlmostEqual(ie.parse_bandwidth_mhz("118600k"), 118.6)
        self.assertEqual(ie.parse_bandwidth_mhz("300.000 kHz"), 0.3)


class SpaceFindings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = pd.read_csv(SPACE_REPORT)

    def test_hand_calculation_csn_sso(self):
        """Recompute CSN-SSO (124545403) from raw IFIC rows, independent of the engine."""
        row = self.report.set_index("notice_id").loc[124545403]
        with sqlite3.connect(SPACE_DB) as con:
            rows = con.execute("""
                SELECT e.pwr_ds_max, e.design_emi, b.gain FROM grp g JOIN emiss e USING (grp_id)
                JOIN s_beam b ON b.ntc_id = g.ntc_id AND b.beam_name = g.beam_name AND b.emi_rcp = 'E'
                WHERE g.ntc_id = 124545403 AND g.emi_rcp = 'E' AND g.beam_name = ?""", (row["beam_name"],)).fetchall()
        # e.i.r.p. in 1 MHz = max over emissions of pwr_ds_max + 10log(min(1 MHz, BW)) + gain
        eirp = max(p + 10 * math.log10(min(1e6, ie.emission_bandwidth_hz(e) or 1e6)) + g for p, e, g in rows)
        self.assertAlmostEqual(eirp, row["eirp_ref_dbw"], delta=0.05)
        # law of cosines slant range at the reported elevation, altitude 592 km
        R, h, el = 6378.137, row["altitude_km"], math.radians(row["elevation_deg"])
        d = math.sqrt((R + h) ** 2 - (R * math.cos(el)) ** 2) - R * math.sin(el)
        pfd = eirp - 10 * math.log10(4 * math.pi * (d * 1000) ** 2)
        self.assertAlmostEqual(pfd, row["pfd_dbw_m2"], delta=0.05)
        # Table 21-4 3400-4200 MHz non-GSO: -138 dB(W/m2)/MHz at 0-5 deg
        self.assertAlmostEqual(row["pfd_limit_dbw_m2"], -138.0, delta=0.05)
        self.assertAlmostEqual(row["margin_of_exceedance"], pfd + 138.0, delta=0.05)

    def test_notified_eirp_not_double_counted(self):
        """s_beam.pwr_max_4k equals pwr_ds_max + 36 dB + gain, i.e. it is already e.i.r.p."""
        with sqlite3.connect(SPACE_DB) as con:
            p4k, gain, pds = con.execute("""
                SELECT b.pwr_max_4k, b.gain, MAX(e.pwr_ds_max) FROM s_beam b
                JOIN grp g ON g.ntc_id = b.ntc_id AND g.beam_name = b.beam_name AND g.emi_rcp = 'E'
                JOIN emiss e USING (grp_id)
                WHERE b.ntc_id = 117520318 AND b.beam_name = 'DLDEM' AND b.emi_rcp = 'E'""").fetchone()
        self.assertAlmostEqual(p4k, pds + 10 * math.log10(4000) + gain, delta=0.1)
        notified = self.report[self.report["power_source"].str.startswith("s_beam")]
        self.assertTrue((notified["calculated_power_dbw"] < notified["eirp_ref_dbw"]).all()
                        or notified.empty)

    def test_no_deep_space_or_invisible_or_typo_notices(self):
        excluded = {121500057, 326500032,                      # JUICE (Jupiter)
                    126545072, 126545073, 126545074, 126545075, 126545076, 125545391,  # lunar
                    124520197, 324520197,                      # NUSANTARA, 4 deg inclination
                    }
        self.assertFalse(excluded & set(self.report["notice_id"]))

    def test_rr_footnote_11_large_constellations(self):
        """RR 21.16.4: 37.5-42.5 GHz NGSO limits only for systems of <= 99 satellites."""
        concerns = pd.read_csv(SPACE_CONCERNS).set_index("notice_id")
        self.assertEqual(concerns.loc[125520047, "result_class"], "LIMIT_NOT_APPLICABLE")   # 3212 satellites
        ngso_mmw = self.report[self.report["table21_4_row"].str.contains("NGSO") &
                               self.report["frequency_mhz"].str.startswith(("375", "405", "420"))]
        with sqlite3.connect(SPACE_DB) as con:
            for ntc in ngso_mmw["notice_id"]:
                n = con.execute("SELECT SUM(nbr_sat_pl) FROM orbit WHERE ntc_id = ?", (int(ntc),)).fetchone()[0]
                self.assertLessEqual(n or 0, 99, ntc)

    def test_rr_footnote_18_sar_mean_pfd(self):
        """RR 21.16.8: PAZ_2-A EESS (active) uses mean power = pep x tau x PRF over Bc."""
        row = self.report.set_index("notice_id").loc[124520174]
        self.assertIn("21.16.8", row["power_source"])
        with sqlite3.connect(SPACE_DB) as con:
            pep, tau, prf, gain = con.execute("""
                SELECT e.pep_max, e.pulse_length, e.pulse_rep, b.gain FROM grp g JOIN emiss e USING (grp_id)
                JOIN s_beam b ON b.ntc_id = g.ntc_id AND b.beam_name = g.beam_name AND b.emi_rcp = 'E'
                WHERE g.ntc_id = 124520174 AND g.beam_name = ? AND e.pulse_length IS NOT NULL""",
                (row["beam_name"],)).fetchone()
        bc_mhz = 10400 - 9900
        eirp = pep + 10 * math.log10(tau * prf * 1e-3) + 10 * math.log10(1 / bc_mhz) + gain
        self.assertAlmostEqual(eirp, row["eirp_ref_dbw"], delta=0.05)

    def test_lvi_typo_bounded_by_pep(self):
        """LVI pwr_ds_max = +73 dBW/Hz (sign typo): density must come from pep_max, never 109 dBW."""
        allrows = pd.concat([self.report, pd.read_csv(SPACE_CONCERNS)])
        lvi = allrows[allrows["notice_id"] == 126545057]
        self.assertTrue((lvi["calculated_power_dbw"] < 0).all())

    def test_itu_bureau_findings_cross_check(self):
        """Every network the BR found unfavourable under 21.16 is either an alert or explained as a concern."""
        with sqlite3.connect(SPACE_DB) as con:
            x = {r[0] for r in con.execute("""SELECT DISTINCT g.ntc_id FROM grp g JOIN fdg_ref f USING (grp_id)
                                               WHERE f.fdg_prov LIKE '%X/21.16%'""")}
        concerns = pd.read_csv(SPACE_CONCERNS)
        covered = set(self.report["notice_id"]) | set(concerns["notice_id"])
        self.assertTrue(x <= covered, x - covered)
        self.assertIn("X/21.16", self.report.set_index("notice_id").loc[124520184, "br_finding"])  # CHINASAT-G agrees

    def test_priority_rules_and_workbook(self):
        r = self.report
        self.assertTrue(r["priority"].isin(["High", "Medium", "Low"]).all())
        rank = r["priority"].map({"High": 0, "Medium": 1, "Low": 2})
        self.assertTrue(rank.is_monotonic_increasing, "alerts must be sorted by priority")
        self.assertTrue((r.loc[r["margin_of_exceedance"] >= 10, "priority"] == "High").all())
        low = r[r["priority"] == "Low"]
        self.assertTrue((low["margin_of_exceedance"] < 3).all() and low["finnish_station_id"].isna().all())
        book = pd.read_excel(SPACE_RUN / "space_3074_results.xlsx", sheet_name=None)
        self.assertEqual(list(book), ["Summary", "Alerts", "Concerns", "Method"])
        self.assertEqual(len(book["Alerts"]), len(r))

    def test_report_invariants(self):
        r = self.report
        self.assertTrue((r["margin_of_exceedance"] >= ie.MIN_EXCEEDANCE_MARGIN_DB).all())
        self.assertTrue(r["rr_reference"].str.contains("Table 21-4|App. 5").all())
        self.assertTrue(r["result_class"].isin(["EXCEEDS LIMIT", "COORDINATION REQUIRED"]).all())
        ap5 = r[r["result_class"] == "COORDINATION REQUIRED"]
        self.assertTrue(ap5["table21_4_row"].str.startswith("AP5_").all())
        self.assertTrue((r["elevation_deg"] >= 0).all())
        self.assertTrue(r["notice_id"].is_unique)
        related = {int(float(x)) for v in r["related_notice_id"].dropna() for x in str(v).split(";") if x}
        self.assertFalse(related & set(r["notice_id"]), "a network appears twice (notice + its modification)")
        limits = set(ie.load_limits(ROOT / "pfd.json")["row_id"])
        self.assertTrue(set(r["table21_4_row"]) <= limits)
        recomputed = r["pfd_dbw_m2"] - r["pfd_limit_dbw_m2"]
        self.assertTrue(np.allclose(recomputed, r["margin_of_exceedance"], atol=0.02))


class TerrestrialPhysics(unittest.TestCase):
    def test_free_space_reference(self):
        # P.1546: 1 kW e.r.p. free-space field 106.9 - 20log(d) dB(uV/m)
        self.assertAlmostEqual(106.9 - 20 * math.log10(10), 86.9)

    @unittest.skipIf(tr.P1546 is None, "Py1546 not installed")
    def test_p1546_bounded_by_free_space_and_monotonic(self):
        for f in (150.0, 450.0, 800.0):
            e = [tr.field_strength_1kw(f, 30.0, [d], ["Land"]) for d in (1, 10, 50, 100, 300, 1000)]
            for d, val in zip((1, 10, 50, 100, 300, 1000), e):
                self.assertLessEqual(val, 106.9 - 20 * math.log10(d) + 0.01, (f, d))
            self.assertEqual(e, sorted(e, reverse=True), f)

    @unittest.skipIf(tr.P1546 is None, "Py1546 not installed")
    def test_short_low_sea_path_does_not_crash(self):
        """Terrestrial IFIC 3075: 14.6 km all-sea path from a 1.5 m mobile crashed Py1546 (h1 None)."""
        e = tr.field_strength_1kw(148.575, 1.5, [14.583], ["Cold"])
        self.assertTrue(np.isfinite(e))
        self.assertAlmostEqual(e, tr.field_strength_1kw(148.575, 3.0, [14.583], ["Cold"]), places=6)

    @unittest.skipIf(tr.P1546 is None, "Py1546 not installed")
    def test_cold_sea_propagates_further_than_land(self):
        land = tr.field_strength_1kw(450.0, 30.0, [100.0], ["Land"])
        sea = tr.field_strength_1kw(450.0, 30.0, [100.0], ["Cold"])
        self.assertGreater(sea, land)

    def test_erp_conversion(self):
        ant = pd.DataFrame({"pwr_dbw": [10.0, 10.0, 10.0], "pwr_eiv": ["E", "I", None],
                            "gain_max": [8.0, 8.0, 8.0], "gain_type": ["I", "I", "I"]})
        self.assertEqual(list(tr.erp_dbw(ant).round(2)), [10.0, 7.85, 15.85])

    def test_haversine(self):
        # Helsinki - Tallinn ~ 80 km
        self.assertAlmostEqual(tr.distance_km(60.1699, 24.9384, np.array([59.4370]), np.array([24.7536]))[0],
                               82.0, delta=2.0)


class TerrestrialFindings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = pd.read_csv(TERRA_REPORT)
        b = pd.read_csv(tr.DEFAULT_BORDER_FILE)
        cls.border = b[b["type"] == "land border"][["lat", "lon"]].to_numpy()

    def test_border_geometry_ruskeala(self):
        # Ruskeala (61.95 N, 30.59 E) is ~15-17 km from the Finnish border (map check).
        d = tr.distance_km(61.95333, 30.58639, self.border[:, 0], self.border[:, 1]).min()
        self.assertTrue(12 <= d <= 20, d)

    def test_land_mask(self):
        self.assertTrue(tr.is_sea(np.array([59.9]), np.array([25.0]))[0])    # Gulf of Finland
        self.assertFalse(tr.is_sea(np.array([62.0]), np.array([26.0]))[0])   # inland Finland

    @unittest.skipIf(tr.P1546 is None, "Py1546 not installed")
    def test_hand_calculation_kostomuksha(self):
        row = self.report.set_index("notice_id").loc[93082383]
        d = tr.distance_km(row["latitude"], row["longitude"], self.border[:, 0], self.border[:, 1]).min()
        self.assertAlmostEqual(d, row["distance_km"], delta=0.5)
        e = tr.field_strength_1kw(row["frequency_mhz"], row["tx_height_m"], [d], ["Land"]) + row["erp_dbw"] - 30
        self.assertAlmostEqual(e, row["field_strength_dbuv_m"], delta=0.3)
        self.assertEqual(row["threshold_dbuv_m"], 20.0)   # 16.8 kHz channel <= 25 kHz reference

    def test_report_invariants(self):
        r = self.report
        self.assertTrue((r["margin_db"] >= tr.MIN_EXCEEDANCE_MARGIN_DB).all())
        self.assertTrue(r["notice_id"].is_unique)
        self.assertTrue(r["station_class"].isin(tr.LAND_MOBILE_CLASSES).all())
        self.assertFalse((r["country"] == "FIN").any())
        self.assertTrue((r["distance_km"] <= tr.MAX_DISTANCE_KM).all())
        self.assertFalse(r["change_type"].isin(["WITHDRAWN", "RECORDED"]).any())

    def test_consecutive_issue_comparison(self):
        """3077 is compared with 3075 and 3076 (brief: new / modified / withdrawn across issues)."""
        run = ROOT / "runs" / "terrestrial_3077"
        if not run.exists():
            self.skipTest("terrestrial 3077 not processed")
        self.assertIn("Compared with earlier Terrestrial IFICs [3075, 3076]", (run / "audit.txt").read_text())
        rows = pd.concat([pd.read_csv(run / "alerts.csv"), pd.read_csv(run / "concerns.csv")])
        self.assertTrue(rows["change_vs_previous_issues"].isin(["NEW", "REPUBLISHED", "MODIFIED"]).all())

    def test_excel_bandwidth_rule(self):
        st = ie.load_stations(EXCEL)
        self.assertEqual(st.loc[st["station_id"] == "440557", "bw_mhz"].iloc[0], 2.5)  # 118600k typo -> 2.5 MHz


if __name__ == "__main__":
    unittest.main()
