"""Build one blend case folder for the blend investigator (harness code; people run it, the agent does not).

The investigator tunes against a real drifter, so it must never see the drifter's TEST period. This script copies
the forcing files into a case folder and writes ONLY the tuning part of the drifter track (positions up to and
including --split). The test part stays in the repository and is read only by the scoring script.

Case folder contents:
  radar.nc     the radar the cleaning step approved (original or repaired)
  model.nc     ocean model, larger box than the radar
  wind.nc      ERA5 10 m wind      (src/data/pull_era5.py)
  stokes.nc    ERA5 Stokes drift   (src/data/pull_era5.py --stokes)
  drifter_tune.csv   time, lat, lon of the drifter, ONLY times <= split
  case.json    case id, window length, tuning window starts, split time, file hashes

  python src/make_blend_case.py --case-id oct21 --out runs/blend_cases/oct21
"""
import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("forecast_eval", REPO / "src" / "forecast_eval.py")
fe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fe)


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def make_case(out, case_id, radar, model, wind, stokes, drifters, drifter_id, split, hours=24, every=6):
    out = Path(out)
    if out.exists():
        raise SystemExit(f"{out} already exists; not overwriting")
    with xr.open_dataset(radar) as r, xr.open_dataset(model) as m:
        t0 = max(pd.Timestamp(r.time.values[0]), pd.Timestamp(m.time.values[0]))
        t1 = min(pd.Timestamp(r.time.values[-1]), pd.Timestamp(m.time.values[-1]))
    track = fe.de.drifter_track(drifters, drifter_id, t0, t1)
    tune, test = fe.split_windows(fe.de.windows(track, hours, every), hours, split)
    if not tune:
        raise SystemExit("no tuning windows before the split")
    tune_track = track[track.index <= pd.Timestamp(split)]
    out.mkdir(parents=True)
    files = {"radar.nc": radar, "model.nc": model, "wind.nc": wind, "stokes.nc": stokes}
    for name, src in files.items():
        shutil.copyfile(src, out / name)
    tune_track.reset_index().to_csv(out / "drifter_tune.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
    meta = {"case_id": case_id, "window_hours": hours, "every_hours": every, "split": str(pd.Timestamp(split)),
            "tune_windows": [str(t) for t in tune], "n_test_windows_held_back": len(test),
            "sources": {k: str(Path(v)) for k, v in files.items()},
            "sha256": {k: sha256(out / k) for k in [*files, "drifter_tune.csv"]}}
    (out / "case.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--radar", default=str(fe.DEF_RADAR))
    ap.add_argument("--model", default=str(fe.DEF_MODEL))
    ap.add_argument("--wind", default=str(fe.DEF_WIND))
    ap.add_argument("--stokes", default=str(fe.DEF_STOKES))
    ap.add_argument("--drifters", default=str(fe.DEF_DRIFTERS))
    ap.add_argument("--id", type=int, default=fe.de.DEF_ID)
    ap.add_argument("--split", default=fe.DEF_SPLIT)
    a = ap.parse_args(argv)
    meta = make_case(a.out, a.case_id, a.radar, a.model, a.wind, a.stokes, a.drifters, a.id, a.split)
    print(json.dumps({k: meta[k] for k in ("case_id", "split", "n_test_windows_held_back")}
                     | {"tune_windows": len(meta["tune_windows"]), "out": a.out}, indent=2))


if __name__ == "__main__":
    sys.exit(main())
