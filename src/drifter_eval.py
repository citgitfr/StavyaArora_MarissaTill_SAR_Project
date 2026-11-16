"""Score drift forecasts against a real drifter track (ground truth). LLM-free.

Purpose: answer, against reality, (1) does adding HF radar to the model (OpenDrift reader priority) make the forecast
closer to where a real object went, and (2) are the probability-of-containment (POC) maps honest: does the real
drifter fall inside the 50% and 90% areas about 50% and 90% of the time? It also runs the frozen `check` on the real
radar/model pair: a pass means the baseline (and, following SKILL.md, the agent) would choose "fused", so the
forecast comparison shows whether that is the choice reality rewards.

Method: slide a forecast window along the drifter track. At each window start, release PARTICLES particles within
RADIUS_M of the drifter's position, run OceanDrift for --hours with each forcing and each horizontal diffusivity,
and compare with the drifter's hourly positions.
  forcings      model_only = [model]; radar+model = [radar, model] (radar where it has data, else the model)
  diffusivity   random spread in m2/s; 0 = deterministic. Containment depends on it, so several values are run.
Metrics per window, forcing and diffusivity:
  sep_km_<h>h   distance from the particle-cloud centroid to the drifter after h hours
  skill         Liu & Weisberg (2011) normalized cumulative separation skill: s = 1 - c/n (0 if c > n), with
                c = sum_i d_i / sum_i l_i over hourly steps (d = centroid-drifter distance, l = distance the drifter
                has travelled from the start), tolerance n = 1. 1 is perfect, 0 is no skill.
  in50_<h>h, in90_<h>h   drifter inside the 50% / 90% highest-probability area of the cloud
  area50_km2_24h, area90_km2_24h   size of those areas (how big a search area they imply)

Inputs (defaults are the October 2021 window): --radar, --model (larger box than the radar), --drifters (CSV from
scripts/find_drifters.py), --id. Outputs: runs/results/drifter/<id>/windows.csv, summary.json, and
docs/figures/drifter_<id>.png.

Limitations: one drifter (windows overlap, so they are not independent); no wind (an undrogued drifter is also pushed
by wind); the 50%/90% areas use a 0.01 deg grid.

  python src/drifter_eval.py
  python src/drifter_eval.py --every 6 --diffusivity 0 10 50
"""
import argparse
import csv
import importlib.util
import json
import logging
import sys
import warnings
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
DEF_RADAR = REPO / "data" / "hfr_20211018.nc"
DEF_MODEL = REPO / "data" / "hycom_20211018.nc"
DEF_DRIFTERS = REPO / "data" / "drifters" / "gdp_hourly_2018-01-01_2025-01-01.csv"
DEF_ID = 300534061399950
PARTICLES, RADIUS_M = 500, 100.0
TIME_STEP_S, OUTPUT_STEP_S = 900, 3600
REPORT_HOURS = (6, 12, 24)
HDR_CELL_DEG = 0.01
EARTH_KM = 6371.0


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- metrics ---------------------------------------------------------------------------------
def haversine_km(lon1, lat1, lon2, lat2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(a))


def liu_weisberg_skill(mod_lon, mod_lat, obs_lon, obs_lat, n=1.0):
    """Arrays over hourly steps 0..N (index 0 = release). Returns (skill, c)."""
    d = haversine_km(mod_lon[1:], mod_lat[1:], obs_lon[1:], obs_lat[1:])
    step = haversine_km(obs_lon[:-1], obs_lat[:-1], obs_lon[1:], obs_lat[1:])
    l = np.cumsum(step)
    if l.sum() == 0:
        return None, None
    c = float(d.sum() / l.sum())
    return (max(0.0, 1 - c / n), c)


