"""blend_tools: the blend investigator's only tool (Lab 4, Phase B). Deterministic and LLM-free: every number the
agent reports must come from this script's JSON output.

The agent works on ONE blend case folder (built by src/make_blend_case.py): radar.nc (already approved by the
cleaning step), model.nc, wind.nc, stokes.nc, drifter_tune.csv (the drifter's TUNING period only) and case.json.
It may build blends from the fixed menu, evaluate forecast setups on the tuning windows, and read diagnostics.
It never sees the drifter's test period; scoring on it happens elsewhere.

Commands (all print one JSON object; errors print {"error": ...} and exit 1):
  menu                                              the blend menu, wind options and the evaluation budget
  build    --case C --run-dir R --name N --spec J   blend radar.nc + model.nc -> R/blends/N.nc (src/blend.py)
  evaluate --case C --run-dir R --currents X --windage W --stokes on|off
                                                    24 h forecasts from every tuning window; X is a blend name
                                                    built in R, or model_only. Costs 1 of the budget, unless an
                                                    identical evaluation already exists (then it is returned free)
  diagnose --run-dir R --eval E                     per-window detail of evaluation E (free): where particles got
                                                    their currents, when the forecast left radar coverage, and the
                                                    24 h miss split into along-wind and cross-wind parts
  list     --run-dir R                              blends and evaluations made so far, best first

Forecast setup (same as src/forecast_eval.py): OpenDrift OceanDrift, 500 particles released within 100 m of the
drifter's position, horizontal diffusivity K = 10 m2/s (the spread is calibrated later by a fixed rule, not by the
agent), readers [blend, model, wind, stokes]. The output folder R comes from the request; nothing is overwritten.
"""
import argparse
import hashlib
import importlib.util
import json
import sys
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


bl = _load("blend", "src/blend.py")
fe = _load("forecast_eval", "src/forecast_eval.py")
de = fe.de

BUDGET = 12                         # evaluations per run folder
WINDAGE_RANGE = (0.0, 0.05)
K_EVAL = fe.TRACK_K
SOURCE_NAMES = {1: "radar", 2: "mixed", 0: "model", -1: "land", 3: "outside_blend_box"}


class ToolError(Exception):
    pass


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(f"{x:.4g}") if np.isfinite(x) else None
    return x


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _case(case):
    c = Path(case)
    need = ["radar.nc", "model.nc", "wind.nc", "stokes.nc", "drifter_tune.csv", "case.json"]
    missing = [n for n in need if not (c / n).exists()]
    if missing:
        raise ToolError(f"case folder {c} is missing {missing}")
    meta = json.loads((c / "case.json").read_text(encoding="utf-8"))
    track = pd.read_csv(c / "drifter_tune.csv", parse_dates=["time"]).set_index("time")[["lat", "lon"]]
    split = pd.Timestamp(meta["split"])
    if track.index.max() > split:
        raise ToolError("drifter_tune.csv runs past the split; refusing (the test period must stay hidden)")
    starts = [pd.Timestamp(t) for t in meta["tune_windows"]]
    h = pd.Timedelta(hours=int(meta["window_hours"]))
    if any(s + h > split for s in starts):
        raise ToolError("a tuning window ends after the split; refusing")
    return c, meta, track, starts


# ---- menu -----------------------------------------------------------------------------------------
def menu():
    return {"blend_methods": {"hard_switch": "radar where it has data, model in holes",
                              "edge_taper": "radar weight fades from 1 to 0 over taper_km towards radar gaps / box edge",
                              "weighted": "radar_weight * radar + (1 - radar_weight) * model where radar has data"},
            "blend_parameters": {k: list(v) for k, v in bl.RANGES.items()},
            "blend_spec_example": {"method": "edge_taper", "taper_km": 10, "gap_fill_hours": 2},
            "currents": "a blend name built in the run folder, or model_only (no radar)",
            "windage": f"fraction of the 10 m wind, {WINDAGE_RANGE[0]}..{WINDAGE_RANGE[1]}",
            "stokes": "on (ERA5 wave Stokes drift) or off",
            "budget_evaluations": BUDGET, "diffusivity_K_m2_s": K_EVAL}


