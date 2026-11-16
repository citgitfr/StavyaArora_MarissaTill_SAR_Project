"""Fast tests for src/drift.py helpers (no OpenDrift run).

Run from the repository root:  python -m unittest tests.test_drift -v
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("drift", REPO / "src" / "drift.py")
dr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dr)


def grid(path, t0, hours, step_h=1, nan_cols=()):
    t = pd.date_range(t0, periods=hours, freq=f"{step_h}h")
    lat, lon = np.linspace(33.0, 34.0, 21), np.linspace(-119.5, -118.0, 31)
    u = np.full((t.size, lat.size, lon.size), 0.1)
    for c in nan_cols:
        u[:, :, c] = np.nan
    xr.Dataset({"u": (("time", "lat", "lon"), u), "v": (("time", "lat", "lon"), u.copy())},
               coords={"time": t, "lat": lat, "lon": lon}).to_netcdf(path)
    return path


class TestHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_haversine(self):
        self.assertAlmostEqual(float(dr.haversine_km(0, 0, 0, 1)), 111.19, places=1)
        self.assertEqual(float(dr.haversine_km(-118, 33, -118, 33)), 0.0)

    def test_default_start_is_centred_and_fits(self):
        r = grid(self.tmp / "r.nc", "2021-08-10", 48)
        m = grid(self.tmp / "m.nc", "2021-08-10", 17, step_h=3)
        s = dr.default_start(r, m)
        self.assertEqual(s, pd.Timestamp("2021-08-10 11:00"))
        with self.assertRaises(ValueError):
            dr.default_start(grid(self.tmp / "short.nc", "2021-08-10", 12), m)

    def test_seed_points_inside_margin_and_coverage(self):
        r = grid(self.tmp / "r.nc", "2021-08-10", 48, nan_cols=range(0, 15))
        pts = dr.seed_points(r, pd.Timestamp("2021-08-10 11:00"), n=4)
        self.assertEqual(len(pts), 4)
        self.assertEqual(len(set(pts)), 4)
        for lon, lat in pts:
            self.assertTrue(33.0 + dr.EDGE_MARGIN_DEG <= lat <= 34.0 - dr.EDGE_MARGIN_DEG)
            self.assertTrue(-118.0 - dr.EDGE_MARGIN_DEG >= lon >= -119.5 + 15 * 0.05 - 1e-9)   # NaN columns excluded

    def test_compare_identical_and_shifted(self):
        lon = np.tile(np.linspace(-118.5, -118.4, 25), (50, 1))
        lat = np.full_like(lon, 33.5)
        same = dr.compare((lon, lat), (lon, lat))
        self.assertEqual((same["sep_km_24h"], same["poc_overlap"]), (0.0, 1.0))
        moved = dr.compare((lon, lat + 0.1), (lon, lat))
        self.assertAlmostEqual(moved["sep_km_24h"], 11.12, places=1)
        self.assertEqual(moved["poc_overlap"], 0.0)

    def test_forcing_hash_ignores_attributes(self):
        a = grid(self.tmp / "a.nc", "2021-08-10", 6)
        with xr.open_dataset(a) as ds:
            b = ds.load()
        b.attrs["history"] = "different"
        b.to_netcdf(self.tmp / "b.nc")
        self.assertEqual(dr.forcing_hash(a, None), dr.forcing_hash(self.tmp / "b.nc", None))
        self.assertNotEqual(dr.forcing_hash(a, None), dr.forcing_hash(None, None))

    def test_ffill(self):
        a = np.array([[1.0, np.nan, np.nan], [1.0, 2.0, np.nan]])
        np.testing.assert_array_equal(dr._ffill(a), [[1, 1, 1], [1, 2, 2]])


if __name__ == "__main__":
    unittest.main()