def hdr_membership(p_lon, p_lat, q_lon, q_lat, cell=HDR_CELL_DEG):
    """Highest-density regions of a particle cloud on a regular grid.
    Returns (in50, in90, area50_km2, area90_km2) for the point q."""
    ok = np.isfinite(p_lon) & np.isfinite(p_lat)
    p_lon, p_lat = p_lon[ok], p_lat[ok]
    lo, hi = min(p_lon.min(), q_lon) - cell, max(p_lon.max(), q_lon) + cell
    la, ha = min(p_lat.min(), q_lat) - cell, max(p_lat.max(), q_lat) + cell
    ex, ey = np.arange(lo, hi + cell, cell), np.arange(la, ha + cell, cell)
    h, _, _ = np.histogram2d(p_lon, p_lat, bins=(ex, ey))
    prob = h / h.sum()
    order = np.argsort(prob, axis=None, kind="stable")[::-1]          # densest cell first
    cum = np.cumsum(prob.ravel()[order])
    # a cell is in the X% region if the mass of the denser cells ranked before it is < X
    before = np.concatenate([[0.0], cum[:-1]])
    in_region = {}
    for x in (0.5, 0.9):
        flat = np.zeros(prob.size, bool)
        flat[order] = before < x                                      # back to grid order
        in_region[x] = flat.reshape(prob.shape) & (prob > 0)
    ix = int(np.clip(np.searchsorted(ex, q_lon) - 1, 0, prob.shape[0] - 1))
    iy = int(np.clip(np.searchsorted(ey, q_lat) - 1, 0, prob.shape[1] - 1))
    cell_km2 = (cell * 111.2) ** 2 * np.cos(np.radians(q_lat))
    return (bool(in_region[0.5][ix, iy]), bool(in_region[0.9][ix, iy]),
            float(in_region[0.5].sum() * cell_km2), float(in_region[0.9].sum() * cell_km2))


# ---- drifter windows -------------------------------------------------------------------------
def drifter_track(path, drifter_id, t0, t1):
    df = pd.read_csv(path, parse_dates=["time"])
    g = df[(df["id"] == drifter_id) & (df["time"] >= t0) & (df["time"] <= t1)].sort_values("time")
    return g.set_index("time")[["lat", "lon"]]


def windows(track, hours, every):
    """Start times with a complete hourly track for the next `hours` hours."""
    idx = set(track.index)
    out = []
    t = track.index.min().ceil("h")
    while t + pd.Timedelta(hours=hours) <= track.index.max():
        need = pd.date_range(t, periods=hours + 1, freq="h")
        if all(x in idx for x in need):
            out.append(t)
        t += pd.Timedelta(hours=every)
    return out


# ---- runs ------------------------------------------------------------------------------------
_READERS = {}


def _reader(path):
    """One reader per file, reused across runs: OpenDrift keeps loaded data blocks, so later runs are ~10x faster
    (results are identical)."""
    from opendrift.readers import reader_netCDF_CF_generic as R
    key = str(path)
    if key not in _READERS:
        _READERS[key] = R.Reader(key)
    return _READERS[key]


def run(readers, lon, lat, start, hours, k):
    from opendrift.models.oceandrift import OceanDrift
    o = OceanDrift(loglevel=50)
    o.add_reader([_reader(p) for p in readers])
    o.set_config("environment:fallback:horizontal_diffusivity", float(k))
    o.set_config("environment:fallback:x_wind", 0)
    o.set_config("environment:fallback:y_wind", 0)
    o.set_config("general:coastline_action", "stranding")
    np.random.seed(0)
    o.seed_elements(lon=lon, lat=lat, radius=RADIUS_M, number=PARTICLES, time=start.to_pydatetime())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        o.run(duration=timedelta(hours=hours), time_step=TIME_STEP_S, time_step_output=OUTPUT_STEP_S)
    plon = o.result["lon"].transpose("trajectory", "time").values.astype(float)
    plat = o.result["lat"].transpose("trajectory", "time").values.astype(float)
    for a in (plon, plat):                      # carry stopped particles' last position forward
        for t in range(1, a.shape[1]):
            bad = np.isnan(a[:, t])
            a[bad, t] = a[bad, t - 1]
    return plon, plat