# ---- build ----------------------------------------------------------------------------------------
def build(case, run_dir, name, spec):
    c, _, _, _ = _case(case)
    if not name.replace("_", "").replace("-", "").isalnum() or name == "model_only":
        raise ToolError("name must be letters, digits, _ or - (and not model_only)")
    out = Path(run_dir) / "blends" / f"{name}.nc"
    if out.exists():
        raise ToolError(f"blend {name} already exists in this run folder; pick another name")
    try:
        s = bl.blend(c / "radar.nc", c / "model.nc", out, spec, require_check=True)
    except bl.BlendError as e:
        raise ToolError(str(e))
    s["out"] = str(out)
    s.pop("dropped", None)
    (out.with_suffix(".json")).write_text(json.dumps(_clean(s), indent=2) + "\n", encoding="utf-8")
    return s


# ---- evaluate -------------------------------------------------------------------------------------
def _sources(blend_ds, times, plon, plat):
    """Source code per particle and hour from the blend file (3 = outside the blend box, so the model is used)."""
    lat, lon = blend_ds.lat.values, blend_ds.lon.values
    tix = pd.DatetimeIndex(blend_ds.time.values)
    src = blend_ds.source.values
    out = np.full(plon.shape, 3, np.int8)
    dlat, dlon = np.mean(np.diff(lat)), np.mean(np.diff(lon))
    for h, t in enumerate(times):
        k = tix.get_indexer([t], method="nearest")[0]
        i = np.rint((plat[:, h] - lat[0]) / dlat).astype(int)
        j = np.rint((plon[:, h] - lon[0]) / dlon).astype(int)
        inside = (i >= 0) & (i < lat.size) & (j >= 0) & (j < lon.size) & np.isfinite(plat[:, h])
        out[inside, h] = src[k, i[inside], j[inside]]
    return out


def _wind_split(case_dir, obs, clon, clat):
    """24 h miss (drifter minus forecast centroid, km) split along / across the mean wind over the window."""
    with xr.open_dataset(case_dir / "wind.nc") as w:
        kw = dict(time=xr.DataArray(obs.index.values), lat=xr.DataArray(obs["lat"].values),
                  lon=xr.DataArray(obs["lon"].values))
        wu, wv = float(w.x_wind.interp(**kw).mean()), float(w.y_wind.interp(**kw).mean())
    lat0 = obs["lat"].iloc[-1]
    ex = (obs["lon"].iloc[-1] - clon[-1]) * 111.195 * np.cos(np.radians(lat0))
    ey = (obs["lat"].iloc[-1] - clat[-1]) * 111.195
    speed = np.hypot(wu, wv)
    if speed < 1e-6:
        return None, None, 0.0
    ax, ay = wu / speed, wv / speed
    return ex * ax + ey * ay, -ex * ay + ey * ax, speed      # along (+ = drifter further downwind), across (+ = left)


