"""Tests for src/make_blend_case.py and src/blend_tools.py.

Uses the committed October fixtures plus the ERA5 files in data/ (skipped if those are missing). Evaluations run
OpenDrift on ONE tuning window to stay fast (about 1 minute in total).

Run from the repository root:  python -m unittest tests.test_blend_tools -v
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bt = _load("blend_tools", "src/blend_tools.py")
mbc = _load("make_blend_case", "src/make_blend_case.py")
fe = bt.fe
HAVE_DATA = Path(fe.DEF_WIND).exists() and Path(fe.DEF_STOKES).exists()


@unittest.skipUnless(HAVE_DATA, "needs data/era5_wind_20211018.nc and data/era5_stokes_20211018.nc")
class TestCaseAndTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.case = cls.tmp / "case"
        cls.meta = mbc.make_case(cls.case, "t", fe.DEF_RADAR, fe.DEF_MODEL, fe.DEF_WIND, fe.DEF_STOKES,
                                 fe.DEF_DRIFTERS, fe.de.DEF_ID, fe.DEF_SPLIT)
        # keep one tuning window so OpenDrift runs stay short
        m = json.loads((cls.case / "case.json").read_text())
        m["tune_windows"] = m["tune_windows"][:1]
        (cls.case / "case.json").write_text(json.dumps(m))

    # ---- the test period stays hidden ---------------------------------------------------------------
    def test_case_has_no_drifter_rows_after_split(self):
        t = pd.read_csv(self.case / "drifter_tune.csv", parse_dates=["time"])
        self.assertLessEqual(t["time"].max(), pd.Timestamp(fe.DEF_SPLIT))
        self.assertEqual(self.meta["n_test_windows_held_back"], 18)
        self.assertEqual(len(self.meta["tune_windows"]), 19)

    def test_tool_refuses_a_case_that_leaks_the_test_period(self):
        leak = self.tmp / "leak"
        mbc.make_case(leak, "leak", fe.DEF_RADAR, fe.DEF_MODEL, fe.DEF_WIND, fe.DEF_STOKES, fe.DEF_DRIFTERS,
                      fe.de.DEF_ID, fe.DEF_SPLIT)
        full = fe.de.drifter_track(fe.DEF_DRIFTERS, fe.de.DEF_ID, pd.Timestamp("2021-10-18"),
                                   pd.Timestamp("2021-10-28 23:00"))
        full.reset_index().to_csv(leak / "drifter_tune.csv", index=False)
        with self.assertRaises(bt.ToolError):
            bt._case(leak)
        m = json.loads((self.case / "case.json").read_text())
        m["tune_windows"] = ["2021-10-23 00:00:00"]                     # ends after the split
        bad = self.tmp / "bad"
        bad.mkdir()
        for f in ("radar.nc", "model.nc", "wind.nc", "stokes.nc", "drifter_tune.csv"):
            (bad / f).symlink_to(self.case / f)
        (bad / "case.json").write_text(json.dumps(m))
        with self.assertRaises(bt.ToolError):
            bt._case(bad)

    # ---- build ----------------------------------------------------------------------------------------
    def test_build_refusals(self):
        run = self.tmp / "run_build"
        with self.assertRaises(bt.ToolError):
            bt.build(self.case, run, "model_only", {})
        with self.assertRaises(bt.ToolError):
            bt.build(self.case, run, "a/b", {})
        with self.assertRaises(bt.ToolError):
            bt.build(self.case, run, "x", {"method": "magic"})
        s = bt.build(self.case, run, "hard", {"method": "hard_switch"})
        self.assertEqual(s["check"], "pass")
        with self.assertRaises(bt.ToolError):
            bt.build(self.case, run, "hard", {"method": "hard_switch"})

    # ---- evaluate: budget, cache, refusals ----------------------------------------------------------------
    def test_evaluate_budget_and_cache(self):
        run = self.tmp / "run_eval"
        old = bt.BUDGET
        bt.BUDGET = 2
        try:
            s1 = bt.evaluate(self.case, run, "model_only", 0.0, "off")
            self.assertEqual(s1["windows"], 1)
            self.assertEqual(s1["budget_used"], 1)
            again = bt.evaluate(self.case, run, "model_only", 0.0, "off")
            self.assertIn("free", again["note"])
            self.assertEqual(again["mean_sep_km_24h"], s1["mean_sep_km_24h"])
            self.assertEqual(again["budget_used"], 1)
            bt.evaluate(self.case, run, "model_only", 0.01, "off")
            with self.assertRaises(bt.ToolError):
                bt.evaluate(self.case, run, "model_only", 0.02, "off")
        finally:
            bt.BUDGET = old
        lst = bt.listing(run)
        self.assertEqual(lst["budget_used"], 2)
        d = bt.diagnose(run, s1["eval_id"])
        self.assertEqual(len(d["mean_sep_km_by_hour"]), 25)
        self.assertEqual(d["windows"][0]["share_particle_hours"]["model"], 1.0)

    def test_evaluate_refusals(self):
        run = self.tmp / "run_ref"
        for args in [("nope", 0.0, "off"), ("model_only", 0.2, "off"), ("model_only", 0.0, "maybe")]:
            with self.assertRaises(bt.ToolError, msg=str(args)):
                bt.evaluate(self.case, run, *args)

    def test_blend_evaluation_reports_radar_share(self):
        run = self.tmp / "run_blend"
        bt.build(self.case, run, "hard", {"method": "hard_switch"})
        s = bt.evaluate(self.case, run, "hard", 0.0, "off")
        self.assertGreaterEqual(s["mean_share_particle_hours_on_radar_or_mixed"], 0.0)
        self.assertLessEqual(s["mean_share_particle_hours_on_radar_or_mixed"], 1.0)


class TestWindSplit(unittest.TestCase):
    def test_signs(self):
        tmp = Path(tempfile.mkdtemp())
        import xarray as xr
        t = pd.date_range("2021-10-18", periods=3, freq="h")
        lat, lon = np.arange(33.0, 36.0, 0.25), np.arange(-123.0, -119.0, 0.25)
        u = np.full((3, lat.size, lon.size), 5.0, "f4")                  # wind blowing east
        xr.Dataset({"x_wind": (("time", "lat", "lon"), u), "y_wind": (("time", "lat", "lon"), np.zeros_like(u))},
                   coords={"time": t, "lat": lat, "lon": lon}).to_netcdf(tmp / "wind.nc")
        obs = pd.DataFrame({"lat": [34.5, 34.5, 34.5], "lon": [-121.0, -120.95, -120.9]}, index=t)
        # forecast stayed at the start, slightly north: the drifter is further downwind (+) and to the right (-)
        along, across, speed = bt._wind_split(tmp, obs, np.array([-121.0, -121.0, -121.0]),
                                              np.array([34.5, 34.5, 34.52]))
        self.assertGreater(along, 0)
        self.assertLess(across, 0)
        self.assertAlmostEqual(speed, 5.0, places=4)


if __name__ == "__main__":
    unittest.main()
