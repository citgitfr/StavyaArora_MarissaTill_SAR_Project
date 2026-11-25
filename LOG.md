# Running Log
Team: Stavya Arora, Marissa Till | REPO: https://github.com/citgitfr/StavyaArora_MarissaTill_SAR_Project

Add a dated entry every work session. Include failures.

## 2026-11-18 (Lab 4 start, branch claude/lab4)

**Done**
- Agreed the Lab 4 plan: stay on the October 2021 window and its one drifter until the whole chain works
  (A wind and honest spread, B blend menu + investigator agent, C search planner, D multi-period loop), then add
  drifters (E). Details and status: docs/lab4_handoff.md.
- Data: the partner's data/ folder (same October radar/HYCOM as fixtures/, plus a 2018-2024 GDP drifter search off
  California, 38 drifters). Only drifter 300534061399950 is in the October radar window; November 2021 has about 3
  drifters near the coast (not pulled yet). Pulled ERA5 hourly 10 m wind and wave Stokes drift for 2021-10-18..29
  with src/data/pull_era5.py (we ran it; free CDS account, key in ~/.cdsapirc). Checks: no gaps, mostly NW winds about
  6 m/s along the track, the Oct 24-25 storm shows as southerly winds; Stokes is missing near the coast (32% NaN) but
  not along the drifter.
- Phase A, src/forecast_eval.py: 24 h windows every 6 h; tuning = windows ending by 2021-10-23 12:00 (19), test =
  windows starting from it (18), no shared hours. Wind barely helps (model only best with no wind; radar+model best at
  1% windage, 0.1 km better than none); Stokes made every setup worse. Along the track the drifter moved north
  (about 9 cm/s) while HYCOM pointed south (about 7 cm/s) and the wind blew SE.
- Phase B: src/blend.py (fixed menu, one gap-free current file, refuses unless check passes), src/blend_tools.py (the
  agent's tool: menu/build/evaluate/diagnose/list, 12-evaluation budget), src/make_blend_case.py (case folder holds
  only the tuning part of the drifter), src/blend_baseline.py (try-everything, 50 evaluations), src/blend_score.py
  (spread calibrated on tuning windows, scored on test windows). Skill .claude/skills/blend-tools/SKILL.md, allowed
  in settings.json and PERMISSIONS.md (owner-approved). make_eval_workspace.py --blend-case.
- Tests: tests/test_pull_era5.py, test_forecast_eval.py, test_blend.py, test_blend_tools.py,
  test_blend_baseline_score.py; all pass.

**Found (one drifter; early evidence)**
- Bug in the Lab 3 containment measure: a 0.01 deg histogram of 500 particles undercounts hits for wide clouds (90%
  area 372 km2 vs true 1447 km2 on a 10 km Gaussian test). Replaced in Lab 4 code by a kernel density estimate
  (forecast_eval.hdr_kde). Lab 3's clouds were about 1 km wide, where both agree, so Lab 3's conclusion stands.
