"""Score blend decisions on the drifter's TEST period (Lab 4, Phase B). LLM-free; people run it, the agent never does.

Each decision (from the agent or src/blend_baseline.py) names currents (model_only or a blend spec), a windage and
Stokes on/off. For every decision and for fixed references, this script
  1. builds the currents (src/blend.py; the frozen check must pass),
  2. calibrates the spread K on the TUNING windows with the fixed rule of src/forecast_eval.py (pick_spread: hit
     rates closest to 50% / 90% at 24 h; ties to the smaller K), so no policy chooses its own spread,
  3. scores the TEST windows (never shown to any policy): centroid separation at 6/12/24 h, Liu & Weisberg skill,
     50% / 90% containment and areas (kernel density, forecast_eval.hdr_kde), plus a no-motion reference.
References: "model only" and "hard_switch" (both no wind), and "reader priority (Lab 3)", i.e. raw radar then model
in OpenDrift, which stretches radar into gaps (kept to show what the blend file changes).

  python src/blend_score.py --decision baseline=runs/results/blend/baseline/decision.json \\
      --decision agent=runs/results/blend/agent_rep1/decision.json --out runs/results/blend/score
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fe = _load("forecast_eval", "src/forecast_eval.py")
bl = _load("blend", "src/blend.py")
de = fe.de

REFERENCES = {
    "ref: model only": {"currents": "model_only", "windage": 0.0, "stokes": "off"},
    "ref: hard_switch blend": {"currents": {"spec": {"method": "hard_switch"}}, "windage": 0.0, "stokes": "off"},
    "ref: reader priority (Lab 3)": {"currents": "reader_priority", "windage": 0.0, "stokes": "off"},
}


def readers_for(dec, files, work, name):
    cur = dec["currents"]
    if cur == "model_only":
        r = [files["model"]]
    elif cur == "reader_priority":
        r = [files["radar"], files["model"]]
    elif isinstance(cur, dict) and "spec" in cur:
        out = Path(work) / f"{name.replace(' ', '_').replace(':', '').replace('(', '').replace(')', '')}.nc"
        if not out.exists():
            bl.blend(files["radar"], files["model"], out, cur["spec"], require_check=True)
        r = [str(out), files["model"]]
    else:
        raise SystemExit(f"{name}: currents must be model_only or {{'spec': {{...}}}}, got {cur!r}")
    w = float(dec["windage"])
    if w > 0:
        r.append(files["wind"])
    if dec["stokes"] == "on":
        r.append(files["stokes"])
    return [str(x) for x in r], w, dec["stokes"] == "on"


def score(track, starts, readers, windage, stokes, k, hours=24):
    rows = []
    for s in starts:
        obs = track.loc[s:s + pd.Timedelta(hours=hours)]
        plon, plat = fe.run(readers, obs["lon"].iloc[0], obs["lat"].iloc[0], s, hours, k, windage, stokes)
        rows.append({"start": str(s), "K": k, **fe.score_window(plon, plat, obs, hours)})
    return rows


def main(argv=None):
    import logging
    import warnings
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decision", action="append", default=[], help="name=path/to/decision.json (repeatable)")
    ap.add_argument("--radar", default=str(fe.DEF_RADAR))
    ap.add_argument("--model", default=str(fe.DEF_MODEL))
    ap.add_argument("--wind", default=str(fe.DEF_WIND))
    ap.add_argument("--stokes", default=str(fe.DEF_STOKES))
    ap.add_argument("--drifters", default=str(fe.DEF_DRIFTERS))
    ap.add_argument("--id", type=int, default=de.DEF_ID)
    ap.add_argument("--split", default=fe.DEF_SPLIT)
    ap.add_argument("--every", type=int, default=6)
    ap.add_argument("--ks", type=float, nargs="+", default=list(fe.SPREAD_KS))
    ap.add_argument("--out", default=str(REPO / "runs" / "results" / "blend" / "score"))
    ap.add_argument("--work", default=str(REPO / "runs" / "work" / "blend" / "score"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    if (out / "summary.json").exists():
        sys.exit(f"{out}/summary.json already exists; not overwriting")
    files = {"radar": a.radar, "model": a.model, "wind": a.wind, "stokes": a.stokes}

    decisions = dict(REFERENCES)
    for item in a.decision:
        name, _, path = item.partition("=")
        if not path:
            sys.exit(f"--decision must be name=path, got {item!r}")
        decisions[name] = json.loads(Path(path).read_text(encoding="utf-8"))

    import xarray as xr
    with xr.open_dataset(a.radar) as r, xr.open_dataset(a.model) as m:
        t0 = max(pd.Timestamp(r.time.values[0]), pd.Timestamp(m.time.values[0]))
        t1 = min(pd.Timestamp(r.time.values[-1]), pd.Timestamp(m.time.values[-1]))
    track = de.drifter_track(a.drifters, a.id, t0, t1)
    tune, test = fe.split_windows(de.windows(track, 24, a.every), 24, a.split)
    print(f"{len(decisions)} setups; spread calibrated on {len(tune)} tuning windows; scored on {len(test)} test windows")

    spread_rows, test_rows, chosen = [], [], {}
    for name, dec in decisions.items():
        readers, w, st = readers_for(dec, files, a.work, name)
        for k in a.ks:
            spread_rows += [{"forcing": name, **r} for r in score(track, tune, readers, w, st, k)]
        sp = pd.DataFrame([r for r in spread_rows if r["forcing"] == name])
        k = fe.pick_spread(sp)[name]["K"]
        chosen[name] = {"K": k, "currents": dec["currents"], "windage": w, "stokes": "on" if st else "off",
                        "evaluations_used": dec.get("evaluations_used")}
        test_rows += [{"setup": name, **r} for r in score(track, test, readers, w, st, k)]
        print(f"  {name}: K={k:g} done")
    for s in test:
        test_rows.append({"setup": "ref: no motion", "start": str(s),
                          **fe.no_motion_row(track.loc[s:s + pd.Timedelta(hours=24)], 24)})

    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(spread_rows).to_csv(out / "spread.csv", index=False)
    tdf = pd.DataFrame(test_rows)
    tdf.to_csv(out / "test.csv", index=False)
    table = fe.summarize(tdf)
    summary = {"split": a.split, "tune_windows": len(tune), "test_windows": len(test), "setups": chosen,
               "test": json.loads(table.round(4).to_json(orient="records"))}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nTEST windows ({len(test)}); separation medians in km; in = share of windows")
    print(f"{'setup':32}{'K':>6}{'6h':>6}{'12h':>6}{'24h':>6}{'mean24':>7}{'skill':>7}{'in50':>6}{'in90':>6}{'area90':>8}")
    for _, r in table.iterrows():
        k = chosen.get(r["setup"], {}).get("K")
        print(f"{r['setup']:32}{(f'{k:g}' if k is not None else '-'):>6}{r['median_sep_km_6h']:>6.1f}"
              f"{r['median_sep_km_12h']:>6.1f}{r['median_sep_km_24h']:>6.1f}{r['mean_sep_km_24h']:>7.1f}"
              f"{r['mean_skill']:>7.2f}"
              + (f"{r['in50_24h']:>6.0%}{r['in90_24h']:>6.0%}{r['median_area90_km2_24h']:>8.0f}"
                 if pd.notna(r.get("in50_24h")) else f"{'-':>6}{'-':>6}{'-':>8}"))
    print(f"\nwrote {out}/summary.json, test.csv, spread.csv")


if __name__ == "__main__":
    sys.exit(main())
