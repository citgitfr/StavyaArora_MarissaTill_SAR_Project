"""Tests for src/make_loop_cases.py and src/sar_loop.py helpers (no OpenDrift run).

Run from the repository root:  python -m unittest tests.test_sar_loop -v
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


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mlc = _load("make_loop_cases", "src/make_loop_cases.py")
sl = _load("sar_loop", "src/sar_loop.py")


class TestDeliveries(unittest.TestCase):
    def test_plan(self):
        dl = mlc.deliveries()
        self.assertEqual(len(dl), len(mlc.SCENARIOS) * mlc.PERIODS)
        for sid, t0, onset, fault in mlc.SCENARIOS:
            mine = [d for d in dl if d[0] == sid]
            self.assertEqual([d[1] for d in mine], list(range(1, mlc.PERIODS + 1)))
            self.assertEqual(mine[-1][2], pd.Timestamp(t0) + pd.Timedelta(hours=mlc.PERIOD_H * mlc.PERIODS))
            for _, k, _, fname, f in mine:
                self.assertEqual(fname is not None, onset is not None and k >= onset)
                self.assertEqual(f is None, fname is None)

    def test_fits_the_fixture(self):
        with xr.open_dataset(mlc.RADAR) as r:
            t0, t1 = pd.Timestamp(r.time.values[0]), pd.Timestamp(r.time.values[-1])
        for _, _, t_end, _, _ in mlc.deliveries():
            self.assertGreaterEqual(t_end - pd.Timedelta(hours=mlc.DELIVERY_H - 1), t0)
            self.assertLessEqual(t_end + pd.Timedelta(hours=3), t1)        # room for a +3 h shift


class TestFaithful(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        t = pd.date_range("2021-10-24", periods=12, freq="h")
        rng = np.random.default_rng(0)
        u = rng.normal(0, 0.2, (12, 5, 5))
        u[:, 0, 0] = np.nan
        self.clean = xr.Dataset({"u": (("time", "lat", "lon"), u), "v": (("time", "lat", "lon"), -u)},
                                coords={"time": t, "lat": np.arange(5.0), "lon": np.arange(5.0)})
        self.t0, self.t1 = t[2], t[8]

    def write(self, ds, name):
        p = self.tmp / name
        ds.to_netcdf(p)
        return p

    def test_identical_and_faulty(self):
        self.assertTrue(sl.faithful(self.write(self.clean, "a.nc"), self.clean, self.t0, self.t1))
        x100 = self.clean.copy(deep=True)
        x100["u"] = x100["u"] * 100
        self.assertFalse(sl.faithful(self.write(x100, "b.nc"), self.clean, self.t0, self.t1))
        back = x100.copy(deep=True)
        back["u"] = back["u"] * 0.01                                  # a correct rescale repair
        self.assertTrue(sl.faithful(self.write(back, "c.nc"), self.clean, self.t0, self.t1))

    def test_time_shift_is_unfaithful(self):
        sh = self.clean.assign_coords(time=self.clean.time.values + np.timedelta64(1, "h"))
        self.assertFalse(sl.faithful(self.write(sh, "d.nc"), self.clean, self.t0, self.t1))

    def test_nan_pattern_matters(self):
        holes = self.clean.copy(deep=True)
        u = holes["u"].values.copy()
        u[4, 2, 2] = np.nan
        holes["u"] = (("time", "lat", "lon"), u)
        self.assertFalse(sl.faithful(self.write(holes, "e.nc"), self.clean, self.t0, self.t1))


class TestPolicyRadar(unittest.TestCase):
    def test_choices(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "cases" / "case01").mkdir(parents=True)
        (tmp / "res").mkdir()
        (tmp / "res" / "case01.json").write_text(json.dumps({"choice": "escalate"}))
        dec = {"p": (tmp / "res", tmp / "work")}
        self.assertEqual(sl.policy_radar("no_qc", "case01", tmp / "cases", dec)[0], tmp / "cases" / "case01" / "radar.nc")
        self.assertEqual(sl.policy_radar("model_only", "case01", tmp / "cases", dec), (None, "model_only"))
        self.assertEqual(sl.policy_radar("p", "case01", tmp / "cases", dec), (None, "escalate"))
        self.assertEqual(sl.policy_radar("p", "case02", tmp / "cases", dec), (None, "missing"))
        (tmp / "res" / "case01.json").write_text(json.dumps({"choice": "fused"}))
        self.assertEqual(sl.policy_radar("p", "case01", str(tmp / "cases"), dec),
                         (tmp / "cases" / "case01" / "radar.nc", "fused"))


if __name__ == "__main__":
    unittest.main()