def evaluate(case, run_dir, currents, windage, stokes):
    c, meta, track, starts = _case(case)
    run_dir = Path(run_dir)
    if not (WINDAGE_RANGE[0] <= windage <= WINDAGE_RANGE[1]):
        raise ToolError(f"windage must be in {WINDAGE_RANGE}")
    if stokes not in ("on", "off"):
        raise ToolError("stokes must be on or off")
    if currents == "model_only":
        blend_path, blend_sha, readers = None, "model_only", [str(c / "model.nc")]
    else:
        blend_path = run_dir / "blends" / f"{currents}.nc"
        if not blend_path.exists():
            raise ToolError(f"no blend named {currents} in this run folder; build it first")
        blend_sha = _sha(blend_path)
        readers = [str(blend_path), str(c / "model.nc")]
    key = json.dumps({"case": meta["case_id"], "blend": blend_sha, "windage": round(windage, 6), "stokes": stokes,
                      "K": K_EVAL}, sort_keys=True)
    eval_id = "e_" + hashlib.sha256(key.encode()).hexdigest()[:10]
    evals = run_dir / "evals"
    done = evals / f"{eval_id}.json"
    if done.exists():
        r = json.loads(done.read_text(encoding="utf-8"))
        r["summary"]["note"] = "identical evaluation already existed; returned free"
        r["summary"]["budget_used"] = len(list(evals.glob("e_*.json")))
        return r["summary"]
    used = len(list(evals.glob("e_*.json"))) if evals.exists() else 0
    if used >= BUDGET:
        raise ToolError(f"evaluation budget used up ({BUDGET}); decide from the evaluations you have")
    if windage > 0:
        readers.append(str(c / "wind.nc"))
    if stokes == "on":
        readers.append(str(c / "stokes.nc"))

    hours = int(meta["window_hours"])
    bds = xr.open_dataset(blend_path) if blend_path else None
    rows = []
    for s in starts:
        obs = track.loc[s:s + pd.Timedelta(hours=hours)]
        if len(obs) != hours + 1:
            raise ToolError(f"window {s} has {len(obs)} positions, expected {hours + 1}")
        plon, plat = fe.run(readers, obs["lon"].iloc[0], obs["lat"].iloc[0], s, hours, K_EVAL, windage,
                            stokes == "on")
        sc = de.score_window(plon, plat, obs, hours)
        clon, clat = np.nanmean(plon, axis=0), np.nanmean(plat, axis=0)
        times = pd.date_range(s, periods=hours + 1, freq="h")
        if bds is not None:
            src = _sources(bds, times, plon, plat)
        else:
            src = np.zeros(plon.shape, np.int8)
        share = {SOURCE_NAMES[k]: float((src == k).mean()) for k in (1, 2, 0, 3)}
        cen_src = [int(np.bincount((src[:, h] + 1).astype(int), minlength=5).argmax()) - 1 for h in range(hours + 1)]
        left = next((h for h in range(1, hours + 1) if cen_src[0] in (1, 2) and cen_src[h] not in (1, 2)), None)
        along, across, wspeed = _wind_split(c, obs, clon, clat)
        sep_curve = de.haversine_km(clon, clat, obs["lon"].values, obs["lat"].values)
        rows.append({"start": str(s), "sep_km_6h": sc["sep_km_6h"], "sep_km_12h": sc["sep_km_12h"],
                     "sep_km_24h": sc["sep_km_24h"], "skill": sc["skill"],
                     "share_particle_hours": share, "majority_source_at_release": SOURCE_NAMES[cen_src[0]],
                     "hour_majority_left_radar": left, "miss24_along_wind_km": along,
                     "miss24_cross_wind_km": across, "mean_wind_m_s": wspeed,
                     "sep_km_by_hour": [float(x) for x in sep_curve]})
    if bds is not None:
        bds.close()

    df = pd.DataFrame(rows)
    radar_share = df["share_particle_hours"].apply(lambda d: d["radar"] + d["mixed"])
    mostly_radar = radar_share >= 0.5
    summary = {
        "eval_id": eval_id, "currents": currents, "windage": windage, "stokes": stokes, "K_m2_s": K_EVAL,
        "windows": len(df), "mean_sep_km_6h": df["sep_km_6h"].mean(), "mean_sep_km_12h": df["sep_km_12h"].mean(),
        "mean_sep_km_24h": df["sep_km_24h"].mean(), "median_sep_km_24h": df["sep_km_24h"].median(),
        "mean_skill": df["skill"].mean(),
        "mean_share_particle_hours_on_radar_or_mixed": radar_share.mean(),
        "mean_sep_km_24h_windows_mostly_on_radar": df.loc[mostly_radar, "sep_km_24h"].mean() if mostly_radar.any() else None,
        "mean_sep_km_24h_windows_mostly_off_radar": df.loc[~mostly_radar, "sep_km_24h"].mean() if (~mostly_radar).any() else None,
        "n_windows_mostly_on_radar": int(mostly_radar.sum()),
        "mean_miss24_along_wind_km": df["miss24_along_wind_km"].mean(),
        "mean_miss24_cross_wind_km": df["miss24_cross_wind_km"].mean(),
        "budget_used": used + 1, "budget_left": BUDGET - used - 1}
    summary = _clean(summary)
    evals.mkdir(parents=True, exist_ok=True)
    done.write_text(json.dumps({"summary": summary, "key": json.loads(key), "windows": _clean(rows)}, indent=2) + "\n",
                    encoding="utf-8")
    return summary


