# Pipeline

How to run everything, step by step. Each step lists its command, what it reads and what it writes. Terms are
defined in the [README](../README.md#key-terms).

```
messy radar → decision → forcing → OpenDrift → POC map → score vs answer key + score vs real drifter
 (cases)     (baseline    (reader     (24 h)                (score.py)          (drifter_eval.py,
             or agent)    priority)                                             decision_reality.py)
```

## 0. Setup (once per machine)
```bash
uv venv --python 3.12 .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt
python -m unittest tests.test_forcing_tools tests.test_baseline tests.test_score tests.test_drift tests.test_drifter_eval
```
Activate the venv in every new terminal: the agent's `python` is whatever is first on PATH.

## The one command
```bash
python src/run_demo.py --replay              # all 22 October cases as one test, ~20 min
python src/run_demo.py --replay --august     # plus an appendix with the August 2021 benchmark
```
No Claude calls, no network, no API key. Three stages:
- **A. Each set separately** (single-fault: 14 cases, seed 1018; harder: 8 cases, seed 2026): regenerate the cases and
  check them against the repo and the key; rerun the baseline and check it against its committed decisions; replay
  every saved agent run (repaired files rebuilt with the frozen tool and checked).
- **B. Merge into one 22-case set:** `runs/cases_all/` (case01-14 single-fault, case15-22 harder; `mapping.json`), one
  key (`~/sar_heldout/labels_all.json`), merged baseline decisions, and merged agent runs `all_rep1` = oct_rep1 +
  cmp_rep1 and `all_rep2` = oct_rep1 + cmp_rep2. Regenerated every run; not in git.
- **C. Score and draw all 22 at once:** `score.py`, `decision_reality.py`, `drift.py`, `decision_viz.py` on the merged
  set (results in `runs/results/*_all/`, figures `*_all*`), plus the clean-data reality check.

Writes `demo_results/lab3_demo.md` (every table and figure, on all 22 cases, plus one by-subset table),
`demo_results/lab3_figures/` and `demo_results/lab3_run_log.txt`, with a PASS/FAIL table of reproducibility checks at
the top. The per-set commands below remain for running a set on its own.

## Data
Everything the demo needs is committed in `fixtures/`. The scripts below only show where it came from; **people run
downloads, the agent never does** (they are denied in `.claude/settings.json`). Sources and dates: [data_sources.md](data_sources.md).
```bash
python scripts/list_ncei.py --month 202110 --save data/ncei_listing_202110.txt
python src/data/pull_hfr_ncei.py --listing data/ncei_listing_202110.txt --start "2021-10-18 00:00" \
    --end "2021-10-28 23:00" --box 33.8 35.8 -122 -119.8 --raw-dir data/hfr_raw_202110 --out data/hfr_20211018.nc
python src/data/pull_fixtures.py --start "2021-10-18 00:00" --end "2021-10-29 00:00" --box 33 36.5 -123 -119 \
    --out data/hycom_20211018.nc                                   # model box larger than the radar box
python scripts/find_drifters.py --start 2018-01-01T00:00:00Z --end 2025-01-01T00:00:00Z --box 32 38.5 -124.5 -117
python scripts/drifter_coverage.py --radar data/hfr_20211018.nc --drifters data/drifters/<csv>   # offline
```
With no options, the pull scripts rebuild the August fixtures. For another region or dates see [new_data.md](new_data.md).

## Steps (October)
Shorthand used below: `R=fixtures/hfr_20211018.nc  M=fixtures/hycom_20211018.nc  D=fixtures/drifter_300534061399950_202110.csv`

| # | Step | Command | Reads | Writes |
|---|---|---|---|---|
| 1 | Plant faults | `python src/inject_faults.py --radar $R --model $M --cases runs/cases_oct --labels ~/sar_heldout/labels_oct.json --seed 1018` | clean radar, model | 14 cases; the answer key **outside the repo** |
| 2 | Baseline decides | `python src/baseline.py --cases-dir runs/cases_oct --results runs/results/baseline_oct --work runs/work/baseline_oct` | cases | one decision JSON per case; repaired files |
| 3 | Agent decides | `python scripts/run_agent_batch.py --run-id oct_rep1 --cases-dir runs/cases_oct` (~$2.60, ~15 min) | cases (in an isolated copy) | `runs/results/agent/oct_rep1/`, transcripts, `eval/batches/oct_rep1.json` |
| 4 | Score vs key | `python src/score.py --labels ~/sar_heldout/labels_oct.json --agent-runs oct_rep1 --baseline runs/results/baseline_oct --baseline-work runs/work/baseline_oct --cases-dir runs/cases_oct --out runs/results/scores_oct` | decisions, key, clean radar | `runs/results/scores_oct/` (`cases.csv` reveals the key) |
| 5 | Drift vs clean run | `python src/drift.py evaluate --cases-dir runs/cases_oct --clean $R --model $M --agent-runs oct_rep1 --baseline runs/results/baseline_oct --baseline-work runs/work/baseline_oct --out runs/results/drift_oct --figure case14 case06 --fig-prefix drift_oct_` | decisions, cases | `runs/results/drift_oct/`, `drift_oct_case*.png` |
| 6 | Clean forecasts vs drifter | `python src/drifter_eval.py --radar $R --model $M --drifters $D` then `python src/drifter_viz.py --radar $R --model $M --drifters $D` | clean radar, model, drifter | `runs/results/drifter/<id>/`, drifter summary PNG and GIFs |
| 7 | Decisions vs drifter | `python src/decision_reality.py --agent-runs oct_rep1 --clean $R --model $M --drifters $D` | decisions, cases, drifter | `runs/results/reality_oct/`, `decisions_vs_reality_oct.png` |
| 8 | Baseline vs agent pictures | `python src/decision_viz.py --agent-run oct_rep1 --clean $R --model $M --drifters $D` | the above | `baseline_vs_agent_oct.png`, `decision_summary_oct.png`, `decision_oct_case*.gif` |

Figures go to `docs/figures/`; `run_demo` copies them into `demo_results/lab3_figures/`. Steps 1, 3 and 4 touch the
answer key or call Claude, so people run them; `run_demo` reruns 1 and 4 itself and replays 3.

### What each step does
1. **Plant faults.** Copies the clean radar 14 times and plants one fault in 10 of the copies (scaling, time shift,
   sign flip, swapped components, blanked hours, a dropout, fill values). Seeded, so it is identical on every machine.
2. **Baseline.** Runs `check`; if it passes, `fused`. If not, the first failing test picks one repair from the menu;
   re-check, at most two repairs; otherwise `model_only`.
3. **Agent.** For each case, `claude -p` runs inside `~/sar_eval/<run-id>/`, a folder holding only the agent's
   instructions, permissions, memory, thresholds, its tool and the cases. It reads `SKILL.md`, runs the tool, decides
   and writes a decision JSON. Results are copied back; the batch stops if a needed tool is denied.
4. **Score vs key.** Counts acceptable choices, silent errors, radar recovered and faithful repairs for every policy.
5. **Drift vs clean run.** For each decision, 1,000 particles from 5 points drift 24 h; distance to the same particles
   in a run on the clean radar shows what the decision did to the forecast.
6. **Clean forecasts vs drifter.** 20 forecasts of 24 h, each released at the real drifter's position: model only vs
   radar + model; miss, skill, and whether the drifter falls inside the 50% / 90% areas.
7. **Decisions vs drifter.** The same 20 forecasts for every policy's decision on every case, plus perfect repair and
   a no-motion guess as references.
8. **Pictures.** Side-by-side decisions, per-case outcomes, and animations of one forecast per policy.

## August 2021 benchmark (the first benchmark; no drifter)
Same steps on the August fixtures, with defaults: `python src/inject_faults.py` (seed 295; move the key from
`heldout/` to `~/sar_heldout/labels.json`), `python src/baseline.py`, `python scripts/run_agent_batch.py --run-id rep1`
(and `rep2`), `python src/score.py`, `python src/drift.py evaluate --figure case10`. Or `run_demo.py --replay --august`.

## Harder cases (compound; where a rule table can fail)
8 cases from the same October radar: 2 clean, 3 with two radar faults at once, 2 rotated radar fields, and 1 with a
faulty **model** (clean radar; the only acceptable choice is `escalate`). `run_demo` reruns this part too (report section 8).
```bash
python src/inject_faults.py --plan compound --radar $R --model $M --cases runs/cases_compound \
    --labels ~/sar_heldout/labels_compound.json --seed 2026
python src/baseline.py --cases-dir runs/cases_compound --results runs/results/baseline_cmp --work runs/work/baseline_cmp
python scripts/run_agent_batch.py --run-id cmp_rep1 --cases-dir runs/cases_compound            # ~$2, ~10 min
python src/score.py --labels ~/sar_heldout/labels_compound.json --agent-runs cmp_rep1 --baseline runs/results/baseline_cmp \
    --baseline-work runs/work/baseline_cmp --cases-dir runs/cases_compound --clean $R --out runs/results/scores_cmp
python src/decision_reality.py --cases-dir runs/cases_compound --baseline runs/results/baseline_cmp \
    --baseline-work runs/work/baseline_cmp --agent-runs cmp_rep1 --clean $R --model $M --drifters $D \
    --out runs/results/reality_cmp --fig-name decisions_vs_reality_cmp.png
```
`decision_reality.py` uses each case's own `model.nc` (it may be faulty) and records `escalate` as handed to a
person, with no automatic forecast.

## Lab 4 (branch claude/lab4; October 2021 window, one drifter)
Status, findings and what is left: `docs/lab4_handoff.md`. Wind data is not in git:

| # | Step | Command | Writes |
|---|---|---|---|
| L1 | ERA5 wind + Stokes (people run it; CDS key in `~/.cdsapirc`) | `python src/data/pull_era5.py --stokes` | `data/era5_wind_20211018.nc`, `data/era5_stokes_20211018.nc` |
| L2 | Phase A: tune wind/Stokes/spread on early windows, score on late ones (~15 min) | `python src/forecast_eval.py` | `runs/results/forecast/` |
| L3 | Blend case (tuning part of the drifter only) | `python src/make_blend_case.py --case-id oct21 --out runs/blend_cases/oct21` | `runs/blend_cases/oct21/` |
| L4 | Try-everything blend baseline (~25 min) | `python src/blend_baseline.py --case runs/blend_cases/oct21 --out runs/results/blend/baseline` | `decision.json`, `ranking.json` |
| L5 | Blend agent (Claude; see below) | `python scripts/run_blend_agent.py --run-id blend_rep1` | `runs/results/blend/agent/blend_rep1/decision.json`, transcript |
| L6 | Score decisions on the test windows (~3 min per setup) | `python src/blend_score.py --decision baseline=runs/results/blend/baseline/decision.json --decision agent=<decision.json> --out runs/results/blend/<new folder>` | `summary.json`, `test.csv`, `spread.csv` |
| L7 | Forcing ensemble, spread calibrated on tuning windows (~25 min) | `python src/ensemble_eval.py` | `runs/results/ensemble/` |
| L8 | Phase C: one search period, 3 forecasts x 2 planners x 1/3 assets x 12/24 h (~15 min; needs L6 and L7 summaries) | `python src/search_eval.py` | `runs/results/search/` |
| L9 | Phase D data stream: 24 radar deliveries (6 scenarios x 4 periods, mid-stream faults) | `python src/make_loop_cases.py` | `runs/cases_loop/` (+ `mapping.json`, no key) |
| L10 | Scripted decisions on the deliveries (~1.5 min) | `python src/baseline.py --cases-dir runs/cases_loop --results runs/results/baseline_loop --work runs/work/baseline_loop` | `runs/results/baseline_loop/` |
| L11 | Agent decisions on the deliveries (~$5, ~25 min) | `python scripts/run_agent_batch.py --run-id loop_rep1 --cases-dir runs/cases_loop` | `runs/results/agent/loop_rep1/` |
| L12 | Phase D loop: all policies, 4 periods each (~1 min) | `python src/sar_loop.py --agent-runs loop_rep1` | `runs/results/loop/` |

One blend by hand: `python src/blend.py --radar $R --model $M --out runs/work/blend/x.nc --spec '{"method": "edge_taper", "taper_km": 10}'`.
Splitting rule: tuning windows end by `--split` (default 2021-10-23 12:00 UTC), test windows start at or after it.

## Rules that keep the evaluation honest
- **Keys stay outside the repo** (`~/sar_heldout/`). The agent cannot read them; `run_demo` and the workspace builder
  refuse to run if `heldout/` holds anything.
- **Commit before an agent run.** The workspace builder refuses uncommitted changes to the files it copies, so every
  run is tied to a commit; the receipt is in `eval/workspaces/<run-id>.json`.
- **Frozen pieces:** `forcing_tools.py` (SHA in LOG.md), `configs/thresholds.yaml`, and `baseline.py` (committed before
  any agent result was scored).
- **One run id, one run.** Folders are never overwritten; pick a new `--run-id`.
- **Blend runs never see the drifter's test period.** The blend case holds only positions up to the split, the tool
  refuses a case that runs past it, and only `blend_score.py` reads the full track.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Skill or `forcing_tools.py` denied in an agent run | the batch passes `--settings .claude/settings.json`; add it if running `claude -p` by hand |
| "allowlisted files have uncommitted changes" | commit, then rerun |
| "heldout/ ... is not empty" | move the key to `~/sar_heldout/` |
| "already exists; not overwriting" | use a new run id or output folder |
| "`python` is not the repository's .venv" | `source .venv/bin/activate` |
| `mkdir ... && python ...` denied in a transcript | harmless; the agent retries without `mkdir` |
