"""Tests for src/blend_baseline.py and src/blend_score.py (real October fixtures + data/ ERA5 files; one tuning
window so OpenDrift stays fast).

Run from the repository root:  python -m unittest tests.test_blend_baseline_score -v
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bb = _load("blend_baseline", "src/blend_baseline.py")
bs = _load("blend_score", "src/blend_score.py")
mbc = _load("make_blend_case", "src/make_blend_case.py")
fe = bs.fe
HAVE_DATA = Path(fe.DEF_WIND).exists() and Path(fe.DEF_STOKES).exists()


class TestGrid(unittest.TestCase):
    def test_grid_is_valid_and_names_unique(self):
        names = [bb.spec_name(s) for s in bb.GRID]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(bb.GRID), 28)
        for s in bb.GRID:
            bs.bl.validate(s)                                      # every grid point is on the menu
        self.assertEqual(bb.spec_name({"method": "weighted", "radar_weight": 0.25, "gap_fill_hours": 3}),
                         "weighted_w0p25_g3")


@unittest.skipUnless(HAVE_DATA, "needs the ERA5 files in data/")
class TestBaselineAndScore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.case = cls.tmp / "case"
        mbc.make_case(cls.case, "t", fe.DEF_RADAR, fe.DEF_MODEL, fe.DEF_WIND, fe.DEF_STOKES, fe.DEF_DRIFTERS,
                      fe.de.DEF_ID, fe.DEF_SPLIT)
        m = json.loads((cls.case / "case.json").read_text())
        m["tune_windows"] = m["tune_windows"][:1]
        (cls.case / "case.json").write_text(json.dumps(m))
        cls.files = {"radar": str(fe.DEF_RADAR), "model": str(fe.DEF_MODEL), "wind": str(fe.DEF_WIND),
                     "stokes": str(fe.DEF_STOKES)}

    def test_baseline_decision_format(self):
        dec, ranking = bb.run(self.case, self.tmp / "run", grid=[{"method": "hard_switch"}], windages=(0.0, 0.01),
                              top_n=1, log=lambda *_: None)
        self.assertEqual(dec["policy"], "baseline_grid")
        self.assertIn(dec["stokes"], ("on", "off"))
        self.assertTrue(dec["currents"] == "model_only" or "spec" in dec["currents"])
        self.assertEqual(dec["evaluations_used"], len(ranking))
        self.assertEqual(dec["tuning_mean_sep_km_24h"], ranking[0]["mean_sep_km_24h"])
        # 2 currents in stage 1, then 1 x 2 windages x 2 Stokes (windage 0 / Stokes off is reused free)
        self.assertEqual(dec["evaluations_used"], 2 + 3)

    def test_score_readers(self):
        work = self.tmp / "work"
        r, w, st = bs.readers_for({"currents": "model_only", "windage": 0.0, "stokes": "off"}, self.files, work, "a")
        self.assertEqual((r, w, st), ([self.files["model"]], 0.0, False))
        r, _, _ = bs.readers_for({"currents": "reader_priority", "windage": 0.01, "stokes": "on"}, self.files, work, "b")
        self.assertEqual(r, [self.files["radar"], self.files["model"], self.files["wind"], self.files["stokes"]])
        r, _, _ = bs.readers_for({"currents": {"spec": {"method": "weighted", "radar_weight": 0.5}},
                                  "windage": 0.0, "stokes": "off"}, self.files, work, "ref: c (x)")
        self.assertTrue(Path(r[0]).exists())
        self.assertEqual(r[1], self.files["model"])
        with self.assertRaises(SystemExit):
            bs.readers_for({"currents": "radar_only", "windage": 0, "stokes": "off"}, self.files, work, "d")


if __name__ == "__main__":
    unittest.main()
