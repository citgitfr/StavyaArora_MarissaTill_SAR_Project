"""Lab 4, Phase A: add wind (and wave Stokes drift) to the drift forecast, tune it on the early part of the real
drifter's track, and score it on the late part. LLM-free.

Why: Lab 3 forecasts used currents only and missed the (undrogued) drifter by about 16 km at 24 h, and their 90%
areas almost never contained it. Here each forecast setup is

  forcing      model_only = [HYCOM] ; radar+model = [HF radar, HYCOM] (reader priority, as in Lab 3)
  windage      wind_drift_factor: the object moves at this fraction of the ERA5 10 m wind (0 = no wind)
  stokes       off, or ERA5 wave-model Stokes drift
  K            horizontal diffusivity (m2/s): the random spread that sets the size of the 50% / 90% areas

Windage and Stokes are tuned separately for each forcing, because HF radar already measures part of the wind- and
wave-driven surface motion and HYCOM does not (counting it twice makes particles overshoot downwind).

Data split (one drifter, so this is early evidence, not a held-out test across drifters):
  24 h windows start every --every hours. TUNE = windows that END at or before --split; TEST = windows that START at
  or after --split. No drifter hour is used by both. Defaults: split 2021-10-23 12:00 UTC.
Tuning, two stages, on TUNE windows only:
  1. track:  for each forcing, the (windage, stokes) with the lowest mean 24 h separation, at a small fixed K.
  2. spread: for that setup, the K whose 50% / 90% hit rates at 24 h are closest to 50% / 90%
             (score |in50 - 0.5| + |in90 - 0.9|; ties go to the smaller K, i.e. the smaller search area).
Test: the tuned setups and the Lab 3 currents-only setups are scored on TEST windows, with a no-motion reference.
Metrics (src/drifter_eval.py, except the areas): separation of the particle-cloud centroid from the drifter at 6/12/24 h,
Liu & Weisberg skill, drifter inside the 50% / 90% area, size of those areas. The 50% / 90% areas come from a
kernel density estimate (hdr_kde below), not drifter_eval's 0.01 deg histogram: with 500 particles the histogram
cannot represent a wide cloud (most cells empty, area capped near 500 cells), so it undercounts hits as the spread
grows (on a 500-particle Gaussian cloud with a 10 km spread it reports a 90% area of 372 km2 against a true
1447 km2, and misses a point one spread from the centre). On clouds 1 km wide, the size of Lab 3's K=10 clouds,
the two agree, so Lab 3's containment results stand. tests/test_forecast_eval.py checks hdr_kde against the exact
Gaussian answer.

Outputs (runs/results/forecast/ by default): tune_track.csv, tune_spread.csv, test.csv, summary.json.

  python src/forecast_eval.py                    # full run (about 15 min)
  python src/forecast_eval.py --quick            # small grid, a few windows: smoke test (about 1 min)
"""
import argparse
import importlib.util
import json
import logging
import sys
import warnings
from datetime import timedelta
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("drifter_eval", REPO / "src" / "drifter_eval.py")
de = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(de)

DEF_RADAR = REPO / "fixtures" / "hfr_20211018.nc"
DEF_MODEL = REPO / "fixtures" / "hycom_20211018.nc"
DEF_WIND = REPO / "data" / "era5_wind_20211018.nc"
DEF_STOKES = REPO / "data" / "era5_stokes_20211018.nc"
DEF_DRIFTERS = REPO / "fixtures" / "drifter_300534061399950_202110.csv"
DEF_SPLIT = "2021-10-23 12:00"
WINDAGES = (0.0, 0.005, 0.01, 0.015, 0.02, 0.03)
STOKES = (False, True)
TRACK_K = 10.0
SPREAD_KS = (10.0, 50.0, 100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0)
FORCINGS = ("model_only", "radar+model")


