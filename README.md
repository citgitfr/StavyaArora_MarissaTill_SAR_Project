# Fixing the forcing: an AI agent for search-and-rescue drift data

AM/ME/CE 295 (Agentic AI in Scientific Discovery), Caltech, Fall 2026 · Stavya Arora & Marissa Till ·
<https://github.com/citgitfr/StavyaArora_MarissaTill_SAR_Project>

**Status: Lab Report 3 (2026-11-15).** The full loop runs on real data: faulty radar data → decision (Claude agent or
scripted baseline) → drift forecast → probability map → scores against an answer key and a real drifter.
Results: [demo_results/lab3_demo.md](demo_results/lab3_demo.md) · Commands: [docs/pipeline.md](docs/pipeline.md)

## Quick start
```bash
uv venv --python 3.12 .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt
python -m unittest tests.test_forcing_tools tests.test_baseline tests.test_score tests.test_drift tests.test_drifter_eval
python src/run_demo.py --replay                               # ~13 min, then open demo_results/lab3_demo.md
```
No downloads, no API key, no Claude calls: the data is in `fixtures/` and the agent's decisions are replayed from
saved runs. The `.venv` is not in git (it is machine-specific); the two lines above build it.

## Proposal

**Problem.** Search-and-rescue (SAR) teams forecast where a person or boat in the water will drift, using ocean
currents from a model (HYCOM) and, near the coast, measured currents from HF radar. Radar data often arrive with
faults: wrong units, time offsets, flipped or swapped components, fill values, gaps. A fault that slips through moves
the forecast, and the search goes to the wrong place. Reviews of SAR forecasting also find that better forecasts are
rarely tested on whether they lead to better searches.

**Approach.** A Claude agent inspects each radar file next to the model with fixed, deterministic tools, diagnoses
any fault, applies at most two repairs from a fixed menu, and chooses one of: use the radar (`fused`), `repair` it,
use the model only (`model_only`), or `escalate` to a person. Every number comes from the tools; the agent's job is
the judgment. The chosen currents drive an OpenDrift simulation and a probability-of-containment (POC) map.

**Evaluation.** All scoring is LLM-free. (1) Against an answer key: faults are planted on purpose, so we know the
right choices. (2) Against reality: forecasts are compared with the track of a real ocean drifter. The agent is
compared with a scripted rule table that uses the same tools (the baseline) and with simple policies (always trust the
radar, always ignore it). Success means: no silent errors, more usable radar recovered than the baseline, at a stated
cost; and forecasts that get closer to real drifters.

**Plan.** Lab 3: the loop on real data with a reality check (done). Lab 4: wind and the Leeway model (for real SAR
objects), forecast ensembles, harder and mid-stream faults, more drifters, and a blend menu the agent can test
hypotheses with. Final: the multi-period SAR loop (search, update probabilities, re-forecast, re-plan) with a search
planner, comparing an IAMSAR-style plan with a highest-probability plan.

### Current project state: Lab 3
- **The loop works end to end on real data:** faulty radar → decision → OpenDrift → POC map → scores against the key
  and a real drifter. Search planners and the 2×2 experiment are not built yet.
- **On clear-cut faults the agent ties a rule table; on harder ones it does better.** On 14 single-fault cases it
  matches the scripted baseline on every case (14/14, no silent errors). On 8 harder cases (two faults at once,
  rotated fields, a faulty model) it scores 8/8 against the baseline's 7/8: it escalated a broken model file that the
  baseline turned into a 180 km forecast error, and it recovered a rotated radar field the baseline threw away. Two
  independent agent runs made the same choice on all 8 cases; each costs about $2.
- **The forecast itself is the bigger limit.** Radar helps, but with no wind even the best forecast misses a real
  drifter by about 16 km in 24 h, slightly more than guessing the object did not move. Wind comes before planners.

