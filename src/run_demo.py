"""One command for the Lab 3 loop on the real October 2021 window:
messy data -> decision (scripted baseline vs Claude agent) -> forcing -> OpenDrift -> map -> score (key and reality).

  python src/run_demo.py --replay              # October 2021 (the real-drifter window); about 10 minutes
  python src/run_demo.py --replay --august     # also the August 2021 benchmark section

--replay reuses the agent decisions saved in the repository instead of calling Claude (no cost, no network, no API
key). Everything else is recomputed from committed code and data and checked against what is committed.

October steps
  1 regenerate the 14 faulty cases (seed 1018) from the clean October radar; check against runs/cases_oct and the key
  2 rerun the scripted baseline; check its decisions against runs/results/baseline_oct
  3 replay every saved agent run (runs/results/agent/oct_rep*), rebuilding missing repaired radar files with the
    frozen repair tool and checking rebuilt files against existing ones
  4 score decisions against the answer key                                   (src/score.py)
  5 drift each decision 24 h and compare with a run on the clean radar       (src/drift.py)
  6 clean forecasts vs the real drifter                                      (src/drifter_eval.py, src/drifter_viz.py)
  7 every policy's decision vs the real drifter                              (src/decision_reality.py)
  8 baseline vs agent pictures: summary over all cases and windows, side-by-side decisions, animations
                                                                             (src/decision_viz.py)
  9 harder cases (seed 2026, --plan compound): regenerate + check, baseline, replay agent runs cmp_rep*, score vs key,
    decisions vs the real drifter                                            (report section 8)
Output: demo_results/lab3_demo.md (all tables and figures), demo_results/lab3_figures/, demo_results/lab3_run_log.txt

Data comes from fixtures/ (committed): October radar, HYCOM and drifter files (data/ is used as a fallback).
Nothing is downloaded. Answer keys are read from outside the repository (default ~/sar_heldout/); a missing key is
regenerated there from the seed (the injector is deterministic).
"""
import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
OUT_DIR = REPO / "demo_results"
MD = OUT_DIR / "lab3_demo.md"
FIG_DIR = OUT_DIR / "lab3_figures"
LOG = OUT_DIR / "lab3_run_log.txt"
HELDOUT = Path.home() / "sar_heldout"
TOOL = REPO / ".claude" / "skills" / "forcing-tools" / "forcing_tools.py"
DRIFTER_ID = 300534061399950
OCT_SEED, AUG_SEED, CMP_SEED = 1018, 295, 2026
OCT = {"radar": ["fixtures/hfr_20211018.nc", "data/hfr_20211018.nc"],
       "model": ["fixtures/hycom_20211018.nc", "data/hycom_20211018.nc"],
       "drifters": ["fixtures/drifter_300534061399950_202110.csv", "data/drifters/gdp_hourly_2018-01-01_2025-01-01.csv"]}
FIG_CASES = ["case14", "case06"]            # October cases drawn in the per-case figures (seed 1018)
TRACE_CASES = ["case14", "case06", "case04"]  # October cases shown as decision traces (a repair, a repair, model only)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drift = _load("drift", "src/drift.py")                 # forcing_hash: compares data, not file bytes


class Demo:
    def __init__(self, log_path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = open(log_path, "w", encoding="utf-8")
        self.checks = []                                # (name, ok, detail)
        self.t0 = time.time()

    def say(self, msg):
        print(msg, flush=True)
        self.log.write(msg + "\n")
        self.log.flush()

    def run(self, *args, label=None):
        shown = " ".join(str(Path(a).relative_to(REPO)) if isinstance(a, Path) and REPO in Path(a).parents
                         else str(a) for a in args)
        self.say(f"\n$ python {shown}")
        t = time.time()
        p = subprocess.run([PY, *map(str, args)], cwd=REPO, capture_output=True, text=True)
        out = "\n".join(l for l in (p.stdout + p.stderr).splitlines()
                        if not any(w in l for w in ("Warning", "warn(", "DeprecationWarning", "np.reshape", "data[key]")))
        self.log.write(out + "\n")
        print("\n".join(out.splitlines()[-25:]), flush=True)
        if p.returncode != 0:
            raise RuntimeError(f"step failed ({label or shown}), exit {p.returncode}; see {LOG}")
        self.say(f"  ({time.time() - t:.0f} s)")
        return p.stdout

    def check(self, name, ok, detail=""):
        self.checks.append((name, bool(ok), detail))
        self.say(f"  CHECK {'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}")


def first_existing(cands):
    for c in cands:
        if (REPO / c).exists():
            return REPO / c
    return None


def decisions(d):
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(d).glob("case*.json"))}


