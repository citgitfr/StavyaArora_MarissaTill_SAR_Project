"""Unit tests for .claude/skills/forcing-tools/forcing_tools.py (unittest, synthetic data + read-only fixtures).

Run from the repository root:  python -m unittest tests.test_forcing_tools -v
"""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("forcing_tools", REPO / ".claude/skills/forcing-tools/forcing_tools.py")
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)

T0 = pd.Timestamp("2021-08-10")
# Test-only values chosen for the synthetic field below. NOT physics thresholds.
TEST_THR = {"date": "test-only", "speed.max_m_s": 5.0, "time.max_gap_s": 3600, "qc.fill_values": [9999.0],
            "coverage.min_hour_fraction": 0.5, "coverage.min_good_hours_fraction": 0.8,
            "speed_ratio.min": 0.5, "speed_ratio.max": 2.0, "correlation.min": 0.0, "lag.max_abs_hours": 0}


def make_ds(source="hfr", nt=48, step_h=1, shift_h=0, units="m s-1", ny=10, nx=12):
    """Smooth synthetic currents. u and v have different periods so swaps and lags are detectable.
    shift_h relabels times (data at true time t gets label t + shift_h)."""
    hours = np.arange(nt) * step_h
    T, Y, X = np.meshgrid(hours.astype(float), np.arange(ny), np.arange(nx), indexing="ij")
    u = 0.2 * np.sin(2 * np.pi * T / 12 + 0.3 * X)
    v = 0.15 * np.cos(2 * np.pi * T / 9 + 0.4 * Y)
    std_u, std_v = ft.STD_NAMES[source]
    return xr.Dataset(
        {"u": (("time", "lat", "lon"), u, {"units": units, "standard_name": std_u}),
         "v": (("time", "lat", "lon"), v, {"units": units, "standard_name": std_v})},
        coords={"time": T0 + pd.to_timedelta(hours + shift_h, unit="h"),
                "lat": np.linspace(33.0, 33.5, ny), "lon": np.linspace(-119.0, -118.4, nx)})


def make_model(**kw):
    """Model covering the HFR hours with margin, with a 2-column land strip."""
    ds = make_ds("model", nt=60, **kw)
    land = np.zeros(ds["u"].shape, bool)
    land[:, :, :2] = True
    return ds.where(~xr.DataArray(land, dims=ds["u"].dims))


def scaled(ds, c, k):
    out = ds.copy()
    out[c] = ds[c].copy(data=ds[c].values * k)
    return out


class TmpDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(ft, "RUNS_DIR", self.tmp / "runs")
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, ds, name):
        path = self.tmp / name
        ds.to_netcdf(path)
        return str(path)

    def read(self, path):
        with xr.open_dataset(path) as ds:
            return ds.load()

    def cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ft.main(list(argv))
        return code, json.loads(buf.getvalue())


class TestUnitsAndThresholds(TmpDir):
    def test_normalize_units(self):
        for s in ("m s-1", "m/s", "M/S", " m  s-1 ", "meters per second"):
            self.assertEqual(ft.normalize_units(s), ("m s-1", 1.0), s)
        self.assertEqual(ft.normalize_units("cm/s"), ("cm s-1", 0.01))
        self.assertIsNone(ft.normalize_units("knots"))
        self.assertIsNone(ft.normalize_units(None))

    def test_load_thresholds(self):
        p = self.tmp / "t.yaml"
        p.write_text("# c\ndate: 2026-11-01  # frozen\n\nspeed:\n  max_m_s: 2.5\nqc:\n"
                     "  fill_values: [9999, -9999.0]\nlag:\n  max_abs_hours: TODO\n", encoding="utf-8")
        thr = ft.load_thresholds(p)
        self.assertEqual(thr, {"date": "2026-11-01", "speed.max_m_s": 2.5,
                               "qc.fill_values": [9999, -9999.0], "lag.max_abs_hours": ft.TODO})

    def test_require_lists_todo_and_missing(self):
        thr = dict(TEST_THR, **{"lag.max_abs_hours": ft.TODO})
        del thr["date"]
        with self.assertRaises(ft.ToolError) as cm:
            ft.require(thr)
        self.assertIn("date", str(cm.exception))
        self.assertIn("lag.max_abs_hours", str(cm.exception))
        ft.require(TEST_THR)

    def test_load_thresholds_missing_file(self):
        with self.assertRaises(ft.ToolError):
            ft.load_thresholds(self.tmp / "nope.yaml")