def diagnose(run_dir, eval_id):
    p = Path(run_dir) / "evals" / f"{eval_id}.json"
    if not p.exists():
        raise ToolError(f"no evaluation {eval_id} in this run folder")
    r = json.loads(p.read_text(encoding="utf-8"))
    curve = np.array([w["sep_km_by_hour"] for w in r["windows"]], float)
    return {"summary": r["summary"],
            "mean_sep_km_by_hour": [round(float(x), 2) for x in np.nanmean(curve, axis=0)],
            "windows": [{k: v for k, v in w.items() if k != "sep_km_by_hour"} for w in r["windows"]],
            "how_to_read": {"miss24_along_wind_km": "+ = the drifter ended further downwind than the forecast",
                            "miss24_cross_wind_km": "+ = the drifter ended to the left of the wind direction",
                            "hour_majority_left_radar": "first hour most particles were off radar/mixed cells, "
                                                        "for windows that started on radar; null = never left"}}


def listing(run_dir):
    run_dir = Path(run_dir)
    blends = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run_dir / "blends").glob("*.json"))]
    evals = [json.loads(p.read_text(encoding="utf-8"))["summary"] for p in sorted((run_dir / "evals").glob("e_*.json"))]
    evals.sort(key=lambda s: s["mean_sep_km_24h"])
    return {"blends": [{"name": Path(b["out"]).stem, "spec": b["spec"], "share_radar": b["share_radar"],
                        "share_mixed": b["share_mixed"], "hours_dropped": b["hours_dropped"]} for b in blends],
            "evaluations_best_first": [{k: e[k] for k in ("eval_id", "currents", "windage", "stokes",
                                                          "mean_sep_km_24h", "mean_skill")} for e in evals],
            "budget_used": len(evals), "budget_left": BUDGET - len(evals)}


def main(argv=None):
    import logging
    import warnings
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("menu")
    b = sub.add_parser("build")
    b.add_argument("--case", required=True)
    b.add_argument("--run-dir", required=True)
    b.add_argument("--name", required=True)
    b.add_argument("--spec", required=True, help="JSON object")
    e = sub.add_parser("evaluate")
    e.add_argument("--case", required=True)
    e.add_argument("--run-dir", required=True)
    e.add_argument("--currents", required=True)
    e.add_argument("--windage", type=float, required=True)
    e.add_argument("--stokes", required=True)
    d = sub.add_parser("diagnose")
    d.add_argument("--run-dir", required=True)
    d.add_argument("--eval", required=True)
    li = sub.add_parser("list")
    li.add_argument("--run-dir", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "menu":
            out = menu()
        elif a.cmd == "build":
            try:
                spec = json.loads(a.spec)
            except json.JSONDecodeError as ex:
                raise ToolError(f"--spec is not valid JSON: {ex}")
            out = build(a.case, a.run_dir, a.name, spec)
        elif a.cmd == "evaluate":
            out = evaluate(a.case, a.run_dir, a.currents, a.windage, a.stokes)
        elif a.cmd == "diagnose":
            out = diagnose(a.run_dir, a.eval)
        else:
            out = listing(a.run_dir)
    except ToolError as ex:
        print(json.dumps({"error": str(ex)}))
        return 1
    print(json.dumps(_clean(out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
