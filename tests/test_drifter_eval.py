"""Fast tests for src/drifter_eval.py metrics and window selection (no OpenDrift run).

Run from the repository root:  python -m unittest tests.test_drifter_eval -v
"""
import importlib.util
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("drifter_eval", REPO / "src" / "drifter_eval.py")
de = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(de)


class TestSkill(unittest.TestCase):
    def setUp(self):
        self.olon = np.linspace(-121.0, -120.8, 25)      # drifter moving east
        self.olat = np.full(25, 34.5)

    def test_perfect_forecast(self):
        s, c = de.liu_weisberg_skill(self.olon, self.olat, self.olon, self.olat)
        self.assertEqual((s, c), (1.0, 0.0))

    def test_staying_put_has_some_skill_then_none(self):
        # a forecast that never moves: d_i equals l_i, so c = 1 and skill 0
        s, c = de.liu_weisberg_skill(np.full(25, -121.0), self.olat, self.olon, self.olat)
        self.assertAlmostEqual(c, 1.0, places=6)
        self.assertAlmostEqual(s, 0.0, places=6)

    def test_half_speed(self):
        half = np.linspace(-121.0, -120.9, 25)
        s, _ = de.liu_weisberg_skill(half, self.olat, self.olon, self.olat)
        self.assertAlmostEqual(s, 0.5, places=2)

    def test_stationary_drifter_returns_none(self):
        self.assertEqual(de.liu_weisberg_skill(self.olon, self.olat, np.full(25, -121.0), self.olat), (None, None))


class TestHDR(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(0)
        self.lon = -121.0 + rng.normal(0, 0.02, 5000)
        self.lat = 34.5 + rng.normal(0, 0.02, 5000)

    def test_centre_inside_both(self):
        i50, i90, a50, a90 = de.hdr_membership(self.lon, self.lat, -121.0, 34.5)
        self.assertTrue(i50 and i90)
        self.assertLess(a50, a90)

    def test_far_point_outside_both(self):
        i50, i90, _, _ = de.hdr_membership(self.lon, self.lat, -120.5, 34.5)
        self.assertFalse(i50 or i90)

    def test_calibrated_cloud(self):
        # points drawn from the cloud's own distribution should land in the 50%/90% areas about that often
        rng = np.random.default_rng(1)
        qs = zip(-121.0 + rng.normal(0, 0.02, 400), 34.5 + rng.normal(0, 0.02, 400))
        hits = np.array([de.hdr_membership(self.lon, self.lat, x, y)[:2] for x, y in qs])
        self.assertAlmostEqual(hits[:, 0].mean(), 0.5, delta=0.1)
        self.assertAlmostEqual(hits[:, 1].mean(), 0.9, delta=0.07)


class TestWindows(unittest.TestCase):
    def test_complete_windows_only(self):
        t = pd.date_range("2021-10-18", periods=60, freq="h")
        tr = pd.DataFrame({"lat": 34.5, "lon": -121.0}, index=t).drop(t[30])     # hour 30 missing
        # every 12 h: 00 (00..24 complete); 12 and 24 contain hour 30; 36 would run past the track
        self.assertEqual(de.windows(tr, 24, 12), [t[0]])
        # every 1 h without hour 0: starts 01..06 are complete, 07..30 contain hour 30, 31..35 are complete
        w = de.windows(tr.drop(t[0]), 24, 1)
        self.assertEqual(w[0], t[1])
        self.assertNotIn(t[7], w)
        self.assertIn(t[31], w)


if __name__ == "__main__":
    unittest.main()
