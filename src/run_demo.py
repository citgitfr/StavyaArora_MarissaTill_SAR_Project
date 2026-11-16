"""One command for the Lab 3 test: 22 faulty October 2021 cases, scored against answer keys and a real drifter.
messy data -> decision (scripted baseline vs Claude agent) -> forcing -> OpenDrift -> map -> score (key and reality).

  python src/run_demo.py --replay              # all 22 October cases as one test; about 20 minutes
  python src/run_demo.py --replay --august     # plus an appendix with the August 2021 benchmark

--replay reuses the agent decisions saved in the repository instead of calling Claude (no cost, no network, no API
key). Everything else is recomputed from committed code and data and checked against what is committed.

Steps
  A. Each set separately (each has its own seed, key, baseline and agent runs):
     single-fault set (14 cases, seed 1018) and harder set (8 cases, seed 2026, --plan compound):
       regenerate the cases and check them against the repo and the key; rerun the scripted baseline and check it
       against its committed decisions; replay every saved agent run, rebuilding missing repaired radar files with the
       frozen repair tool and checking rebuilt files against existing ones.
  B. Merge into ONE 22-case set: runs/cases_all/ (case01-14 single-fault, case15-22 harder; mapping.json), one key
     (outside the repo), and one decision folder per policy: baseline, and for the agent all_rep1 = oct_rep1 + cmp_rep1,
     all_rep2 = oct_rep1 + cmp_rep2.
  C. On the 22 cases, once: score vs the key (score.py), forecast impact vs a clean-radar run (drift.py), every
     decision vs the real drifter (decision_reality.py), baseline vs agent figures and animations (decision_viz.py).
     Plus the clean-data reality check, which does not depend on the cases (drifter_eval.py, drifter_viz.py).
Output: demo_results/lab3_demo.md (all tables and figures), demo_results/lab3_figures/, demo_results/lab3_run_log.txt

Data comes from fixtures/ (committed): October radar, HYCOM and drifter files (data/ is used as a fallback).
Nothing is downloaded. Answer keys are read from outside the repository (default ~/sar_heldout/); a missing key is
regenerated there from the seed (the injector is deterministic).
"""
import argparse
import csv
import importlib.util
import json
import os
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
ALL_CASES = REPO / "runs" / "cases_all"
ALL_BASE_RES, ALL_BASE_WORK = REPO / "runs/results/_merged_baseline", REPO / "runs/work/_merged_baseline"
# merged case ids drawn in the per-case figures and animations: a recovered rotation and the faulty model (harder set),
# and a rescaling fault (single-fault set). Seeds are fixed, so these ids are stable.
FIG_CASES = ["case22", "case21", "case14"]
TRACE_CASES = ["case14", "case06", "case04"]      # agreeing examples shown as decision traces, besides every difference


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


def _steps(d):
    r = d.get("repair")
    return r if isinstance(r, list) else ([] if r is None else [r])


def same_decisions(a, b):
    def norm(x):
        return x.get("choice"), tuple((s.get("action"), str(float(s["param"])) if isinstance(s.get("param"), (int, float))
                                       else str(s.get("param"))) for s in _steps(x))
    keys = sorted(set(a) | set(b))
    diff = [k for k in keys if k not in a or k not in b or norm(a[k]) != norm(b[k])]
    return len(keys) - len(diff), len(keys), diff