def same_decisions(a, b):
    def norm(x):
        r = x.get("repair")
        r = r if isinstance(r, list) else ([] if r is None else [r])
        return x.get("choice"), tuple((s.get("action"), str(float(s["param"])) if isinstance(s.get("param"), (int, float))
                                       else str(s.get("param"))) for s in r)
    keys = sorted(set(a) | set(b))
    diff = [k for k in keys if k not in a or k not in b or norm(a[k]) != norm(b[k])]
    return len(keys) - len(diff), len(keys), diff


# ---- stages ----------------------------------------------------------------------------------
def cases_stage(demo, tag, radar, model, cases_dir, labels, seed, plan="standard"):
    """Regenerate cases into a temp folder; compare with the repo's cases and the key; fill whichever is missing."""
    tmp = Path(tempfile.mkdtemp(prefix=f"sar_{tag}_"))
    try:
        demo.run("src/inject_faults.py", "--radar", radar, "--model", model, "--cases", tmp / "cases",
                 "--labels", tmp / "labels.json", "--seed", seed, "--plan", plan, label=f"{tag} cases")
        new_key = json.loads((tmp / "labels.json").read_text(encoding="utf-8"))
        if labels.exists():
            old = json.loads(labels.read_text(encoding="utf-8"))
            demo.check(f"{tag}: regenerated answer key equals the stored key", old.get("cases") == new_key.get("cases"))
        else:
            labels.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp / "labels.json", labels)
            demo.say(f"  key was missing; regenerated at {labels}")
        ids = sorted(p.name for p in (tmp / "cases").iterdir())
        if cases_dir.exists():
            same = sum(drift.forcing_hash(tmp / "cases" / c / "radar.nc", None)
                       == drift.forcing_hash(cases_dir / c / "radar.nc", None) for c in ids if (cases_dir / c).exists())
            demo.check(f"{tag}: regenerated cases have the same data as {cases_dir.relative_to(REPO)}",
                       same == len(ids), f"{same}/{len(ids)} radar files identical in value")
        else:
            shutil.copytree(tmp / "cases", cases_dir)
            demo.say(f"  cases were missing; written to {cases_dir.relative_to(REPO)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def baseline_stage(demo, tag, cases_dir, committed):
    res, work = REPO / "runs" / "results" / f"_replay_baseline_{tag}", REPO / "runs" / "work" / f"_replay_baseline_{tag}"
    for d in (res, work):
        shutil.rmtree(d, ignore_errors=True)
    demo.run("src/baseline.py", "--cases-dir", cases_dir, "--results", res, "--work", work, label=f"{tag} baseline")
    if committed.exists():
        same, n, diff = same_decisions(decisions(res), decisions(committed))
        demo.check(f"{tag}: rerun baseline equals {committed.relative_to(REPO)}", same == n, f"{same}/{n} identical"
                   + (f"; differ: {diff}" if diff else ""))
    return res, work


def replay_agent(demo, tag, run, cases_dir):
    """Rebuild missing repaired files from repairs.json with the frozen tool; check rebuilt files against existing."""
    work = REPO / "runs" / "work" / "agent" / run
    tmp_root = REPO / "runs" / "work" / f"_replay_tmp_{run}"
    shutil.rmtree(tmp_root, ignore_errors=True)
    rebuilt = checked = matched = 0
    for log in sorted(work.glob("case*/repairs.json")):
        case = log.parent.name
        current = cases_dir / case / "radar.nc"
        for i, e in enumerate(json.loads(log.read_text(encoding="utf-8")), 1):
            target = log.parent / Path(e["out"].replace("\\", "/")).name
            tmp_out = tmp_root / case / f"step{i}.nc"
            args = [TOOL, "repair", "--file", current, "--action", e["action"], "--out", tmp_out]
            if e.get("param") is not None:
                args[6:6] = ["--param", e["param"]]                   # before --out
            subprocess.run([PY, *map(str, args)], cwd=REPO, capture_output=True, text=True, check=True)
            if target.exists():
                checked += 1
                matched += drift.forcing_hash(tmp_out, None) == drift.forcing_hash(target, None)
            else:
                shutil.copyfile(tmp_out, target)
                rebuilt += 1
            current = target
    shutil.rmtree(tmp_root, ignore_errors=True)
    n = len(decisions(REPO / "runs" / "results" / "agent" / run))
    demo.say(f"  agent {run}: {n} saved decisions replayed (no Claude call); repaired files rebuilt {rebuilt}, "
             f"re-checked {checked}")
    if checked:
        demo.check(f"{tag}: agent {run} repaired files reproduce with the frozen tool", matched == checked,
                   f"{matched}/{checked} identical in value")


# ---- report helpers --------------------------------------------------------------------------
def jload(rel):
    p = REPO / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return "\n".join(out + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows])


