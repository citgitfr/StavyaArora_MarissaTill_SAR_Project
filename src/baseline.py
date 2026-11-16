"""Scripted baseline for the SAR forcing benchmark: the same tools as the agent, a fixed rule table, no LLM.

For each case folder runs/cases/caseNN/ it runs forcing_tools.py check (through the command line, so it sees
exactly the JSON the agent sees). If check passes, the choice is "fused". If it fails, the FIRST failing test in
check's own order picks one repair from the SKILL.md menu; the repaired radar is checked again, up to two repairs.
Still failing, or no rule fits -> "model_only". A tool error -> "escalate". Decisions use the agent's JSON schema.

The rules are written from SKILL.md's repair menu and the frozen thresholds only, and are frozen before any agent
result is scored. Never reads heldout/.

Run from the repository root:
  python src/baseline.py                         # all cases -> runs/results/baseline/, runs/work/baseline/
  python src/baseline.py --cases case01 case03   # a subset
  python src/baseline.py --results DIR --work DIR
"""
import argparse
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / ".claude" / "skills" / "forcing-tools" / "forcing_tools.py"
CASES = REPO / "runs" / "cases"
RESULTS = REPO / "runs" / "results" / "baseline"
WORK = REPO / "runs" / "work" / "baseline"
MAX_REPAIRS = 2
# check's own test order; the first failing test decides the repair.
ORDER = ("units", "speed_bound", "time_axis", "fill_values", "coverage", "speed_ratio", "correlation_sign", "lag")

_spec = importlib.util.spec_from_file_location("forcing_tools", TOOL)
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)            # only load_thresholds is used; the tools themselves run as subprocesses


class BaselineError(Exception):
    pass


def tool(*args):
    """Run forcing_tools.py exactly as the agent does and return its JSON. A tool "error" raises."""
    p = subprocess.run([sys.executable, str(TOOL), *map(str, args)], cwd=REPO, capture_output=True, text=True)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise BaselineError(f"{args[0]}: no JSON output (exit {p.returncode}): {p.stderr.strip()[-300:]}") from None
    if "error" in out:
        raise BaselineError(f"{args[0]}: {out['error']}")
    return out


def _fmt(x):
    return repr(x) if isinstance(x, float) else str(x)


def rule(chk, thr):
    """(failed_test, action, param) for the first failing test, or (failed_test, None, None) if no rule fits."""
    tests = chk["tests"]
    failed = [n for n in ORDER if not tests[n]["pass"]]
    first = failed[0]
    mask_x = 2 * thr["speed.max_m_s"]       # above any real current (SKILL.md: above the speed bound)

    if first == "speed_bound" or first == "fill_values":
        r = tests["speed_ratio"]["speed_ratio_median"]
        if first == "speed_bound" and not tests["speed_ratio"]["pass"] and r:
            k = 10.0 ** round(-math.log10(r))
            if k != 1.0:
                return first, "rescale", k
        offending = tests["speed_bound"]["speed_max"]
        if first == "fill_values" or (offending is not None and offending >= mask_x):
            return first, "mask_fill", mask_x
        return first, None, None

    if first == "speed_ratio":
        r = tests["speed_ratio"]["speed_ratio_median"]
        k = 10.0 ** round(-math.log10(r)) if r else 1.0
        return (first, "rescale", k) if k != 1.0 else (first, None, None)

    if first in ("time_axis", "lag"):
        lag = tests["lag"]["best_lag_hours"]
        if not tests["lag"]["pass"] and lag:
            return first, "shift_time", float(-lag)
        return first, None, None

    if first == "correlation_sign":
        t = tests["correlation_sign"]
        lo_u, lo_v = t["corr_u"] < t["min"], t["corr_v"] < t["min"]
        if lo_u != lo_v:                    # exactly one component disagrees -> its sign is flipped
            return first, "flip_sign", "v" if lo_v else "u"
        direct = (t["corr_u"] + t["corr_v"]) / 2
        if t["corr_swapped"] is not None and t["corr_swapped"] > direct:
            return first, "swap_components", None
        return first, None, None

    return first, None, None                 # units, coverage: nothing on the menu fixes these


