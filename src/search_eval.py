"""Lab 4, Phase C: one search period, planned on a forecast and scored against the real drifter. LLM-free.

For every TEST window (never used to choose anything) a forecast is made from the drifter's position at the window
start; at the search time (--hours after the start, default 12 and 24) a plan is made with src/search.py and scored:

  found     POD of the plan at the drifter's TRUE position = probability this plan would have found it
  pos_pred  the POS the planner predicted from the forecast (if found << pos_pred, the forecast was overconfident)

Forecasts (spread calibrated on the TUNING windows earlier, read from those summaries, not re-tuned here):
  model only    HYCOM, no wind, K from runs/results/blend/score/summary.json
  hard_switch   blend file (radar where valid, model in holes), no wind, K from the same summary
  ensemble      9 forcings (src/ensemble_eval.py), K from runs/results/ensemble/summary.json
Planners: greedy (optimal for the exponential detection law) and iamsar (uniform square around the datum).
Effort: 1 and 3 assets with the ASSUMED planning numbers in search.DEFAULTS (1 km sweep width, 27.8 km/h, 4 h).
The 2x2 headline is (model only vs the best forecast) x (iamsar vs greedy); all combinations are reported.

  python src/search_eval.py                      (about 15 min)
Outputs (never overwritten): runs/results/search/{windows.csv, summary.json}.
"""
import argparse
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


fe = _load("forecast_eval", "src/forecast_eval.py")
ee = _load("ensemble_eval", "src/ensemble_eval.py")
se = _load("search", "src/search.py")
bl = ee.bl
de = fe.de
PLANNERS = ("iamsar", "greedy")
ASSETS = (1, 3)


def forecasts(files, work, blend_summary, ens_summary):
    """name -> function(lon, lat, start, hours) returning particle lon, lat arrays (n, hours + 1)."""
    ks = json.loads(Path(blend_summary).read_text(encoding="utf-8"))["setups"]
    k_ens = json.loads(Path(ens_summary).read_text(encoding="utf-8"))["K_chosen"]
    hard = Path(work) / "ens_hard_switch.nc"
    if not hard.exists():
        bl.blend(files["radar"], files["model"], hard, {"method": "hard_switch"}, require_check=True)
    k_model, k_hard = ks["ref: model only"]["K"], ks["ref: hard_switch blend"]["K"]
    mems = ee.members(files, work)
    return {
        f"model only (K={k_model:g})":
            lambda lon, lat, s, h: fe.run([files["model"]], lon, lat, s, h, k_model, 0.0, False),
        f"hard_switch (K={k_hard:g})":
            lambda lon, lat, s, h: fe.run([str(hard), files["model"]], lon, lat, s, h, k_hard, 0.0, False),
        f"ensemble (K={k_ens:g})":
            lambda lon, lat, s, h: ee.run_ensemble(mems, lon, lat, s, h, k_ens)[:2],
    }


def evaluate(track, starts, fcs, horizons, hours=24, log=print):
    rows = []
    for n, s in enumerate(starts, 1):
        obs = track.loc[s:s + pd.Timedelta(hours=hours)]
        for fname, f in fcs.items():
            plon, plat = f(obs["lon"].iloc[0], obs["lat"].iloc[0], s, hours)
            for h in horizons:
                tl, ta = obs["lon"].iloc[h], obs["lat"].iloc[h]
                for assets in ASSETS:
                    e = se.effort_km2(assets=assets)
                    for pl in PLANNERS:
                        r = se.plan(plon[:, h], plat[:, h], effort=e, planner=pl)
                        g = r["grid"]
                        rows.append({"start": str(s), "forecast": fname, "hours": h, "assets": assets, "planner": pl,
                                     "found": float(se.pod_at(r, tl, ta)[0]), "pos_pred": r["pos_predicted"],
                                     "area_searched_km2": r["area_searched_km2"], "side_km": r["side_km"],
                                     "datum_to_truth_km": float(de.haversine_km(g.lon0, g.lat0, tl, ta))})
        log(f"  window {n}/{len(starts)} {s} done")
    return pd.DataFrame(rows)


