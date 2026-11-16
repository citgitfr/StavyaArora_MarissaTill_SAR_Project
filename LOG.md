# Running Log
Team: Stavya Arora, Marissa Till | REPO: https://github.com/citgitfr/StavyaArora_MarissaTill_SAR_Project

Add a dated entry every work session. Include failures.

## 2026-11-15 

**Done**
- Corrected the case count: the injector produces 14 cases (4 clean, 8 detectable faults, 2 below the
  detection limit), not 15. Its own summary line miscounted; there is no case15 (the blank result in an
  earlier loop was the tool reporting that runs/cases/case15/ does not exist).
- Fixed instruction inconsistencies found by a repo inventory
- Froze forcing_tools.py for the evaluation runs: SHA-256 7AFE6B2FB8F9862D3D15085FC950A047BD3CEEF17186EF47244D648EFBDB2B13,
  last changed in commit dcd0f02. Thresholds frozen 2026-10-29.
- Following the TA's feedback on Lab Report 2, memory/MEMORY.md is now written by the agent (decisions and
  lasting facts, under 40 lines, no case results or answer-key information) and reviewed by us in the PR;
  memory/USER.md is ours. Updated settings.json (allow Skill(forcing-tools), Edit(memory/MEMORY.md),
  git add memory/MEMORY.md; ask for memory/USER.md), PERMISSIONS.md and CLAUDE.md to match. Evaluation runs pass
  --disallowedTools "Edit(memory/)" "Bash(git add memory/)" so every run sees the same memory.
- Set up the .venv on Marissa's Mac (uv, Python 3.12) and regenerated the cases with src/inject_faults.py.
  The regenerated case03 gives the same tool numbers as the Windows pilot, so the generator is reproducible
  across machines.
- Permission smoke test (claude -p, dontAsk, memory locked) on case03: the Skill tool now loads, every tool call
  ran, outputs went only to the paths in the prompt, memory was untouched, no permission denials; 7 turns.
  Same decision as the pilot (mask_fill), different parameter (5 vs 10); both are allowed by SKILL.md, so the
  scorer must not require one exact mask_fill value. Transcript: eval/transcripts/smoke_case03.jsonl (not a result).
- Probe: asked the agent to run `wc -c heldout/labels.json`. No permission denial fired; the agent refused because
  CLAUDE.md says never read heldout/. So for shell commands only an instruction protects the key.
  Transcript: eval/transcripts/probe_heldout.jsonl. Decision: keep labels.json outside the repo folder during agent runs.
- Wrote src/baseline.py (scripted baseline: same tools via the command line, first failing check test -> one repair
  from the SKILL.md menu, re-check, at most 2 repairs, else model_only; tool error -> escalate) and
  tests/test_baseline.py (15 tests). All 55 tests pass. Ran it on the 14 cases in about 25 s: 6 fused, 7 repair,
  1 model_only (not yet scored against the key).
- Found: compare's lag estimate is biased by the clean data's own -1 h best lag, so a shift_time -L repair can leave
  a residual offset of about 1 h that the lag test cannot see, and check then passes. The scorer needs repair
  fidelity (repaired radar vs. the clean fixture), not just check pass/fail. SKILL.md gives the agent the same -L rule.
- Moved heldout/labels.json to ~/sar_heldout/ (outside the repo); heldout/ is now empty.
- Found a wider leak: inside the repo, an evaluation run's shell could read the fault plan and acceptable answers
  (src/inject_faults.py, docs/dev_notes.md), the clean radar (fixtures/), baseline decisions (runs/results/),
  findings (LOG.md), transcripts (eval/) and git history. Read deny rules do not cover shell reads (the smoke test
  ran `cat` with no denial). Fix: scripts/make_eval_workspace.py builds a folder outside the repo with only an
  allowlist (CLAUDE.md, PERMISSIONS.md, README.md, memory/, configs/thresholds.yaml, .claude/settings.json, the
  forcing-tools skill and script) plus runs/cases/. No .git, no heldout. It refuses uncommitted allowlisted files and
  refuses to run if labels.json is in the repo, and writes a manifest (commit, SHA-256 per file) to
  eval/workspaces/<run_id>.json. Self-test: 37 files, check runs inside it.
- Trial run (case06) in a workspace failed first: every allowed tool was denied, because a new folder is untrusted
  and Claude Code ignores its own .claude/settings.json. The agent wrote nothing and reported the block ($0.21).
  Fix: pass --settings .claude/settings.json. Rerun: Skill and tools worked; decision repair flip_sign v, the same
  as the baseline; 9 turns, $0.13. One harmless denial: `mkdir ... && python ...` (compounds need every part allowed);
  the agent retried without mkdir. Practice run only, not scored (eval/transcripts/trial/, runs/results/agent/trial/).
- Wrote scripts/run_agent_batch.py: one call per repeat; builds the workspace, pre-creates output folders, runs
  claude -p per case (dontAsk, --settings, memory locked), copies decisions, repaired files and transcripts back,
  stops on a denial of a needed tool, logs turns/cost/denials to eval/batches/<run_id>.json. Dry run tested.


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
- NCEI archive reached; listing shows three variants per hour (SIO hourly, NDBC, 25-hour average), about 227 KB each; using SIO hourly only; excluded 25hr_average to avoid mixing products.
-NCEI archive works from the WorkSpace; 48 hourly SIO files, about 11 MB; Claude Code wrote the pull script, we reviewed it and ran it ourselves.
- Had Claude Code write .claude/skills/forcing-tools/forcing_tools.py (qc, compare, repair, check)
  and tests/test_forcing_tools.py; we reviewed both and ran the tests ourselves: 40 tests pass.
- Ran qc and compare by hand on the real fixtures. compare reproduces our independent first-look numbers
  (n_pairs 130635, rms_u 0.1755, corr_u 0.498, speed ratio 1.107). check's coverage (mean 0.416, min 0.344
  of 7313 ocean cells) matches scripts/cov_check.py (0.42, 0.34).
- Froze configs/thresholds.yaml (2026-10-29) from the clean data only, before generating any fault cases.
  check passes all 8 tests on the clean pair.
- Findings: units differ as strings ('m s-1' vs 'm/s') but normalize to the same unit; the clean lag curve
  is flat (best lag -1, corr 0.4440 vs 0.4425 at lag 0), so best_lag_nonzero fires on clean data and a
  +-1 h shift is undetectable with this data; the radar covers 34-44% of ocean cells in any hour
  - Wrote .claude/skills/forcing-tools/SKILL.md; ran the agent (claude -p, dontAsk) on case01 and case03.
  case01 (clean): check passed, decision fused, no repair. case03: check failed (speed_max 1099 m/s, corr ~0,
  lag 3 h); agent masked values >= 10 m/s (7,479 cells), re-check passed, decision repair. Cost about $0.20 and
  8 turns for case03. Transcripts in eval/transcripts/.
- Observed: the Skill tool was denied by our permissions (agent read SKILL.md as a file instead); decisions are still grounded in tool output.

**Remaining**
- Run all 14 cases x 2 repeats; minimal score.py (floor, oracle, repair success);
  baseline rule; fresh-clone smoke test; allow rule for the Skill tool.

**Next Steps**
Next: implement the fixed blend recipe (radar where valid, model elsewhere, with a source mask), run OpenDrift Leeway ensembles from the chosen forcing, and score both forcing options against held-out drifter tracks, which is what turns the model-only versus fused choice into a measurable one.

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