def _failed_evidence(prefix, chk):
    ev = [f"{prefix}.result = {chk['result']}"]
    for name in ORDER:
        t = chk["tests"][name]
        if not t["pass"]:
            ev += [f"{prefix}.tests.{name}.{k} = {v}" for k, v in t.items() if k != "pass" and not isinstance(v, list)]
    return ev


def decide(case_dir, work_dir, thr):
    case_id = case_dir.name
    radar, model = case_dir / "radar.nc", case_dir / "model.nc"
    repairs, evidence, reason = [], [], ""
    try:
        chk = tool("check", "--hfr", radar, "--model", model)
        evidence += _failed_evidence("check", chk)
        current = radar
        while chk["result"] == "fail":
            if len(repairs) == MAX_REPAIRS:
                reason = f"check still fails after {MAX_REPAIRS} repairs."
                break
            failed, action, param = rule(chk, thr)
            if action is None:
                reason = f"first failing test '{failed}' has no repair rule."
                break
            out = work_dir / f"radar_fixed_{len(repairs) + 1}.nc"
            args = ["repair", "--file", current, "--action", action, "--out", out]
            if param is not None:
                args[5:5] = ["--param", _fmt(param)]
            rep = tool(*args)
            repairs.append({"action": action, "param": param})
            evidence.append(f"repair{len(repairs)}.rule = {failed} -> {action} {'' if param is None else _fmt(param)}".rstrip())
            if "n_masked" in rep:
                evidence.append(f"repair{len(repairs)}.n_masked = {rep['n_masked']}")
            current = out
            chk = tool("check", "--hfr", current, "--model", model)
            evidence += _failed_evidence(f"check(repair{len(repairs)})", chk)
    except BaselineError as e:
        return {"case_id": case_id, "choice": "escalate", "repair": repairs or None,
                "evidence": evidence + [f"error = {e}"], "reason": "A tool returned an error."}

    if chk["result"] == "pass":
        choice = "repair" if repairs else "fused"
        reason = ("check passes on the original radar." if not repairs
                  else f"check passes after {len(repairs)} repair(s) chosen by the rule table.")
    else:
        choice = "model_only"
    rep = None if not repairs else (repairs[0] if len(repairs) == 1 else repairs)
    return {"case_id": case_id, "choice": choice, "repair": rep, "evidence": evidence, "reason": reason}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", nargs="*", help="case ids (default: every runs/cases/case*)")
    ap.add_argument("--cases-dir", default=str(CASES))
    ap.add_argument("--results", default=str(RESULTS))
    ap.add_argument("--work", default=str(WORK))
    a = ap.parse_args(argv)

    cases_dir, results, work = Path(a.cases_dir), Path(a.results), Path(a.work)
    ids = a.cases or sorted(p.name for p in cases_dir.glob("case*") if p.is_dir())
    if not ids:
        sys.exit(f"no cases in {cases_dir}; run python src/inject_faults.py first")
    for d in (results, work):
        if d.exists() and any(d.iterdir()):
            sys.exit(f"{d} is not empty; not overwriting. Remove it yourself or pass another folder.")
    thr = ft.load_thresholds()
    ft.require(thr)

    results.mkdir(parents=True, exist_ok=True)
    for cid in ids:
        dec = decide(cases_dir / cid, work / cid, thr)
        (results / f"{cid}.json").write_text(json.dumps(dec, indent=2) + "\n", encoding="utf-8")
        rep = dec["repair"]
        rep_s = "" if rep is None else " " + json.dumps(rep)
        print(f"{cid}: {dec['choice']}{rep_s}")


if __name__ == "__main__":
    main()
