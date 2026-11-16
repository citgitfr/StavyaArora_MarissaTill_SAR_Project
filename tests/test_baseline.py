"""Tests for src/baseline.py.

Unit tests drive rule() with hand-built check outputs (no files). Two end-to-end tests run the real tools on the
fixtures: the clean pair, and the radar multiplied by 100. They test the mechanics only; the baseline is never
tuned against runs/cases or heldout/.

Run from the repository root:  python -m unittest tests.test_baseline -v
"""
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import xarray as xr

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("baseline", REPO / "src" / "baseline.py")
bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bl)

THR = {"speed.max_m_s": 3.0}


def make_check(**fail):
    """A passing check output; fail={test: {field: value}} marks tests failed and sets their fields."""
    tests = {
        "units": {"pass": True},
        "speed_bound": {"pass": True, "speed_max": 1.0, "max_m_s": 3.0},
        "time_axis": {"pass": True},
        "fill_values": {"pass": True, "hfr_hits": 0, "model_hits": 0},
        "coverage": {"pass": True},
        "speed_ratio": {"pass": True, "speed_ratio_median": 1.1, "min": 0.5, "max": 2.0},
        "correlation_sign": {"pass": True, "corr_u": 0.5, "corr_v": 0.4, "corr_swapped": -0.2, "min": 0.1},
        "lag": {"pass": True, "best_lag_hours": -1, "max_abs_hours": 1},
    }
    for name, fields in fail.items():
        tests[name].update(fields, **{"pass": False})
    return {"result": "fail" if fail else "pass", "tests": tests}


class TestRule(unittest.TestCase):
    def r(self, **fail):
        return bl.rule(make_check(**fail), THR)

    def test_x100_rescales_down(self):
        self.assertEqual(self.r(speed_bound={"speed_max": 100.0}, speed_ratio={"speed_ratio_median": 110.7}),
                         ("speed_bound", "rescale", 0.01))

    def test_x001_rescales_up(self):
        self.assertEqual(self.r(speed_ratio={"speed_ratio_median": 0.0111}), ("speed_ratio", "rescale", 100.0))

    def test_spikes_with_normal_ratio_mask(self):
        self.assertEqual(self.r(speed_bound={"speed_max": 1099.0}, correlation_sign={"corr_u": 0.0},
                                lag={"best_lag_hours": 3}), ("speed_bound", "mask_fill", 6.0))

    def test_fill_values_mask(self):
        self.assertEqual(self.r(speed_bound={"speed_max": 14140.0}, fill_values={"hfr_hits": 900}),
                         ("speed_bound", "mask_fill", 6.0))

    def test_speed_just_over_bound_has_no_rule(self):
        self.assertEqual(self.r(speed_bound={"speed_max": 4.0}), ("speed_bound", None, None))

    def test_time_axis_with_lag_shifts(self):
        self.assertEqual(self.r(time_axis={}, lag={"best_lag_hours": 3}), ("time_axis", "shift_time", -3.0))

    def test_time_axis_without_lag_has_no_rule(self):
        self.assertEqual(self.r(time_axis={}), ("time_axis", None, None))

    def test_lag_only_shifts(self):
        self.assertEqual(self.r(lag={"best_lag_hours": -4}), ("lag", "shift_time", 4.0))

    def test_v_flip(self):
        self.assertEqual(self.r(correlation_sign={"corr_v": -0.39}), ("correlation_sign", "flip_sign", "v"))

    def test_u_flip(self):
        self.assertEqual(self.r(correlation_sign={"corr_u": -0.5}), ("correlation_sign", "flip_sign", "u"))

    def test_swap(self):
        self.assertEqual(self.r(correlation_sign={"corr_u": -0.2, "corr_v": -0.1, "corr_swapped": 0.45}),
                         ("correlation_sign", "swap_components", None))

    def test_units_and_coverage_have_no_rule(self):
        self.assertEqual(self.r(units={}), ("units", None, None))
        self.assertEqual(self.r(coverage={}), ("coverage", None, None))

    def test_first_failing_test_wins(self):
        # coverage comes before correlation_sign in check's order
        self.assertEqual(self.r(coverage={}, correlation_sign={"corr_v": -0.39}), ("coverage", None, None))


class TestEndToEnd(unittest.TestCase):
    """Real tools on the fixtures, written under runs/ (repair only writes there) and removed afterwards."""

    def setUp(self):
        (REPO / "runs" / "work").mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="_test_baseline_", dir=REPO / "runs" / "work"))
        self.thr = bl.ft.load_thresholds()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_case(self, name, factor=None):
        d = self.tmp / "cases" / name
        d.mkdir(parents=True)
        with xr.open_dataset(REPO / "fixtures" / "hfr_2km_slice.nc") as src:
            ds = src.load()
        if factor is not None:
            for c in ("u", "v"):
                ds[c] = ds[c] * factor
                ds[c].attrs = src[c].attrs
        ds.to_netcdf(d / "radar.nc")
        shutil.copyfile(REPO / "fixtures" / "hycom_uv_slice.nc", d / "model.nc")
        return d

    def test_clean_is_fused(self):
        dec = bl.decide(self.make_case("clean"), self.tmp / "work" / "clean", self.thr)
        self.assertEqual((dec["choice"], dec["repair"]), ("fused", None))

    def test_x100_is_repaired(self):
        dec = bl.decide(self.make_case("x100", 100.0), self.tmp / "work" / "x100", self.thr)
        self.assertEqual(dec["choice"], "repair")
        self.assertEqual(dec["repair"], {"action": "rescale", "param": 0.01})
        self.assertTrue(os.path.exists(self.tmp / "work" / "x100" / "radar_fixed_1.nc"))


if __name__ == "__main__":
    unittest.main()
