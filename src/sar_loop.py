"""Lab 4, Phase D: the multi-period SAR loop, scored against the real drifter. LLM-free (it replays decisions).

Each scenario (runs/cases_loop/mapping.json, built by src/make_loop_cases.py) starts at t0 with 500 particles at the
drifter's real position (equal weights). For each period k = 1..4 (6 h each):
  1. data:     the radar delivery that arrived at t_k (a 72 h case folder) was decided by a POLICY
  2. forcing:  the radar the policy approved (as delivered, or repaired) is turned into a hard_switch blend with the
               model (src/blend.py; radar where it has data, model in holes); no approved radar -> model only
  3. forecast: particles move from t_{k-1} to t_k with that forcing (OpenDrift OceanDrift, no wind, K = 1600 m2/s,
               the model-only spread calibrated on the tuning windows; the same K for every policy)
  4. search:   at t_k, a plan (src/search.py, greedy, ASSETS assets) on the weighted particles
  5. score:    POD at the drifter's TRUE position at t_k; P(found by t_k) = 1 - prod(1 - POD_j)
  6. update:   Bayes update of the particle weights (the search found nothing) and continue
Simplifications (stated): hindcast forcing (the delivery covers the period being forecast); every policy re-forecasts
every period; the plan is made for the moment t_k.

Policies:  no_qc (radar as delivered), model_only, scripted (src/baseline.py decisions), agent_<run> (cleaning
agent decisions, scripts/run_agent_batch.py), perfect (the clean radar every period).
Judged without an answer key, by comparing radar with the clean fixture over the period's hours:
  silent error   the policy used radar for the period that differs from the clean radar (fault let through, or a
                 repair that is wrong)
  false alarm    the delivery was clean over the period but the policy used no radar
  escalation     the agent handed the delivery to a person (the loop continues with the model)

  python src/sar_loop.py --agent-runs loop_rep1
Outputs (never overwritten): runs/results/loop/{periods.csv, summary.json}.
"""
import argparse
import hashlib
import importlib.util
import json
import sys
import warnings
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
se = _load("search", "src/search.py")
sc = _load("score", "src/score.py")
de = fe.de

K = 1600.0
ASSETS = 3
PARTICLES = 500
CLEAN = REPO / "fixtures" / "hfr_20211018.nc"
MODEL = REPO / "fixtures" / "hycom_20211018.nc"


def run_from(readers, lons, lats, start, hours, k):
    """Advance particles that start at (lons, lats) for `hours`; returns their final positions (order kept)."""
    plon, plat = fe.run([str(p) for p in readers], np.asarray(lons), np.asarray(lats), start, hours, k, 0.0, False,
                        number=len(lons), radius=0)
    return plon[:, -1], plat[:, -1]


def faithful(radar_path, clean, t0, t1, tol=1e-3):
    """True if the radar file equals the clean radar at every hour in [t0, t1] (values and NaN pattern)."""
    with xr.open_dataset(radar_path) as r:
        hours = pd.date_range(t0, t1, freq="h")
        rt = pd.DatetimeIndex(r.time.values)
        if any(h not in rt for h in hours):
            return False
        a = r[["u", "v"]].sel(time=hours).load()
    b = clean[["u", "v"]].sel(time=hours)
    for c in ("u", "v"):
        x, y = a[c].values, b[c].values
        if x.shape != y.shape or not np.array_equal(np.isfinite(x), np.isfinite(y)):
            return False
        m = np.isfinite(x)
        if m.any() and np.nanmax(np.abs(x[m] - y[m])) > tol:
            return False
    return True


class Forcing:
    """Blend files cached by the content hash of the radar file they came from."""

    def __init__(self, work, model=MODEL):
        self.work, self.model = Path(work), Path(model)
        self.work.mkdir(parents=True, exist_ok=True)

    def readers(self, radar):
        if radar is None:
            return [self.model]
        h = hashlib.sha256(Path(radar).read_bytes()).hexdigest()[:16]
        out = self.work / f"blend_{h}.nc"
        if not out.exists():
            bl.blend(radar, self.model, out, {"method": "hard_switch"}, require_check=False)
        return [out, self.model]


def policy_radar(policy, case, cases_dir, decisions):
    """(radar path or None, choice) for one delivery under one policy."""
    if policy == "no_qc":
        return Path(cases_dir) / case / "radar.nc", "fused"
    if policy == "model_only":
        return None, "model_only"
    if policy == "perfect":
        return CLEAN, "clean"
    res_dir, work = decisions[policy]
    p = Path(res_dir) / f"{case}.json"
    if not p.exists():
        return None, "missing"
    dec = json.loads(p.read_text(encoding="utf-8"))
    ch = dec.get("choice")
    if ch in sc.RADAR_CHOICES:
        r = sc.radar_used(dec, case, work, Path(cases_dir))
        return (Path(r) if r else None), (ch if r else "repair_file_missing")
    return None, ch