### Project status
```
 [1] data pull ──► [2] fault injector ──► [3] decision ─────────────► [5] blend ──► [6] OpenDrift ──► [7] score
  (NOAA, HYCOM)     (cases + key)          agent [3a] | baseline [3b]   (reader      (24 h, POC map)   vs key [7a]
                                            using tools [4]               priority)                     vs drifter [7b]
 built: 1-7, 10 (first version)   planned (Lab 4): 8 wind + Leeway, 9 ensembles, 10 mid-stream faults,
                                  11 blend menu + investigator agent
                 proposed (final): 12 search planner, 13 multi-period SAR loop
```
| # | Module | Status | Input | Output |
|---|---|---|---|---|
| 1 | Data pull (`src/data/`, `scripts/find_drifters.py`) | built | NOAA HF-radar archive, HYCOM server, drifter server | `fixtures/*.nc`, drifter CSV |
| 2 | Fault injector (`src/inject_faults.py`) | built | clean radar + model | 14 single-fault cases (or 8 harder ones with `--plan compound`) + answer key |
| 3a | Agent (Claude + `SKILL.md`, `scripts/run_agent_batch.py`) | built | one case | decision JSON (choice, repair, evidence, reason), repaired radar, transcript |
| 3b | Scripted baseline (`src/baseline.py`) | built | one case | decision JSON, repaired radar |
| 4 | Forcing tools (`forcing_tools.py`: qc, compare, repair, check) | built, frozen | radar + model files | JSON numbers and pass/fail tests |
| 5 | Blend (OpenDrift reader priority) | built (simple) | decision + files | radar where it has data, model elsewhere |
| 6 | Drift (`src/drift.py`, OpenDrift OceanDrift) | built | forcing, release points | particle tracks, POC map |
| 7a | Score vs key (`src/score.py`) | built | decisions + key | acceptable, silent errors, radar recovered, faithful |
| 7b | Score vs real drifter (`src/drifter_eval.py`, `src/decision_reality.py`) | built | tracks + drifter | miss (km), skill, 50%/90% containment |
| – | One command (`src/run_demo.py`) | built | all of the above | `demo_results/lab3_demo.md` |
| 8 | Wind + Leeway model | planned (Lab 4) | wind data | drift for real SAR objects |
| 9 | Forecast ensembles | planned (Lab 4) | perturbed forcing | calibrated POC maps |
| 10 | Harder faults | built: compound (two faults, rotations, faulty model); planned (Lab 4): mid-stream | clean radar + model | harder cases where a rule table can fail |
| 11 | Blend menu + investigator agent | planned (Lab 4) | drift errors vs drifters | chosen blend (edge taper, hourly trust, ...) |
| 12 | Search planner | proposed (final) | POC map, search assets | search plan, probability of success |
| 13 | Multi-period SAR loop | proposed (final) | plan, unsuccessful search, new data | updated map and plan per period |

### Headline results (real October 2021 window; full tables in the demo report)
**All 22 October cases in one test** (14 single-fault + 8 harder; scored against the answer keys and a real drifter):

| Policy | Acceptable | Silent errors | Radar recovered | Escalations | Mean 24 h miss vs drifter | Worst case vs perfect repair | Cost |
|---|---|---|---|---|---|---|---|
| No QC (radar as delivered) | 8/22 | 14 | 8/20 | 0 | 21.2 km | +52 km | $0 |
| Model only (radar ignored) | 21/22 | 0 | 0/20 | 0 | 26.2 km | +164 km | $0 |
| Scripted baseline | 21/22 | 0 | 17/20 | 0 | 23.9 km | +164 km | $0 |
| **Claude agent** (2 runs on the harder cases, identical) | **22/22** | **0** | **18/20** | 1 (correct) | **16.4 km** (21 forecast; 1 escalated) | **+3 km** | ~$4.60 |
| Perfect repair (reference) | — | — | — | — | 16.0 km | 0 | — |

The two subsets:

**Single-fault cases** (14; 4 clean, 10 with one planted fault):

| Policy | Decisions vs the key | Mean 24 h miss vs real drifter |
|---|---|---|
| No QC (use the radar as delivered) | 6/14 acceptable, 8 silent errors | 19.8 km (59 km on the worst case) |
| Model only (ignore the radar) | 14/14 acceptable, uses no radar | 18.9 km |
| Scripted baseline | 14/14, 0 silent errors | 16.3 km |
| Claude agent | 14/14, 0 silent errors, same choices as the baseline | 16.3 km |
| Perfect repair (reference) | — | 16.0 km |
| No-motion guess (reference) | — | 15.0 km |

**Harder cases** (8; two faults at once, rotated fields, or a faulty model with clean radar):

