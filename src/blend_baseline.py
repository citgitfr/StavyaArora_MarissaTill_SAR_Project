"""Try-everything baseline for the blend choice (Lab 4, Phase B). LLM-free; uses the SAME tool (src/blend_tools.py)
and the SAME case folder as the agent, so it sees exactly what the agent sees (tuning period only).

Two stages, both on the tuning windows, ranked by mean 24 h separation (as blend_tools reports it):
  1. currents: model_only and every blend in GRID, at windage 0, Stokes off
  2. wind:     the TOP_N best currents x WINDAGES x Stokes {off, on}
The best setup overall is the baseline's decision. It uses many more evaluations than the agent's budget; the number
is recorded so the comparison states its cost.

Output (to --out, never overwritten): decision.json in the same format as the agent's decision
  {"policy", "currents": "model_only" or {"spec": {...}}, "windage", "stokes", "evaluations_used",
   "tuning_mean_sep_km_24h", "reason"}
plus ranking.json (every evaluation). Blend files go to --run-dir (default runs/work/blend/baseline_<case>).

  python src/blend_baseline.py --case runs/blend_cases/oct21 --out runs/results/blend/baseline   (about 25 min)
"""
import argparse
import importlib.util
import json
import sys
from itertools import product
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("blend_tools", REPO / "src" / "blend_tools.py")
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)

TRUST = {"trust_min_coverage": 0.3, "trust_max_rms": 0.4}
BASES = [{"method": "hard_switch"},
         *({"method": "edge_taper", "taper_km": k} for k in (5, 15, 30)),
         *({"method": "weighted", "radar_weight": w} for w in (0.25, 0.5, 0.75))]
GRID = [{**b, **({"gap_fill_hours": 3} if g else {}), **(TRUST if t else {})}
        for b, g, t in product(BASES, (False, True), (False, True))]
WINDAGES = (0.0, 0.005, 0.01, 0.02)
TOP_N = 3


def spec_name(s):
    parts = [s["method"]]
    if "taper_km" in s:
        parts.append(f"t{s['taper_km']:g}")
    if "radar_weight" in s:
        parts.append(f"w{s['radar_weight']:g}")
    if s.get("gap_fill_hours"):
        parts.append(f"g{s['gap_fill_hours']}")
    if s.get("trust_min_coverage") is not None:
        parts.append("trust")
    return "_".join(parts).replace(".", "p")


def run(case, run_dir, grid=GRID, windages=WINDAGES, top_n=TOP_N, log=print):
    bt.BUDGET = 10 ** 6                          # the baseline has no budget; its cost is reported instead
    names = {"model_only": None}
    for s in grid:
        n = spec_name(s)
        if not (Path(run_dir) / "blends" / f"{n}.nc").exists():
            bt.build(case, run_dir, n, s)
        names[n] = s
    stage1 = []
    for i, (n, s) in enumerate(names.items(), 1):
        r = bt.evaluate(case, run_dir, n, 0.0, "off")
        stage1.append((r["mean_sep_km_24h"], n))
        log(f"  stage 1 {i}/{len(names)} {n}: mean sep 24 h {r['mean_sep_km_24h']} km")
    stage1.sort()
    evals = []
    for _, n in stage1[:top_n]:
        for w, st in product(windages, ("off", "on")):
            r = bt.evaluate(case, run_dir, n, w, st)
            evals.append({**r, "spec": names[n]})
            log(f"  stage 2 {n} windage {w:g} stokes {st}: {r['mean_sep_km_24h']} km")
    all_evals = [json.loads(p.read_text(encoding="utf-8"))["summary"]
                 for p in sorted((Path(run_dir) / "evals").glob("e_*.json"))]
    best = min(evals, key=lambda r: (r["mean_sep_km_24h"], r["windage"], r["stokes"] == "on"))
    decision = {"policy": "baseline_grid",
                "currents": "model_only" if best["currents"] == "model_only" else {"spec": best["spec"]},
                "windage": best["windage"], "stokes": best["stokes"],
                "evaluations_used": len(all_evals), "tuning_mean_sep_km_24h": best["mean_sep_km_24h"],
                "reason": f"lowest mean 24 h separation on the tuning windows among {len(all_evals)} evaluations"}
    ranking = sorted(({k: e[k] for k in ("eval_id", "currents", "windage", "stokes", "mean_sep_km_24h",
                                        "mean_skill")} for e in all_evals), key=lambda e: e["mean_sep_km_24h"])
    return decision, ranking


def main(argv=None):
    import logging
    import warnings
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-dir", default=None)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if (out / "decision.json").exists():
        sys.exit(f"{out}/decision.json already exists; not overwriting")
    run_dir = Path(a.run_dir) if a.run_dir else REPO / "runs" / "work" / "blend" / f"baseline_{Path(a.case).name}"
    print(f"{len(GRID) + 1} currents x stage 1, then top {TOP_N} x {len(WINDAGES)} windages x Stokes off/on")
    decision, ranking = run(a.case, run_dir)
    decision["case"] = str(a.case)
    out.mkdir(parents=True, exist_ok=True)
    (out / "decision.json").write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")
    (out / "ranking.json").write_text(json.dumps(ranking, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    sys.exit(main())