def run_scenario(sid, scen, cases, policy, cases_dir, decisions, forcing, track, clean):
    t0 = pd.Timestamp(scen["t0"])
    ph = int(scen["period_h"])
    lons = np.full(PARTICLES, track.loc[t0, "lon"])
    lats = np.full(PARTICLES, track.loc[t0, "lat"])
    rng = np.random.default_rng(0)                       # release within 100 m, as elsewhere
    lons = lons + rng.normal(0, 0.1, PARTICLES) / (111.195 * np.cos(np.radians(lats[0]))) / 2
    lats = lats + rng.normal(0, 0.1, PARTICLES) / 111.195 / 2
    w = np.ones(PARTICLES) / PARTICLES
    miss_all, rows = 1.0, []
    for k in range(1, int(scen["periods"]) + 1):
        case = cases[k]
        ta, tb = t0 + pd.Timedelta(hours=ph * (k - 1)), t0 + pd.Timedelta(hours=ph * k)
        radar, choice = policy_radar(policy, case, cases_dir, decisions)
        delivered_ok = faithful(Path(cases_dir) / case / "radar.nc", clean, ta, tb)
        used_ok = faithful(radar, clean, ta, tb) if radar is not None else None
        lons, lats = run_from(forcing.readers(radar), lons, lats, ta, ph, K)
        truth = track.loc[tb]
        plan = se.plan(lons, lats, w, effort=se.effort_km2(assets=ASSETS), planner="greedy")
        pod_truth = float(se.pod_at(plan, truth["lon"], truth["lat"])[0])
        p_found_now = miss_all * pod_truth
        miss_all *= 1 - pod_truth
        g = plan["grid"]
        rows.append({"scenario": sid, "policy": policy, "period": k, "case": case, "t_end": str(tb), "choice": choice,
                     "radar_used": radar is not None, "delivered_clean": delivered_ok,
                     "silent_error": radar is not None and not used_ok,
                     "false_alarm": radar is None and delivered_ok and policy not in ("model_only",),
                     "escalated": choice == "escalate", "pod_truth": pod_truth, "p_found_this_period": p_found_now,
                     "p_found_cum": 1 - miss_all, "pos_pred": plan["pos_predicted"],
                     "datum_to_truth_km": float(de.haversine_km(g.lon0, g.lat0, truth["lon"], truth["lat"]))})
        w, _ = se.bayes_update(w, plan, lons, lats)
    return rows


def main(argv=None):
    import logging
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases-dir", default=str(REPO / "runs" / "cases_loop"))
    ap.add_argument("--baseline", default=str(REPO / "runs" / "results" / "baseline_loop"))
    ap.add_argument("--baseline-work", default=str(REPO / "runs" / "work" / "baseline_loop"))
    ap.add_argument("--agent-runs", nargs="*", default=[])
    ap.add_argument("--drifters", default=str(fe.DEF_DRIFTERS))
    ap.add_argument("--id", type=int, default=de.DEF_ID)
    ap.add_argument("--out", default=str(REPO / "runs" / "results" / "loop"))
    ap.add_argument("--work", default=str(REPO / "runs" / "work" / "loop"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    if (out / "summary.json").exists():
        sys.exit(f"{out}/summary.json already exists; not overwriting")
    mp = json.loads((Path(a.cases_dir) / "mapping.json").read_text(encoding="utf-8"))
    by_scen = {}
    for case, info in mp["cases"].items():
        by_scen.setdefault(info["scenario"], {})[info["period"]] = case
    decisions = {"scripted": (a.baseline, a.baseline_work)}
    for run in a.agent_runs:
        decisions[f"agent_{run}"] = (REPO / "runs" / "results" / "agent" / run, REPO / "runs" / "work" / "agent" / run)
    policies = ["no_qc", "model_only", "scripted", *[f"agent_{r}" for r in a.agent_runs], "perfect"]
    track = de.drifter_track(a.drifters, a.id, pd.Timestamp("2021-10-18"), pd.Timestamp("2021-10-28 23:00"))
    clean = xr.open_dataset(CLEAN).load()
    forcing = Forcing(a.work)
    rows = []
    for sid in sorted(by_scen):
        for pol in policies:
            rows += run_scenario(sid, mp["scenarios"][sid], by_scen[sid], pol, a.cases_dir, decisions, forcing,
                                 track, clean)
        print(f"  {sid} done")
    df = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "periods.csv", index=False)
    last = df[df["period"] == df.groupby("scenario")["period"].transform("max")]
    summ = (df.groupby("policy", sort=False)
              .agg(radar_periods=("radar_used", "sum"), silent_errors=("silent_error", "sum"),
                   false_alarms=("false_alarm", "sum"), escalations=("escalated", "sum"),
                   mean_datum_to_truth_km=("datum_to_truth_km", "mean"))
              .join(last.groupby("policy", sort=False)["p_found_cum"].mean().rename("mean_p_found_24h"))
              .reset_index())
    per_scen = last.pivot(index="scenario", columns="policy", values="p_found_cum")[policies]
    cost = {}
    for run in a.agent_runs:
        b = REPO / "eval" / "batches" / f"{run}.json"
        if b.exists():
            cost[f"agent_{run}"] = json.loads(b.read_text(encoding="utf-8")).get("total_cost_usd")
    summary = {"K": K, "assets_per_period": ASSETS, "effort_km2_per_period": se.effort_km2(assets=ASSETS),
               "particles": PARTICLES, "policies": json.loads(summ.round(4).to_json(orient="records")),
               "p_found_24h_by_scenario": json.loads(per_scen.round(4).to_json(orient="index")), "agent_cost_usd": cost}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("\nper policy (6 scenarios x 4 periods)")
    print(f"{'policy':20}{'P(found) 24h':>14}{'radar periods':>15}{'silent err':>12}{'false alarm':>13}{'escalated':>11}"
          f"{'datum-truth km':>16}")
    for _, r in summ.iterrows():
        print(f"{r['policy']:20}{r['mean_p_found_24h']:>14.3f}{r['radar_periods']:>15}{r['silent_errors']:>12}"
              f"{r['false_alarms']:>13}{r['escalations']:>11}{r['mean_datum_to_truth_km']:>16.1f}")
    print("\nP(found) by 24 h, per scenario")
    print(per_scen.round(3).to_string())
    print(f"\nwrote {out}/periods.csv, summary.json")


if __name__ == "__main__":
    sys.exit(main())
