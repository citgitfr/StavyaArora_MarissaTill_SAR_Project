"""LLM-free scorer for the SAR forcing benchmark.

Grades every policy's decisions against the answer key and against the clean radar fixture. No LLM, no tools.

Policies
  always_model_only, always_fused   trivial policies built here
  baseline                           runs/results/baseline/            (src/baseline.py)
  agent_<run>                        runs/results/agent/<run>/          (scripts/run_agent_batch.py)
  oracle                             best acceptable choice from the key (radar-using if any is acceptable)

Metrics, per policy
  acceptable       decision is one of the key's acceptable choices. A mask_fill matches on the action and any
                   threshold above the speed bound (SKILL.md allows a range); rescale/shift_time compare numbers
                   (-2 == -2.0); a 2-repair decision matches only if the key lists that sequence.
  silent_errors    chose fused or repair where that is NOT acceptable: bad radar let through. Lower is better.
  radar_recovered  out of the cases where some radar-using choice is acceptable, how many the policy used radar
                   acceptably. Higher is better. (model_only is acceptable everywhere, so plain accuracy is not enough.)
  faithful_radar   out of the policy's radar-using decisions, how many hand on a radar field equal to the clean
                   fixture (vector RMS <= 0.001 m/s on shared valid cells, every clean hour present). Catches a repair
                   that passes check but is wrong, e.g. a time shift off by an hour.
  escalations, and cost/turns/minutes from eval/batches/<run>.json for agent runs.
  agreement        for agent runs: share of cases where two runs made the same choice and repair action.

Outputs (runs/results/scores/): summary.json holds aggregates only. cases.csv holds one row per policy and case,
INCLUDING each case's fault type, so it reveals the answer key: commit it only after all agent runs are final.

Run from the repository root (the key lives outside the repo):
  python src/score.py
  python src/score.py --labels ~/sar_heldout/labels.json --agent-runs rep1 rep2
"""
import argparse
import csv
import json
import math
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LABELS = Path.home() / "sar_heldout" / "labels.json"
CLEAN = REPO / "fixtures" / "hfr_2km_slice.nc"
CASES = REPO / "runs" / "cases"
RADAR_CHOICES = ("fused", "repair")
FAITHFUL_RMS = 1e-3


# ---- loading ---------------------------------------------------------------------------------
def load_decisions(results_dir):
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(results_dir).glob("case*.json"))}


def repairs_of(dec):
    r = dec.get("repair")
    return [] if r is None else (r if isinstance(r, list) else [r])


def speed_bound():
    import importlib.util
    spec = importlib.util.spec_from_file_location("forcing_tools", REPO / ".claude/skills/forcing-tools/forcing_tools.py")
    ft = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ft)
    return float(ft.load_thresholds()["speed.max_m_s"])


# ---- matching --------------------------------------------------------------------------------
def _same_repair(d, k, speed_max):
    if d.get("action") != k.get("action"):
        return False
    a, dp, kp = d["action"], d.get("param"), k.get("param")
    if a == "swap_components":
        return True
    if a == "flip_sign":
        return str(dp) == str(kp)
    try:
        dp = float(dp)
    except (TypeError, ValueError):
        return False
    if a == "mask_fill":
        return dp > speed_max
    return math.isclose(dp, float(kp), rel_tol=1e-9, abs_tol=1e-12)


def matches(dec, acc, speed_max):
    if dec.get("choice") != acc["choice"]:
        return False
    if acc["choice"] != "repair":
        return True
    d, k = repairs_of(dec), repairs_of(acc)
    return len(d) == len(k) and all(_same_repair(x, y, speed_max) for x, y in zip(d, k))


def acceptable(dec, accs, speed_max):
    return any(matches(dec, a, speed_max) for a in accs)


def oracle_choice(accs):
    radar = [a for a in accs if a["choice"] in RADAR_CHOICES]
    return dict((radar or accs)[0])


# ---- fidelity --------------------------------------------------------------------------------
def radar_used(dec, case, work_dir, cases_dir=CASES):
    """Path of the radar field a decision hands on, or None (model_only, escalate, or file not found)."""
    if dec.get("choice") == "fused":
        return cases_dir / case / "radar.nc"
    if dec.get("choice") == "repair" and work_dir is not None:
        log = Path(work_dir) / case / "repairs.json"
        if log.exists():
            entries = json.loads(log.read_text(encoding="utf-8"))
            if entries:
                p = Path(work_dir) / case / Path(entries[-1]["out"].replace("\\", "/")).name
                return p if p.exists() else None
    return None


