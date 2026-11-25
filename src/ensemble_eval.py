"""Lab 4, step U: a forcing ensemble instead of random-walk spread, checked against the real drifter. LLM-free.

Why: in Phase A, honest 50% / 90% areas needed diffusivity K = 1600-3200 m2/s, and then the areas were too wide at
6 h and 12 h. Random-walk spread grows like sqrt(time); our real error is a steady drift caused by WHICH current and
wind are right. So here the spread comes from running several plausible forcings and pooling their particles:

  members  currents {model only, hard_switch blend, 50/50 weighted blend} x windage {0, 1%, 2%}, Stokes off
           (9 members; fixed a priori, not tuned), PER_MEMBER particles each, pooled with equal weight
  K        a small extra random spread, chosen on the TUNING windows by the fixed rule in src/forecast_eval.py
           (pick_spread: 24 h hit rates closest to 50% / 90%; ties go to the smaller K)

Scored on the TEST windows (never used for any choice) at 6, 12 and 24 h: centroid separation, Liu & Weisberg skill,
whether the drifter is inside the 50% / 90% areas (kernel density, forecast_eval.hdr_kde) and their size. The
references are the single-forcing setups from src/blend_score.py (read from its test.csv, so they are not re-run).

  python src/ensemble_eval.py                              (about 30 min)
  python src/ensemble_eval.py --quick                      (2 windows each side, 2 K values; smoke test)
Outputs (never overwritten): runs/results/ensemble/{tune.csv, test.csv, summary.json}.
"""
import argparse
import importlib.util
import json
import sys
from itertools import product
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
bl = _load("blend", "src/blend.py")
de = fe.de

CURRENTS = {"model_only": None, "hard_switch": {"method": "hard_switch"},
            "weighted_0.5": {"method": "weighted", "radar_weight": 0.5}}
WINDAGES = (0.0, 0.01, 0.02)
PER_MEMBER = 100
KS = (10.0, 100.0, 400.0, 1600.0)
HORIZONS = (6, 12, 24)


def members(files, work):
    """[(name, readers, windage)] for every current x windage. Blend files are built once (frozen check required)."""
    out = []
    for cname, spec in CURRENTS.items():
        if spec is None:
            cur = [files["model"]]
        else:
            path = Path(work) / f"ens_{cname}.nc"
            if not path.exists():
                bl.blend(files["radar"], files["model"], path, spec, require_check=True)
            cur = [str(path), files["model"]]
        for w in WINDAGES:
            out.append((f"{cname}|w{w:g}", cur + ([files["wind"]] if w > 0 else []), w))
    return out


def run_ensemble(mems, lon, lat, start, hours, k, per_member=PER_MEMBER):
    """Pooled particles (n_members * per_member, hours + 1) and the member index of each particle."""
    lons, lats, idx = [], [], []
    for i, (_, readers, w) in enumerate(mems):
        plon, plat = fe.run(readers, lon, lat, start, hours, k, w, False, number=per_member)
        lons.append(plon)
        lats.append(plat)
        idx.append(np.full(plon.shape[0], i))
    return np.vstack(lons), np.vstack(lats), np.concatenate(idx)


def member_spread_km(plon, plat, idx, h):
    """Mean distance of the member centroids from the ensemble centroid at hour h: the spread due to forcing alone."""
    clon, clat = np.nanmean(plon[:, h]), np.nanmean(plat[:, h])
    ms = [(np.nanmean(plon[idx == i, h]), np.nanmean(plat[idx == i, h])) for i in np.unique(idx)]
    return float(np.mean([de.haversine_km(a, b, clon, clat) for a, b in ms]))


def score_ensemble(track, starts, mems, k, hours=24):
    rows = []
    for s in starts:
        obs = track.loc[s:s + pd.Timedelta(hours=hours)]
        plon, plat, idx = run_ensemble(mems, obs["lon"].iloc[0], obs["lat"].iloc[0], s, hours, k)
        rows.append({"start": str(s), "K": k, **fe.score_window(plon, plat, obs, hours),
                     **{f"member_spread_km_{h}h": member_spread_km(plon, plat, idx, h) for h in HORIZONS}})
    return rows