class TestQC(TmpDir):
    def test_clean(self):
        ds = make_ds()
        r = ft.qc(self.save(ds, "h.nc"), "hfr", [9999.0])
        self.assertEqual((r["n_time"], r["time_step_s"], r["largest_gap_s"]), (48, 3600, 3600))
        self.assertEqual((r["units_attr"], r["units_normalized"]), ("m s-1", "m s-1"))
        self.assertEqual(r["valid_fraction"], 1.0)
        self.assertEqual(r["fill_value_hits"], 0)
        self.assertEqual(r["flags"], [])
        sp = np.hypot(ds["u"].values, ds["v"].values)
        self.assertAlmostEqual(r["speed_median"], float(np.median(sp)))
        self.assertAlmostEqual(r["speed_p95"], float(np.percentile(sp, 95)))
        self.assertAlmostEqual(r["speed_max"], float(sp.max()))
        self.assertIn("h.nc", r["file"])

    def test_gap_and_blanked_hours(self):
        ds = make_ds().isel(time=[i for i in range(48) if i not in (10, 11, 12)])
        ds["u"][0] = np.nan
        r = ft.qc(self.save(ds, "h.nc"), "hfr", [9999.0])
        self.assertEqual((r["time_step_s"], r["largest_gap_s"]), (3600, 4 * 3600))
        self.assertIn("time_step_irregular", r["flags"])
        self.assertAlmostEqual(r["valid_fraction"], 44 / 45)

    def test_cm_per_s_converted(self):
        ref = ft.qc(self.save(make_ds(), "a.nc"), "hfr", [9999.0])
        cm = make_ds(units="cm/s")
        cm = scaled(scaled(cm, "u", 100), "v", 100)
        r = ft.qc(self.save(cm, "b.nc"), "hfr", [9999.0])
        self.assertEqual(r["units_normalized"], "cm s-1")
        self.assertAlmostEqual(r["speed_max"], ref["speed_max"])

    def test_unknown_units_and_wrong_standard_name(self):
        ds = make_ds()
        ds["u"].attrs["units"] = "furlongs/fortnight"
        r = ft.qc(self.save(ds, "h.nc"), "model", [9999.0])
        self.assertIsNone(r["units_normalized"])
        self.assertIn("units_unrecognized", r["flags"])
        self.assertIn("units_u_v_differ", r["flags"])
        self.assertIn("standard_name_unexpected", r["flags"])

    def test_fill_values(self):
        ds = make_ds()
        ds["u"][3, 2, 2] = 9999.0
        ds["v"][4, 1, 1] = 9999.0
        path = self.save(ds, "h.nc")
        r = ft.qc(path, "hfr", [9999.0])
        self.assertEqual(r["fill_value_hits"], 2)
        self.assertIn("fill_values_present", r["flags"])
        r = ft.qc(path, "hfr", None)
        self.assertIsNone(r["fill_value_hits"])
        self.assertIn("fill_values_not_configured", r["flags"])

    def test_missing_variable(self):
        with self.assertRaises(ft.ToolError):
            ft.qc(self.save(make_ds().drop_vars("v"), "h.nc"), "hfr")