def fidelity(used, clean=CLEAN):
    """Compare a radar field with the clean fixture on shared timestamps and shared valid cells."""
    with xr.open_dataset(used) as a, xr.open_dataset(clean) as b:
        a, b = a[["u", "v"]].load(), b[["u", "v"]].load()
    common = np.intersect1d(a["time"].values, b["time"].values)
    out = {"common_hours": int(common.size), "clean_hours": int(b.sizes["time"])}
    if not common.size:
        return {**out, "rms_m_s": None, "coverage_retained": None, "faithful": False}
    a, b = a.sel(time=common), b.sel(time=common)
    va = np.isfinite(a["u"].values) & np.isfinite(a["v"].values)
    vb = np.isfinite(b["u"].values) & np.isfinite(b["v"].values)
    both = va & vb
    if not both.any():
        return {**out, "rms_m_s": None, "coverage_retained": 0.0, "faithful": False}
    du = a["u"].values[both] - b["u"].values[both]
    dv = a["v"].values[both] - b["v"].values[both]
    rms = float(np.sqrt(np.mean(du ** 2 + dv ** 2)))
    return {**out, "rms_m_s": rms, "coverage_retained": float(both.sum() / vb.sum()),
            "faithful": bool(rms <= FAITHFUL_RMS and common.size == out["clean_hours"])}


# ---- scoring ---------------------------------------------------------------------------------
def score_policy(name, decisions, labels, speed_max, work_dir=None, with_fidelity=True, cases_dir=CASES, clean=CLEAN):
    rows = []
    for case, lab in sorted(labels.items()):
        dec = decisions.get(case)
        accs = lab["acceptable_choices"]
        row = {"policy": name, "case": case, "fault_type": lab["fault_type"], "detectable": lab["detectable"],
               "choice": None, "repair": None, "acceptable": False, "silent_error": False,
               "radar_recoverable": any(a["choice"] in RADAR_CHOICES for a in accs), "radar_recovered": False,
               "rms_m_s": None, "coverage_retained": None, "faithful": None}
        if dec is not None:
            ok = acceptable(dec, accs, speed_max)
            uses = dec.get("choice") in RADAR_CHOICES
            row.update(choice=dec.get("choice"), repair=json.dumps(dec.get("repair")), acceptable=ok,
                       silent_error=uses and not ok, radar_recovered=uses and ok and row["radar_recoverable"])
            if with_fidelity and uses:
                p = radar_used(dec, case, work_dir, cases_dir)
                if p is not None:
                    f = fidelity(p, clean)
                    row.update(rms_m_s=f["rms_m_s"], coverage_retained=f["coverage_retained"], faithful=f["faithful"])
                else:
                    row["faithful"] = False
        rows.append(row)
    return rows


def summarize(rows):
    n = len(rows)
    rec = [r for r in rows if r["radar_recoverable"]]
    radar = [r for r in rows if r["choice"] in RADAR_CHOICES]
    fid = [r for r in radar if r["faithful"] is not None]
    return {"cases": n, "decided": sum(r["choice"] is not None for r in rows),
            "acceptable": sum(r["acceptable"] for r in rows),
            "silent_errors": sum(r["silent_error"] for r in rows),
            "radar_recoverable": len(rec), "radar_recovered": sum(r["radar_recovered"] for r in rec),
            "radar_decisions": len(radar),
            "faithful_radar": sum(bool(r["faithful"]) for r in fid) if fid else None,
            "fidelity_checked": len(fid),
            "escalations": sum(r["choice"] == "escalate" for r in rows)}


def agreement(a, b):
    keys = sorted(set(a) & set(b))
    def key(d):
        return d.get("choice"), tuple(r.get("action") for r in repairs_of(d))
    same = sum(key(a[k]) == key(b[k]) for k in keys)
    return {"cases": len(keys), "same_choice_and_action": same}


def batch_cost(run):
    p = REPO / "eval" / "batches" / f"{run}.json"
    if not p.exists():
        return None
    log = json.loads(p.read_text(encoding="utf-8"))
    cs = log["cases"].values()
    return {"cost_usd": round(sum(c["cost_usd"] or 0 for c in cs), 4),
            "mean_turns": round(float(np.mean([c["turns"] or 0 for c in cs])), 2),
            "minutes": round(sum(c["seconds"] for c in cs) / 60, 1)}


