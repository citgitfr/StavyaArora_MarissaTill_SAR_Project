"""Step 0 of the observation idea: can a marker buoy's first hours of drift correct the forecast? LLM-free.

In a real search a datum marker buoy is dropped near the last known position and reports where it drifts. Here the
"marker" is the drifter itself for the first M hours of each 24 h window: a BEST CASE (a real marker drifts like the
object, not identically). If even this does not shrink the miss, the idea is not worth more data.

For every window (start t0) and M in --hours-observed, everything forecasts hours M..24 from the marker's position at
t0 + M, so all methods know the same thing; only the use of the marker's drift differs:
  restart      the forecast restarted from the marker's position, no correction (what knowing the position alone gives)
  corrected    the forcing plus a uniform velocity offset = (marker displacement - forecast centroid displacement) / M
               over hours 0..M; a corrected current file is written and OpenDrift is re-run
  reweighted   the 9-member forcing ensemble of src/ensemble_eval.py restarted from the marker, each member weighted by
               exp(-d^2 / (2 SIGMA_KM^2)), d = its centroid's distance from the marker at t0 + M
  persistence  the marker's mean velocity over hours 0..M carried straight on (no model at all)
Forcings for restart/corrected: model only, and the hard_switch blend (radar where valid, model in holes).
Scored at t0 + 12 h and t0 + 24 h: distance from the (weighted) centroid to the drifter. 100 particles, K = 10 m2/s
(this measures the track, not the spread). All windows are scored (nothing is tuned); tuning and test halves are
reported separately.

  python src/marker_eval.py                       (about 15 min)
Outputs (never overwritten): runs/results/marker/{windows.csv, summary.json}.
"""
import argparse
import importlib.util
import json
import sys
import tempfile
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


fe = _load("forecast_eval", "src/forecast_eval.py")
ee = _load("ensemble_eval", "src/ensemble_eval.py")
bl = ee.bl
de = fe.de

N, K, HOURS = 100, 10.0, 24
SIGMA_KM = 2.0
SCORE_AT = (12, 24)
KM = 111.195


def centroid(plon, plat, h, w=None):
    return (float(np.average(plon[:, h], weights=w)), float(np.average(plat[:, h], weights=w)))


def offset_files(readers, du, dv, tmp):
    """Copies of the current files (not wind) with u + du, v + dv (m/s) wherever the current has a value."""
    out = []
    for p in readers:
        with xr.open_dataset(p) as ds:
            if "u" not in ds:
                out.append(p)
                continue
            d = ds.load()
        d["u"] = d["u"] + du
        d["v"] = d["v"] + dv
        q = Path(tmp) / f"off_{len(out)}_{Path(p).name}"
        d.to_netcdf(q)
        out.append(str(q))
    return out


