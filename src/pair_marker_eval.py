"""The honest marker test: correct a forecast with a DIFFERENT drifter's drift, score it on the target. LLM-free.

src/marker_eval.py used the target's own first hours as the marker (a best case that partly uses the answer). Here
two real drifters that floated near each other play the roles: one is the TARGET (the object being searched for),
the other the MARKER (a datum marker buoy dropped nearby). Then the roles are swapped.

For each 24 h window (start t0, every --every hours) where both drifters have complete hourly tracks and are at
most --max-km apart at t0:
  the target is known ONLY at t0 (its last known position); its later positions are used only for scoring
  the marker is observed for hours 0..M (M in --hours-observed) from its own position at t0
  no marker     forecast the target from t0 for 24 h (model only; OpenDrift OceanDrift, no wind)
  corrected     correction = (marker displacement - forecast marker-centroid displacement) / M over hours 0..M,
                added uniformly to the model currents; the target is forecast again from t0 with corrected currents
  reweighted    3 members (model only x windage 0, 1%, 2%) weighted by exp(-d^2 / (2 SIGMA_KM^2)), d = member's
                marker-centroid distance from the marker at t0 + M; the target is forecast with those weights
  persistence   the target's last known position moved by the marker's mean velocity over 0..M
Scored at t0 + 12 h and t0 + 24 h: distance from the (weighted) target centroid to the target's true position.
Nothing is tuned. 100 particles, K = 10 m2/s.

Data: HYCOM and ERA5 for the pair's period (src/data/pull_fixtures.py, src/data/pull_era5.py); drifters from the GDP
CSV saved by scripts/find_drifters.py. Drogue status of these drifters is not stated in the file.

  python src/pair_marker_eval.py
Outputs (never overwritten): runs/results/pair_marker/{windows.csv, summary.json}.
"""
import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


me = _load("marker_eval", "src/marker_eval.py")
fe, de = me.fe, me.de
N, K, HOURS, SIGMA_KM = me.N, me.K, me.HOURS, me.SIGMA_KM
SCORE_AT = me.SCORE_AT
WINDAGES = (0.0, 0.01, 0.02)
PAIRS = [(300534061391260, 300534061399930), (300534061390370, 300534061391260)]


def tracks(csv, ids):
    t = pd.read_csv(csv, parse_dates=["time"])
    t["time"] = t["time"].dt.floor("h")
    return {i: g.drop_duplicates("time").set_index("time")[["lat", "lon"]].sort_index()
            for i, g in t[t["id"].isin(ids)].groupby("id")}


def pair_windows(a, b, every, max_km, hours=HOURS):
    """Starts where both tracks have every hour of the window and are at most max_km apart at the start."""
    common = a.index.intersection(b.index)
    out = []
    if common.empty:
        return out
    t = common.min().ceil("h")
    while t + pd.Timedelta(hours=hours) <= common.max():
        need = pd.date_range(t, periods=hours + 1, freq="h")
        if need.isin(common).all():
            d0 = float(de.haversine_km(a.loc[t, "lon"], a.loc[t, "lat"], b.loc[t, "lon"], b.loc[t, "lat"]))
            if d0 <= max_km:
                out.append((t, d0))
        t += pd.Timedelta(hours=every)
    return out


def run_window(t0, d0, target, marker, files, ms):
    tobs = target.loc[t0:t0 + pd.Timedelta(hours=HOURS)]
    mobs = marker.loc[t0:t0 + pd.Timedelta(hours=HOURS)]
    tlon, tlat = tobs["lon"].iloc[0], tobs["lat"].iloc[0]          # the target's last known position
    mlon0, mlat0 = mobs["lon"].iloc[0], mobs["lat"].iloc[0]
    truth = {h: (tobs["lon"].iloc[h], tobs["lat"].iloc[h]) for h in SCORE_AT}
    model = [files["model"]]

    def miss(plon, plat, w=None):
        return {f"miss_km_{h}h": float(de.haversine_km(*me.centroid(plon, plat, h, w), *truth[h])) for h in SCORE_AT}

    base = {"start": str(t0), "start_distance_km": d0}
    rows = []
    plon, plat = fe.run(model, tlon, tlat, t0, HOURS, K, 0.0, False, number=N)
    rows.append({**base, "hours_observed": 0, "method": "no marker", **miss(plon, plat)})
    mf = fe.run(model, mlon0, mlat0, t0, HOURS, K, 0.0, False, number=N)                 # forecast of the marker
    members_t = [fe.run(model + ([files["wind"]] if w > 0 else []), tlon, tlat, t0, HOURS, K, w, False, number=N)
                 for w in WINDAGES]
    members_m = [fe.run(model + ([files["wind"]] if w > 0 else []), mlon0, mlat0, t0, HOURS, K, w, False, number=N)
                 for w in WINDAGES]
    for m in ms:
        mlon, mlat = mobs["lon"].iloc[m], mobs["lat"].iloc[m]
        kx = me.KM * np.cos(np.radians(mlat))
        clon, clat = me.centroid(*mf, m)
        du = (mlon - clon) * kx * 1000 / (m * 3600)
        dv = (mlat - clat) * me.KM * 1000 / (m * 3600)
        with tempfile.TemporaryDirectory() as tmp:
            plon, plat = fe.run(me.offset_files(model, du, dv, tmp), tlon, tlat, t0, HOURS, K, 0.0, False, number=N)
            de._READERS.clear()
        rows.append({**base, "hours_observed": m, "method": "corrected", "du_m_s": du, "dv_m_s": dv,
                     **miss(plon, plat)})
        d = np.array([de.haversine_km(*me.centroid(pl, pa, m), mlon, mlat) for pl, pa in members_m])
        wm = np.exp(-d ** 2 / (2 * SIGMA_KM ** 2))
        wm = wm / wm.sum() if wm.sum() > 0 else np.full(len(WINDAGES), 1 / len(WINDAGES))
        plon = np.vstack([p for p, _ in members_t])
        plat = np.vstack([q for _, q in members_t])
        rows.append({**base, "hours_observed": m, "method": "reweighted", "top_windage": WINDAGES[int(np.argmax(wm))],
                     **miss(plon, plat, np.repeat(wm, N))})
        vx, vy = (mlon - mlon0) / m, (mlat - mlat0) / m
        rows.append({**base, "hours_observed": m, "method": "persistence",
                     **{f"miss_km_{h}h": float(de.haversine_km(tlon + vx * h, tlat + vy * h, *truth[h]))
                        for h in SCORE_AT}})
    return rows


