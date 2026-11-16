"""Score each policy's decisions against a REAL drifter (step D). LLM-free; does not read the answer key.

For every policy and case, the decision is turned into a forcing (OpenDrift reader priority, as in src/drift.py):
  fused -> [case radar, model]   repair -> [repaired radar, model]   model_only / escalate -> [model]
and that forcing is run for the same sliding 24 h windows as src/drifter_eval.py, released at the real drifter.
Each (policy, case) gets the median 24 h miss over windows, the mean Liu & Weisberg skill and the share of windows
with the drifter inside the 90% area. Policies:
  always_model_only, always_fused   trivial
  baseline                          runs/results/<baseline>/ + its repaired files
  agent_<run>                       runs/results/agent/<run>/ + its repaired files
  clean_radar                       the clean radar + model in every case: what a perfect repair would give
Forcings are de-duplicated by the data they contain, so identical files are run once.

  python src/decision_reality.py                                   # baseline_oct only
  python src/decision_reality.py --agent-runs oct_rep1             # plus the agent

Outputs: runs/results/reality_oct/cases.csv, summary.json, docs/figures/decisions_vs_reality_oct.png
"""
import argparse
import csv
import importlib.util
import json
import logging
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


de = _load("drifter_eval", "src/drifter_eval.py")
sc = _load("score", "src/score.py")
dr = _load("drift", "src/drift.py")
COLORS = {"always_model_only": "tab:orange", "always_fused": "tab:red", "baseline": "tab:gray",
          "clean_radar": "tab:green"}


def forcing_for(dec, case, work, cases_dir, model=None):
    """(readers, radar_path or None) for a decision. The model is the case's own model.nc (it may be faulty);
    `model` is only a fallback if a case folder has none. Returns (None, None) if a repaired file is missing,
    and (None, "escalated") for an escalation: the case goes to a person, so no automatic forecast is made."""
    choice = dec.get("choice") if dec else None
    case_model = Path(cases_dir) / case / "model.nc"
    m = case_model if case_model.exists() else model
    if choice == "escalate":
        return None, "escalated"
    if choice in sc.RADAR_CHOICES:
        radar = sc.radar_used(dec, case, work, cases_dir)
        return ([radar, m], radar) if radar is not None else (None, None)
    return [m], None


