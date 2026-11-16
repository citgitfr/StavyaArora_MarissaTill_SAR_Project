"""Drift step for the SAR forcing benchmark: OpenDrift runs from each decision's forcing, POC maps, separation.

Blend (Lab 3): OpenDrift reader priority. Readers are stacked [radar, model]; for every particle at every step
OpenDrift uses the radar if it has a valid value there and then, otherwise the model (a per-particle hard switch,
see opendrift/models/basemodel/environment.py get_environment). model_only stacks [model] alone.

Model: OceanDrift (currents only). Wind is not supplied (fallback 0, so the default wind drift factor has no effect),
horizontal diffusivity 0, coastline action "stranding". Runs are deterministic. Where neither reader covers a
particle, OpenDrift's current fallback is 0 (the particle stops). In this dataset the model box is the same box as
the radar's, so release points are kept EDGE_MARGIN_DEG inside the grid edge.

Release: SEED_POINTS points chosen from the clean radar's well-covered cells (valid in >= 90% of the run's hours),
spread out by farthest-point sampling; PER_POINT particles per point within RADIUS_M; one release time, 24 h run.
The same release is used for every run, so particles can be paired across runs.

Scoring (decision impact, LLM-free): every run is compared with a REFERENCE run on the clean radar + model.
  sep_km_<h>h   median distance between paired particles after h hours (6, 12, 24)
  poc_overlap   overlap of the 24 h probability-of-containment maps (sum of min of the two normalized histograms,
                0 = disjoint, 1 = identical)
The reference is itself a reader-priority (hard switch) run, so this judges QC decisions, NOT blend methods.

Run from the repository root:
  python src/drift.py evaluate                       # policies x cases -> runs/results/drift/
  python src/drift.py evaluate --figure case10       # also a 3-panel figure for one case
  python src/drift.py run --radar runs/cases/case01/radar.nc --model runs/cases/case01/model.nc
"""
import argparse
import csv
import hashlib
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
CLEAN = REPO / "fixtures" / "hfr_2km_slice.nc"
MODEL = REPO / "fixtures" / "hycom_uv_slice.nc"
CASES = REPO / "runs" / "cases"
CACHE = REPO / "runs" / "work" / "drift"
OUT = REPO / "runs" / "results" / "drift"
HOURS = 24
SEP_HOURS = (6, 12, 24)
SEED_POINTS, PER_POINT, RADIUS_M = 5, 200, 1000.0
MIN_COVER = 0.9
EDGE_MARGIN_DEG = 0.25           # about 25 km: more than a particle travels in 24 h at typical speeds here
POC_CELL_DEG = 0.02
TIME_STEP_S, OUTPUT_STEP_S = 900, 3600
EARTH_KM = 6371.0

_spec = importlib.util.spec_from_file_location("score", REPO / "src" / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)


# ---- release ---------------------------------------------------------------------------------
def default_start(radar, model, hours=HOURS):
    """Centre the run in the window both files cover, on a whole hour."""
    with xr.open_dataset(radar) as r, xr.open_dataset(model) as m:
        t0 = max(pd.Timestamp(r.time.values[0]), pd.Timestamp(m.time.values[0]))
        t1 = min(pd.Timestamp(r.time.values[-1]), pd.Timestamp(m.time.values[-1]))
    slack = (t1 - t0) - pd.Timedelta(hours=hours)
    if slack < pd.Timedelta(0):
        raise ValueError(f"shared window {t0}..{t1} is shorter than {hours} h")
    return (t0 + slack / 2).floor("h")


def seed_points(clean, start, hours=HOURS, n=SEED_POINTS, min_cover=MIN_COVER, margin=EDGE_MARGIN_DEG):
    """n well-separated (lon, lat) points where the clean radar is valid in >= min_cover of the run's hours,
    at least `margin` degrees inside the grid edge (particles that leave every reader get zero current)."""
    with xr.open_dataset(clean) as ds:
        win = ds.sel(time=slice(start, start + pd.Timedelta(hours=hours)))
        valid = (np.isfinite(win["u"]) & np.isfinite(win["v"])).mean("time").values
        lat, lon = ds["lat"].values, ds["lon"].values
    inside = ((lat[:, None] >= lat.min() + margin) & (lat[:, None] <= lat.max() - margin)
              & (lon[None, :] >= lon.min() + margin) & (lon[None, :] <= lon.max() - margin))
    iy, ix = np.nonzero((valid >= min_cover) & inside)
    if iy.size < n:
        raise ValueError(f"only {iy.size} radar cells are valid in >= {min_cover:.0%} of hours")
    pts = np.column_stack([lon[ix], lat[iy]])
    km = np.column_stack([pts[:, 0] * np.cos(np.radians(pts[:, 1].mean())), pts[:, 1]])
    chosen = [int(np.argmin(np.hypot(*(km - km.mean(0)).T)))]       # start nearest the centroid
    d = np.hypot(*(km - km[chosen[0]]).T)
    while len(chosen) < n:                                          # farthest-point sampling
        nxt = int(np.argmax(d))
        chosen.append(nxt)
        d = np.minimum(d, np.hypot(*(km - km[nxt]).T))
    return [(float(pts[i, 0]), float(pts[i, 1])) for i in chosen]