def score_table(s):
    rows = []
    for name, p in s["policies"].items():
        b = p.get("batch") or {}
        rows.append([name, f"{p['acceptable']}/{p['cases']}", p["silent_errors"],
                     f"{p['radar_recovered']}/{p['radar_recoverable']}",
                     "n/a" if p["faithful_radar"] is None else f"{p['faithful_radar']}/{p['fidelity_checked']}",
                     p["escalations"], "" if b.get("cost_usd") is None else f"${b['cost_usd']:.2f}",
                     "" if b.get("mean_turns") is None else b["mean_turns"]])
    t = table(["policy", "acceptable", "silent errors", "radar recovered", "faithful radar", "escalations",
               "cost", "mean turns"], rows)
    agr = "; ".join(f"{k}: {v['same_choice_and_action']}/{v['cases']}" for k, v in s.get("agreement", {}).items())
    return t + (f"\n\nAgreement on choice and repair action: {agr}." if agr else "")


def drift_table(d):
    rows = [[k, v["median_sep_km_24h"], v["mean_of_mean_sep_km_24h"], v["max_sep_km_24h"], v["mean_poc_overlap"]]
            for k, v in d["policies"].items()]
    return table(["policy", "median of case medians 24 h (km)", "mean 24 h (km)", "worst case 24 h (km)",
                  "mean POC-map overlap"], rows)


def fig(name, alt):
    src = REPO / "docs" / "figures" / name
    if not src.exists():
        return []
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, FIG_DIR / name)
    return ["", f"![{alt}]({FIG_DIR.name}/{name})"]


