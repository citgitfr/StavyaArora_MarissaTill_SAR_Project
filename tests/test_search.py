"""Tests for src/search.py (pure numpy/scipy; no OpenDrift).

Run from the repository root:  python -m unittest tests.test_search -v
"""
import importlib.util
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("search", REPO / "src" / "search.py")
se = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(se)


def cloud(n=2000, s_km=5.0, lon0=-121.0, lat0=34.5, seed=0):
    rng = np.random.default_rng(seed)
    kx = 111.195 * np.cos(np.radians(lat0))
    return lon0 + rng.normal(0, s_km, n) / kx, lat0 + rng.normal(0, s_km, n) / 111.195


class TestGreedy(unittest.TestCase):
    def test_two_cells_known_solution(self):
        p = np.array([[0.8, 0.2]])
        z = se.plan_greedy(p, 1.0, 1.0)                    # below ln 4: everything in the denser cell
        self.assertAlmostEqual(z[0, 0], 1.0, places=6)
        self.assertAlmostEqual(z[0, 1], 0.0, places=6)
        z = se.plan_greedy(p, 1.0, 3.0)                    # z1 - z2 = ln 4, z1 + z2 = 3
        self.assertAlmostEqual(z[0, 0], (3 + np.log(4)) / 2, places=5)
        self.assertAlmostEqual(z[0, 1], (3 - np.log(4)) / 2, places=5)

    def test_uniform(self):
        p = np.full((10, 10), 0.01)
        z = se.plan_greedy(p, 2.0, 50.0)
        self.assertTrue(np.allclose(z, 0.5))
        pos = float((p * se.pod(z, 2.0)).sum())
        self.assertAlmostEqual(pos, 1 - np.exp(-50.0 / 200.0), places=6)

    def test_budget_and_optimality(self):
        lon, lat = cloud()
        e = 150.0
        g = se.plan(lon, lat, effort=e, planner="greedy")
        s = se.plan(lon, lat, effort=e, planner="iamsar")
        self.assertAlmostEqual(g["z"].sum(), e, places=4)
        self.assertAlmostEqual(s["z"].sum(), e, places=4)
        self.assertGreaterEqual(g["pos_predicted"], s["pos_predicted"] - 1e-9)
        self.assertIn(s["side_km"], se.SQUARE_SIDES_KM)


class TestGridAndScoring(unittest.TestCase):
    def test_grid_normalized_and_weighted_centre(self):
        lon, lat = cloud(n=1000)
        g, p = se.probability_grid(lon, lat)
        self.assertAlmostEqual(p.sum(), 1.0, places=9)
        w = np.where(lon > -121.0, 3.0, 1.0)
        g2, _ = se.probability_grid(lon, lat, w)
        self.assertGreater(g2.lon0, g.lon0)

    def test_degenerate_cloud(self):
        g, p = se.probability_grid(np.full(20, -121.0), np.full(20, 34.5))
        self.assertAlmostEqual(p.max(), 1.0)

    def test_pod_at_truth(self):
        lon, lat = cloud()
        r = se.plan(lon, lat, effort=150.0, planner="greedy")
        centre = se.pod_at(r, -121.0, 34.5)[0]
        far = se.pod_at(r, -119.0, 34.5)[0]                # about 180 km away: outside the grid
        self.assertGreater(centre, 0.3)
        self.assertEqual(far, 0.0)

    def test_bayes_update(self):
        lon, lat = cloud(n=1000)
        r = se.plan(lon, lat, effort=150.0, planner="greedy")
        w, miss = se.bayes_update(np.ones(lon.size), r, lon, lat)
        self.assertAlmostEqual(w.sum(), 1.0, places=9)
        self.assertAlmostEqual(1 - miss, r["pos_predicted"], delta=0.03)   # particle vs grid estimate of POS
        near = np.hypot((lon + 121.0) * 91.6, (lat - 34.5) * 111.2) < 2
        self.assertLess(w[near].mean(), w[~near].mean())    # searched centre is down-weighted

    def test_effort(self):
        self.assertAlmostEqual(se.effort_km2(), 1.0 * 27.8 * 4.0 * 1)
        self.assertAlmostEqual(se.effort_km2(assets=3), 3 * 111.2, places=6)


if __name__ == "__main__":
    unittest.main()