# ---- running ---------------------------------------------------------------------------------
def forcing_hash(radar, model):
    """Hash of the data a run depends on (u, v, time of each file), not file bytes or attributes."""
    h = hashlib.sha256()
    for p in (radar, model):
        if p is None:
            h.update(b"none")
            continue
        with xr.open_dataset(p) as ds:
            for k in ("time", "u", "v"):
                h.update(np.ascontiguousarray(ds[k].values).tobytes())
    return h.hexdigest()[:16]


def run_drift(radar, model, points, start, hours=HOURS):
    """One OceanDrift run. radar=None means model only. Returns lon, lat arrays of shape (particles, outputs)."""
    from opendrift.models.oceandrift import OceanDrift
    from opendrift.readers import reader_netCDF_CF_generic as R
    o = OceanDrift(loglevel=50)
    o.add_reader([R.Reader(str(p)) for p in ([radar] if radar else []) + [model]])
    o.set_config("environment:fallback:horizontal_diffusivity", 0)     # no reader supplies it, so this is used
    o.set_config("environment:fallback:x_wind", 0)
    o.set_config("environment:fallback:y_wind", 0)
    o.set_config("general:coastline_action", "stranding")
    np.random.seed(0)                                   # the seeding radius uses numpy's random generator
    for lon, lat in points:
        o.seed_elements(lon=lon, lat=lat, radius=RADIUS_M, number=PER_POINT, time=start.to_pydatetime())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        o.run(duration=timedelta(hours=hours), time_step=TIME_STEP_S, time_step_output=OUTPUT_STEP_S)
    lon = o.result["lon"].transpose("trajectory", "time").values.astype(float)
    lat = o.result["lat"].transpose("trajectory", "time").values.astype(float)
    return _ffill(lon), _ffill(lat)


def _ffill(a):
    """Carry the last position forward where OpenDrift wrote NaN after a particle stopped."""
    out = a.copy()
    for t in range(1, out.shape[1]):
        bad = np.isnan(out[:, t])
        out[bad, t] = out[bad, t - 1]
    return out


def cached_run(radar, model, points, start, cache=CACHE):
    key = forcing_hash(radar, model) + "_" + hashlib.sha256(json.dumps([points, str(start)]).encode()).hexdigest()[:8]
    path = Path(cache) / f"{key}.npz"
    if path.exists():
        z = np.load(path)
        return z["lon"], z["lat"], key
    lon, lat = run_drift(radar, model, points, start)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, lon=lon, lat=lat)
    return lon, lat, key


# ---- metrics ---------------------------------------------------------------------------------
def haversine_km(lon1, lat1, lon2, lat2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(a))


def poc_map(lon, lat, edges):
    h, _, _ = np.histogram2d(lon, lat, bins=edges)
    return h / h.sum() if h.sum() else h


def poc_edges(*runs):
    lo = min(np.nanmin(r[0][:, -1]) for r in runs) - POC_CELL_DEG
    hi = max(np.nanmax(r[0][:, -1]) for r in runs) + POC_CELL_DEG
    la = min(np.nanmin(r[1][:, -1]) for r in runs) - POC_CELL_DEG
    ha = max(np.nanmax(r[1][:, -1]) for r in runs) + POC_CELL_DEG
    return (np.arange(lo, hi + POC_CELL_DEG, POC_CELL_DEG), np.arange(la, ha + POC_CELL_DEG, POC_CELL_DEG))


def compare(run, ref):
    out = {}
    for h in SEP_HOURS:
        t = h * 3600 // OUTPUT_STEP_S
        d = haversine_km(run[0][:, t], run[1][:, t], ref[0][:, t], ref[1][:, t])
        out[f"sep_km_{h}h"] = float(np.nanmedian(d))
        if h == HOURS:
            out[f"mean_sep_km_{h}h"] = float(np.nanmean(d))
            out[f"p90_sep_km_{h}h"] = float(np.nanpercentile(d, 90))
    edges = poc_edges(run, ref)
    p, q = poc_map(run[0][:, -1], run[1][:, -1], edges), poc_map(ref[0][:, -1], ref[1][:, -1], edges)
    out["poc_overlap"] = float(np.minimum(p, q).sum())
    return out