| Policy | Decisions vs the key | Radar recovered | Mean 24 h miss vs real drifter |
|---|---|---|---|
| No QC | 2/8 acceptable, 6 silent errors | 2/7 | 23.7 km |
| Model only | 7/8 (fails on the faulty model) | 0/7 | 39.0 km (180 km on the faulty model) |
| Scripted baseline | 7/8, 0 silent errors | 5/7 | 37.2 km (180 km on the faulty model) |
| Claude agent (2 runs, identical) | **8/8**, 0 silent errors, 1 escalation | **6/7** | **16.4 km** on the 7 cases it forecast; escalated the faulty model |
| Perfect repair (reference) | — | — | 16.0 km |

Clean data only: radar + model beats model only against the drifter in 72% of forecasts (skill 0.22 vs 0.07), but
the forecast's 90% area contained the drifter in at most 5% of forecasts (the maps are overconfident).

## Key terms
| Term | Meaning |
|---|---|
| HF radar | Shore-based radar that measures surface currents (top ~1 m), patchy in space and time |
| HYCOM | A global ocean model; currents everywhere, but a model |
| Forcing | The current field that drives a drift simulation |
| OpenDrift | Open-source particle drift simulator; we use its OceanDrift model (currents only) |
| Reader priority | How we combine radar and model: for each particle and hour, use radar where it has data, else the model |
| POC map | Probability-of-containment map: where the object is likely to be, from many simulated particles |
| Case | One radar file plus the model file; 14 per benchmark (4 clean, 10 with a planted fault) |
| Answer key | The hidden list of which fault is in which case and which choices are acceptable; kept outside the repository so the agent can never read it |
| Policy | A way of deciding: the agent, the baseline, or a simple rule |
| Scripted baseline | A fixed rule table using the same tools as the agent; no AI |
| No QC | Always use the radar as delivered, faults included |
| Model only | Always ignore the radar |
| Perfect repair | The clean radar before faults were planted: the best any repair could do |
| Silent error | Using radar that should not have been used (a fault let through) |
| Radar recovered | Using usable radar correctly, out of the cases where that was possible |
| Faithful | The radar handed on equals the clean original (catches a repair that passes checks but is wrong) |
| Drifter | A GPS-tracked float from the Global Drifter Program; our ground truth. "Drogue lost" means it follows the top meter, like HF radar |
| Skill | Liu & Weisberg (2011) trajectory score: 1 = perfect, 0 = no better than not moving |
| No-motion guess | A reference forecast that says the object stays where it was reported |
| Replay | Re-using the agent's saved decisions instead of calling Claude again |

## How the agent is set up
The repository is the agent. `CLAUDE.md` holds its instructions, `.claude/skills/forcing-tools/` its playbook and its
one tool script, `memory/` what earlier agents learned, and `PERMISSIONS.md` + `.claude/settings.json` what it may do
(run only its tool script, write only under `runs/`, no network, never read the answer key). Evaluation runs happen in
an isolated copy of the repo outside the project folder that contains only those files and the cases, so the agent
cannot see fault plans, results, clean data or keys.

