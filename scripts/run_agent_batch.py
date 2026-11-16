"""Run the agent on every benchmark case once, in an isolated workspace, and bring the answers back.

One call = one repeat (one --run-id). For two repeats, run it twice: --run-id rep1, then --run-id rep2.

What it does:
  1. Builds the workspace ~/sar_eval/<run_id> with scripts/make_eval_workspace.py (allowlisted files + cases only).
  2. Pre-creates the output folders there, so the agent never needs mkdir.
  3. For each case, runs `claude -p` INSIDE the workspace with the frozen settings passed explicitly
     (--settings .claude/settings.json; a new folder is untrusted, so its own settings would be ignored),
     dontAsk mode, and memory locked. The transcript goes straight to eval/transcripts/<run_id>/<case>.jsonl.
  4. Copies the decision and any repaired files back into the repository:
     runs/results/agent/<run_id>/<case>.json and runs/work/agent/<run_id>/<case>/.
  5. Stops the batch if a tool the agent needs was denied (the Skill, forcing_tools.py, or writing its decision),
     because later cases would not be comparable. Other denials (mkdir, ls) are only logged.
  6. Writes a batch log with turns, cost, time and denials per case: eval/batches/<run_id>.json.

Run from the repository root with the .venv active (the agent's `python` must be the venv's):
  python scripts/run_agent_batch.py --run-id rep1
  python scripts/run_agent_batch.py --run-id rep1 --cases case01 case02     # a subset
  python scripts/run_agent_batch.py --run-id dry --dry-run                  # build workspace, print commands, no cost
Never reads heldout/; refuses to run while heldout/labels.json is inside the repository.
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DEST = Path.home() / "sar_eval"
TIMEOUT_S = 900
PROMPT = ("Evaluation run. Use the forcing-tools skill to decide runs/cases/{case}. Write any repaired file under "
          "runs/work/agent/{case}/ and the decision to runs/results/agent/{case}.json. The output folders already "
          "exist. Do not edit memory. Do not commit.")
FLAGS = ["--permission-mode", "dontAsk", "--settings", ".claude/settings.json",
         "--output-format", "stream-json", "--verbose",
         "--disallowedTools", "Edit(memory/**)"]

_spec = importlib.util.spec_from_file_location("make_eval_workspace", REPO / "scripts" / "make_eval_workspace.py")
mew = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mew)


TOOL_CMD = "python .claude/skills/forcing-tools/forcing_tools.py"


def critical(denial):
    """True if a denied call is one the agent needs and is allowed to make: the Skill, writing under runs/,
    or a shell command made only of forcing_tools.py calls. A denied compound like `mkdir ... && python ...`
    is not critical: the mkdir caused it, and the agent can retry without it."""
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


def last_result(transcript):
    """The final 'result' event of a stream-json transcript, or {} if the run produced none."""
    result = {}
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "result":
            result = d
    return result


def venv_python_active():
    py = shutil.which("python")
    return py is not None and Path(py).parent.parent == REPO / ".venv"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--cases", nargs="*", help="case ids (default: every case in the workspace)")
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--dry-run", action="store_true", help="build the workspace and print the commands only")
    a = ap.parse_args(argv)

    results = REPO / "runs" / "results" / "agent" / a.run_id
    work = REPO / "runs" / "work" / "agent" / a.run_id
    transcripts = REPO / "eval" / "transcripts" / a.run_id
    batch_log = REPO / "eval" / "batches" / f"{a.run_id}.json"
    for p in (results, work, transcripts, batch_log):
        if p.exists():
            sys.exit(f"{p} already exists; not overwriting. Pick another --run-id.")
    claude = shutil.which("claude")
    if not a.dry_run:
        if claude is None:
            sys.exit("claude is not on PATH.")
        if not venv_python_active():
            sys.exit("`python` is not the repository's .venv; activate it first (source .venv/bin/activate).")

    # a dry run costs nothing and is never scored, so it may use uncommitted files; a real run may not
    ws = mew.main(["--run-id", a.run_id, "--dest", a.dest] + (["--allow-dirty"] if a.dry_run else []))
    ids = a.cases or sorted(p.name for p in (ws / "runs" / "cases").glob("case*") if p.is_dir())
    missing = [c for c in ids if not (ws / "runs" / "cases" / c).is_dir()]
    if missing:
        sys.exit(f"cases not in the workspace: {missing}")
    (ws / "runs" / "results" / "agent").mkdir(parents=True, exist_ok=True)
    for c in ids:
        (ws / "runs" / "work" / "agent" / c).mkdir(parents=True, exist_ok=True)

    if a.dry_run:
        print(f"\n[dry run] would run {len(ids)} case(s) from {ws}:")
        for c in ids:
            print("  claude -p " + json.dumps(PROMPT.format(case=c)) + " " + " ".join(FLAGS)
                  + f" > {transcripts / (c + '.jsonl')}")
        return

    for p in (results, work, transcripts, batch_log.parent):
        p.mkdir(parents=True, exist_ok=True)
    log = {"run_id": a.run_id, "workspace": str(ws), "started_utc": datetime.now(timezone.utc).isoformat(),
           "prompt_template": PROMPT, "flags": FLAGS, "cases": {}, "stopped_early": None}
    total = 0.0
    for n, c in enumerate(ids, 1):
        tpath = transcripts / f"{c}.jsonl"
        t0 = time.time()
        print(f"[{n}/{len(ids)}] {c} ...", end=" ", flush=True)
        with open(tpath, "w", encoding="utf-8") as f:
            try:
                p = subprocess.run([claude, "-p", PROMPT.format(case=c), *FLAGS], cwd=ws, stdout=f,
                                   stderr=subprocess.PIPE, text=True, timeout=TIMEOUT_S)
                code, err = p.returncode, p.stderr.strip()[-500:]
            except subprocess.TimeoutExpired:
                code, err = None, f"timed out after {TIMEOUT_S} s"
        res = last_result(tpath)
        denials = res.get("permission_denials") or []
        crit = [d for d in denials if critical(d)]
        dec_src = ws / "runs" / "results" / "agent" / f"{c}.json"
        if dec_src.exists():
            shutil.copy2(dec_src, results / f"{c}.json")
        shutil.copytree(ws / "runs" / "work" / "agent" / c, work / c, dirs_exist_ok=True)
        cost = res.get("total_cost_usd") or 0.0
        total += cost
        log["cases"][c] = {"seconds": round(time.time() - t0, 1), "exit_code": code, "turns": res.get("num_turns"),
                           "cost_usd": cost, "decision_written": dec_src.exists(),
                           "denials": [{"tool": d.get("tool_name"), "input": d.get("tool_input")} for d in denials],
                           "critical_denials": len(crit), "stderr_tail": err or None}
        choice = json.loads(dec_src.read_text(encoding="utf-8")).get("choice") if dec_src.exists() else "NO DECISION"
        print(f"{choice}  ({res.get('num_turns')} turns, ${cost:.2f}, {len(denials)} denial(s))")
        batch_log.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
        if crit:
            log["stopped_early"] = f"critical permission denial in {c}: " + json.dumps(crit)[:400]
            print("STOPPING: " + log["stopped_early"])
            break

    log["finished_utc"] = datetime.now(timezone.utc).isoformat()
    log["total_cost_usd"] = round(total, 4)
    batch_log.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    done = sum(v["decision_written"] for v in log["cases"].values())
    print(f"\n{done}/{len(ids)} decisions in {results.relative_to(REPO)}; total ${total:.2f}; "
          f"log {batch_log.relative_to(REPO)}")
    if log["stopped_early"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