def main(argv=None):
    import logging
    import warnings
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radar", default=str(fe.DEF_RADAR))
    ap.add_argument("--model", default=str(fe.DEF_MODEL))
    ap.add_argument("--wind", default=str(fe.DEF_WIND))
    ap.add_argument("--drifters", default=str(fe.DEF_DRIFTERS))
    ap.add_argument("--id", type=int, default=de.DEF_ID)
    ap.add_argument("--split", default=fe.DEF_SPLIT)
    ap.add_argument("--every", type=int, default=6)
    ap.add_argument("--horizons", type=int, nargs="+", default=[12, 24])
    ap.add_argument("--blend-summary", default=str(REPO / "runs" / "results" / "blend" / "score" / "summary.json"))
    ap.add_argument("--ensemble-summary", default=str(REPO / "runs" / "results" / "ensemble" / "summary.json"))
    ap.add_argument("--out", default=str(REPO / "runs" / "results" / "search"))
    ap.add_argument("--work", default=str(REPO / "runs" / "work" / "blend" / "ensemble"))
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if (out / "summary.json").exists():
        sys.exit(f"{out}/summary.json already exists; not overwriting")
    files = {"radar": a.radar, "model": a.model, "wind": a.wind}
    with xr.open_dataset(a.radar) as r, xr.open_dataset(a.model) as m:
        t0 = max(pd.Timestamp(r.time.values[0]), pd.Timestamp(m.time.values[0]))
        t1 = min(pd.Timestamp(r.time.values[-1]), pd.Timestamp(m.time.values[-1]))
    track = de.drifter_track(a.drifters, a.id, t0, t1)
    _, test = fe.split_windows(de.windows(track, 24, a.every), 24, a.split)
    if a.quick:
        test = test[:2]
    fcs = forecasts(files, a.work, a.blend_summary, a.ensemble_summary)
    print(f"{len(fcs)} forecasts x {len(PLANNERS)} planners x assets {ASSETS} x horizons {a.horizons}; "
          f"{len(test)} test windows; effort per asset {se.effort_km2():.1f} km2 (assumed)")
    df = evaluate(track, test, fcs, a.horizons)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "windows.csv", index=False)
    g = (df.groupby(["hours", "assets", "forecast", "planner"], sort=False)
           .agg(found_mean=("found", "mean"), pos_pred_mean=("pos_pred", "mean"),
                found_any_share=("found", lambda x: float((x > 0.05).mean())),
                area_searched_km2=("area_searched_km2", "median"), datum_to_truth_km=("datum_to_truth_km", "median"))
           .reset_index())
    summary = {"test_windows": len(test), "effort_km2_per_asset": se.effort_km2(), "assumptions": se.DEFAULTS,
               "cell_km": se.CELL_KM, "quick": a.quick, "table": json.loads(g.round(4).to_json(orient="records"))}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("\nmean probability the plan would have found the drifter (found) vs what the planner predicted (pos_pred)")
    for (h, n), sub in g.groupby(["hours", "assets"], sort=False):
        print(f"\n  search at {h} h, {n} asset(s):")
        print(f"  {'forecast':26}{'planner':>8}{'found':>8}{'pos_pred':>10}{'area km2':>10}{'datum-truth km':>16}")
        for _, r in sub.iterrows():
            print(f"  {r['forecast']:26}{r['planner']:>8}{r['found_mean']:>8.3f}{r['pos_pred_mean']:>10.3f}"
                  f"{r['area_searched_km2']:>10.0f}{r['datum_to_truth_km']:>16.1f}")
    print(f"\nwrote {out}/windows.csv, summary.json")


if __name__ == "__main__":
    sys.exit(main())
