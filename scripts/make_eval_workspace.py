"""Build an isolated workspace for one agent evaluation run.

Permission rules cannot stop the agent's shell from reading files (read-only commands such as cat are allowed),
so an evaluation run must not happen inside the repository, where it could read the fault plan
(src/inject_faults.py, docs/dev_notes.md), the clean radar (fixtures/), baseline decisions (runs/results/),
findings (LOG.md), transcripts (eval/) or git history. This script copies an ALLOWLIST of files into a new folder
outside the repository, plus the generated cases. Nothing else is copied: no .git, no heldout/.

The allowlisted files must be committed and unchanged, so every run is tied to a commit. A manifest (commit,
SHA-256 of every copied file) is written to eval/workspaces/<run_id>.json in the repository, not in the workspace.

Run from the repository root (stdlib only, works on macOS and Windows):
  python scripts/make_eval_workspace.py --run-id rep1
  python scripts/make_eval_workspace.py --run-id rep1 --dest ~/sar_eval
Then run claude -p from inside the printed folder and copy the results back afterwards.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ALLOWLIST = (
    "CLAUDE.md",
    "PERMISSIONS.md",
    "memory/MEMORY.md",
    "memory/USER.md",
    "configs/thresholds.yaml",
    ".claude/settings.json",
    ".claude/skills/forcing-tools/SKILL.md",
    ".claude/skills/forcing-tools/forcing_tools.py",
)
# Blend runs (Lab 4, Phase B) also need the blend tool and the code it imports. None of these files holds a fault
# plan, an answer key or a drifter's test period.
BLEND_ALLOWLIST = (
    ".claude/skills/blend-tools/SKILL.md",
    "src/blend_tools.py",
    "src/blend.py",
    "src/forecast_eval.py",
    "src/drifter_eval.py",
)
CASES = Path("runs/cases")
BLEND_CASES = Path("runs/blend_cases")
DEFAULT_DEST = Path.home() / "sar_eval"
# The real README.md holds results (which policy did what on which kind of case). CLAUDE.md loads README.md into the
# agent's context, so an evaluation workspace gets this neutral stand-in instead (added 2026-11-15, before cmp_rep2).
WORKSPACE_README = """# SAR forcing agent

Search-and-rescue drift forecasts use ocean currents from a model (HYCOM) and, near the coast, measured currents from
HF radar. This agent checks one radar file against the model file with its forcing tools, diagnoses any problem,
and decides which forcing a drift forecast should use: fused, repair (from the fixed menu), model_only, or escalate
to a person. Every number must come from the tools. See CLAUDE.md, PERMISSIONS.md and the forcing-tools skill.
"""


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True, help="name for this run, e.g. rep1")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help=f"parent folder outside the repo (default {DEFAULT_DEST})")
    ap.add_argument("--allow-dirty", action="store_true", help="testing only: allow uncommitted allowlisted files")
    ap.add_argument("--cases-dir", default=str(CASES),
                    help="where the cases come from (copied to runs/cases/ in the workspace, so prompts do not change)")
    ap.add_argument("--blend-case", default=None,
                    help="blend run instead of cleaning cases: copy this blend case folder (src/make_blend_case.py) to "
                         "runs/blend_cases/<name>/ plus the blend tool and its skill; no runs/cases")
    a = ap.parse_args(argv)
    allowlist = ALLOWLIST + (BLEND_ALLOWLIST if a.blend_case else ())

    ws = Path(a.dest).expanduser().resolve() / a.run_id
    if REPO.resolve() in (ws, *ws.parents):
        sys.exit(f"{ws} is inside the repository; choose a folder outside it.")
    if ws.exists():
        sys.exit(f"{ws} already exists; not overwriting. Remove it yourself or pick another --run-id.")
    out = REPO / "eval" / "workspaces" / f"{a.run_id}.json"
    if out.exists():
        sys.exit(f"{out} already exists; not overwriting. Pick another --run-id.")

    dirty = git("status", "--porcelain", "--", *allowlist)
    if dirty and not a.allow_dirty:
        sys.exit("These allowlisted files have uncommitted changes; commit them first:\n" + dirty)
    src_cases = Path(a.cases_dir) if Path(a.cases_dir).is_absolute() else REPO / a.cases_dir
    cases = [] if a.blend_case else sorted(p for p in src_cases.glob("case*") if p.is_dir())
    if not cases and not a.blend_case:
        sys.exit(f"no case* folders in {src_cases}; run python src/inject_faults.py first")
    blend_case = None
    if a.blend_case:
        blend_case = Path(a.blend_case) if Path(a.blend_case).is_absolute() else REPO / a.blend_case
        meta = json.loads((blend_case / "case.json").read_text(encoding="utf-8"))
        last = max(line.split(",")[0] for line in
                   (blend_case / "drifter_tune.csv").read_text(encoding="utf-8").splitlines()[1:])
        if last > meta["split"]:
            sys.exit(f"{blend_case}/drifter_tune.csv runs past the split {meta['split']}; refusing")
    if (REPO / "heldout").is_dir() and any((REPO / "heldout").iterdir()):
        sys.exit("heldout/ inside the repository is not empty; move answer keys out before an agent run.")

    copied = {}
    for rel in allowlist:
        dst = ws / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dst)
        copied[rel] = sha256(dst)
    (ws / "README.md").write_text(WORKSPACE_README, encoding="utf-8")       # neutral stand-in, never the real one
    copied["README.md (workspace stand-in)"] = sha256(ws / "README.md")
    for c in cases:
        for f in sorted(c.iterdir()):
            rel = (CASES / c.name / f.name).as_posix()
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, ws / rel)
            copied[rel] = sha256(ws / rel)
    if blend_case:
        for f in sorted(blend_case.iterdir()):
            rel = (BLEND_CASES / blend_case.name / f.name).as_posix()
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, ws / rel)
            copied[rel] = sha256(ws / rel)

    manifest = {"run_id": a.run_id, "workspace": str(ws), "created_utc": datetime.now(timezone.utc).isoformat(),
                "cases_from": str(src_cases.relative_to(REPO)) if REPO in src_cases.parents else str(src_cases),
                "commit": git("rev-parse", "HEAD"), "dirty_allowed": bool(dirty), "files": copied}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"workspace: {ws}\n{len(copied)} files ({len(cases)} cases); manifest: {out.relative_to(REPO)}")
    return ws


if __name__ == "__main__":
    main()