## Repository layout
```
StavyaArora_MarissaTill_SAR_Project/
├── README.md                     
├── CLAUDE.md                     agent instructions, loaded every session
├── PERMISSIONS.md                what the agent may and may not do, and how each rule is enforced
├── LOG.md                        dated work log, including failures
├── AI_USE.md                     disclosure of AI assistance
├── requirements.txt              pinned Python dependencies (OpenDrift 1.14.12, xarray, ...)
├── .gitignore                    ignores data/, heldout/, .venv, *.nc (except fixtures), caches
│
├── .claude/                      ── the agent's permissions and skill
│   ├── settings.json             enforced permissions (allow / ask / deny), pinned model
│   ├── skills/forcing-tools/
│   │   ├── SKILL.md              the agent's playbook: commands, repair menu, decision rules
│   │   └── forcing_tools.py      the agent's only tool: qc, compare, repair, check (frozen)
│   └── commands/litreview.md     /litreview command from the Lab 1 literature review
├── memory/
│   ├── MEMORY.md                 facts and decisions the agent writes for future agents
│   └── USER.md                   who the agent works for
├── configs/
│   └── thresholds.yaml           frozen pass/fail limits for `check` (set from clean data, 2026-10-29)
│
├── fixtures/                     ── committed input data (the demo needs nothing else)
│   ├── hfr_2km_slice.nc          HF radar, Southern California, 2021-08-10..11 (August benchmark)
│   ├── hycom_uv_slice.nc         HYCOM currents, same box and dates
│   ├── hfr_20211018.nc           HF radar off Point Arguello, 2021-10-18..28 (October, real-drifter window)
│   ├── hycom_20211018.nc         HYCOM currents, larger box, same dates
│   └── drifter_300534061399950_202110.csv   the real drifter's hourly track (ground truth)
│
├── src/                          ── the pipeline
│   ├── inject_faults.py          plants seeded faults in a clean radar file → cases + answer key
│   ├── baseline.py               scripted baseline: rule table, same tools, no AI
│   ├── score.py                  decisions vs the answer key
│   ├── drift.py                  OpenDrift runs per decision vs a clean-radar run; POC maps
│   ├── drifter_eval.py           clean forecasts vs the real drifter (miss, skill, containment)
│   ├── drifter_viz.py            figures and animations for drifter_eval
│   ├── decision_reality.py       every policy's decision vs the real drifter
│   ├── decision_viz.py           No QC vs baseline vs agent figures and animations
│   ├── plot_style.py             shared names and colours for every figure
│   ├── run_demo.py               one command: reruns and checks everything, writes demo_results/
│   └── data/
│       ├── pull_hfr_ncei.py      downloads HF radar (people run it)
│       ├── pull_fixtures.py      downloads HYCOM (people run it)
│       └── look.py               first look at the August data
├── scripts/                      ── helpers
│   ├── run_agent_batch.py        runs the agent on every case (one call = one repeat)
│   ├── make_eval_workspace.py    builds the isolated folder the agent runs in
│   ├── find_drifters.py          finds drifter tracks on NOAA's server (people run it)
│   ├── drifter_coverage.py       checks radar coverage along a drifter track (offline)
│   ├── list_ncei.py              lists one month of the HF-radar archive (people run it)
│   └── cov_check.py              radar coverage check used when setting thresholds
├── tests/                        unit tests: forcing_tools, baseline, score, drift, drifter_eval
│
├── runs/                         ── everything the pipeline produces
│   ├── cases/  cases_oct/  cases_compound/   generated cases (August, October, harder); regenerated by run_demo
│   ├── cases_all/                one 22-case October set (links + mapping.json), built by run_demo; not in git
│   ├── results/
│   │   ├── baseline/  baseline_oct/  baseline_cmp/   baseline decisions
│   │   ├── agent/                         agent decisions: rep1, rep2, oct_rep1, cmp_rep1 (smoke, trial = practice)
│   │   ├── scores/  scores_oct/  scores_cmp/  scores_all/   scores vs the answer key (scores_all = all 22)
│   │   ├── drift/  drift_oct/  drift_all/ forecast impact vs a clean-radar run
│   │   ├── drifter/                       clean forecasts vs the real drifter
│   │   └── reality_oct/  reality_cmp/  reality_all/   every decision vs the real drifter
│   └── work/                     repaired radar files and repair logs per policy and case; drift run cache
├── eval/                         ── records of agent runs
│   ├── batches/                  per-run log: turns, cost, time, denials
│   ├── transcripts/              full agent transcripts per run and case
│   ├── workspaces/               receipt for each isolated workspace (commit, file hashes)
│   └── transcript_perm_commit.jsonl   the Lab 2 permission test
├── docs/
│   ├── pipeline.md               every step: command, input, output
│   ├── new_data.md               what to change for another region or dates
│   ├── data_sources.md           where every dataset came from, with access dates
│   ├── SAR_context.md            how a real SAR case runs (IAMSAR stages)
│   ├── related_work.md  papers/  lit_notes/   literature review
│   ├── dev_notes.md              the original build spec for the tools
│   └── figures/                  every figure and animation
├── demo_results/                 written by run_demo: lab3_demo.md (results report), lab3_figures/, lab3_run_log.txt
├── data/                         raw downloads (not in git)
└── heldout/                      must stay empty; answer keys live outside the repository
```