# ---- pure helpers (tested without OpenDrift) ----------------------------------------------------
def split_windows(starts, hours, split):
    """TUNE = windows ending at or before split; TEST = windows starting at or after split."""
    split = pd.Timestamp(split)
    h = pd.Timedelta(hours=hours)
    return [s for s in starts if s + h <= split], [s for s in starts if s >= split]


def pick_track(df):
    """Per forcing, the (windage, stokes) row with the lowest mean 24 h separation. Ties: less windage, no Stokes."""
    g = (df.groupby(["forcing", "windage", "stokes"], as_index=False)
           .agg(mean_sep_km_24h=("sep_km_24h", "mean"), mean_skill=("skill", "mean"), n=("sep_km_24h", "size")))
    g = g.sort_values(["forcing", "mean_sep_km_24h", "windage", "stokes"])
    return {f: r.to_dict() for f, r in g.groupby("forcing").head(1).set_index("forcing").iterrows()}


def pick_spread(df):
    """Per forcing, the K whose 24 h hit rates are closest to 50% / 90%. Ties: smaller K."""
    g = (df.groupby(["forcing", "K"], as_index=False)
           .agg(in50_24h=("in50_24h", "mean"), in90_24h=("in90_24h", "mean"),
                median_area90_km2_24h=("area90_km2_24h", "median")))
    g["calib_error"] = (g["in50_24h"] - 0.5).abs() + (g["in90_24h"] - 0.9).abs()
    g = g.sort_values(["forcing", "calib_error", "K"])
    return {f: r.to_dict() for f, r in g.groupby("forcing").head(1).set_index("forcing").iterrows()}


def no_motion_row(obs, hours):
    """The reference forecast that the object stays where it was: separation and Liu & Weisberg skill."""
    olon, olat = obs["lon"].values, obs["lat"].values
    row = {f"sep_km_{h}h": float(de.haversine_km(olon[0], olat[0], olon[h], olat[h]))
           for h in de.REPORT_HOURS if h <= hours}
    row["skill"] = de.liu_weisberg_skill(np.full_like(olon, olon[0]), np.full_like(olat, olat[0]), olon, olat)[0]
    return row


def hdr_kde(p_lon, p_lat, q_lon, q_lat, levels=(0.5, 0.9), ngrid=160):
    """Highest-density regions of a particle cloud from a Gaussian kernel density estimate (KDE).

    Replaces drifter_eval.hdr_membership (a 0.01 deg histogram) for wide clouds: with 500 particles a histogram
    leaves most cells of a wide cloud empty, so the drifter's cell is often empty (counted as outside) and the
    area can never exceed about 500 cells. Here the X% region is {density >= t_X}, where t_X is the (1 - X)
    quantile of the density at the particles (the standard sample-based HDR estimate). Area: density on a grid
    over the cloud plus 3 bandwidths. Coordinates are local km around the cloud centre.
    Returns {"in50": bool, "in90": bool, "area50_km2": float, "area90_km2": float}; None if the cloud is degenerate
    (all particles at one point), where a KDE is undefined."""
    from scipy.stats import gaussian_kde
    ok = np.isfinite(p_lon) & np.isfinite(p_lat)
    p_lon, p_lat = p_lon[ok], p_lat[ok]
    lat0, lon0 = float(np.mean(p_lat)), float(np.mean(p_lon))
    kx = 111.195 * np.cos(np.radians(lat0))
    x, y = (p_lon - lon0) * kx, (p_lat - lat0) * 111.195
    qx, qy = (q_lon - lon0) * kx, (q_lat - lat0) * 111.195
    if len(x) < 5 or np.std(x) < 1e-6 or np.std(y) < 1e-6:
        return None
    try:
        kde = gaussian_kde(np.vstack([x, y]))
    except np.linalg.LinAlgError:
        return None
    dens_p = kde(np.vstack([x, y]))
    dens_q = float(kde([[qx], [qy]])[0])
    bw = 3 * np.sqrt(np.diag(kde.covariance))
    gx = np.linspace(x.min() - bw[0], x.max() + bw[0], ngrid)
    gy = np.linspace(y.min() - bw[1], y.max() + bw[1], ngrid)
    GX, GY = np.meshgrid(gx, gy)
    dens_g = kde(np.vstack([GX.ravel(), GY.ravel()]))
    cell = (gx[1] - gx[0]) * (gy[1] - gy[0])
    out = {}
    for lv in levels:
        t = float(np.quantile(dens_p, 1 - lv))
        out[f"in{int(lv * 100)}"] = bool(dens_q >= t)
        out[f"area{int(lv * 100)}_km2"] = float((dens_g >= t).sum() * cell)
    return out