- OpenDrift's default interpolation fills radar holes from the largest neighbouring radar value (up to 10 times), so
  Lab 3's "radar where it has data, else the model" was really 56% radar, 40% stretched radar (median 4.7 km, max
  30 km from real data), 3% HYCOM along the drifter. src/blend.py removes this (57% radar, 43% model). Lab 3 wording
  left for later (owners' decision).
- Honest 50/90% areas need K = 1600-3200 m2/s (90% areas 3,500-7,500 km2), and then they are too wide on the test
  windows; random-walk spread grows like sqrt(t) while our error is a steady drift.
- The try-everything baseline picked model only, no wind (every step toward more radar was worse on the tuning
  windows). On the test windows the ranking flips: mean 24 h miss 15.4 km model only, 13.2 km hard_switch, 11.8 km
  Lab 3 reader priority, 12.2 km no motion. A choice made on five days did not carry over to the next five.

**Failures / problems**
- First quick test suggested "wind makes it worse"; that was 3 windows. The full tuning grid showed it is nearly flat.
- xarray's interpolate_na(max_gap) needs bottleneck (not installed); blend.py does its own gap fill.
- Blend files were 40 MB each uncompressed; now zlib (about 13 MB).

**Next**
- Run the blend agent once and score it; ensemble spread; Phase C search planner; Phase D loop.

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
- Agent batch rep1: 14/14 decisions, 0 denials, $2.46, about 6 min. rep2: 14/14, 0 denials, $2.43. rep1 and rep2
  made the same choice and repair action on all 14 cases (only the mask_fill threshold varied: 5 or 10), and both
  match the scripted baseline's actions on all 14 (baseline mask_fill threshold 6). The agent explains its choices
  well, but on these single, clean-cut faults the rule table does as well for free.
- Wrote src/score.py (LLM-free: acceptable, silent errors, radar recovered, faithful radar vs. the clean fixture,
  escalations, cost/turns, run agreement; policies always_model_only, always_fused, baseline, agent per run, oracle)
  and tests/test_score.py (14 tests, made-up key). Plumbing run on a made-up all-clean key: 1.5 s; fidelity flags
  two radar decisions per policy as not faithful (vector RMS 0.125 m/s, 47 of 48 hours shared), consistent with a
  1-hour time offset left in the data. Not yet run on the real key.
- Scored on the real key (python src/score.py): acceptable 13/14 for baseline, agent rep1 and rep2; 1 silent error each;
  radar recovered 12/13; faithful radar 11/13; always_fused 8 silent errors; always_model_only recovers 0/13.
  rep1 vs rep2 and baseline vs rep1 agree on 14/14.
- Checked how OpenDrift 1.14.12 combines readers (models/basemodel/environment.py, get_environment): readers are
  tried in order per particle and time step, and NaN or out-of-coverage values fall through to the next reader.
  So stacking [radar, model] is a hard-switch blend; Lab 3 uses this instead of writing src/blend.py. Weighted or
  tapered blends need a written file (Lab 4).
- Wrote src/drift.py (OceanDrift, currents only, deterministic; 5 release points x 200 particles; 24 h from
  2021-08-10 11:00; reference = clean radar + HYCOM; separation at 6/12/24 h and POC-map overlap; runs cached by
  data hash) and tests/test_drift.py (6 tests). First version picked release points in the box corners, where
  particles leave both readers (the HYCOM slice is the same box as the radar) and stop; fixed with a 0.25 deg margin.
- Drift results (24 h, median km from the reference): model_only 13.9 km; always_fused up to 38.8 km on the x0.01
  case and 6-10 km on swap, sign flip and x100; baseline and agent repairs bring those to 0.0; mask_fill cases
  0.3-0.4 km (masked cells fall back to the model); the remaining 1 h offset costs 0.74 km (same as the undetectable
  +1 h case); using the 60%-blanked radar is 11.3 km from the reference versus 13.9 km for model_only.
  Figure: docs/figures/drift_case10.png.
- Ground truth search (scripts/find_drifters.py, run by Marissa): no GDP drifters in the radar box on 2021-08-10..12;
  4 in 2018-2024 in that box, 38 in a wider California box. Five drifters were offshore on our exact dates, but the
  radar did not reach them (0% coverage; pulled a wider Aug 2021 radar cut to check, then deleted it). Measured the
  radar's reach (about 120 W at 33.75 N, 121.5 W at 34.75 N) and ranked drifter visits inside it. Chose undrogued
  drifter 300534061399950, 2021-10-18..28 (radar data in its cell 64% of hours, within 5 km 91%).
- Added command-line options to scripts/list_ncei.py, src/data/pull_hfr_ncei.py and src/data/pull_fixtures.py
  (defaults reproduce the original fixtures; dropped the course User-Agent header). New offline
  scripts/drifter_coverage.py. Added deny rules so Claude cannot run the download scripts (people pull data).
- Wrote src/drifter_eval.py and tests/test_drifter_eval.py (8 tests). The tests caught a bug in the 50%/90% area
  code (cells mapped back to the grid in the wrong order). Reusing OpenDrift readers made runs ~10x faster.
- Result vs the real drifter (20 windows, 24 h): radar+model beats model_only (median 24 h separation 16.0 vs
  18.9 km at K=10; skill 0.22 vs 0.07; closer in 72% of window/K pairs). But both are far off, and the POC maps are
  badly overconfident: the drifter is inside the 90% area in 0% of windows (5% at K=50, area ~80 km2). In the example
  window both forecasts go west while the drifter goes east. Likely missing physics: wind (the drogue was lost) and
  model error. The frozen check passes on this real pair, so baseline and agent would choose fused, which reality
  rewards over model_only, but the forecast itself is not good enough to plan a search on.
- Wrote src/drifter_viz.py: summary figure of all 20 windows and animated GIFs of single windows. Pattern: radar
  helps most where it covers the drifter's path (e.g. windows 10-23..10-25, 90-100% coverage: 6-11 km vs 15-17 km
  for model_only), but in some well-covered windows (10-19 12:00, 10-20 00:00) radar+model is worse (30-37 km).
  Checked determinism: the same window gives the same result in a fresh process and after other runs.
- Decisions vs reality (step D). inject_faults.py now takes --radar --model --cases --labels --seed and writes
  zlib-compressed cases (values identical; 354 MB -> 77 MB for 264 h). Generated 14 October cases from the clean
  October radar, seed 1018, key written straight to ~/sar_heldout/labels_oct.json. make_eval_workspace.py and
  run_agent_batch.py take --cases-dir (cases still appear as runs/cases/ in the workspace) and refuse to run if
  heldout/ holds anything. score.py takes --cases-dir --baseline-work --clean (clean radar read from the key).