def link(src, dst):
    """Symlink a file (copy where symlinks are not allowed, e.g. Windows without developer mode)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(Path(src).resolve(), dst)
    except OSError:
        shutil.copyfile(src, dst)


# ---- A. each set separately ------------------------------------------------------------------
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


# ---- B. merge into one 22-case set -----------------------------------------------------------
def merge_stage(demo, sets, key_out, agent_pairs):
    """sets: [(subset name, cases dir, key path, baseline results, baseline work, {agent run: source run})].
    agent_pairs: {merged agent run: {subset name: source run}}. Writes runs/cases_all/ (links), mapping.json,
    subsets.json, the merged key (outside the repo), merged baseline and agent folders, merged agent batch logs."""
    for d in [ALL_CASES, ALL_BASE_RES, ALL_BASE_WORK,
              *[REPO / "runs/results/agent" / r for r in agent_pairs], *[REPO / "runs/work/agent" / r for r in agent_pairs]]:
        shutil.rmtree(d, ignore_errors=True)
    mapping, key, n = {}, {"seed": "merged", "plan": "merged", "cases": {}}, 0
    batches = {r: {"run_id": r, "merged_from": src, "cases": {}} for r, src in agent_pairs.items()}
    for subset, cdir, kpath, b_res, b_work in sets:
        sub_key = json.loads(kpath.read_text(encoding="utf-8"))["cases"]
        for old in sorted(p.name for p in cdir.glob("case*") if p.is_dir()):
            n += 1
            new = f"case{n:02d}"
            mapping[new] = {"subset": subset, "source": f"{cdir.relative_to(REPO).as_posix()}/{old}"}
            for f in ("radar.nc", "model.nc"):
                link(cdir / old / f, ALL_CASES / new / f)
            key["cases"][new] = {**sub_key[old], "subset": subset, "source_case": old}
            # baseline
            dec = json.loads((b_res / f"{old}.json").read_text(encoding="utf-8"))
            ALL_BASE_RES.mkdir(parents=True, exist_ok=True)
            (ALL_BASE_RES / f"{new}.json").write_text(json.dumps({**dec, "case_id": new}, indent=2), encoding="utf-8")
            if (b_work / old).is_dir():
                for f in (b_work / old).iterdir():
                    link(f, ALL_BASE_WORK / new / f.name)
            # agent runs
            for run, src in agent_pairs.items():
                s_run = src[subset]
                s_dec = REPO / "runs/results/agent" / s_run / f"{old}.json"
                if not s_dec.exists():
                    continue
                d = json.loads(s_dec.read_text(encoding="utf-8"))
                out = REPO / "runs/results/agent" / run / f"{new}.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps({**d, "case_id": new}, indent=2), encoding="utf-8")
                s_work = REPO / "runs/work/agent" / s_run / old
                if s_work.is_dir():
                    for f in s_work.iterdir():
                        link(f, REPO / "runs/work/agent" / run / new / f.name)
                blog = REPO / "eval/batches" / f"{s_run}.json"
                if blog.exists():
                    c = json.loads(blog.read_text(encoding="utf-8"))["cases"].get(old)
                    if c:
                        batches[run]["cases"][new] = c
    ALL_CASES.mkdir(parents=True, exist_ok=True)
    (ALL_CASES / "mapping.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    (ALL_CASES / "subsets.json").write_text(json.dumps({k: v["subset"] for k, v in mapping.items()}, indent=2),
                                            encoding="utf-8")
    key_out.parent.mkdir(parents=True, exist_ok=True)
    key_out.write_text(json.dumps(key, indent=2), encoding="utf-8")
    for run, b in batches.items():
        (REPO / "eval/batches" / f"{run}.json").write_text(json.dumps(b, indent=2), encoding="utf-8")
    counts = {s: sum(v["subset"] == s for v in mapping.values()) for s in dict.fromkeys(v["subset"] for v in mapping.values())}
    demo.say(f"  merged {n} cases into runs/cases_all ({', '.join(f'{k}: {v}' for k, v in counts.items())}); agent runs "
             + ", ".join(f"{r} = {' + '.join(s.values())}" for r, s in agent_pairs.items()))
    demo.check("merged set: every case has a key entry, a baseline decision and a decision in each merged agent run",
               len(key["cases"]) == n and len(decisions(ALL_BASE_RES)) == n and all(
                   len(decisions(REPO / "runs/results/agent" / r)) == n for r in agent_pairs), f"{n} cases")
    return mapping


# ---- report helpers --------------------------------------------------------------------------
def jload(rel):
    p = REPO / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _csv(rel):
    p = REPO / rel
    return list(csv.DictReader(open(p, encoding="utf-8"))) if p.exists() else []


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return "\n".join(out + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows])


def label_of(policy, agent_pairs):
    if policy.startswith("agent_"):
        run = policy[len("agent_"):]
        return f"Claude agent ({' + '.join(agent_pairs.get(run, {}).values()) or run})"
    return {"always_model_only": "Model only (radar ignored)", "always_fused": "No QC (radar as delivered)",
            "baseline": "Scripted baseline", "oracle": "Oracle (best possible)",
            "clean_radar": "Perfect repair (clean radar)"}.get(policy, policy)


def score_table(s, agent_pairs):
    rows = []
    for name, p in s["policies"].items():
        b = p.get("batch") or {}
        rows.append([label_of(name, agent_pairs), f"{p['acceptable']}/{p['cases']}", p["silent_errors"],
                     f"{p['radar_recovered']}/{p['radar_recoverable']}",
                     "n/a" if p["faithful_radar"] is None else f"{p['faithful_radar']}/{p['fidelity_checked']}",
                     p["escalations"], "" if b.get("cost_usd") is None else f"${b['cost_usd']:.2f}"])
    t = table(["policy", "acceptable", "silent errors", "radar recovered", "faithful radar", "escalations", "agent cost"],
              rows)
    agr = "; ".join(f"{k}: {v['same_choice_and_action']}/{v['cases']}" for k, v in s.get("agreement", {}).items())
    return t + (f"\n\nAgreement on choice and repair action: {agr}." if agr else "")


def subset_table(mapping, agent_pairs):
    """Per policy and subset: acceptable, silent errors, radar recovered (key) and mean 24 h miss (drifter)."""
    keyrows, realrows = _csv("runs/results/scores_all/cases.csv"), _csv("runs/results/reality_all/cases.csv")
    if not keyrows:
        return []
    subsets = list(dict.fromkeys(v["subset"] for v in mapping.values()))
    policies = list(dict.fromkeys(r["policy"] for r in keyrows if r["policy"] != "oracle"))
    rows = []
    for p in policies:
        for s in subsets:
            k = [r for r in keyrows if r["policy"] == p and mapping[r["case"]]["subset"] == s]
            m = [float(r["median_sep_km_24h"]) for r in realrows if r["policy"] == p and r.get("median_sep_km_24h")
                 and mapping[r["case"]]["subset"] == s]
            esc = sum(1 for r in realrows if r["policy"] == p and mapping[r["case"]]["subset"] == s
                      and (r.get("note") or "").startswith("escalated"))
            rec = [r for r in k if r["radar_recoverable"] == "True"]
            rows.append([label_of(p, agent_pairs), s, f"{sum(r['acceptable'] == 'True' for r in k)}/{len(k)}",
                         sum(r["silent_error"] == "True" for r in k),
                         f"{sum(r['radar_recovered'] == 'True' for r in rec)}/{len(rec)}",
                         f"{sum(m) / len(m):.1f}" if m else "n/a", esc])
    return ["", "### The same results by subset", "",
            table(["policy", "subset", "acceptable", "silent errors", "radar recovered", "mean 24 h miss (km)",
                   "escalated"], rows)]


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


def decision_traces(agent_run, base_dir, mapping):
    """Every case where the agent and the baseline chose differently, plus a few agreeing examples."""
    ps = _load("plot_style", "src/plot_style.py")
    agent, base = decisions(REPO / "runs/results/agent" / agent_run), decisions(base_dir)
    key_rows = {(r["policy"], r["case"]): r for r in _csv("runs/results/scores_all/cases.csv")}
    real_rows = {(r["policy"], r["case"]): r for r in _csv("runs/results/reality_all/cases.csv")}
    differ = [c for c in sorted(agent) if c in base and
              (agent[c].get("choice"), sorted(x.get("action") for x in _steps(agent[c])))
              != (base[c].get("choice"), sorted(x.get("action") for x in _steps(base[c])))]
    cases = sorted(set(differ) | set(TRACE_CASES))
    L = ["", "## 6. Decision traces: what the agent saw, concluded and did", "",
         f"Every case where the agent and the scripted baseline chose differently ({', '.join(differ) or 'none'}), plus "
         "three agreeing examples. *Checks that failed* come from the baseline's first `check`; the agent's evidence "
         "lines and reason are copied from its decision file (the numbers come from the tools)."]
    for c in cases:
        a, b = agent.get(c), base.get(c)
        if not a or not b:
            continue
        failed = sorted({e.split(".")[2] for e in b.get("evidence", []) if e.startswith("check.tests.")})
        ka, kb = key_rows.get((f"agent_{agent_run}", c), {}), key_rows.get(("baseline", c), {})
        pols = (f"agent_{agent_run}", "baseline", "always_fused", "clean_radar")
        miss = {p: real_rows.get((p, c), {}) for p in pols}
        fmt = lambda r: ("escalated, no forecast" if (r.get("note") or "").startswith("escalated")
                         else "n/a" if not r.get("median_sep_km_24h") else f"{float(r['median_sep_km_24h']):.1f} km")
        L += ["", f"### {ps.case_label(c)} ({mapping[c]['subset']}{', agent and baseline differ' if c in differ else ''})",
              "",
              f"- **Checks that failed:** {', '.join(failed) if failed else 'none (all 8 passed)'}",
              "- **Agent's evidence:** " + "; ".join(f"`{e}`" for e in a.get("evidence", [])[:6])
              + (" …" if len(a.get("evidence", [])) > 6 else ""),
              f"- **Agent's reason:** {a.get('reason', '')}",
              f"- **Agent chose:** {ps.decision_text(a)}  ·  **Baseline chose:** {ps.decision_text(b)}"]
        if ka:
            L.append(f"- **Answer key:** planted fault `{ka.get('fault_type')}`; agent acceptable: "
                     f"{ka.get('acceptable')}, baseline acceptable: {kb.get('acceptable')}")
        L.append(f"- **24 h miss vs real drifter (median of 20 forecasts):** agent {fmt(miss[pols[0]])}, baseline "
                 f"{fmt(miss['baseline'])}, No QC {fmt(miss['always_fused'])}, perfect repair {fmt(miss['clean_radar'])}")
    return L


def write_report(demo, commit, ok, aug, mapping, agent_pairs, base_dir):
    n = len(mapping)
    counts = {s: sum(v["subset"] == s for v in mapping.values()) for s in dict.fromkeys(v["subset"] for v in mapping.values())}
    L = [f"# Lab 3 demo: SAR forcing agent on {n} faulty October 2021 cases", "",
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
          table(["check", "result", "detail"], [[c, "PASS" if o else "**FAIL**", d] for c, o, d in demo.checks])]
    if not ok:
        L += ["", "## October sections skipped", "", "The October radar, HYCOM and drifter files were not found in "
              "fixtures/ or data/ (see the run log)."]
    else:
        L += ["", f"## 1. The test: {n} faulty October cases", "",
              f"All cases come from the same clean HF-radar file (2021-10-18..28, off Point Arguello), the same HYCOM file "
              f"and the same real drifter. **{counts.get('single-fault', 0)} single-fault cases** (seed {OCT_SEED}; "
              "case01-14): 4 clean and 10 with one planted fault (scaling, time shifts, sign flip, swapped components, "
              f"blanked hours, a dropout, fill values). **{counts.get('harder', 0)} harder cases** (seed {CMP_SEED}; "
              "case15-22): 2 clean, 3 with two radar faults at once, 2 rotated radar fields, and 1 with a faulty "
              "**model** file and clean radar (only escalating to a person is acceptable). Answer keys stay outside the "
              "repository. Case numbers map to their source in `runs/cases_all/mapping.json`."]
        s = jload("runs/results/scores_all/summary.json")
        if s:
            L += ["", "## 2. Headline: decisions vs the answer key", "",
                  "*Acceptable*: a choice the key allows. *Silent errors*: radar trusted when it should not be. *Radar "
                  "recovered*: usable radar used correctly. *Faithful*: the radar handed on equals the clean original. "
                  "*Escalations*: cases handed to a person.", "", score_table(s, agent_pairs)]
        L += subset_table(mapping, agent_pairs)
        L += fig("baseline_vs_agent_all.png", "baseline vs agent")
        ro = jload("runs/results/reality_all/summary.json")
        if ro:
            rows = [[label_of(k, agent_pairs), v["cases"], v.get("escalated", 0), v["mean_miss_km_24h"], v["mean_skill"],
                     f"{v['mean_excess_over_clean_km']:+.2f}", f"{v['worst_excess_over_clean_km']:+.2f}",
                     f"{v['share_cases_better_than_model_only']:.0%}"] for k, v in ro["policies"].items()]
            nm = ro["no_motion_reference"]
            rows.append(["No-motion guess (reference)", "", "", nm["median_miss_km_24h"], 0.0, "", "", ""])
            L += ["", "## 3. Each decision's forecast vs the real drifter", "",
                  f"Every policy's decision on every case, forecast {ro['windows']} times (24 h, released at the real "
                  f"drifter, spread {ro['k']:g} m²/s). The miss is averaged over the cases a policy actually forecast; "
                  "an escalated case goes to a person and has no automatic forecast. *Perfect repair* uses the clean "
                  "radar. *No-motion* guesses the object stays where it was reported.", "",
                  table(["policy", "cases forecast", "escalated", "mean 24 h miss (km)", "skill", "vs perfect repair (km)",
                         "worst case vs perfect repair (km)", "cases better than model only"], rows)]
            L += fig("decision_summary_all.png", "No QC vs baseline vs agent summary")
            L += fig("decisions_vs_reality_all.png", "decisions vs reality")
            for c in FIG_CASES:
                L += fig(f"decision_all_{c}.gif", f"decisions {c}")
        do = jload("runs/results/drift_all/summary.json")
        if do:
            rows = [[label_of(k, agent_pairs), v["median_sep_km_24h"], v["mean_of_mean_sep_km_24h"], v["max_sep_km_24h"],
                     v["mean_poc_overlap"]] for k, v in do["policies"].items()]
            L += ["", "## 4. What each decision does to the forecast (vs a run on the clean radar)", "",
                  f"OceanDrift, {do['particles']} particles from 5 points, {do['hours']} h from {do['start']}. Separation "
                  "is from the same particles in the clean-radar run (decision impact, not reality); escalated cases "
                  "are left out.", "",
                  table(["policy", "median of case medians 24 h (km)", "mean 24 h (km)", "worst case 24 h (km)",
                         "mean POC-map overlap"], rows)]
            for c in FIG_CASES:
                L += fig(f"drift_all_{c}.png", f"drift {c}")
        r = jload(f"runs/results/drifter/{DRIFTER_ID}/summary.json")
        if r:
            rows = [[k.split("|")[0], k.split("|")[1], v["median_sep_km_6h"], v["median_sep_km_12h"],
                     v["median_sep_km_24h"], v["mean_skill"], f"{v['in50_24h']:.0%}", f"{v['in90_24h']:.0%}",
                     v["median_area90_km2_24h"]] for k, v in r["results"].items()]
            chk = r["frozen_check_on_real_pair"]
            L += ["", f"## 5. Reality check on clean data: forecasts vs real drifter {DRIFTER_ID}", "",
                  "No faults here: how good are the forecasts themselves? Undrogued Global Drifter Program drifter (it "
                  f"follows the top meter, like HF radar). {r['windows']} forecasts of {r['window_hours']} h, a new one "
                  f"every {r['every_hours']} h, each releasing {r['particles']} particles at the drifter's real position. "
                  f"Frozen `check` on the clean pair: **{chk.get('result')}**.", "",
                  table(["forcing", "spread (m²/s)", "miss 6 h (km)", "miss 12 h (km)", "miss 24 h (km)", "skill",
                         "inside 50% area", "inside 90% area", "90% area (km²)"], rows), "",
                  f"Radar+model closer than model only at 24 h in {r['radar_better_share_24h']:.0%} of window/spread "
                  "pairs. Skill: Liu & Weisberg (2011), 1 = perfect, 0 = no better than not moving."]
            L += fig(f"drifter_{DRIFTER_ID}_summary.png", "drifter summary")
            for g in sorted((REPO / "docs" / "figures").glob(f"drifter_{DRIFTER_ID}_window_*.gif")):
                L += fig(g.name, "one forecast")
        first_run = next(iter(agent_pairs), None)
        if first_run:
            L += decision_traces(first_run, base_dir, mapping)
        costs = []
        for run, src in agent_pairs.items():
            b = jload(f"eval/batches/{run}.json")
            if b and b["cases"]:
                cs = b["cases"].values()
                costs.append([label_of(f"agent_{run}", agent_pairs), len(b["cases"]),
                              f"${sum(c['cost_usd'] or 0 for c in cs):.2f}",
                              f"{sum(c['turns'] or 0 for c in cs) / len(b['cases']):.1f}",
                              f"{sum(c['seconds'] for c in cs) / len(b['cases']):.0f}",
                              sum(len(c['denials']) for c in cs)])
        if costs:
            L += ["", "## 7. Cost of the agent", "",
                  table(["agent", "cases", "cost", "turns per case", "seconds per case", "permission denials"], costs), "",
                  "The scripted baseline costs $0 and about 3 s per case."]
    if aug:
        s, d = jload("runs/results/scores/summary.json"), jload("runs/results/drift/summary.json")
        L += ["", "## Appendix: August 2021 benchmark (Southern California fixtures, no drifter)"]
        if s:
            L += ["", score_table(s, {})]
        L += fig("drift_case10.png", "August drift example")
    L += ["", "## Limitations", "",
          "- One region and one drifter (overlapping windows, not independent); 22 cases.",
          "- The harder set has one faulty-model case and two rotations; more varied hard cases are needed.",
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
    ok = all((radar, model, drifters))
    oct_runs = sorted(p.name for p in (REPO / "runs/results/agent").glob("oct_rep*") if p.is_dir())
    cmp_runs = sorted(p.name for p in (REPO / "runs/results/agent").glob("cmp_rep*") if p.is_dir())
    # one merged agent run per harder-set run, each paired with an October single-fault run
    agent_pairs = {f"all_rep{i + 1}": {"single-fault": oct_runs[min(i, len(oct_runs) - 1)], "harder": r}
                   for i, r in enumerate(cmp_runs)} if oct_runs and cmp_runs else {}
    mapping = {}
    if not ok:
        demo.say("\n=== October data not found in fixtures/ or data/; see docs/pipeline.md ===")
    else:
        demo.say(f"\n=== A. Each set: radar {radar.relative_to(REPO)}, model {model.relative_to(REPO)}, drifters "
                 f"{drifters.relative_to(REPO)}; agent runs {oct_runs} (single-fault), {cmp_runs} (harder) ===")
        o_cases, c_cases = REPO / "runs/cases_oct", REPO / "runs/cases_compound"
        k_oct, k_cmp = Path(a.labels_oct).expanduser(), Path(a.labels_cmp).expanduser()
        cases_stage(demo, "Single-fault", radar, model, o_cases, k_oct, OCT_SEED)
        o_res, o_work = baseline_stage(demo, "Single-fault", o_cases, REPO / "runs/results/baseline_oct")
        for r in oct_runs:
            replay_agent(demo, "Single-fault", r, o_cases)
        cases_stage(demo, "Harder", radar, model, c_cases, k_cmp, CMP_SEED, plan="compound")
        c_res, c_work = baseline_stage(demo, "Harder", c_cases, REPO / "runs/results/baseline_cmp")
        for r in cmp_runs:
            replay_agent(demo, "Harder", r, c_cases)

        demo.say("\n=== B. Merge into one 22-case set ===")
        k_all = HELDOUT / "labels_all.json"
        mapping = merge_stage(demo, [("single-fault", o_cases, k_oct, o_res, o_work),
                                     ("harder", c_cases, k_cmp, c_res, c_work)], k_all, agent_pairs)

        demo.say("\n=== C. Score and draw all 22 cases at once ===")
        runs = list(agent_pairs)
        cases = ALL_CASES.relative_to(REPO)
        base = [ALL_BASE_RES.relative_to(REPO), ALL_BASE_WORK.relative_to(REPO)]
        demo.run("src/score.py", "--labels", k_all, "--agent-runs", *runs, "--baseline", base[0], "--baseline-work",
                 base[1], "--cases-dir", cases, "--clean", radar, "--out", "runs/results/scores_all", label="score vs key")
        demo.run("src/decision_reality.py", "--cases-dir", cases, "--agent-runs", *runs, "--baseline", base[0],
                 "--baseline-work", base[1], "--clean", radar, "--model", model, "--drifters", drifters,
                 "--out", "runs/results/reality_all", "--fig-name", "decisions_vs_reality_all.png",
                 label="decisions vs reality")
        demo.run("src/drift.py", "evaluate", "--cases-dir", cases, "--clean", radar, "--model", model,
                 "--agent-runs", *runs, "--baseline", base[0], "--baseline-work", base[1], "--out", "runs/results/drift_all",
                 "--figure", *FIG_CASES, "--fig-prefix", "drift_all_", label="drift vs clean")
        if runs:
            demo.run("src/decision_viz.py", "--agent-run", runs[0], "--cases", *FIG_CASES, "--cases-dir", cases,
                     "--baseline", base[0], "--baseline-work", base[1], "--clean", radar, "--model", model,
                     "--drifters", drifters, "--reality", "runs/results/reality_all/cases.csv",
                     "--scores", "runs/results/scores_all/summary.json", "--tag", "all",
                     "--subset-of", (ALL_CASES / "subsets.json").relative_to(REPO), label="baseline vs agent pictures")
        demo.run("src/drifter_eval.py", "--radar", radar, "--model", model, "--drifters", drifters, label="drifter eval")
        demo.run("src/drifter_viz.py", "--radar", radar, "--model", model, "--drifters", drifters, label="drifter viz")

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

    write_report(demo, commit, ok, a.august, mapping, agent_pairs, ALL_BASE_RES)
    n_fail = sum(not o for _, o, _ in demo.checks)
    demo.say(f"\n=== done in {(time.time() - demo.t0) / 60:.1f} min; {len(demo.checks) - n_fail}/{len(demo.checks)} "
             f"reproducibility checks passed; report: {MD.relative_to(REPO)} ===")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