def score_window(plon, plat, obs, hours):
    """drifter_eval.score_window, with the 50% / 90% areas from hdr_kde (histogram fallback if the KDE is undefined)."""
    row = de.score_window(plon, plat, obs, hours)
    olon, olat = obs["lon"].values, obs["lat"].values
    for h in de.REPORT_HOURS:
        if h > hours:
            continue
        r = hdr_kde(plon[:, h], plat[:, h], olon[h], olat[h])
        row[f"hdr_method_{h}h"] = "kde" if r else "histogram"
        if r:
            row[f"in50_{h}h"], row[f"in90_{h}h"] = r["in50"], r["in90"]
            if h == hours:
                row[f"area50_km2_{h}h"], row[f"area90_km2_{h}h"] = r["area50_km2"], r["area90_km2"]
    return row


# ---- OpenDrift run ------------------------------------------------------------------------------
def run(readers, lon, lat, start, hours, k, windage, stokes, number=None):
    """Like drifter_eval.run, plus wind/Stokes readers and a wind drift factor. Same seed and radius; `number`
    particles (default drifter_eval.PARTICLES = 500)."""
    from opendrift.models.oceandrift import OceanDrift
    o = OceanDrift(loglevel=50)
    o.add_reader([de._reader(p) for p in readers])
    o.set_config("environment:fallback:horizontal_diffusivity", float(k))
    o.set_config("environment:fallback:x_wind", 0)
    o.set_config("environment:fallback:y_wind", 0)
    o.set_config("drift:stokes_drift", bool(stokes))
    o.set_config("general:coastline_action", "stranding")
    np.random.seed(0)
    o.seed_elements(lon=lon, lat=lat, radius=de.RADIUS_M, number=number or de.PARTICLES,
                    time=start.to_pydatetime(), wind_drift_factor=float(windage))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        o.run(duration=timedelta(hours=hours), time_step=de.TIME_STEP_S, time_step_output=de.OUTPUT_STEP_S)
    plon = o.result["lon"].transpose("trajectory", "time").values.astype(float)
    plat = o.result["lat"].transpose("trajectory", "time").values.astype(float)
    n_out = hours * 3600 // de.OUTPUT_STEP_S + 1
    if plon.shape[1] < n_out:
        pad = np.full((plon.shape[0], n_out - plon.shape[1]), np.nan)
        plon, plat = np.hstack([plon, pad]), np.hstack([plat, pad])
    for a in (plon, plat):
        for t in range(1, a.shape[1]):
            bad = np.isnan(a[:, t])
            a[bad, t] = a[bad, t - 1]
    return plon, plat


class Runner:
    def __init__(self, a, track):
        self.a, self.track = a, track
        self.currents = {"model_only": [a.model], "radar+model": [a.radar, a.model]}

    def score(self, start, forcing, windage, stokes, k):
        obs = self.track.loc[start:start + pd.Timedelta(hours=self.a.hours)]
        readers = list(self.currents[forcing])
        if windage > 0:
            readers.append(self.a.wind)
        if stokes:
            readers.append(self.a.stokes)
        plon, plat = run(readers, obs["lon"].iloc[0], obs["lat"].iloc[0], start, self.a.hours, k, windage, stokes)
        return {"start": str(start), "forcing": forcing, "windage": windage, "stokes": stokes, "K": k,
                **score_window(plon, plat, obs, self.a.hours)}


