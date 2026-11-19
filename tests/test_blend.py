"""Tests for src/blend.py on small synthetic grids (no OpenDrift, no real data).

Run from the repository root:  python -m unittest tests.test_blend -v
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
_spec = importlib.util.spec_from_file_location("blend", REPO / "src" / "blend.py")
bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bl)

LAT = np.round(34.0 + 0.02 * np.arange(20), 4)    # 20 x 20 cells of about 2 km
LON = np.round(-121.0 + 0.02 * np.arange(20), 4)


def write(path, u, v, times, lat=LAT, lon=LON, units="m s-1"):
    xr.Dataset({"u": (("time", "lat", "lon"), u, {"units": units}),
                "v": (("time", "lat", "lon"), v, {"units": units})},
               coords={"time": times, "lat": lat, "lon": lon}).to_netcdf(path)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.times = pd.date_range("2021-10-18", periods=6, freq="h")
        # model: 3-hourly, coarser grid covering the radar box, u = 0.1, v = 0
        mt = pd.date_range("2021-10-18", periods=3, freq="3h")
        mlat, mlon = np.arange(33.9, 34.5, 0.08), np.arange(-121.1, -120.5, 0.08)
        mu = np.full((3, mlat.size, mlon.size), 0.1)
        self.model = write(self.tmp / "m.nc", mu, np.zeros_like(mu), mt, mlat, mlon)
        # radar: u = 0.5, v = 0.2 everywhere, with a hole at hour 2 cell (10, 10)
        self.ru = np.full((6, 20, 20), 0.5)
        self.rv = np.full((6, 20, 20), 0.2)

    def radar(self, name="r.nc"):
        return write(self.tmp / name, self.ru, self.rv, self.times)

    def go(self, spec, name="out.nc", radar=None):
        s = bl.blend(radar or self.radar(), self.model, self.tmp / name, spec, require_check=False)
        return s, xr.open_dataset(self.tmp / name)


class TestValidate(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(bl.validate({})["method"], "hard_switch")

    def test_refusals(self):
        for bad in [{"method": "magic"}, {"colour": 1}, {"method": "edge_taper"},
                    {"method": "hard_switch", "taper_km": 10}, {"method": "weighted", "radar_weight": 1.5},
                    {"gap_fill_hours": 7}, {"gap_fill_hours": 1.5}, {"trust_max_rms": 0.0},
                    {"method": "weighted"}, {"trust_min_coverage": True}, "hard_switch"]:
            with self.assertRaises(bl.BlendError, msg=str(bad)):
                bl.validate(bad)


class TestBlend(Base):
    def test_hard_switch_radar_where_valid_model_in_holes(self):
        self.ru[2, 10, 10] = np.nan
        s, o = self.go({"method": "hard_switch"})
        self.assertAlmostEqual(float(o.u[0, 5, 5]), 0.5, places=5)
        self.assertAlmostEqual(float(o.v[0, 5, 5]), 0.2, places=5)
        self.assertAlmostEqual(float(o.u[2, 10, 10]), 0.1, places=5)       # model, not stretched radar
        self.assertAlmostEqual(float(o.v[2, 10, 10]), 0.0, places=5)
        self.assertEqual(int(o.source[2, 10, 10]), 0)
        self.assertEqual(int(o.source[0, 5, 5]), 1)
        self.assertEqual(o.u.attrs["standard_name"], "surface_eastward_sea_water_velocity")
        self.assertEqual(json.loads(o.attrs["blend_spec"])["method"], "hard_switch")
        self.assertEqual(s["share_model"], round(1 / (6 * 400), 4))
        self.assertFalse(np.isnan(o.u.values).any())                      # no holes left for OpenDrift

    def test_units_converted(self):
        self.ru *= 100
        self.rv *= 100
        p = write(self.tmp / "rcm.nc", self.ru, self.rv, self.times, units="cm s-1")
        _, o = self.go({}, radar=p)
        self.assertAlmostEqual(float(o.u[0, 0, 0]), 0.5, places=5)

    def test_weighted(self):
        _, o = self.go({"method": "weighted", "radar_weight": 0.25})
        self.assertAlmostEqual(float(o.u[1, 3, 3]), 0.25 * 0.5 + 0.75 * 0.1, places=5)
        self.assertEqual(int(o.source[1, 3, 3]), 2)

    def test_edge_taper(self):
        self.ru[:, :, 15:] = np.nan                                     # radar covers columns 0..14 only
        self.rv[:, :, 15:] = np.nan
        _, o = self.go({"method": "edge_taper", "taper_km": 10})
        w = o.weight.values[0]
        self.assertEqual(w[10, 16], 0)                                  # no radar: model
        self.assertLess(w[10, 14], 0.3)                                 # next to the gap: mostly model
        self.assertGreater(w[10, 7], w[10, 12])                         # weight grows away from the gap
        self.assertLess(w[10, 0], 1)                                    # the box edge also counts as an edge
        self.assertTrue(np.all((w >= 0) & (w <= 1)))

    def test_land_is_not_an_edge_and_stays_nan(self):
        mu = xr.open_dataset(self.model)
        land_model = mu.u.values.copy()
        land_model[:, :, :2] = np.nan                                   # model land on the west side
        model2 = write(self.tmp / "m2.nc", land_model, np.where(np.isnan(land_model), np.nan, 0.0),
                       mu.time.values, mu.lat.values, mu.lon.values)
        self.ru[:, :, :3] = np.nan                                      # radar also has no data there
        self.rv[:, :, :3] = np.nan
        s = bl.blend(self.radar(), model2, self.tmp / "land.nc", {"method": "edge_taper", "taper_km": 4},
                     require_check=False)
        o = xr.open_dataset(self.tmp / "land.nc")
        self.assertTrue(np.isnan(float(o.u[0, 10, 0])))                 # land
        self.assertEqual(int(o.source[0, 10, 0]), -1)
        self.assertEqual(s["share_neither"], 0.0)                       # land is not counted as ocean

    def test_gap_fill(self):
        self.ru[2:4, 10, 10] = np.nan                                   # 2-hour hole between 0.5 and 0.8
        self.ru[4:, 10, 10] = 0.8
        self.ru[3:, 5, 5] = np.nan                                      # hole running to the end: never filled
        _, o1 = self.go({"gap_fill_hours": 1}, "g1.nc")
        self.assertAlmostEqual(float(o1.u[2, 10, 10]), 0.1, places=5)  # too long for 1 h: model
        s2, o2 = self.go({"gap_fill_hours": 2}, "g2.nc")
        self.assertAlmostEqual(float(o2.u[2, 10, 10]), 0.6, places=5)  # 0.5 + 1/3 * 0.3
        self.assertAlmostEqual(float(o2.u[3, 10, 10]), 0.7, places=5)
        self.assertAlmostEqual(float(o2.u[5, 5, 5]), 0.1, places=5)
        self.assertEqual(s2["cells_gap_filled"], 2)

    def test_trust_drops_low_coverage_hour(self):
        self.ru[4, :, 2:] = np.nan                                      # hour 4 covers 10% of the box
        self.rv[4, :, 2:] = np.nan
        s, o = self.go({"trust_min_coverage": 0.2})
        self.assertEqual(s["hours_dropped"], 1)
        self.assertAlmostEqual(float(o.u[4, 0, 0]), 0.1, places=5)     # the whole hour is model
        self.assertAlmostEqual(float(o.u[3, 0, 0]), 0.5, places=5)

    def test_trust_drops_hour_far_from_model(self):
        self.ru[1] = 2.0
        s, _ = self.go({"trust_max_rms": 0.6})
        self.assertEqual([d["time"] for d in s["dropped"]], ["2021-10-18 01:00:00"])

    def test_model_interpolated_in_time(self):
        m = xr.open_dataset(self.model)
        mu = m.u.values.copy()
        mu[1] = 0.4                                                     # 0.1 at 00h, 0.4 at 03h
        model2 = write(self.tmp / "m3.nc", mu, np.zeros_like(mu), m.time.values, m.lat.values, m.lon.values)
        self.ru[1] = np.nan
        self.rv[1] = np.nan
        bl.blend(self.radar(), model2, self.tmp / "t.nc", {}, require_check=False)
        o = xr.open_dataset(self.tmp / "t.nc")
        self.assertAlmostEqual(float(o.u[1, 10, 10]), 0.2, places=4)    # 1 h of 3 between 0.1 and 0.4

    def test_never_overwrites(self):
        self.go({})
        with self.assertRaises(bl.BlendError):
            self.go({})

    def test_refuses_when_check_fails(self):
        class Stub:
            normalize_units = staticmethod(lambda s: ("m/s", 1.0))

            @staticmethod
            def load_thresholds():
                return {}

            @staticmethod
            def check(*_):
                return {"result": "fail", "tests": {"speed": {"pass": False}}}
        real = bl._ft
        bl._ft = lambda: Stub
        try:
            with self.assertRaises(bl.BlendError):
                bl.blend(self.radar(), self.model, self.tmp / "x.nc", {}, require_check=True)
            self.assertFalse((self.tmp / "x.nc").exists())
        finally:
            bl._ft = real


if __name__ == "__main__":
    unittest.main()