class TestCompare(TmpDir):
    def setUp(self):
        super().setUp()
        self.model = self.save(make_model(), "m.nc")

    def test_identical(self):
        r = ft.compare(self.save(make_ds(), "h.nc"), self.model)
        self.assertIn(r["n_pairs"], (48 * 10 * 10, 48 * 9 * 10))  # land excluded; linear interp may drop the next column
        for k in ("rms_u", "rms_v", "bias_u", "bias_v"):
            self.assertAlmostEqual(r[k], 0.0, places=6, msg=k)
        self.assertAlmostEqual(r["corr_u"], 1.0, places=6)
        self.assertAlmostEqual(r["corr_v"], 1.0, places=6)
        self.assertAlmostEqual(r["speed_ratio_median"], 1.0, places=6)
        self.assertEqual(r["best_lag_hours"], 0)
        self.assertLess(r["corr_swapped"], 0.5)
        self.assertEqual(r["flags"], [])
        self.assertIn("h.nc", r["hfr"])
        self.assertIn("m.nc", r["model"])

    def test_scaled(self):
        r = ft.compare(self.save(scaled(scaled(make_ds(), "u", 100), "v", 100), "h.nc"), self.model)
        self.assertAlmostEqual(r["speed_ratio_median"], 100.0, places=3)
        self.assertAlmostEqual(r["corr_u"], 1.0, places=6)

    def test_time_shift_gives_lag(self):
        for shift in (1, 3, -2):
            r = ft.compare(self.save(make_ds(shift_h=shift), f"h{shift}.nc"), self.model)
            self.assertEqual(r["best_lag_hours"], shift)
            self.assertIn("best_lag_nonzero", r["flags"])

    def test_v_sign_flip(self):
        r = ft.compare(self.save(scaled(make_ds(), "v", -1), "h.nc"), self.model)
        self.assertAlmostEqual(r["corr_v"], -1.0, places=6)
        self.assertIn("negative_corr_v", r["flags"])

    def test_swapped(self):
        ds = make_ds()
        sw = ds.copy()
        sw["u"] = ds["u"].copy(data=ds["v"].values)
        sw["v"] = ds["v"].copy(data=ds["u"].values)
        r = ft.compare(self.save(sw, "h.nc"), self.model)
        self.assertAlmostEqual(r["corr_swapped"], 1.0, places=6)
        self.assertIn("swapped_corr_exceeds_direct", r["flags"])

    def test_no_overlap(self):
        far = make_ds()
        far = far.assign_coords(time=far["time"] + pd.Timedelta("30D"))
        r = ft.compare(self.save(far, "h.nc"), self.model)
        self.assertEqual(r["n_pairs"], 0)
        self.assertIsNone(r["rms_u"])
        self.assertIn("no_overlap", r["flags"])


class TestRepair(TmpDir):
    def setUp(self):
        super().setUp()
        self.ds = make_ds()
        self.src = self.save(self.ds, "h.nc")
        self.case = self.tmp / "runs" / "cases" / "case01"

    def run_repair(self, action, param, name):
        return ft.repair(self.src, action, param, self.case / name)

    def test_rescale(self):
        r = self.run_repair("rescale", "0.01", "a.nc")
        out = self.read(r["out"])
        np.testing.assert_allclose(out["u"].values, self.ds["u"].values * 0.01)
        self.assertEqual(out["u"].attrs["standard_name"], self.ds["u"].attrs["standard_name"])
        self.assertEqual((r["repairs_used"], r["repairs_left"]), (1, 1))

    def test_shift_time_undoes_lag(self):
        shifted = self.save(make_ds(shift_h=3), "s.nc")
        r = ft.repair(shifted, "shift_time", "-3", self.case / "a.nc")
        np.testing.assert_array_equal(self.read(r["out"])["time"].values, self.ds["time"].values)
        c = ft.compare(r["out"], self.save(make_model(), "m.nc"))
        self.assertEqual(c["best_lag_hours"], 0)

    def test_flip_sign(self):
        out = self.read(self.run_repair("flip_sign", "v", "a.nc")["out"])
        np.testing.assert_allclose(out["v"].values, -self.ds["v"].values)
        np.testing.assert_allclose(out["u"].values, self.ds["u"].values)

    def test_swap_components(self):
        out = self.read(self.run_repair("swap_components", None, "a.nc")["out"])
        np.testing.assert_allclose(out["u"].values, self.ds["v"].values)
        np.testing.assert_allclose(out["v"].values, self.ds["u"].values)
        self.assertEqual(out["u"].attrs["standard_name"], ft.STD_NAMES["hfr"][0])

    def test_mask_fill(self):
        ds = self.ds.copy(deep=True)
        ds["u"][3, 2, 2] = 9999.0
        ds["v"][4, 1, 1] = -9999.0
        r = ft.repair(self.save(ds, "f.nc"), "mask_fill", "9999", self.case / "a.nc")
        out = self.read(r["out"])
        self.assertEqual(r["n_masked"], 2)
        self.assertTrue(np.isnan(out["v"].values[3, 2, 2]) and np.isnan(out["u"].values[4, 1, 1]))
        self.assertEqual(int(np.isnan(out["u"].values).sum()), 2)

    def test_third_repair_refused(self):
        self.run_repair("rescale", "2", "a.nc")
        self.run_repair("rescale", "0.5", "b.nc")
        with self.assertRaises(ft.ToolError):
            self.run_repair("rescale", "1", "c.nc")
        self.assertFalse((self.case / "c.nc").exists())
        self.assertEqual(len(json.loads((self.case / "repairs.json").read_text())), 2)

    def test_refusals(self):
        with self.assertRaises(ft.ToolError):
            ft.repair(self.src, "rescale", "2", self.tmp / "elsewhere" / "a.nc")     # outside runs/
        with self.assertRaises(ft.ToolError):
            ft.repair(self.src, "rescale", "2", self.tmp / "runs" / ".." / "a.nc")  # escapes runs/
        for action, param in (("smooth", "1"), ("rescale", None), ("rescale", "0"), ("rescale", "nan"),
                              ("flip_sign", "w"), ("swap_components", "1"), ("mask_fill", "-1")):
            with self.assertRaises(ft.ToolError, msg=(action, param)):
                self.run_repair(action, param, "x.nc")
        self.run_repair("rescale", "2", "a.nc")
        with self.assertRaises(ft.ToolError):
            self.run_repair("rescale", "2", "a.nc")                                # no overwrite
        self.assertFalse((self.case / "x.nc").exists())