def main(argv=None):
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases-dir", default="runs/cases_oct")
    ap.add_argument("--baseline", default="runs/results/baseline_oct")
    ap.add_argument("--baseline-work", default="runs/work/baseline_oct")
    ap.add_argument("--agent-runs", nargs="*", default=[])
    ap.add_argument("--clean", default=str(de.DEF_RADAR), help="the clean radar the cases were made from")
    ap.add_argument("--model", default=str(de.DEF_MODEL))
    ap.add_argument("--drifters", default=str(de.DEF_DRIFTERS))
    ap.add_argument("--id", type=int, default=de.DEF_ID)
    ap.add_argument("--k", type=float, default=10.0, help="diffusivity m2/s")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--every", type=int, default=12)
    ap.add_argument("--out", default="runs/results/reality_oct")
    ap.add_argument("--fig-name", default="decisions_vs_reality_oct.png")
    a = ap.parse_args(argv)

    cases_dir, model, clean = REPO / a.cases_dir, Path(a.model), Path(a.clean)
    ids = sorted(p.name for p in cases_dir.glob("case*") if p.is_dir())
    if not ids:
        sys.exit(f"no cases in {cases_dir}")
    policies = {
        "always_model_only": ({c: {"choice": "model_only"} for c in ids}, None),
        "always_fused": ({c: {"choice": "fused"} for c in ids}, None),
        "baseline": (sc.load_decisions(REPO / a.baseline), REPO / a.baseline_work),
    }
    for r in a.agent_runs:
        d = REPO / "runs" / "results" / "agent" / r
        if not d.is_dir():
            sys.exit(f"{d} not found")
        policies[f"agent_{r}"] = (sc.load_decisions(d), REPO / "runs" / "work" / "agent" / r)
    policies["clean_radar"] = ({c: {"choice": "fused"} for c in ids}, None)

    with xr.open_dataset(clean) as rr, xr.open_dataset(model) as mm:
        t1 = min(pd.Timestamp(rr.time.values[-1]), pd.Timestamp(mm.time.values[-1]))
        t0 = max(pd.Timestamp(rr.time.values[0]), pd.Timestamp(mm.time.values[0]))
    track = de.drifter_track(a.drifters, a.id, t0, t1)
    starts = de.windows(track, a.hours, a.every)
    print(f"drifter {a.id}: {len(starts)} windows of {a.hours} h; K={a.k:g} m2/s; cases {cases_dir.name}; "
          f"policies {list(policies)}")

    cache, rows = {}, []
    for name, (decs, work) in policies.items():
        for c in ids:
            dec = decs.get(c)
            if name == "clean_radar":
                readers, radar = [clean, model], clean
            else:
                readers, radar = forcing_for(dec, c, work, cases_dir, model)
            row = {"policy": name, "case": c, "choice": dec.get("choice") if dec else None,
                   "repair": json.dumps(dec.get("repair")) if dec else None,
                   "radar": None if radar in (None, "escalated") else str(
                       Path(radar).resolve().relative_to(REPO) if REPO in Path(radar).resolve().parents else radar)}
            if readers is None:
                rows.append({**row, "note": "escalated to a person (no forecast)" if radar == "escalated"
                             else "repaired file missing"})
                continue
            key = dr.forcing_hash(readers[0] if len(readers) == 2 else None, readers[-1])
            if key not in cache:
                per = []
                for s in starts:
                    obs = track.loc[s:s + pd.Timedelta(hours=a.hours)]
                    plon, plat = de.run(readers, obs["lon"].iloc[0], obs["lat"].iloc[0], s, a.hours, a.k)
                    per.append(de.score_window(plon, plat, obs, a.hours))
                p = pd.DataFrame(per)
                cache[key] = {"median_sep_km_24h": float(p["sep_km_24h"].median()),
                              "median_sep_km_12h": float(p["sep_km_12h"].median()),
                              "mean_skill": float(p["skill"].mean()), "in90_24h": float(p["in90_24h"].mean()),
                              "in50_24h": float(p["in50_24h"].mean())}
            rows.append({**row, "forcing": key, **cache[key]})
        print(f"  {name}: done ({len(cache)} distinct forcings run)")

    out = REPO / a.out
    out.mkdir(parents=True, exist_ok=True)
    fields = ["policy", "case", "choice", "repair", "radar", "forcing", "median_sep_km_12h", "median_sep_km_24h",
              "mean_skill", "in50_24h", "in90_24h", "note"]
    with open(out / "cases.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    df = pd.DataFrame(rows)
    clean_sep = df[df.policy == "clean_radar"].set_index("case")["median_sep_km_24h"]
    model_sep = df[df.policy == "always_model_only"].set_index("case")["median_sep_km_24h"]
    summary = {"drifter": a.id, "windows": len(starts), "k": a.k, "cases_dir": a.cases_dir,
               "clean_radar": str(clean), "model": str(model), "distinct_forcings": len(cache), "policies": {}}
    print(f"\n{'policy':20}{'miss 24h':>10}{'skill':>8}{'in90':>7}{'vs clean':>10}{'beats model':>13}"
          "   (means over cases; miss = median km over windows)")
    for name in policies:
        g = df[(df.policy == name) & df["median_sep_km_24h"].notna()].set_index("case")
        excess = (g["median_sep_km_24h"] - clean_sep.reindex(g.index))
        beats = float((g["median_sep_km_24h"] < model_sep.reindex(g.index) - 1e-9).mean())
        s = {"cases": int(len(g)), "mean_miss_km_24h": round(float(g["median_sep_km_24h"].mean()), 3),
             "mean_skill": round(float(g["mean_skill"].mean()), 3), "mean_in90_24h": round(float(g["in90_24h"].mean()), 3),
             "mean_excess_over_clean_km": round(float(excess.mean()), 3),
             "worst_excess_over_clean_km": round(float(excess.max()), 3),
             "share_cases_better_than_model_only": round(beats, 3),
             "escalated": int(((df.policy == name) & (df.get("note", pd.Series(dtype=str)).fillna("")
                                                     .str.startswith("escalated"))).sum())}
        summary["policies"][name] = s
        print(f"{name:20}{s['mean_miss_km_24h']:>10.2f}{s['mean_skill']:>8.2f}{s['mean_in90_24h']:>7.0%}"
              f"{s['mean_excess_over_clean_km']:>+10.2f}{s['share_cases_better_than_model_only']:>13.0%}")
    # no-motion reference: guess the object stays at the release point; its miss is how far the drifter moved
    disp = [float(de.haversine_km(track.loc[s, "lon"], track.loc[s, "lat"],
                                  track.loc[s + pd.Timedelta(hours=a.hours), "lon"],
                                  track.loc[s + pd.Timedelta(hours=a.hours), "lat"])) for s in starts]
    summary["no_motion_reference"] = {"median_miss_km_24h": round(float(np.median(disp)), 3),
                                      "mean_miss_km_24h": round(float(np.mean(disp)), 3), "skill": 0.0}
    print(f"{'no_motion (ref.)':20}{np.median(disp):>10.2f}{0.0:>8.2f}   <- 'it stays where it was reported' "
          f"(median over windows)")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ps = _load("plot_style", "src/plot_style.py")
    names = list(policies)
    fig, ax = plt.subplots(figsize=(max(12, len(ids) * 1.0), 5.4))
    width = 0.8 / len(names)
    for j, name in enumerate(names):
        g = df[df.policy == name].set_index("case").reindex(ids)
        ax.bar(np.arange(len(ids)) + (j - (len(names) - 1) / 2) * width, g["median_sep_km_24h"], width,
               color=ps.color(name), label=ps.name(name), hatch="//" if name.startswith("agent") else None,
               edgecolor="white" if name.startswith("agent") else None)
    ax.set_xticks(np.arange(len(ids)))
    ax.set_xticklabels([ps.case_label(c) for c in ids], rotation=0, fontsize=8)
    ax.set_ylabel("Median 24 h miss vs real drifter (km)", fontsize=ps.LABEL_SIZE)
    ax.set_title(f"Each policy's decision on every faulty October 2021 case, forecast against real drifter {a.id}\n"
                 f"median over {len(starts)} forecasts of 24 h per case (spread {a.k:g} m²/s)",
                 fontsize=ps.SUBTITLE_SIZE + 1)
    ax.axhline(np.median(disp), color="k", ls="--", lw=1, label="No-motion guess (object stays where reported)")
    ax.legend(fontsize=ps.LEGEND_SIZE, ncol=3, loc="upper left")
    ax.grid(axis="y", alpha=0.3)
    fig_path = REPO / "docs" / "figures" / a.fig_name
    fig.savefig(fig_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\nwrote {(out / 'cases.csv').relative_to(REPO)}, {(out / 'summary.json').relative_to(REPO)}, "
          f"{fig_path.relative_to(REPO)}")


if __name__ == "__main__":
    sys.exit(main())