# ---- evaluate --------------------------------------------------------------------------------
def policies(agent_runs, baseline_dir, baseline_work=None, cases_dir=CASES):
    ids = sorted(p.name for p in Path(cases_dir).glob("case*") if p.is_dir())
    pols = {"always_model_only": ({c: {"choice": "model_only"} for c in ids}, None),
            "always_fused": ({c: {"choice": "fused"} for c in ids}, None),
            "baseline": (score.load_decisions(baseline_dir),
                         Path(baseline_work) if baseline_work else REPO / "runs" / "work" / "baseline")}
    for r in agent_runs:
        pols[f"agent_{r}"] = (score.load_decisions(REPO / "runs" / "results" / "agent" / r),
                              REPO / "runs" / "work" / "agent" / r)
    return ids, pols


def figure(case, panels, ref, path, start=None):
    """panels: {policy key: ((lon, lat), decision)}. One panel per policy plus the clean-radar reference."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ps = _load_style()
    runs = {k: v[0] for k, v in panels.items()}
    edges = poc_edges(ref, *runs.values())
    n = len(runs) + 1
    fig, axs = plt.subplots(1, n, figsize=(4.2 * n, 5.4), sharex=True, sharey=True)
    fig.subplots_adjust(top=0.80, bottom=0.25, wspace=0.08, left=0.05, right=0.99)
    items = [("clean_radar", ref, None), *[(k, v[0], v[1]) for k, v in panels.items()]]
    for ax, (k, r, dec) in zip(axs, items):
        ax.pcolormesh(edges[0], edges[1], poc_map(r[0][:, -1], r[1][:, -1], edges).T, cmap="Blues", shading="flat")
        ax.plot(r[0][::25].T, r[1][::25].T, lw=0.5, color=ps.color(k), alpha=0.8)
        ax.plot([], [], lw=1, color=ps.color(k), label="Sample particle tracks (24 h)")
        ax.plot([], [], "s", color="#6baed6", label="Probability map at 24 h (shaded)")
        ax.plot(ref[0][:, -1], ref[1][:, -1], ".", ms=1, color="k")
        ax.plot([], [], ".", color="k", label="Reference end points (clean radar)")
        sub = "reference for this figure" if dec is None else ps.decision_text(dec)
        ax.set_title(f"{ps.name(k)}\n{sub}", fontsize=ps.SUBTITLE_SIZE - 1, color=ps.color(k))
        ax.set_xlabel("Longitude", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.13), frameon=False)
    lons = np.concatenate([r[0].ravel() for r in (ref, *runs.values())])
    lats = np.concatenate([r[1].ravel() for r in (ref, *runs.values())])
    axs[0].set_xlim(np.nanpercentile(lons, 0.5) - 0.05, np.nanpercentile(lons, 99.5) + 0.05)
    axs[0].set_ylim(np.nanpercentile(lats, 0.5) - 0.05, np.nanpercentile(lats, 99.5) + 0.05)
    axs[0].set_ylabel("Latitude", fontsize=8)
    when = f" from {start:%Y-%m-%d %H:%M} UTC" if start is not None else ""
    fig.suptitle(f"{ps.case_label(case)}: what each decision does to a 24 h forecast{when}\n"
                 "1,000 particles from 5 release points; each panel is compared with the clean-radar reference (left)",
                 fontsize=ps.SUBTITLE_SIZE)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _load_style():
    spec = importlib.util.spec_from_file_location("plot_style", REPO / "src" / "plot_style.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def evaluate(a):
    cases_dir = Path(a.cases_dir)
    ids, pols = policies(a.agent_runs, a.baseline, a.baseline_work, cases_dir)
    clean, model0 = Path(a.clean), Path(a.model)
    start = pd.Timestamp(a.start) if a.start else default_start(clean, model0)
    points = seed_points(clean, start)
    print(f"start {start}, {len(points)} release points x {PER_POINT} particles, {HOURS} h")
    ref = cached_run(clean, model0, points, start)[:2]

    rows, done = [], {}
    for name, (decs, work) in pols.items():
        for c in ids:
            dec = decs.get(c)
            choice = dec.get("choice") if dec else None
            row = {"policy": name, "case": c, "choice": choice, "radar": None, "run": None}
            if choice in ("model_only", "escalate") or (choice in score.RADAR_CHOICES):
                radar = score.radar_used(dec, c, work, cases_dir) if choice in score.RADAR_CHOICES else None
                if choice in score.RADAR_CHOICES and radar is None:
                    rows.append({**row, "radar": "MISSING"})
                    continue
                lon, lat, key = cached_run(radar, cases_dir / c / "model.nc", points, start)
                done[key] = True
                row.update(radar=None if radar is None else str(Path(radar).resolve().relative_to(REPO)), run=key,
                           **compare((lon, lat), ref))
            rows.append(row)
        print(f"  {name}: done ({len(done)} distinct runs so far)")

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    fields = (["policy", "case", "choice", "radar", "run"] + [f"sep_km_{h}h" for h in SEP_HOURS]
              + [f"mean_sep_km_{HOURS}h", f"p90_sep_km_{HOURS}h", "poc_overlap"])
    with open(out / "cases.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    summary = {"start": str(start), "hours": HOURS, "release_points": points, "particles": len(points) * PER_POINT,
               "radius_m": RADIUS_M, "model": "OceanDrift, currents only, reader priority [radar, model]",
               "reference": f"{clean.name} + {model0.name}", "distinct_runs": len(done), "policies": {}}
    for name in pols:
        r = [x for x in rows if x["policy"] == name and x.get("sep_km_24h") is not None]
        summary["policies"][name] = {
            "cases_run": len(r),
            **{f"median_sep_km_{h}h": round(float(np.median([x[f"sep_km_{h}h"] for x in r])), 3) for h in SEP_HOURS},
            "mean_of_mean_sep_km_24h": round(float(np.mean([x["mean_sep_km_24h"] for x in r])), 3),
            "max_sep_km_24h": round(max(x["sep_km_24h"] for x in r), 3),
            "mean_poc_overlap": round(float(np.mean([x["poc_overlap"] for x in r])), 3)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    hdr = (f"{'policy':18}{'med 6h':>8}{'med 12h':>8}{'med 24h':>8}{'mean 24h':>10}{'max 24h':>9}{'POC overlap':>13}")
    print("\n" + hdr + "   (km; med = median over cases of the per-case median)\n" + "-" * len(hdr))
    for name, s in summary["policies"].items():
        print(f"{name:18}{s['median_sep_km_6h']:>8.2f}{s['median_sep_km_12h']:>8.2f}{s['median_sep_km_24h']:>8.2f}"
              f"{s['mean_of_mean_sep_km_24h']:>10.2f}{s['max_sep_km_24h']:>9.2f}{s['mean_poc_overlap']:>13.2f}")
    print(f"\nwrote {(out / 'summary.json').relative_to(REPO)} and {(out / 'cases.csv').relative_to(REPO)}")

    agents = [n for n in pols if n.startswith("agent_")][:1]
    for c in a.figure or []:
        panels = {}
        for name in ("always_fused", "baseline", *agents, "always_model_only"):
            decs, work = pols[name]
            dec = decs[c]
            radar = score.radar_used(dec, c, work, cases_dir) if dec["choice"] in score.RADAR_CHOICES else None
            panels[name] = (cached_run(radar, cases_dir / c / "model.nc", points, start)[:2], dec)
        fig_path = REPO / "docs" / "figures" / f"{a.fig_prefix}{c}.png"
        figure(c, panels, ref, fig_path, start)
        print(f"figure: {fig_path.relative_to(REPO)}")


def main(argv=None):
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("evaluate")
    e.add_argument("--agent-runs", nargs="*", default=["rep1", "rep2"])
    e.add_argument("--baseline", default=str(REPO / "runs" / "results" / "baseline"))
    e.add_argument("--baseline-work", default=str(REPO / "runs" / "work" / "baseline"))
    e.add_argument("--clean", default=str(CLEAN), help="clean radar for the reference run and release points")
    e.add_argument("--model", default=str(MODEL))
    e.add_argument("--start", help="release time (default: centred in the shared window)")
    e.add_argument("--out", default=str(OUT))
    e.add_argument("--figure", nargs="*", help="case id(s) for a panel figure in docs/figures/")
    e.add_argument("--fig-prefix", default="drift_", help="figure file name prefix (e.g. drift_oct_)")
    e.add_argument("--cases-dir", default=str(CASES))
    r = sub.add_parser("run")
    r.add_argument("--radar", help="radar file, or omit for model only")
    r.add_argument("--model", required=True)
    r.add_argument("--clean", default=str(CLEAN))
    r.add_argument("--start")
    a = ap.parse_args(argv)

    if a.cmd == "evaluate":
        evaluate(a)
    else:
        start = pd.Timestamp(a.start) if a.start else default_start(a.clean, a.model)
        pts = seed_points(a.clean, start)
        lon, lat, key = cached_run(a.radar, a.model, pts, start)
        print(json.dumps({"run": key, "start": str(start), "particles": int(lon.shape[0]),
                          "end_lon_median": float(np.median(lon[:, -1])), "end_lat_median": float(np.median(lat[:, -1]))}))


if __name__ == "__main__":
    sys.exit(main())