def run_window(s, track, forcings, mems, ms):
    obs = track.loc[s:s + pd.Timedelta(hours=HOURS)]
    lon0, lat0 = obs["lon"].iloc[0], obs["lat"].iloc[0]
    rows = []
    full = {name: fe.run(r, lon0, lat0, s, HOURS, K, 0.0, False, number=N) for name, r in forcings.items()}
    ens = [fe.run(r, lon0, lat0, s, HOURS, K, w, False, number=N) for _, r, w in mems]
    for m in ms:
        tm = s + pd.Timedelta(hours=m)
        mlon, mlat = obs["lon"].iloc[m], obs["lat"].iloc[m]
        rest = HOURS - m
        kx = KM * np.cos(np.radians(mlat))
        truth = {h: (obs["lon"].iloc[h], obs["lat"].iloc[h]) for h in SCORE_AT}

        def miss(plon, plat, w=None, offset=0):
            return {f"miss_km_{h}h": float(de.haversine_km(*centroid(plon, plat, h - offset, w), *truth[h]))
                    for h in SCORE_AT}

        # persistence: the marker's mean velocity over 0..m, carried on
        vx = (mlon - lon0) / m
        vy = (mlat - lat0) / m
        rows.append({"start": str(s), "hours_observed": m, "method": "persistence", "forcing": "-",
                     **{f"miss_km_{h}h": float(de.haversine_km(lon0 + vx * h, lat0 + vy * h, *truth[h]))
                        for h in SCORE_AT}})
        for name, readers in forcings.items():
            plon, plat = fe.run(readers, mlon, mlat, tm, rest, K, 0.0, False, number=N)
            rows.append({"start": str(s), "hours_observed": m, "method": "restart", "forcing": name,
                         **miss(plon, plat, offset=m)})
            flon, flat = full[name]
            clon, clat = centroid(flon, flat, m)
            du = ((mlon - lon0) - (clon - lon0)) * kx * 1000 / (m * 3600)
            dv = ((mlat - lat0) - (clat - lat0)) * KM * 1000 / (m * 3600)
            with tempfile.TemporaryDirectory() as tmp:
                plon, plat = fe.run(offset_files(readers, du, dv, tmp), mlon, mlat, tm, rest, K, 0.0, False,
                                    number=N)
                de._READERS.clear()                     # the temporary files are gone after this block
            rows.append({"start": str(s), "hours_observed": m, "method": "corrected", "forcing": name,
                         "du_m_s": du, "dv_m_s": dv, **miss(plon, plat, offset=m)})
        d = np.array([de.haversine_km(*centroid(pl, pa, m), mlon, mlat) for pl, pa in ens])
        wm = np.exp(-d ** 2 / (2 * SIGMA_KM ** 2))
        wm = wm / wm.sum() if wm.sum() > 0 else np.full(len(ens), 1 / len(ens))
        restarted = [fe.run(r, mlon, mlat, tm, rest, K, w, False, number=N) for _, r, w in mems]
        plon = np.vstack([p for p, _ in restarted])
        plat = np.vstack([q for _, q in restarted])
        w = np.repeat(wm, N)
        rows.append({"start": str(s), "hours_observed": m, "method": "reweighted", "forcing": "9-member ensemble",
                     "top_member": mems[int(np.argmax(wm))][0], "top_weight": float(wm.max()),
                     **miss(plon, plat, w, offset=m)})
        rows.append({"start": str(s), "hours_observed": m, "method": "equal-weight ensemble",
                     "forcing": "9-member ensemble", **miss(plon, plat, None, offset=m)})
    for name, (plon, plat) in full.items():                  # no marker at all, from t0 (today's forecast)
        rows.append({"start": str(s), "hours_observed": 0, "method": "no marker", "forcing": name,
                     **{f"miss_km_{h}h": float(de.haversine_km(*centroid(plon, plat, h), *truth[h]))
                        for h in SCORE_AT}})
    return rows


def main(argv=None):
    import logging
    import warnings
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours-observed", type=int, nargs="+", default=[3, 6])
    ap.add_argument("--every", type=int, default=6)
    ap.add_argument("--split", default=fe.DEF_SPLIT)
    ap.add_argument("--out", default=str(REPO / "runs" / "results" / "marker"))
    ap.add_argument("--work", default=str(REPO / "runs" / "work" / "blend" / "ensemble"))
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if (out / "summary.json").exists():
        sys.exit(f"{out}/summary.json already exists; not overwriting")
    files = {"radar": str(fe.DEF_RADAR), "model": str(fe.DEF_MODEL), "wind": str(fe.DEF_WIND)}
    mems = ee.members(files, a.work)
    hard = Path(a.work) / "ens_hard_switch.nc"
    forcings = {"model only": [files["model"]], "hard_switch": [str(hard), files["model"]]}
    track = de.drifter_track(str(fe.DEF_DRIFTERS), de.DEF_ID, pd.Timestamp("2021-10-18"),
                             pd.Timestamp("2021-10-28 23:00"))
    tune, test = fe.split_windows(de.windows(track, HOURS, a.every), HOURS, a.split)
    if a.quick:
        tune, test = tune[:1], test[:1]
    rows = []
    for half, starts in (("tune", tune), ("test", test)):
        for n, s in enumerate(starts, 1):
            rows += [{"half": half, **r} for r in run_window(s, track, forcings, mems, a.hours_observed)]
            print(f"  {half} {n}/{len(starts)} {s} done", flush=True)
    df = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "windows.csv", index=False)
    g = (df.groupby(["half", "hours_observed", "method", "forcing"], sort=False)
           .agg(windows=("miss_km_24h", "size"), mean_miss_km_12h=("miss_km_12h", "mean"),
                mean_miss_km_24h=("miss_km_24h", "mean"), median_miss_km_24h=("miss_km_24h", "median"))
           .reset_index())
    summary = {"particles": N, "K": K, "sigma_km": SIGMA_KM, "hours_observed": a.hours_observed, "quick": a.quick,
               "note": "the marker is the drifter itself: a best case", "table": json.loads(g.round(3).to_json(orient="records"))}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for half in ("tune", "test"):
        print(f"\n{half} windows: mean miss (km) at 12 h and 24 h after t0")
        sub = g[g["half"] == half]
        for _, r in sub.iterrows():
            print(f"  M={r['hours_observed']} {r['method']:22}{r['forcing']:20}{r['mean_miss_km_12h']:>7.1f}"
                  f"{r['mean_miss_km_24h']:>7.1f}")
    print(f"\nwrote {out}/windows.csv, summary.json")


if __name__ == "__main__":
    sys.exit(main())
