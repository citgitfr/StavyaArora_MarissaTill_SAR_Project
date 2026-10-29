# Running Log
Team: Stavya Arora, Marissa Till | REPO: https://github.com/citgitfr/StavyaArora_MarissaTill_SAR_Project

Add a dated entry every work session. Include failures.

## 2026-10-28

**Done**
- Read the course template's PERMISSIONS.md and Lab 4 Part C; adapted them for our agent
  (one tool script, narrow git rights, hidden answer key, no network, no memory).
- Wrote CLAUDE.md (operating instructions), PERMISSIONS.md v0.1 (the spec), and
  .claude/settings.json (the enforcement: allow/ask/deny, bypass mode disabled, model pinned).
- Created project folders: fixtures/, heldout/, runs/, eval/, scripts/, configs/.
- Kept the answer key out of git (heldout/ in .gitignore, never committed).
- Tested the rules with `claude -p ... --permission-mode dontAsk` on a claude/perm-test branch.
  Saved transcript: eval/transcript_perm_commit.jsonl.
  - Allowed and worked: git status; git add runs/perm_test.txt, git commit, git push origin
    claude/perm-test (commit 81ba7f4; permission_denials was empty).
  - Refused before running: git push origin HEAD:main; git add .; git push --force; read
    heldout/labels.json (the tool returned "File is in a directory that is denied by your
    permission settings"); curl; pip list.


## 2026-10-14
**Done**
- Created repo and project skeleton
- Python installers were blocked on our WorkSpace, so we installed uv (per its docs) and used it to get Python 3.12 in a project venv
- Environment setup
- Added .venv and initial requirements
- SAR context added (docs/SAR_context.md)
- Draft proposal updated
- Created initial literature review agent (.claude/commands)
- - Set up initial rules, and got claude running in project terminal
- Initial literature review (docs/related_work.md)
- Installed OpenDrift 1.14.12 and ran its own `example_leeway.py` (not our code); figure saved to docs/figures/leeway_smoke_test_example.png

**Bottlenecks**
- Test reports don't name which rule fired, only that the command was denied. Only the
  heldout read returned a tool-level permission error.
- Known gap: the heldout deny covers Claude's file tools, not shell commands.

**Next-Steps**
- Set up OpenDrift on Marissa’s VM
- Get the literature review agent online
- - The initial agent was not able to read PDFs over 20 MB (appropriate libraries weren't downloaded)
- Solidify the project goal and feasibility
- - Further literature review in this field
- - Explore existing data and limitations
- - Explore maritime SAR simulations