def main(argv=None):
    import logging
    import warnings
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drifters", default=str(REPO / "data" / "drifters" / "gdp_hourly_2018-01-01_2025-01-01.csv"))
    ap.add_argument("--model", default=str(REPO / "data" / "hycom_20211108.nc"))
    ap.add_argument("--wind", default=str(REPO / "data" / "era5_wind_20211108.nc"))
    ap.add_argument("--hours-observed", type=int, nargs="+", default=[3, 6])
    ap.add_argument("--every", type=int, default=6)
    ap.add_argument("--max-km", type=float, default=30.0)
    ap.add_argument("--out", default=str(REPO / "runs" / "results" / "pair_marker"))
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if (out / "summary.json").exists():
        sys.exit(f"{out}/summary.json already exists; not overwriting")
    for p in (a.drifters, a.model, a.wind):
        if not Path(p).exists():
            sys.exit(f"{p} not found")
    files = {"model": a.model, "wind": a.wind}
    tr = tracks(a.drifters, {i for p in PAIRS for i in p})
    rows = []
    for x, y in PAIRS:
        for target, marker in ((x, y), (y, x)):
            ws = pair_windows(tr[target], tr[marker], a.every, a.max_km)
            if a.quick:
                ws = ws[:1]
            print(f"target {target}, marker {marker}: {len(ws)} windows", flush=True)
            for t0, d0 in ws:
                rows += [{"target": target, "marker": marker, **r}
                         for r in run_window(t0, d0, tr[target], tr[marker], files, a.hours_observed)]
    df = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "windows.csv", index=False)
    df["distance_band"] = pd.cut(df["start_distance_km"], [0, 10, 20, a.max_km], labels=["0-10 km", "10-20 km",
                                                                                          f"20-{a.max_km:g} km"])
    g = (df.groupby(["hours_observed", "method"], sort=False)
           .agg(windows=("miss_km_24h", "size"), mean_miss_km_12h=("miss_km_12h", "mean"),
                mean_miss_km_24h=("miss_km_24h", "mean"), median_miss_km_24h=("miss_km_24h", "median"))
           .reset_index())
    gb = (df.groupby(["distance_band", "hours_observed", "method"], sort=False, observed=True)
            .agg(windows=("miss_km_24h", "size"), mean_miss_km_24h=("miss_km_24h", "mean")).reset_index())
    summary = {"pairs": [list(map(int, p)) for p in PAIRS], "max_km": a.max_km, "every_h": a.every,
               "particles": N, "K": K, "quick": a.quick, "overall": json.loads(g.round(3).to_json(orient="records")),
               "by_start_distance": json.loads(gb.round(3).to_json(orient="records"))}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("\nall windows: mean miss (km) of the target at 12 h and 24 h")
    for _, r in g.iterrows():
        print(f"  M={r['hours_observed']} {r['method']:12}{r['windows']:>5}{r['mean_miss_km_12h']:>8.1f}"
              f"{r['mean_miss_km_24h']:>8.1f}  (median 24 h {r['median_miss_km_24h']:.1f})")
    print("\nby the marker's distance from the target at t0: mean 24 h miss (km)")
    print(gb.pivot_table(index=["distance_band", "hours_observed"], columns="method", values="mean_miss_km_24h",
                         observed=True).round(1).to_string())
    print(f"\nwrote {out}/windows.csv, summary.json")


if __name__ == "__main__":
    sys.exit(main())