class TestCheck(TmpDir):
    def setUp(self):
        super().setUp()
        self.ds = make_ds()
        self.model = self.save(make_model(), "m.nc")

    def failed(self, ds, name="h.nc"):
        r = ft.check(self.save(ds, name), self.model, TEST_THR)
        return r["result"], sorted(k for k, t in r["tests"].items() if not t["pass"])

    def test_clean_passes(self):
        r = ft.check(self.save(self.ds, "h.nc"), self.model, TEST_THR)
        self.assertEqual(r["result"], "pass", r["tests"])
        self.assertEqual(set(r["tests"]), {"units", "speed_bound", "time_axis", "fill_values",
                                           "coverage", "speed_ratio", "correlation_sign", "lag"})
        self.assertEqual(r["tests"]["coverage"]["ocean_cells"], 10 * 10)
        self.assertEqual(r["thresholds_date"], "test-only")

    def test_refuses_todo(self):
        with self.assertRaises(ft.ToolError):
            ft.check(self.save(self.ds, "h.nc"), self.model, dict(TEST_THR, **{"speed.max_m_s": ft.TODO}))

    def test_x100(self):
        self.assertEqual(self.failed(scaled(scaled(self.ds, "u", 100), "v", 100)),
                         ("fail", ["speed_bound", "speed_ratio"]))

    def test_x001(self):
        self.assertEqual(self.failed(scaled(scaled(self.ds, "u", 0.01), "v", 0.01)), ("fail", ["speed_ratio"]))

    def test_time_shift(self):
        self.assertEqual(self.failed(make_ds(shift_h=1)), ("fail", ["lag"]))

    def test_time_shift_past_model_end(self):
        short = self.save(make_model().isel(time=slice(0, 48)), "m48.nc")
        r = ft.check(self.save(make_ds(shift_h=3), "h.nc"), short, TEST_THR)
        self.assertFalse(r["tests"]["time_axis"]["pass"])
        self.assertFalse(r["tests"]["time_axis"]["inside_model_times"])

    def test_v_flip(self):
        result, failed = self.failed(scaled(self.ds, "v", -1))   # lag may also fail: a flip moves the peak
        self.assertEqual(result, "fail")
        self.assertIn("correlation_sign", failed)

    def test_swap(self):
        sw = self.ds.copy()
        sw["u"] = self.ds["u"].copy(data=self.ds["v"].values)
        sw["v"] = self.ds["v"].copy(data=self.ds["u"].values)
        result, failed = self.failed(sw)
        self.assertEqual(result, "fail")
        self.assertIn("correlation_sign", failed)

    def test_blanked_hours(self):
        ds = self.ds.copy(deep=True)
        ds["u"][::5] = np.nan
        ds["u"][1::5] = np.nan
        ds["u"][2::5] = np.nan                                                  # 60% of hours blank
        self.assertEqual(self.failed(ds), ("fail", ["coverage"]))

    def test_fill_values(self):
        ds = self.ds.copy(deep=True)
        ds["u"][3, 2, 2] = 9999.0
        result, failed = self.failed(ds)
        self.assertIn("fill_values", failed)
        self.assertIn("speed_bound", failed)