def score_window(plon, plat, obs, hours):
    clon, clat = np.nanmean(plon, axis=0), np.nanmean(plat, axis=0)
    olon, olat = obs["lon"].values, obs["lat"].values
    row = {}
    for h in REPORT_HOURS:
        if h <= hours:
            row[f"sep_km_{h}h"] = float(haversine_km(clon[h], clat[h], olon[h], olat[h]))
            i50, i90, a50, a90 = hdr_membership(plon[:, h], plat[:, h], olon[h], olat[h])
            row[f"in50_{h}h"], row[f"in90_{h}h"] = i50, i90
            if h == hours:
                row[f"area50_km2_{h}h"], row[f"area90_km2_{h}h"] = a50, a90
    row["skill"], row["c"] = liu_weisberg_skill(clon, clat, olon, olat)
    return row


# ---- decision on the real pair ---------------------------------------------------------------
def real_pair_decision(radar, model):
    """What the frozen check and the scripted baseline say about this real radar/model pair."""
    ft = _load("forcing_tools", REPO / ".claude/skills/forcing-tools/forcing_tools.py")
    try:
        chk = ft.check(str(radar), str(model), ft.load_thresholds())
    except Exception as e:              # report, do not crash the evaluation
        return {"error": str(e)}
    return {"result": chk["result"], "failed": [n for n, t in chk["tests"].items() if not t["pass"]],
            "tests": chk["tests"]}


