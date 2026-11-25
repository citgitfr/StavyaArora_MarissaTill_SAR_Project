"""Run the blend investigator agent once on one blend case, in an isolated workspace, and bring its answer back.

One call = one repeat (one --run-id). What it does (same pattern as scripts/run_agent_batch.py):
  1. Builds ~/sar_eval/<run_id> with scripts/make_eval_workspace.py --blend-case (allowlisted files, the blend tool's
     code and the case folder, which holds only the TUNING part of the drifter track). Allowlisted files must be
     committed, so the run is tied to a commit.
  2. Pre-creates the run folder there, so the agent never needs mkdir.
  3. Runs `claude -p` INSIDE the workspace with the frozen settings passed explicitly, dontAsk mode, memory locked.
     The transcript goes to eval/transcripts/<run_id>/<case>.jsonl.
  4. Copies the decision and the run folder's JSON files (blend summaries, evaluations; not the .nc blends) back to
     runs/results/blend/agent/<run_id>/.
  5. Writes eval/batches/<run_id>.json: turns, cost, time, denials, whether a needed tool was denied.

Run from the repository root with the .venv active:
  python scripts/run_blend_agent.py --run-id blend_rep1
  python scripts/run_blend_agent.py --run-id dry --dry-run          # build the workspace, print the command, no cost
Then score: python src/blend_score.py --decision agent=runs/results/blend/agent/<run_id>/decision.json ...
"""
import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DEST = Path.home() / "sar_eval"
DEFAULT_CASE = "runs/blend_cases/oct21"
TIMEOUT_S = 2700
RUN_DIR = "runs/results/blend/agent"
PROMPT = ("Evaluation run. Use the blend-tools skill to choose the forcing setup for {case}. Use the run folder "
          f"{RUN_DIR} and write your decision to {RUN_DIR}/decision.json. The run folder already exists. "
          "Do not edit memory. Do not commit.")
FLAGS = ["--permission-mode", "dontAsk", "--settings", ".claude/settings.json",
         "--output-format", "stream-json", "--verbose",
         "--disallowedTools", "Edit(memory/**)"]
TOOL_CMD = "python src/blend_tools.py"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rab = _load("run_agent_batch", "scripts/run_agent_batch.py")
mew = rab.mew


def critical(denial):
    """A denied call the agent needs and is allowed to make: the Skill, writing under runs/, or blend_tools.py."""
    name, inp = denial.get("tool_name"), denial.get("tool_input") or {}
    if name == "Skill":
        return True
    if name in ("Write", "Edit"):
        return "/runs/" in str(inp.get("file_path", "")).replace("\\", "/")
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        for op in ("&&", "||", ";", "|"):
            cmd = cmd.replace(op, "\n")
        parts = [p.strip() for p in cmd.splitlines() if p.strip()]
        return bool(parts) and all(p.startswith(TOOL_CMD) for p in parts)
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--blend-case", default=DEFAULT_CASE)
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    case_name = Path(a.blend_case).name
    results = REPO / "runs" / "results" / "blend" / "agent" / a.run_id
    transcripts = REPO / "eval" / "transcripts" / a.run_id
    batch_log = REPO / "eval" / "batches" / f"{a.run_id}.json"
    for p in (results, transcripts, batch_log):
        if p.exists():
            sys.exit(f"{p} already exists; not overwriting. Pick another --run-id.")
    claude = shutil.which("claude")
    if not a.dry_run:
        if claude is None:
            sys.exit("claude is not on PATH.")
        if not rab.venv_python_active():
            sys.exit("`python` is not the repository's .venv; activate it first (source .venv/bin/activate).")

    ws = mew.main(["--run-id", a.run_id, "--dest", a.dest, "--blend-case", a.blend_case]
                  + (["--allow-dirty"] if a.dry_run else []))
    (ws / RUN_DIR).mkdir(parents=True, exist_ok=True)
    prompt = PROMPT.format(case=f"runs/blend_cases/{case_name}")
    if a.dry_run:
        print(f"\n[dry run] would run in {ws}:\n  claude -p {json.dumps(prompt)} {' '.join(FLAGS)}")
        return

    for p in (results, transcripts, batch_log.parent):
        p.mkdir(parents=True, exist_ok=True)
    tpath = transcripts / f"{case_name}.jsonl"
    t0 = time.time()
    print(f"running the blend agent on {case_name} in {ws} (up to {TIMEOUT_S // 60} min) ...", flush=True)
    with open(tpath, "w", encoding="utf-8") as f:
        try:
            p = subprocess.run([claude, "-p", prompt, *FLAGS], cwd=ws, stdout=f, stderr=subprocess.PIPE, text=True,
                               timeout=TIMEOUT_S)
            code, err = p.returncode, p.stderr.strip()[-500:]
        except subprocess.TimeoutExpired:
            code, err = None, f"timed out after {TIMEOUT_S} s"
    res = rab.last_result(tpath)
    denials = res.get("permission_denials") or []
    crit = [d for d in denials if critical(d)]
    run_src = ws / RUN_DIR
    for f in run_src.rglob("*.json"):
        dst = results / f.relative_to(run_src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    dec = results / "decision.json"
    log = {"run_id": a.run_id, "workspace": str(ws), "blend_case": a.blend_case,
           "started_utc": datetime.fromtimestamp(t0, timezone.utc).isoformat(),
           "finished_utc": datetime.now(timezone.utc).isoformat(), "seconds": round(time.time() - t0, 1),
           "prompt": prompt, "flags": FLAGS, "exit_code": code, "turns": res.get("num_turns"),
           "cost_usd": res.get("total_cost_usd"), "decision_written": dec.exists(),
           "evaluations_used": len(list((results / "evals").glob("e_*.json"))) if (results / "evals").exists() else 0,
           "denials": [{"tool": d.get("tool_name"), "input": d.get("tool_input")} for d in denials],
           "critical_denials": len(crit), "stderr_tail": err or None}
    batch_log.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    print(f"decision written: {dec.exists()}; {log['turns']} turns; ${log['cost_usd'] or 0:.2f}; "
          f"{log['evaluations_used']} evaluations; {len(denials)} denial(s), {len(crit)} critical; "
          f"log {batch_log.relative_to(REPO)}")
    if crit or not dec.exists():
        sys.exit(1)


if __name__ == "__main__":
    main()
