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
    "README.md",
    "memory/MEMORY.md",
    "memory/USER.md",
    "configs/thresholds.yaml",
    ".claude/settings.json",
    ".claude/skills/forcing-tools/SKILL.md",
    ".claude/skills/forcing-tools/forcing_tools.py",
)
CASES = Path("runs/cases")
DEFAULT_DEST = Path.home() / "sar_eval"


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True, help="name for this run, e.g. rep1")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help=f"parent folder outside the repo (default {DEFAULT_DEST})")
    ap.add_argument("--allow-dirty", action="store_true", help="testing only: allow uncommitted allowlisted files")
    a = ap.parse_args(argv)

    ws = Path(a.dest).expanduser().resolve() / a.run_id
    if REPO.resolve() in (ws, *ws.parents):
        sys.exit(f"{ws} is inside the repository; choose a folder outside it.")
    if ws.exists():
        sys.exit(f"{ws} already exists; not overwriting. Remove it yourself or pick another --run-id.")
    out = REPO / "eval" / "workspaces" / f"{a.run_id}.json"
    if out.exists():
        sys.exit(f"{out} already exists; not overwriting. Pick another --run-id.")

    dirty = git("status", "--porcelain", "--", *ALLOWLIST)
    if dirty and not a.allow_dirty:
        sys.exit("These allowlisted files have uncommitted changes; commit them first:\n" + dirty)
    cases = sorted(p for p in (REPO / CASES).glob("case*") if p.is_dir())
    if not cases:
        sys.exit("no runs/cases/case*; run python src/inject_faults.py first")
    if (REPO / "heldout" / "labels.json").exists():
        sys.exit("heldout/labels.json is inside the repository; move it out before an agent run.")

    copied = {}
    for rel in ALLOWLIST:
        dst = ws / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dst)
        copied[rel] = sha256(dst)
    for c in cases:
        for f in sorted(c.iterdir()):
            rel = (CASES / c.name / f.name).as_posix()
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, ws / rel)
            copied[rel] = sha256(ws / rel)

    manifest = {"run_id": a.run_id, "workspace": str(ws), "created_utc": datetime.now(timezone.utc).isoformat(),
                "commit": git("rev-parse", "HEAD"), "dirty_allowed": bool(dirty), "files": copied}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"workspace: {ws}\n{len(copied)} files ({len(cases)} cases); manifest: {out.relative_to(REPO)}")
    return ws


if __name__ == "__main__":
    main()