class TestCLI(TmpDir):
    def test_qc_and_compare_json(self):
        h, m = self.save(make_ds(), "h.nc"), self.save(make_model(), "m.nc")
        code, r = self.cli("qc", "--file", h, "--source", "hfr")
        self.assertEqual((code, r["tool"], r["file"]), (0, "qc", h))
        code, r = self.cli("compare", "--hfr", h, "--model", m)
        self.assertEqual((code, r["hfr"], r["model"]), (0, h, m))
        self.assertIsInstance(r["corr_by_lag"]["0"], float)

    def test_check_uses_config_file_and_refuses_todo(self):
        h, m = self.save(make_ds(), "h.nc"), self.save(make_model(), "m.nc")
        cfg = self.tmp / "thr.yaml"
        cfg.write_text("date: TODO\nspeed:\n  max_m_s: TODO\n", encoding="utf-8")
        with mock.patch.object(ft, "THRESHOLDS", cfg):
            code, r = self.cli("check", "--hfr", h, "--model", m)
        self.assertEqual(code, 2)
        self.assertIn("thresholds not set", r["error"])
        self.assertEqual((r["hfr"], r["model"]), (h, m))

    def test_error_is_json(self):
        code, r = self.cli("qc", "--file", str(self.tmp / "missing.nc"), "--source", "hfr")
        self.assertEqual(code, 2)
        self.assertIn("error", r)

    def test_nan_becomes_null(self):
        self.assertEqual(ft._clean({"a": np.float64("nan"), "b": np.int64(3), "c": [np.bool_(True), 0.123456]}),
                         {"a": None, "b": 3, "c": [True, 0.1235]})


FIX_H, FIX_M = REPO / "fixtures" / "hfr_2km_slice.nc", REPO / "fixtures" / "hycom_uv_slice.nc"


@unittest.skipUnless(FIX_H.exists() and FIX_M.exists(), "fixtures not present")
class TestFixtures(unittest.TestCase):
    """Reproduce the clean-data reference numbers in docs/dev_notes.md (fixtures are read only)."""

    def test_qc_hfr(self):
        r = ft.qc(FIX_H, "hfr")
        self.assertEqual((r["n_time"], r["time_step_s"], r["largest_gap_s"]), (48, 3600, 3600))
        self.assertEqual(r["units_normalized"], "m s-1")
        self.assertNotIn("standard_name_unexpected", r["flags"])
        for k, ref in (("speed_median", 0.192), ("speed_p95", 0.424), ("speed_max", 1.000)):
            self.assertAlmostEqual(r[k], ref, delta=0.0006, msg=k)

    def test_qc_hycom(self):
        r = ft.qc(FIX_M, "model")
        self.assertEqual((r["n_time"], r["time_step_s"], r["largest_gap_s"]), (17, 10800, 10800))
        self.assertEqual(r["units_normalized"], "m s-1")
        self.assertNotIn("standard_name_unexpected", r["flags"])
        self.assertAlmostEqual(r["valid_fraction"], 0.78, delta=0.006)
        for k, ref in (("speed_median", 0.174), ("speed_p95", 0.412), ("speed_max", 0.633)):
            self.assertAlmostEqual(r[k], ref, delta=0.0006, msg=k)

    def test_compare(self):
        r = ft.compare(FIX_H, FIX_M)
        self.assertEqual(r["n_pairs"], 130635)
        for k, ref, tol in (("rms_u", 0.176, 0.0006), ("rms_v", 0.167, 0.0006), ("corr_u", 0.50, 0.006),
                            ("corr_v", 0.39, 0.006), ("bias_u", 0.020, 0.0006), ("bias_v", -0.011, 0.0006)):
            self.assertAlmostEqual(r[k], ref, delta=tol, msg=k)


if __name__ == "__main__":
    unittest.main()