def status_section():
    """The 'Project status' section of README.md (diagram + module table), so the report and README never differ."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    start = text.find("### Project status")
    if start < 0:
        return []
    end = text.find("\n### ", start + 5)
    body = text[start + len("### Project status"):end if end > 0 else None].strip("\n")
    return ["", "## Project status: which modules are finished", "", body]


def _csv(rel):
    import csv
    p = REPO / rel
    return list(csv.DictReader(open(p, encoding="utf-8"))) if p.exists() else []


def _steps(d):
    r = d.get("repair")
    return r if isinstance(r, list) else ([] if r is None else [r])


def decision_traces(agent_run, base_dir, cases=None, scores_csv="runs/results/scores_oct/cases.csv",
                    reality_csv="runs/results/reality_oct/cases.csv",
                    heading="## 7. Decision traces: what the agent saw, concluded and did", which="three October cases",
                    only_differences=False):
    """For a few cases: what the checks found, what the agent concluded and why, what the baseline chose, and how
    each decision scored against the key and the real drifter."""
    ps = _load("plot_style", "src/plot_style.py")
    agent = decisions(REPO / "runs" / "results" / "agent" / agent_run)
    base = decisions(base_dir)
    key_rows = {(r["policy"], r["case"]): r for r in _csv(scores_csv)}
    real_rows = {(r["policy"], r["case"]): r for r in _csv(reality_csv)}
    if only_differences:                 # cases where the two chose differently (not just order or mask threshold)
        cases = [c for c in sorted(agent) if c in base and
                 (agent[c].get("choice"), sorted(x.get("action") for x in _steps(agent[c])))
                 != (base[c].get("choice"), sorted(x.get("action") for x in _steps(base[c])))]
    L = ["", heading, "",
         f"For {which}: the checks that failed (from the scripted baseline's first `check`), the agent's "
         "evidence and its own reason, the baseline's choice, and how each scored. Evidence lines are copied verbatim "
         "from the agent's decision file; numbers in them come from the tools."]
    for c in cases or TRACE_CASES:
        a, b = agent.get(c), base.get(c)
        if not a or not b:
            continue
        failed = sorted({e.split(".")[2] for e in b.get("evidence", []) if e.startswith("check.tests.")})
        ka, kb = key_rows.get((f"agent_{agent_run}", c), {}), key_rows.get(("baseline", c), {})
        miss = {p: real_rows.get((p, c), {}).get("median_sep_km_24h") for p in
                (f"agent_{agent_run}", "baseline", "always_fused", "clean_radar")}
        notes = {p: real_rows.get((p, c), {}).get("note") for p in miss}
        fmt = lambda v, p=None: ("escalated, no forecast" if p and (notes.get(p) or "").startswith("escalated")
                                 else "n/a" if v in (None, "") else f"{float(v):.1f} km")
        L += ["", f"### {ps.case_label(c)}", "",
              f"- **Checks that failed:** {', '.join(failed) if failed else 'none (all 8 passed)'}",
              f"- **Agent's evidence:** " + "; ".join(f"`{e}`" for e in a.get("evidence", [])[:6])
              + (" …" if len(a.get("evidence", [])) > 6 else ""),
              f"- **Agent's reason:** {a.get('reason', '')}",
              f"- **Agent chose:** {ps.decision_text(a)}  ·  **Baseline chose:** {ps.decision_text(b)}"]
        if ka:
            L.append(f"- **Answer key:** planted fault `{ka.get('fault_type')}`; agent acceptable: "
                     f"{ka.get('acceptable')}, baseline acceptable: {kb.get('acceptable')}")
        L.append(f"- **24 h miss vs real drifter (median of 20 forecasts):** agent "
                 f"{fmt(miss[f'agent_{agent_run}'], f'agent_{agent_run}')}, "
                 f"baseline {fmt(miss['baseline'])}, No QC {fmt(miss['always_fused'])}, "
                 f"perfect repair {fmt(miss['clean_radar'])}")
    return L


def compound_section(cmp_runs, base_dir):
    """Section 8: the harder cases (two faults at once, or a faulty model) where a fixed rule table can go wrong."""
    s, r = jload("runs/results/scores_cmp/summary.json"), jload("runs/results/reality_cmp/summary.json")
    if not s and not r:
        return []
    L = ["", "## 8. Harder cases: where judgment matters", "",
         f"8 cases made from the same clean October radar (seed {CMP_SEED}): two clean, three with two radar faults at "
         "once (controls a step-by-step rule can handle), two rotated radar fields (both components negated, or swapped "
         "and one negated), and one case where the **model** file is faulty and the radar is clean. For the faulty "
         "model the only acceptable choice is to escalate to a person: repairing good radar or falling back to the "
         "broken model are both wrong."]
    if s:
        L += ["", "### Decisions vs the answer key", "", score_table(s)]
    if r:
        rows = [[k, v["cases"], v.get("escalated", 0), v["mean_miss_km_24h"], f"{v['mean_excess_over_clean_km']:+.2f}",
                 f"{v['worst_excess_over_clean_km']:+.2f}"] for k, v in r["policies"].items()]
        L += ["", "### Each decision's forecast vs the real drifter", "",
              "Mean over the cases each policy actually forecast; an escalated case is handed to a person and has no "
              "automatic forecast, so it is counted separately.", "",
              table(["policy", "cases forecast", "escalated", "mean 24 h miss (km)", "vs perfect repair (km)",
                     "worst case vs perfect repair (km)"], rows)]
    L += fig("decisions_vs_reality_cmp.png", "compound decisions vs reality")
    if cmp_runs:
        L += decision_traces(cmp_runs[0], base_dir, scores_csv="runs/results/scores_cmp/cases.csv",
                             reality_csv="runs/results/reality_cmp/cases.csv",
                             heading="### Decision traces where the agent and the baseline chose differently",
                             which="every harder case where the two chose differently", only_differences=True)
    return L


def write_report(demo, commit, oct_ok, aug, oct_runs, base_dir=None, cmp_runs=(), cmp_base=None):
    L = ["# Lab 3 demo: SAR forcing agent on a real October 2021 window", "",
         f"Generated by `python src/run_demo.py --replay{' --august' if aug else ''}` on "
         f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from commit `{commit}`. Agent decisions are replayed from the "
         "repository (no new Claude calls); everything else is recomputed. Full output: "
         f"[{LOG.name}]({LOG.name}). Commands and file formats: [docs/pipeline.md](../docs/pipeline.md).", "",
         "**The loop:** messy radar data → decision (scripted baseline or Claude agent: use the radar, repair it, use "
         "the model only, or escalate) → forcing (OpenDrift reader priority: radar where it has data, model elsewhere) "
         "→ OpenDrift drift → probability-of-containment (POC) map → LLM-free scores against the answer key and against "
         "a real drifter."]
    L += status_section()
    L += ["", "## Reproducibility checks", "",
          table(["check", "result", "detail"], [[n, "PASS" if ok else "**FAIL**", d] for n, ok, d in demo.checks])]
    if not oct_ok:
        L += ["", "## October section skipped", "", "The October radar, HYCOM and drifter files were not found in "
              "fixtures/ or data/ (see the run log)."]
    else:
        L += ["", "## 1. The October cases", "",
              f"14 cases (4 clean, 10 planted faults: scaling, time shifts, sign flip, swapped components, blanked hours, "
              f"a dropout, fill values) made from the clean HF-radar file for 2021-10-18..28 off Point Arguello "
              f"(seed {OCT_SEED}). Each case pairs a radar file with the HYCOM model file. The answer key stays outside "
              "the repository."]
        so = jload("runs/results/scores_oct/summary.json")
        if so:
            L += ["", "## 2. Decisions vs the answer key", "",
                  "*Acceptable*: a choice the key allows. *Silent errors*: radar trusted when it should not be. *Radar "
                  "recovered*: usable radar used correctly. *Faithful*: the radar handed on equals the clean original.", "",
                  score_table(so)]
        L += fig("baseline_vs_agent_oct.png", "baseline vs agent")
        do = jload("runs/results/drift_oct/summary.json")
        if do:
            L += ["", "## 3. What each decision does to the forecast (vs a run on the clean radar)", "",
                  f"OceanDrift, {do['particles']} particles from 5 points, {do['hours']} h from {do['start']}. Separation "
                  "is from the same particles in the clean-radar run (decision impact, not reality).", "", drift_table(do)]
            for c in FIG_CASES:
                L += fig(f"drift_oct_{c}.png", f"drift {c}")
        r = jload(f"runs/results/drifter/{DRIFTER_ID}/summary.json")
        if r:
            rows = [[k.split("|")[0], k.split("|")[1], v["median_sep_km_6h"], v["median_sep_km_12h"],
                     v["median_sep_km_24h"], v["mean_skill"], f"{v['in50_24h']:.0%}", f"{v['in90_24h']:.0%}",
                     v["median_area90_km2_24h"]] for k, v in r["results"].items()]
            chk = r["frozen_check_on_real_pair"]
            L += ["", f"## 4. Reality check: clean forecasts vs real drifter {DRIFTER_ID}", "",
                  f"Undrogued Global Drifter Program drifter (it follows the top meter, like HF radar). "
                  f"{r['windows']} forecasts of {r['window_hours']} h, a new one every {r['every_hours']} h, each "
                  f"releasing {r['particles']} particles at the drifter's real position. Frozen `check` on the clean "
                  f"pair: **{chk.get('result')}** (so the baseline and the agent choose fused).", "",
                  table(["forcing", "diffusivity (m²/s)", "miss 6 h (km)", "miss 12 h (km)", "miss 24 h (km)", "skill",
                         "inside 50% area", "inside 90% area", "90% area (km²)"], rows), "",
                  f"Radar+model closer than model only at 24 h in {r['radar_better_share_24h']:.0%} of window/diffusivity "
                  "pairs. Skill: Liu & Weisberg (2011), 1 = perfect, 0 = no better than not moving."]
            L += fig(f"drifter_{DRIFTER_ID}_summary.png", "drifter summary")
            for g in sorted((REPO / "docs" / "figures").glob(f"drifter_{DRIFTER_ID}_window_*.gif")):
                L += fig(g.name, "one forecast")
        ro = jload("runs/results/reality_oct/summary.json")
        if ro:
            rows = [[k, v["mean_miss_km_24h"], v["mean_skill"], f"{v['mean_in90_24h']:.0%}",
                     f"{v['mean_excess_over_clean_km']:+.2f}", f"{v['worst_excess_over_clean_km']:+.2f}",
                     f"{v['share_cases_better_than_model_only']:.0%}"] for k, v in ro["policies"].items()]
            nm = ro["no_motion_reference"]
            rows.append(["no-motion guess (reference)", nm["median_miss_km_24h"], 0.0, "", "", "", ""])
            L += ["", "## 5. Each decision's forecast vs the real drifter (baseline vs agent vs reality)", "",
                  f"Every policy's decision on every faulty case, forecast {ro['windows']} times against the real "
                  f"drifter (diffusivity {ro['k']:g} m²/s). *clean_radar* = a perfect repair. *no-motion* = guess the "
                  "object stays where it was reported.", "",
                  table(["policy", "mean 24 h miss (km)", "skill", "inside 90% area", "vs perfect repair (km)",
                         "worst case vs perfect repair (km)", "cases better than model only"], rows)]
            L += fig("decision_summary_oct.png", "no QC vs baseline vs agent summary")
            L += fig("decisions_vs_reality_oct.png", "decisions vs reality")
            for c in FIG_CASES:
                L += fig(f"decision_oct_{c}.gif", f"decisions {c}")
        b = jload(f"eval/batches/{oct_runs[0]}.json") if oct_runs else None
        if b:
            cs = b["cases"].values()
            L += ["", "## 6. Cost of the agent", "",
                  f"Agent run `{oct_runs[0]}`: {len(b['cases'])} cases, ${sum(c['cost_usd'] or 0 for c in cs):.2f}, "
                  f"{sum(c['turns'] or 0 for c in cs) / len(b['cases']):.1f} turns and "
                  f"{sum(c['seconds'] for c in cs) / len(b['cases']):.0f} s per case, "
                  f"{sum(len(c['denials']) for c in cs)} permission denials. The scripted baseline costs $0 and about "
                  "3 s per case. Agent runs replayed in this demo: " + ", ".join(f"`{r}`" for r in oct_runs) + "."]
        if oct_runs:
            L += decision_traces(oct_runs[0], base_dir or REPO / "runs/results/baseline_oct")
        L += compound_section(list(cmp_runs), cmp_base or REPO / "runs/results/baseline_cmp")
    if aug:
        s, d = jload("runs/results/scores/summary.json"), jload("runs/results/drift/summary.json")
        L += ["", "## Appendix: August 2021 benchmark (Southern California fixtures, no drifter)"]
        if s:
            L += ["", score_table(s)]
        if d:
            L += ["", drift_table(d)]
        L += fig("drift_case10.png", "August drift example")
    L += ["", "## Limitations", "",
          "- One region and one drifter (overlapping windows, not independent); planted faults are single and clear-cut.",
          "- No wind in the drift model, and the drifter had lost its drogue, so wind matters; forecasts do not beat a "
          "no-motion guess on 24 h end position.",
          "- POC areas come from lightly diffused particle clouds (no forecast ensemble), so they are overconfident.",
          "- Agent decisions are replayed from saved runs; new Claude calls need `scripts/run_agent_batch.py`."]
    MD.write_text("\n".join(L) + "\n", encoding="utf-8")


# ---- main ------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", action="store_true", required=True, help="replay saved agent decisions (required)")
    ap.add_argument("--august", action="store_true", help="also run and report the August 2021 benchmark")
    ap.add_argument("--labels-oct", default=str(HELDOUT / "labels_oct.json"))
    ap.add_argument("--labels-cmp", default=str(HELDOUT / "labels_compound.json"), help="key for the harder cases")
    ap.add_argument("--labels", default=str(HELDOUT / "labels.json"), help="August key (with --august)")
    a = ap.parse_args(argv)

    if (REPO / "heldout").is_dir() and any((REPO / "heldout").iterdir()):
        sys.exit("heldout/ inside the repository is not empty; keys belong outside it (default ~/sar_heldout/).")
    shutil.rmtree(FIG_DIR, ignore_errors=True)
    demo = Demo(LOG)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "scripts", ".claude", "configs", "fixtures"],
                           cwd=REPO, capture_output=True, text=True).stdout.strip()
    demo.say(f"run_demo --replay at commit {commit}{' (uncommitted code changes present)' if dirty else ''}")

    radar, model, drifters = (first_existing(OCT[k]) for k in ("radar", "model", "drifters"))
    oct_ok = all((radar, model, drifters))
    oct_runs = sorted(p.name for p in (REPO / "runs/results/agent").glob("oct_rep*") if p.is_dir())
    cmp_runs = sorted(p.name for p in (REPO / "runs/results/agent").glob("cmp_rep*") if p.is_dir())
    if not oct_ok:
        demo.say("\n=== October data not found in fixtures/ or data/; see docs/pipeline.md stage 8 ===")
    else:
        demo.say(f"\n=== October 2021: radar {radar.relative_to(REPO)}, model {model.relative_to(REPO)}, "
                 f"drifters {drifters.relative_to(REPO)}; agent runs {oct_runs} ===")
        cases = REPO / "runs/cases_oct"
        cases_stage(demo, "October", radar, model, cases, Path(a.labels_oct).expanduser(), OCT_SEED)
        o_res, o_work = baseline_stage(demo, "October", cases, REPO / "runs/results/baseline_oct")
        for r in oct_runs:
            replay_agent(demo, "October", r, cases)
        demo.run("src/score.py", "--labels", Path(a.labels_oct).expanduser(), "--agent-runs", *oct_runs,
                 "--baseline", o_res, "--baseline-work", o_work, "--cases-dir", cases, "--clean", radar,
                 "--out", REPO / "runs/results/scores_oct", label="score vs key")
        demo.run("src/drift.py", "evaluate", "--cases-dir", cases, "--clean", radar, "--model", model,
                 "--agent-runs", *oct_runs, "--baseline", o_res, "--baseline-work", o_work,
                 "--out", REPO / "runs/results/drift_oct", "--figure", *FIG_CASES, "--fig-prefix", "drift_oct_",
                 label="drift vs clean")
        demo.run("src/drifter_eval.py", "--radar", radar, "--model", model, "--drifters", drifters, label="drifter eval")
        demo.run("src/drifter_viz.py", "--radar", radar, "--model", model, "--drifters", drifters, label="drifter viz")
        demo.run("src/decision_reality.py", "--agent-runs", *oct_runs, "--baseline", o_res.relative_to(REPO),
                 "--baseline-work", o_work.relative_to(REPO), "--clean", radar, "--model", model, "--drifters", drifters,
                 label="decisions vs reality")
        if oct_runs:
            demo.run("src/decision_viz.py", "--agent-run", oct_runs[0], "--cases", *FIG_CASES,
                     "--baseline", o_res.relative_to(REPO), "--baseline-work", o_work.relative_to(REPO),
                     "--clean", radar, "--model", model, "--drifters", drifters, label="baseline vs agent pictures")

        demo.say("\n=== Harder (compound) October cases ===")
        ccases = REPO / "runs/cases_compound"
        cases_stage(demo, "Compound", radar, model, ccases, Path(a.labels_cmp).expanduser(), CMP_SEED, plan="compound")
        c_res, c_work = baseline_stage(demo, "Compound", ccases, REPO / "runs/results/baseline_cmp")
        for r in cmp_runs:
            replay_agent(demo, "Compound", r, ccases)
        demo.run("src/score.py", "--labels", Path(a.labels_cmp).expanduser(), "--agent-runs", *cmp_runs,
                 "--baseline", c_res, "--baseline-work", c_work, "--cases-dir", ccases, "--clean", radar,
                 "--out", REPO / "runs/results/scores_cmp", label="compound score vs key")
        demo.run("src/decision_reality.py", "--cases-dir", ccases.relative_to(REPO), "--agent-runs", *cmp_runs,
                 "--baseline", c_res.relative_to(REPO), "--baseline-work", c_work.relative_to(REPO), "--clean", radar,
                 "--model", model, "--drifters", drifters, "--out", "runs/results/reality_cmp",
                 "--fig-name", "decisions_vs_reality_cmp.png", label="compound decisions vs reality")

    if a.august:
        demo.say("\n=== Appendix: August 2021 benchmark ===")
        cases_stage(demo, "August", REPO / "fixtures/hfr_2km_slice.nc", REPO / "fixtures/hycom_uv_slice.nc",
                    REPO / "runs/cases", Path(a.labels).expanduser(), AUG_SEED)
        b_res, b_work = baseline_stage(demo, "August", REPO / "runs/cases", REPO / "runs/results/baseline")
        aug_runs = [r for r in ("rep1", "rep2") if (REPO / "runs/results/agent" / r).is_dir()]
        for r in aug_runs:
            replay_agent(demo, "August", r, REPO / "runs/cases")
        demo.run("src/score.py", "--labels", Path(a.labels).expanduser(), "--agent-runs", *aug_runs, "--baseline", b_res,
                 "--baseline-work", b_work, "--cases-dir", REPO / "runs/cases", "--clean",
                 REPO / "fixtures/hfr_2km_slice.nc", "--out", REPO / "runs/results/scores", label="August score")
        demo.run("src/drift.py", "evaluate", "--agent-runs", *aug_runs, "--baseline", b_res, "--baseline-work", b_work,
                 "--figure", "case10", label="August drift")

    write_report(demo, commit, oct_ok, a.august, oct_runs,
                 REPO / "runs/results/_replay_baseline_October" if oct_ok else None,
                 cmp_runs, REPO / "runs/results/_replay_baseline_Compound" if oct_ok else None)
    n_fail = sum(not ok for _, ok, _ in demo.checks)
    demo.say(f"\n=== done in {(time.time() - demo.t0) / 60:.1f} min; {len(demo.checks) - n_fail}/{len(demo.checks)} "
             f"reproducibility checks passed; report: {MD.relative_to(REPO)} ===")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