def table(df):
    agg = {"windows": ("sep_km_24h", "size"), "mean_skill": ("skill", "mean")}
    for h in HORIZONS:
        agg[f"median_sep_km_{h}h"] = (f"sep_km_{h}h", "median")
        for x in ("in50", "in90"):
            if f"{x}_{h}h" in df:
                agg[f"{x}_{h}h"] = (f"{x}_{h}h", "mean")
    for h in (24,):
        if f"area90_km2_{h}h" in df:
            agg["median_area50_km2_24h"] = ("area50_km2_24h", "median")
            agg["median_area90_km2_24h"] = ("area90_km2_24h", "median")
    return df.groupby("setup", sort=False).agg(**agg).reset_index()


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
    ap.add_argument("--reference", default=str(REPO / "runs" / "results" / "blend" / "score" / "test.csv"),
                    help="blend_score test.csv with the single-forcing references")
    ap.add_argument("--out", default=str(REPO / "runs" / "results" / "ensemble"))
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
    tune, test = fe.split_windows(de.windows(track, 24, a.every), 24, a.split)
    ks = KS
    if a.quick:
        tune, test, ks = tune[:2], test[:2], (10.0, 400.0)
    mems = members(files, a.work)
    print(f"{len(mems)} members x {PER_MEMBER} particles; K in {list(ks)}; {len(tune)} tuning, {len(test)} test windows")

    tune_rows = []
    for k in ks:
        tune_rows += [{"forcing": "ensemble", **r} for r in score_ensemble(track, tune, mems, k)]
        g = pd.DataFrame([r for r in tune_rows if r["K"] == k])
        print(f"  tuning K={k:g}: in50 24h {g['in50_24h'].mean():.0%}, in90 24h {g['in90_24h'].mean():.0%}, "
              f"area90 {g['area90_km2_24h'].median():.0f} km2")
    tdf = pd.DataFrame(tune_rows)
    kbest = fe.pick_spread(tdf)["ensemble"]["K"]
    print(f"chosen K = {kbest:g}")

    test_rows = [{"setup": f"ensemble (9 forcings), K={kbest:g}", **r}
                 for r in score_ensemble(track, test, mems, kbest)]
    if kbest != ks[0]:
        test_rows += [{"setup": f"ensemble (9 forcings), K={ks[0]:g}", **r}
                      for r in score_ensemble(track, test, mems, ks[0])]
    ref = Path(a.reference)
    if ref.exists() and not a.quick:
        rdf = pd.read_csv(ref)
        rdf = rdf[rdf["setup"].isin(["ref: model only", "ref: hard_switch blend", "ref: no motion"])]
        test_rows += rdf.to_dict("records")
    df = pd.DataFrame(test_rows)
    out.mkdir(parents=True, exist_ok=True)
    tdf.to_csv(out / "tune.csv", index=False)
    df.to_csv(out / "test.csv", index=False)
    tab = table(df)
    summary = {"members": [m[0] for m in mems], "per_member": PER_MEMBER, "K_grid": list(ks), "K_chosen": kbest,
               "split": a.split, "tune_windows": len(tune), "test_windows": len(test), "quick": a.quick,
               "test": json.loads(tab.round(4).to_json(orient="records")),
               "member_spread_km_test": {f"{h}h": round(float(df[f"member_spread_km_{h}h"].dropna().mean()), 3)
                                         for h in HORIZONS if f"member_spread_km_{h}h" in df}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nTEST windows ({len(test)}); separation medians (km); in = share of windows")
    hdr = "".join(f"{'sep' + str(h):>7}{'in50_' + str(h):>8}{'in90_' + str(h):>8}" for h in HORIZONS)
    print(f"{'setup':34}{hdr}{'skill':>7}{'area90':>8}")
    for _, r in tab.iterrows():
        cells = ""
        for h in HORIZONS:
            cells += f"{r[f'median_sep_km_{h}h']:>7.1f}"
            for x in ("in50", "in90"):
                v = r.get(f"{x}_{h}h")
                cells += f"{v:>8.0%}" if pd.notna(v) else f"{'-':>8}"
        a90 = r.get("median_area90_km2_24h")
        print(f"{r['setup']:34}{cells}{r['mean_skill']:>7.2f}" + (f"{a90:>8.0f}" if pd.notna(a90) else f"{'-':>8}"))
    print(f"\nwrote {out}/summary.json, tune.csv, test.csv")


if __name__ == "__main__":
    sys.exit(main())
