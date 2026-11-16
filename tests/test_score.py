"""Tests for src/score.py with made-up labels and small synthetic netCDF files (never the real key).

Run from the repository root:  python -m unittest tests.test_score -v
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("score", REPO / "src" / "score.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

SMAX = 3.0
SAFE = {"choice": "model_only", "repair": None}
FUSED = {"choice": "fused", "repair": None}


def rep(action, param=None):
    return {"choice": "repair", "repair": {"action": action, "param": param}}


# made-up key with the same structure as heldout/labels.json
LABELS = {
    "case01": {"fault_type": "clean", "detectable": True, "acceptable_choices": [SAFE, FUSED]},
    "case02": {"fault_type": "x100", "detectable": True, "acceptable_choices": [rep("rescale", 0.01), SAFE]},
    "case03": {"fault_type": "spikes", "detectable": True, "acceptable_choices": [rep("mask_fill", 777.0), SAFE]},
    "case04": {"fault_type": "blank", "detectable": True, "acceptable_choices": [SAFE, {"choice": "escalate", "repair": None}]},
}


class TestMatching(unittest.TestCase):
    def ok(self, dec, case):
        return sc.acceptable(dec, LABELS[case]["acceptable_choices"], SMAX)

    def test_choices(self):
        self.assertTrue(self.ok(FUSED, "case01"))
        self.assertTrue(self.ok(SAFE, "case02"))
        self.assertFalse(self.ok(FUSED, "case02"))

    def test_numbers_compare_as_numbers(self):
        self.assertTrue(self.ok(rep("rescale", 0.01), "case02"))
        self.assertTrue(self.ok(rep("rescale", "0.01"), "case02"))
        self.assertFalse(self.ok(rep("rescale", 100), "case02"))

    def test_mask_fill_any_threshold_above_bound(self):
        self.assertTrue(self.ok(rep("mask_fill", 5), "case03"))
        self.assertTrue(self.ok(rep("mask_fill", 10.0), "case03"))
        self.assertFalse(self.ok(rep("mask_fill", 2.0), "case03"))

    def test_two_repairs_do_not_match_one(self):
        two = {"choice": "repair", "repair": [{"action": "rescale", "param": 0.01}, {"action": "mask_fill", "param": 6}]}
        self.assertFalse(self.ok(two, "case02"))

    def test_shift_int_equals_float(self):
        accs = [rep("shift_time", -3.0)]
        self.assertTrue(sc.acceptable(rep("shift_time", -3), accs, SMAX))
        self.assertFalse(sc.acceptable(rep("shift_time", -2), accs, SMAX))

    def test_oracle_prefers_radar(self):
        self.assertEqual(sc.oracle_choice(LABELS["case01"]["acceptable_choices"]), FUSED)
        self.assertEqual(sc.oracle_choice(LABELS["case04"]["acceptable_choices"]), SAFE)


class TestSummary(unittest.TestCase):
    def test_metrics(self):
        decs = {"case01": FUSED, "case02": FUSED, "case03": rep("mask_fill", 6), "case04": SAFE}
        s = sc.summarize(sc.score_policy("p", decs, LABELS, SMAX, with_fidelity=False))
        self.assertEqual((s["acceptable"], s["silent_errors"]), (3, 1))          # fused on x100 is a silent error
        self.assertEqual((s["radar_recovered"], s["radar_recoverable"]), (2, 3))
        self.assertEqual(s["faithful_radar"], None)

    def test_always_model_only_is_safe_but_recovers_nothing(self):
        s = sc.summarize(sc.score_policy("m", {c: SAFE for c in LABELS}, LABELS, SMAX, with_fidelity=False))
        self.assertEqual((s["acceptable"], s["silent_errors"], s["radar_recovered"]), (4, 0, 0))

    def test_missing_decision_counts_as_undecided(self):
        s = sc.summarize(sc.score_policy("x", {"case01": FUSED}, LABELS, SMAX, with_fidelity=False))
        self.assertEqual((s["decided"], s["acceptable"]), (1, 1))

    def test_agreement(self):
        a = {"case01": FUSED, "case03": rep("mask_fill", 10)}
        b = {"case01": FUSED, "case03": rep("mask_fill", 5)}
        self.assertEqual(sc.agreement(a, b), {"cases": 2, "same_choice_and_action": 2})


class TestFidelity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        t = pd.date_range("2021-08-10", periods=6, freq="h")
        u = np.arange(6 * 3 * 4, dtype=float).reshape(6, 3, 4) / 100
        u[:, 0, 0] = np.nan
        self.clean = self.save("clean.nc", t, u, -u)

    def save(self, name, t, u, v):
        ds = xr.Dataset({"u": (("time", "lat", "lon"), u), "v": (("time", "lat", "lon"), v)},
                        coords={"time": t, "lat": [33.0, 33.1, 33.2], "lon": [-119.0, -118.9, -118.8, -118.7]})
        ds.to_netcdf(self.tmp / name)
        return self.tmp / name

    def test_identical_is_faithful(self):
        f = sc.fidelity(self.clean, self.clean)
        self.assertEqual((f["rms_m_s"], f["coverage_retained"], f["faithful"]), (0.0, 1.0, True))

    def test_masked_cells_still_faithful(self):
        with xr.open_dataset(self.clean) as ds:
            u = ds["u"].values.copy()
            t = ds["time"].values
        u[:, 1, 1] = np.nan
        f = sc.fidelity(self.save("masked.nc", t, u, -u), self.clean)
        self.assertTrue(f["faithful"])
        self.assertLess(f["coverage_retained"], 1.0)

    def test_hour_offset_is_not_faithful(self):
        with xr.open_dataset(self.clean) as ds:
            u, t = ds["u"].values.copy(), ds["time"].values + np.timedelta64(1, "h")
        f = sc.fidelity(self.save("shifted.nc", t, u, -u), self.clean)
        self.assertEqual(f["common_hours"], 5)
        self.assertFalse(f["faithful"])

    def test_radar_used_reads_repair_log(self):
        work = self.tmp / "work"
        (work / "case02").mkdir(parents=True)
        out = work / "case02" / "radar_fixed.nc"
        out.write_bytes(b"x")
        (work / "case02" / "repairs.json").write_text(json.dumps([{"out": "C:\\ws\\runs\\work\\agent\\case02\\radar_fixed.nc"}]))
        self.assertEqual(sc.radar_used(rep("rescale", 0.01), "case02", work), out)
        self.assertEqual(sc.radar_used(FUSED, "case07", work, self.tmp), self.tmp / "case07" / "radar.nc")
        self.assertIsNone(sc.radar_used(SAFE, "case02", work))


if __name__ == "__main__":
    unittest.main()