# ---- cli -------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", default=str(DEFAULT_LABELS))
    ap.add_argument("--agent-runs", nargs="*", default=["rep1", "rep2"])
    ap.add_argument("--baseline", default=str(REPO / "runs" / "results" / "baseline"))
    ap.add_argument("--out", default=str(REPO / "runs" / "results" / "scores"))
    ap.add_argument("--no-fidelity", action="store_true", help="skip the netCDF comparison (faster)")
    ap.add_argument("--baseline-work", default=str(REPO / "runs" / "work" / "baseline"),
                    help="where the baseline's repaired files are")
    ap.add_argument("--cases-dir", default=str(CASES), help="the cases these decisions were made on")
    ap.add_argument("--clean", help="clean radar for fidelity (default: the key's radar_path, else the fixture)")
    a = ap.parse_args(argv)

    labels_path = Path(a.labels).expanduser()
    if not labels_path.exists():
        sys.exit(f"{labels_path} not found. Generate cases with src/inject_faults.py and move the key there.")
    key = json.loads(labels_path.read_text(encoding="utf-8"))
    labels = key["cases"]
    smax = speed_bound()
    cases_dir = Path(a.cases_dir)
    if a.clean:
        clean = Path(a.clean)
    elif key.get("radar_path"):
        clean = Path(key["radar_path"]) if Path(key["radar_path"]).is_absolute() else REPO / key["radar_path"]
    else:
        clean = CLEAN
    if not a.no_fidelity and not clean.exists():
        sys.exit(f"clean radar {clean} not found; pass --clean or --no-fidelity")
    print(f"clean radar for fidelity: {clean}; cases: {cases_dir}")
    fid = not a.no_fidelity

    policies = {
        "always_model_only": ({c: {"choice": "model_only", "repair": None} for c in labels}, None),
        "always_fused": ({c: {"choice": "fused", "repair": None} for c in labels}, None),
        "baseline": (load_decisions(a.baseline), Path(a.baseline_work)),
    }
    for run in a.agent_runs:
        d = REPO / "runs" / "results" / "agent" / run
        if not d.is_dir():
            sys.exit(f"{d} not found")
        policies[f"agent_{run}"] = (load_decisions(d), REPO / "runs" / "work" / "agent" / run)
    policies["oracle"] = ({c: oracle_choice(lab["acceptable_choices"]) for c, lab in labels.items()}, None)

    all_rows, summary = [], {"labels": str(labels_path), "seed": key.get("seed"), "faithful_rms_m_s": FAITHFUL_RMS,
                             "clean_radar": str(clean), "cases_dir": str(cases_dir),
                             "policies": {}, "agreement": {}}
    for name, (decs, work) in policies.items():
        rows = score_policy(name, decs, labels, smax, work, with_fidelity=fid and name != "oracle",
                            cases_dir=cases_dir, clean=clean)
        all_rows += rows
        s = summarize(rows)
        if name.startswith("agent_"):
            s["batch"] = batch_cost(name[len("agent_"):])
        elif name == "baseline":
            s["batch"] = {"cost_usd": 0.0, "mean_turns": None, "minutes": None}
        summary["policies"][name] = s
    for r1, r2 in combinations(a.agent_runs, 2):
        summary["agreement"][f"{r1}_vs_{r2}"] = agreement(policies[f"agent_{r1}"][0], policies[f"agent_{r2}"][0])
    if a.agent_runs:
        summary["agreement"]["baseline_vs_" + a.agent_runs[0]] = agreement(
            policies["baseline"][0], policies[f"agent_{a.agent_runs[0]}"][0])

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with open(out / "cases.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0]))
        w.writeheader()
        w.writerows(all_rows)

    hdr = f"{'policy':18}{'acceptable':>11}{'silent err':>11}{'radar rec.':>11}{'faithful':>10}{'escal.':>7}{'cost $':>8}{'turns':>7}"
    print(hdr + "\n" + "-" * len(hdr))
    for name, s in summary["policies"].items():
        b = s.get("batch") or {}
        faithful = "n/a" if s["faithful_radar"] is None else f"{s['faithful_radar']}/{s['fidelity_checked']}"
        cost = "" if b.get("cost_usd") is None else f"{b['cost_usd']:.2f}"
        turns = "" if b.get("mean_turns") is None else f"{b['mean_turns']:.1f}"
        print(f"{name:18}{s['acceptable']:>5}/{s['cases']:<5}{s['silent_errors']:>11}"
              f"{s['radar_recovered']:>5}/{s['radar_recoverable']:<5}{faithful:>10}{s['escalations']:>7}{cost:>8}{turns:>7}")
    for k, v in summary["agreement"].items():
        print(f"agreement {k}: {v['same_choice_and_action']}/{v['cases']}")
    show = (lambda p: p.relative_to(REPO)) if REPO in out.parents else (lambda p: p)
    print(f"\nwrote {show(out / 'summary.json')} and {show(out / 'cases.csv')} (cases.csv reveals the key)")


if __name__ == "__main__":
    main()
