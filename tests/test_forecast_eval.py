"""Fast tests for src/forecast_eval.py helpers (no OpenDrift run).

Run from the repository root:  python -m unittest tests.test_forecast_eval -v
"""
import importlib.util
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("forecast_eval", REPO / "src" / "forecast_eval.py")
fe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fe)


class TestSplit(unittest.TestCase):
    def test_no_shared_hours(self):
        starts = list(pd.date_range("2021-10-18", "2021-10-27 18:00", freq="6h"))
        tune, test = fe.split_windows(starts, 24, "2021-10-23 12:00")
        self.assertEqual(tune[-1], pd.Timestamp("2021-10-22 12:00"))       # ends exactly at the split
        self.assertEqual(test[0], pd.Timestamp("2021-10-23 12:00"))
        self.assertLessEqual(tune[-1] + pd.Timedelta(hours=24), test[0])
        self.assertEqual(len(tune), 19)
        self.assertEqual(len(test), 18)
        # windows straddling the split are in neither set
        self.assertNotIn(pd.Timestamp("2021-10-23 00:00"), tune + test)


class TestPick(unittest.TestCase):
    def test_track_lowest_mean_sep_per_forcing(self):
        df = pd.DataFrame([
            {"forcing": "a", "windage": 0.0, "stokes": False, "sep_km_24h": 10, "skill": 0.1},
            {"forcing": "a", "windage": 0.0, "stokes": False, "sep_km_24h": 12, "skill": 0.1},
            {"forcing": "a", "windage": 0.01, "stokes": False, "sep_km_24h": 8, "skill": 0.2},
            {"forcing": "a", "windage": 0.01, "stokes": False, "sep_km_24h": 9, "skill": 0.2},
            {"forcing": "b", "windage": 0.0, "stokes": True, "sep_km_24h": 5, "skill": 0.3},
            {"forcing": "b", "windage": 0.02, "stokes": True, "sep_km_24h": 5, "skill": 0.3},
        ])
        best = fe.pick_track(df)
        self.assertEqual(best["a"]["windage"], 0.01)
        self.assertEqual(best["b"]["windage"], 0.0)          # tie -> less windage

    def test_spread_closest_to_nominal(self):
        rows = []
        for k, hits50, hits90 in [(10, 0, 0), (100, 0.4, 0.8), (400, 0.9, 1.0)]:
            for i in range(10):
                rows.append({"forcing": "a", "K": k, "in50_24h": i < hits50 * 10, "in90_24h": i < hits90 * 10,
                             "area90_km2_24h": k})
        self.assertEqual(fe.pick_spread(pd.DataFrame(rows))["a"]["K"], 100)

    def test_spread_tie_goes_to_smaller_k(self):
        rows = [{"forcing": "a", "K": k, "in50_24h": False, "in90_24h": False, "area90_km2_24h": 1.0}
                for k in (50, 10)]
        self.assertEqual(fe.pick_spread(pd.DataFrame(rows))["a"]["K"], 10)


class TestHdrKde(unittest.TestCase):
    """For a 2-D Gaussian with spread s, the 50% region is a disc of radius 1.177 s and the 90% region one of
    radius 2.146 s (P(r < a s) = 1 - exp(-a^2 / 2)); areas 4.35 s^2 and 14.47 s^2."""

    def cloud(self, s_km, n=2000):
        rng = np.random.default_rng(1)
        kx = 111.195 * np.cos(np.radians(34.5))
        return -121.0 + rng.normal(0, s_km, n) / kx, 34.5 + rng.normal(0, s_km, n) / 111.195, kx

    def test_matches_gaussian_wide_cloud(self):
        s = 10.0
        lon, lat, kx = self.cloud(s)
        for r, in50, in90 in [(0.0, True, True), (0.9, True, True), (1.6, False, True), (2.6, False, False)]:
            res = fe.hdr_kde(lon, lat, -121.0 + r * s / kx, 34.5)
            self.assertEqual((res["in50"], res["in90"]), (in50, in90), f"r = {r} s")
        self.assertAlmostEqual(res["area90_km2"] / (14.47 * s * s), 1.0, delta=0.12)
        self.assertAlmostEqual(res["area50_km2"] / (4.35 * s * s), 1.0, delta=0.15)

    def test_degenerate_cloud_returns_none(self):
        self.assertIsNone(fe.hdr_kde(np.full(50, -121.0), np.full(50, 34.5), -121.0, 34.5))


class TestNoMotion(unittest.TestCase):
    def test_separation_is_distance_travelled_from_start(self):
        obs = pd.DataFrame({"lon": np.full(25, -121.0), "lat": 34.0 + np.arange(25) * 0.01})
        row = fe.no_motion_row(obs, 24)
        self.assertAlmostEqual(row["sep_km_24h"], 0.24 * 111.19, delta=0.1)
        self.assertAlmostEqual(row["sep_km_6h"], 0.06 * 111.19, delta=0.05)


if __name__ == "__main__":
    unittest.main()