- Baseline on October cases: 41 s; shift_time -3 this time (lag estimate correct on this data). Agent oct_rep1:
  14/14, 0 denials, $2.63. Against the key: baseline and agent both 14/14 acceptable, 0 silent errors, radar
  recovered 12/13, faithful 11/12; they agree on 14/14 (only the mask_fill threshold differs, 10 vs 6).
- Wrote src/decision_reality.py: each policy's decision on each faulty October case, forecast against the real
  drifter (20 windows, K=10). Mean 24 h miss: always_model_only 18.9 km, always_fused 19.8 km (59 km on the worst
  case), baseline 16.3 km, agent 16.3 km (identical forcings), clean radar 16.0 km. So good QC decisions get within
  0.3 km of a perfect repair, and blind trust or ignoring the radar costs 3-4 km on average. But a no-motion guess
  (the object stays where reported) misses by 15.0 km, less than every current-only forecast at 24 h, although the
  forecasts keep higher path skill (0.20-0.22 vs 0). Without wind the forecast does not beat doing nothing on
  end position; this limits what any QC decision can gain. Fixed: drifter_eval.run() now pads runs that OpenDrift
  ends early when every particle leaves the domain; score.py crashed on a relative --out (files were written).
- Wrote src/run_demo.py --replay: runs every stage, replays saved agent decisions (no Claude calls), and writes
  reports/lab3/README.md (all tables and figures) and run_log.txt, with reproducibility checks at the top.
  Tested without the keys: rerun baseline equals the committed decisions (August 14/14, October 14/14); agent repaired
  files rebuilt with the frozen tool equal the originals (rep1, rep2, oct_rep1: 7/7 each); in a simulated fresh clone
  (repair logs only, no .nc) all 7 repaired files were rebuilt identical. Fixed an argument-order bug in the rebuild.
  Added --baseline-work to drift.py; gitignored runs/*/_replay_*. Only one October agent run so far (oct_rep1);
  August showed rep1 = rep2 on all 14 actions, but a second October run (oct_rep2) would confirm it there.
- run_demo now runs October only by default (--august adds the August appendix) and writes
  demo_results/lab3_demo.md, demo_results/lab3_figures/ and demo_results/lab3_run_log.txt. drift.py takes
  --cases-dir, several --figure cases, --fig-prefix, and adds the agent as a figure panel; October figures
  drift_oct_case14/06.png. New src/decision_viz.py: baseline_vs_agent_oct.png (decisions side by side, per-case miss
  vs the real drifter baseline vs agent, metrics) and decision_oct_case14/06.gif (one forecast per policy with the
  perfect repair and the real drifter). In case14 (x0.01 radar) blind trust misses by 54.5 km in the animated
  window, baseline and agent (rescale 0.01) 10.6 km, model only 17.2 km.
- Figures made consistent: new src/plot_style.py gives every figure the same policy names and colours (No QC red,
  scripted baseline blue, Claude agent purple hatched/dashed, model only orange, perfect repair green, drifter black),
  plain-sentence titles and labelled axes. Every animation panel now has its own legend. New
  docs/figures/decision_summary_oct.png (the drifter-summary layout for No QC vs baseline vs agent vs perfect repair:
  case 14 forecast paths, miss by hour over all 14 cases x 20 windows, 24 h miss per case); it is in the demo report.
- Rewrote README.md (proposal, a Lab 3 note on why results differ from the Lab 2 proposal, headline results, key
  terms, how the agent is set up, every file and folder) and docs/pipeline.md (setup, the one command, data, each
  step with command / reads / writes, rules, troubleshooting). README is loaded into the agent's workspace through
  CLAUDE.md, so it no longer names the key folder; added deny Read(~/sar_heldout/**) to settings.json and
  PERMISSIONS.md. Gitignored the drift run cache runs/work/drift/.
- Checked our work against the Lab 3 requirements: loop, decisions on real tool output, LLM-free scoring and a
  baseline were all in place; missing was a project diagram labelling finished modules with inputs and outputs.
  Added to README: a drawn repo tree, a short "Where we are at Lab 3" note, and a "Project status" section (diagram
  with module numbers + table: status built / planned / proposed, input, output). run_demo copies that section into
  the report and adds "Decision traces" for three October cases (checks that failed, the agent's evidence and reason,
  agent vs baseline choice, key verdict, 24 h miss vs the drifter), so the agent's decisions are visible, not just
  counted. Honest note kept: on single clear-cut faults the agent matches the rule table; compound faults are next.
- Test of real agency (harder cases). inject_faults.py --plan compound: 8 cases from the clean October radar (seed
  2026): 2 clean, 3 controls with two radar faults (x100 + 3 h shift, swap + x0.01, spikes + v flip), radar rotated
  180 deg and 90 deg, and a faulty MODEL (x100, clean radar; only escalate is acceptable). Every model file is
  rewritten the same way so the faulty one is not visible from its encoding. Standard plan unchanged (October cases
  regenerate 14/14 identical). Before the real run, a throwaway-seed test confirmed the frozen baseline fails where
  predicted (rotations -> model only; faulty model -> repairs good radar, then uses the broken model).
- decision_reality.py now uses each case's own model.nc and records escalate as handed to a person (no forecast);
  October results unchanged (same data hash).
- Agent cmp_rep1: 8/8 decisions, 0 denials, $2.00. Against the key: agent 8/8 acceptable, 0 silent errors, radar
  recovered 6/7, 1 escalation; baseline 7/8, 0 silent, 5/7; always_fused 2/8 with 6 silent errors; always_model_only
  7/8. The agent escalated the faulty model ("the model reports a median of 14.34 m/s and a max of 66.97 m/s ...
  a person must inspect the model file") where the baseline produced a forecast 180 km off the real drifter; it
  recovered the 180-deg rotation (flip u then v; 16.0 km, equal to perfect repair) where the baseline used model only
  (18.9 km). It missed the 90-deg rotation (first tried a time shift, then model only, like the baseline). Mean 24 h
  miss: agent 16.4 km on the 7 cases it forecast, baseline 37.2 km (16.8 km without the faulty-model case).
  One run only; a second (cmp_rep2) would test whether this is reliable. run_demo reruns this part (report section 8,
  with decision traces for the cases where the two chose differently); README and pipeline.md updated.
- Caught a leak before cmp_rep2: the evaluation workspace copied README.md, which CLAUDE.md loads into the agent's
  context, and the updated README now describes the agent's compound results (the escalation, the rotation). A second
  run would have read the answers. make_eval_workspace.py now writes a neutral stand-in README into the workspace
  instead of the real one (PERMISSIONS.md updated); a test workspace contains no results text. cmp_rep1 is clean: the
  README it saw (commit fb9620a) only said compound faults were the next benchmark.
- Agent cmp_rep2 (neutral-README workspace, commit 3217faf): 8/8 decisions, 0 denials, $1.90. Same action as cmp_rep1
  on all 8 cases (only the mask_fill threshold differs, 10 vs 5): escalated the faulty model again ("the model file
  reports speeds far above it (qc max 66.97 m s-1) ... makes model_only unsafe"), recovered the 180-deg rotation again
  (flip u then v), missed the 90-deg rotation again the same way. Against the real drifter both runs are identical
  (mean 24 h miss 16.43 km on 7 forecast cases, +0.40 km vs perfect repair). The compound result is reproducible.
  Against the key (Marissa ran score.py): cmp_rep1 and cmp_rep2 both 8/8 acceptable, 0 silent errors, radar recovered
  6/7, faithful 6/6, 1 escalation; rep1 vs rep2 agree 8/8; baseline 7/8, 5/7; baseline vs agent agree 4/8.
- One combined test: run_demo already runs both October sets; its report now opens with "all 22 October cases in one
  test" (key scores summed, drifter misses averaged over the cases each policy forecast). Agent (oct_rep1 + either
  harder run): 22/22 acceptable, 0 silent errors, radar recovered 18/20, 1 correct escalation, mean 24 h miss 16.4 km
  over 21 forecast cases, worst case +2.9 km vs perfect repair, ~$4.60. Baseline: 21/22, 0 silent, 17/20, 23.9 km,
  worst +164 km (the faulty-model case). No QC: 8/22 with 14 silent errors. Model only: 21/22, 0/20 radar, 26.2 km.
  Same table at the top of the README results. A single shuffled 22-case benchmark (--plan full) is left for Lab 4.
- Whole report on all 22 cases. run_demo now checks each set separately (cases, keys, baseline, agent replays), then
  merges them into runs/cases_all/ (case01-14 single-fault, case15-22 harder; mapping.json), one key outside the repo,
  merged baseline decisions and merged agent runs all_rep1 (oct_rep1 + cmp_rep1) and all_rep2 (oct_rep1 + cmp_rep2),
  and runs score, decision_reality, drift and decision_viz once on all 22. Every table and figure covers all 22, with one
  by-subset table. Figures handle the new situations: an escalated decision is drawn as "escalated to a person, no
  automatic forecast" (drift.py and decision_viz.py no longer forecast it with the model), maps stay on the drifter and
  say how many particles end off the map and how far, the per-case bar chart caps very large misses and labels them,
  marks escalations with E and shades the harder cases. Plumbing tested with made-up keys; real-drifter numbers on all
  22 cases (no key needed): agent 16.37 km (21 forecast + 1 escalated), baseline 23.93 km, No QC 21.24, model only
  26.22, perfect repair 16.02, no-motion 15.02.


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