def figure(track, examples, path, drifter_id):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, len(examples), figsize=(5 * len(examples), 5), sharex=True, sharey=True)
    axs = np.atleast_1d(axs)
    for ax, (title, (plon, plat, obs)) in zip(axs, examples.items()):
        ax.plot(track["lon"], track["lat"], color="0.8", lw=1, label="whole drifter track")
        ax.plot(plon[::10].T, plat[::10].T, color="tab:blue", lw=0.3, alpha=0.5)
        ax.plot(plon[:, -1], plat[:, -1], ".", ms=1.5, color="tab:blue", label="particles at 24 h")
        ax.plot(obs["lon"], obs["lat"], color="k", lw=1.5, label="drifter, same 24 h")
        ax.plot(obs["lon"].iloc[0], obs["lat"].iloc[0], "o", color="k", ms=4)
        ax.plot(obs["lon"].iloc[-1], obs["lat"].iloc[-1], "*", color="tab:red", ms=10, label="drifter at 24 h")
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("lon")
    axs[0].set_ylabel("lat")
    axs[0].legend(fontsize=7, loc="best")
    fig.suptitle(f"Drifter {drifter_id}: 24 h forecasts vs. the real track (one example window)", fontsize=10)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main(argv=None):
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radar", default=str(DEF_RADAR))
    ap.add_argument("--model", default=str(DEF_MODEL))
    ap.add_argument("--drifters", default=str(DEF_DRIFTERS))
    ap.add_argument("--id", type=int, default=DEF_ID)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--every", type=int, default=12, help="hours between window starts")
    ap.add_argument("--diffusivity", type=float, nargs="+", default=[0.0, 10.0, 50.0], help="m2/s")
    ap.add_argument("--out", default=None, help="default runs/results/drifter/<id>/")
    a = ap.parse_args(argv)

    radar, model = Path(a.radar), Path(a.model)
    for p in (radar, model, Path(a.drifters)):
        if not p.exists():
            sys.exit(f"{p} not found")
    with xr.open_dataset(radar) as r, xr.open_dataset(model) as m:
        t0 = max(pd.Timestamp(r.time.values[0]), pd.Timestamp(m.time.values[0]))
        t1 = min(pd.Timestamp(r.time.values[-1]), pd.Timestamp(m.time.values[-1]))
    track = drifter_track(a.drifters, a.id, t0, t1)
    starts = windows(track, a.hours, a.every)
    if not starts:
        sys.exit(f"no complete {a.hours} h drifter window inside {t0}..{t1}")
    print(f"drifter {a.id}: {len(track)} hourly positions in {t0}..{t1}; {len(starts)} windows of {a.hours} h "
          f"every {a.every} h; {PARTICLES} particles within {RADIUS_M:g} m; diffusivity {a.diffusivity} m2/s")

    decision = real_pair_decision(radar, model)
    print(f"frozen check on the real pair: {decision.get('result', 'ERROR')} "
          f"{decision.get('failed', decision.get('error', ''))}")

    forcings = {"model_only": [model], "radar+model": [radar, model]}
    rows, example = [], {}
    for n, s in enumerate(starts, 1):
        obs = track.loc[s:s + pd.Timedelta(hours=a.hours)]
        for fname, readers in forcings.items():
            for k in a.diffusivity:
                plon, plat = run(readers, obs["lon"].iloc[0], obs["lat"].iloc[0], s, a.hours, k)
                rows.append({"start": str(s), "forcing": fname, "diffusivity": k, **score_window(plon, plat, obs, a.hours)})
                if n == (len(starts) + 1) // 2 and k == a.diffusivity[min(1, len(a.diffusivity) - 1)]:
                    example[f"{fname} (K={k:g} m2/s), start {s:%m-%d %H:%M}"] = (plon, plat, obs)
        print(f"  window {n}/{len(starts)} {s} done")

    out = Path(a.out) if a.out else REPO / "runs" / "results" / "drifter" / str(a.id)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "windows.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    df = pd.DataFrame(rows)
    summary = {"drifter": a.id, "radar": str(radar.relative_to(REPO)) if radar.is_relative_to(REPO) else str(radar),
               "model": str(model.relative_to(REPO)) if model.is_relative_to(REPO) else str(model),
               "window_hours": a.hours, "windows": len(starts), "every_hours": a.every, "particles": PARTICLES,
               "radius_m": RADIUS_M, "frozen_check_on_real_pair": {k: v for k, v in decision.items() if k != "tests"},
               "results": {}}
    print(f"\n{'forcing':12}{'K':>5}{'sep 6h':>8}{'sep 12h':>8}{'sep 24h':>8}{'skill':>7}"
          f"{'in50 24h':>10}{'in90 24h':>10}{'area90 km2':>12}   (medians over windows; in = share of windows)")
    for (fname, k), g in df.groupby(["forcing", "diffusivity"], sort=False):
        s = {"median_sep_km_6h": g["sep_km_6h"].median(), "median_sep_km_12h": g["sep_km_12h"].median(),
             "median_sep_km_24h": g["sep_km_24h"].median(), "mean_skill": g["skill"].mean(),
             "in50_24h": g["in50_24h"].mean(), "in90_24h": g["in90_24h"].mean(),
             "in50_12h": g["in50_12h"].mean(), "in90_12h": g["in90_12h"].mean(),
             "median_area50_km2_24h": g["area50_km2_24h"].median(), "median_area90_km2_24h": g["area90_km2_24h"].median()}
        summary["results"][f"{fname}|K={k:g}"] = {x: round(float(v), 4) for x, v in s.items()}
        print(f"{fname:12}{k:>5g}{s['median_sep_km_6h']:>8.2f}{s['median_sep_km_12h']:>8.2f}{s['median_sep_km_24h']:>8.2f}"
              f"{s['mean_skill']:>7.2f}{s['in50_24h']:>10.0%}{s['in90_24h']:>10.0%}{s['median_area90_km2_24h']:>12.1f}")
    pivot = df.pivot_table(index=["start", "diffusivity"], columns="forcing", values="sep_km_24h")
    better = float((pivot["radar+model"] < pivot["model_only"]).mean())
    summary["radar_better_share_24h"] = round(better, 4)
    print(f"\nradar+model closer to the drifter than model_only at 24 h in {better:.0%} of window/K pairs")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    fig = REPO / "docs" / "figures" / f"drifter_{a.id}.png"
    figure(track, example, fig, a.id)
    print(f"wrote {(out / 'windows.csv').relative_to(REPO)}, {(out / 'summary.json').relative_to(REPO)}, "
          f"{fig.relative_to(REPO)}")


if __name__ == "__main__":
    sys.exit(main())