def summarize(df):
    agg = {"windows": ("sep_km_24h", "size"),
           "median_sep_km_6h": ("sep_km_6h", "median"), "median_sep_km_12h": ("sep_km_12h", "median"),
           "median_sep_km_24h": ("sep_km_24h", "median"), "mean_sep_km_24h": ("sep_km_24h", "mean"),
           "mean_skill": ("skill", "mean")}
    for c in ("in50_12h", "in90_12h", "in50_24h", "in90_24h"):
        if c in df:
            agg[c] = (c, "mean")
    if "area90_km2_24h" in df:
        agg["median_area50_km2_24h"] = ("area50_km2_24h", "median")
        agg["median_area90_km2_24h"] = ("area90_km2_24h", "median")
    return df.groupby("setup", sort=False).agg(**agg).reset_index()


def main(argv=None):
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radar", default=str(DEF_RADAR))
    ap.add_argument("--model", default=str(DEF_MODEL))
    ap.add_argument("--wind", default=str(DEF_WIND))
    ap.add_argument("--stokes", default=str(DEF_STOKES))
    ap.add_argument("--drifters", default=str(DEF_DRIFTERS))
    ap.add_argument("--id", type=int, default=de.DEF_ID)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--every", type=int, default=6, help="hours between window starts")
    ap.add_argument("--split", default=DEF_SPLIT, help="UTC time between the tuning and test windows")
    ap.add_argument("--out", default=str(REPO / "runs" / "results" / "forecast"))
    ap.add_argument("--quick", action="store_true", help="smoke test: 3 windows each side, tiny grid")
    a = ap.parse_args(argv)
    for p in (a.radar, a.model, a.wind, a.stokes, a.drifters):
        if not Path(p).exists():
            sys.exit(f"{p} not found")

    with xr.open_dataset(a.radar) as r, xr.open_dataset(a.model) as m:
        t0 = max(pd.Timestamp(r.time.values[0]), pd.Timestamp(m.time.values[0]))
        t1 = min(pd.Timestamp(r.time.values[-1]), pd.Timestamp(m.time.values[-1]))
    track = de.drifter_track(a.drifters, a.id, t0, t1)
    tune, test = split_windows(de.windows(track, a.hours, a.every), a.hours, a.split)
    windages, ks = WINDAGES, SPREAD_KS
    if a.quick:
        tune, test, windages, ks = tune[:3], test[:3], (0.0, 0.02), (10.0, 200.0)
    if not tune or not test:
        sys.exit(f"need windows on both sides of --split {a.split}: tune {len(tune)}, test {len(test)}")
    print(f"drifter {a.id}: {len(tune)} tuning windows ({tune[0]} .. {tune[-1]}), {len(test)} test windows "
          f"({test[0]} .. {test[-1]}); {a.hours} h each, every {a.every} h; split {a.split}")
    R = Runner(a, track)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    # ---- stage 1: track (windage, stokes) per forcing, on TUNE --------------------------------------
    grid = list(product(FORCINGS, windages, STOKES))
    print(f"\nstage 1: {len(grid)} setups x {len(tune)} windows at K={TRACK_K:g}")
    rows = []
    for i, (f, w, s) in enumerate(grid, 1):
        rows += [R.score(t, f, w, s, TRACK_K) for t in tune]
        print(f"  {i}/{len(grid)} {f} windage {w:g} stokes {s}: mean sep 24 h "
              f"{np.mean([r['sep_km_24h'] for r in rows[-len(tune):]]):.1f} km")
    track_df = pd.DataFrame(rows)
    track_df.to_csv(out / "tune_track.csv", index=False)
    best = pick_track(track_df)

    # ---- stage 2: spread (K) per forcing, on TUNE ---------------------------------------------------
    print(f"\nstage 2: K in {list(ks)} for the stage-1 setups")
    rows = []
    for f in FORCINGS:
        w, s = best[f]["windage"], bool(best[f]["stokes"])
        for k in ks:
            rows += [R.score(t, f, w, s, k) for t in tune]
    spread_df = pd.DataFrame(rows)
    spread_df.to_csv(out / "tune_spread.csv", index=False)
    kbest = pick_spread(spread_df)

    # ---- test ---------------------------------------------------------------------------------------
    setups = {f"{f} currents only (Lab 3), K={TRACK_K:g}": (f, 0.0, False, TRACK_K) for f in FORCINGS}
    for f in FORCINGS:
        w, s, k = best[f]["windage"], bool(best[f]["stokes"]), kbest[f]["K"]
        setups[f"{f} tuned: windage {w:g}, stokes {'on' if s else 'off'}, K={k:g}"] = (f, w, s, k)
    print(f"\ntest: {len(setups)} setups x {len(test)} windows")
    rows = []
    for name, (f, w, s, k) in setups.items():
        rows += [{"setup": name, **R.score(t, f, w, s, k)} for t in test]
    for t in test:
        rows.append({"setup": "no-motion reference", "start": str(t),
                     **no_motion_row(track.loc[t:t + pd.Timedelta(hours=a.hours)], a.hours)})
    test_df = pd.DataFrame(rows)
    test_df.to_csv(out / "test.csv", index=False)
    table = summarize(test_df)

    summary = {
        "drifter": a.id, "split": a.split, "window_hours": a.hours, "every_hours": a.every,
        "tune_windows": [str(t) for t in tune], "test_windows": [str(t) for t in test],
        "inputs": {k: str(Path(v).relative_to(REPO)) if Path(v).is_relative_to(REPO) else str(v)
                   for k, v in {"radar": a.radar, "model": a.model, "wind": a.wind, "stokes": a.stokes,
                                "drifters": a.drifters}.items()},
        "particles": de.PARTICLES, "radius_m": de.RADIUS_M, "quick": a.quick,
        "grid": {"windage": list(windages), "stokes": list(STOKES), "track_K": TRACK_K, "spread_K": list(ks)},
        "tuned_track": {f: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}
                        for f, d in best.items()},
        "tuned_spread": {f: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}
                         for f, d in kbest.items()},
        "test": json.loads(table.round(4).to_json(orient="records")),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("\ntuned on the early windows:")
    for f in FORCINGS:
        print(f"  {f:12} windage {best[f]['windage']:g}, stokes {bool(best[f]['stokes'])}, K {kbest[f]['K']:g} "
              f"(tuning: mean sep 24 h {best[f]['mean_sep_km_24h']:.1f} km, in50 {kbest[f]['in50_24h']:.0%}, "
              f"in90 {kbest[f]['in90_24h']:.0%})")
    print(f"\nTEST windows ({len(test)}), medians of separation; in = share of windows")
    print(f"{'setup':62}{'6h':>6}{'12h':>6}{'24h':>6}{'skill':>7}{'in50':>6}{'in90':>6}{'area90':>8}")
    for _, r in table.iterrows():
        print(f"{r['setup']:62}{r['median_sep_km_6h']:>6.1f}{r['median_sep_km_12h']:>6.1f}"
              f"{r['median_sep_km_24h']:>6.1f}{r['mean_skill']:>7.2f}"
              + (f"{r['in50_24h']:>6.0%}{r['in90_24h']:>6.0%}{r['median_area90_km2_24h']:>8.0f}"
                 if pd.notna(r.get("in50_24h")) else f"{'-':>6}{'-':>6}{'-':>8}"))
    print(f"\nwrote {out}/tune_track.csv, tune_spread.csv, test.csv, summary.json")


if __name__ == "__main__":
    sys.exit(main())
