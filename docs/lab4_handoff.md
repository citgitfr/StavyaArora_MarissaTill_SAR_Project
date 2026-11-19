# Lab 4 handoff (2026-11-18)

Branch: `claude/lab4` (from `main` at 00745c6). Nothing is merged into `main`; the owners merge.
Every change below was made with Claude Code in one session and reviewed by Stavya as it went.

## 1. Plan we agreed on
Work on the October 2021 window and its one real drifter (300534061399950) first; add more drifters later.

| Phase | Goal | Status |
|---|---|---|
| A | Add wind (and wave Stokes drift) to the forecast; make the 50% / 90% areas honest | done |
| B | Blend menu for radar + model, investigator agent vs a try-everything baseline | tools, baseline, scorer and skill done; **agent not run yet** |
| C | Search planner, one period (IAMSAR-style vs highest-probability), scored against the drifter | not started |
| D | Multi-period loop: search, update probabilities, new data, re-forecast, re-plan; agent vs scripted loop | not started |
| E | More drifters (November 2021 looks best), re-run A-D | not started |

## 2. What was built (commits on `claude/lab4`, oldest first)
| Commit | What |
|---|---|
| 132f44e | `src/data/pull_era5.py` (ERA5 hourly 10 m wind + optional Stokes drift from the Copernicus CDS) + `tests/test_pull_era5.py` |
| 09865ee | ERA5 source, licence and citation in `docs/data_sources.md` |
| 0181882 | `src/forecast_eval.py` (Phase A tuning/test with wind, Stokes, spread) + tests + results in `runs/results/forecast/` |
| a87bc18 | `src/blend.py`: fixed blend menu that writes ONE gap-free current file + `tests/test_blend.py` |
| c89ed4b | `src/blend_tools.py` (the agent's tool), `src/make_blend_case.py` (hides the drifter's test period) + tests |
| 7ee01fb | `src/blend_baseline.py` (try-everything), `src/blend_score.py` (test-half scorer) + tests + results in `runs/results/blend/` |
| 3e289b9 | `.claude/skills/blend-tools/SKILL.md`, `.claude/settings.json` + `PERMISSIONS.md` allow the blend tool, `scripts/make_eval_workspace.py --blend-case` |
| fccdbaa | `memory/MEMORY.md`: one agent, two skills; OpenDrift fills radar holes from neighbours |

All new tests pass (49 when last run together, plus 3 in `tests/test_blend_baseline_score.py`):
```bash
python -m unittest tests.test_pull_era5 tests.test_forecast_eval tests.test_blend tests.test_blend_tools tests.test_blend_baseline_score
```
Tests that need the ERA5 files skip themselves if `data/era5_*.nc` is missing.

## 3. Data you need locally (not in git)
- `data/` from the partner's Desktop folder: `hfr_20211018.nc`, `hycom_20211018.nc` (identical to `fixtures/`),
  `ncei_listing_202110.txt`, `drifters/gdp_hourly_2018-01-01_2025-01-01.csv` (38 drifters off California, 2018-2024).
- ERA5, pulled 2026-11-18: `data/era5_wind_20211018.nc`, `data/era5_stokes_20211018.nc`. To rebuild: free CDS account,
  accept the ERA5 licence on the dataset's Download tab, key in `~/.cdsapirc` (never in the repo), `uv pip install cdsapi`,
  then `python src/data/pull_era5.py --stokes`. Or copy the two files from Stavya's laptop.
- `runs/blend_cases/oct21/*.nc` are copies of the four forcing files (gitignored); rebuild with
  `python src/make_blend_case.py --case-id oct21 --out runs/blend_cases/oct21` (refuses if the folder exists).

## 4. What we found
All numbers are from the scripts' outputs; one drifter, so this is early evidence, not a general result.

**Setup.** 24 h forecasts start every 6 h along the drifter track (Oct 18-28). Tuning windows = those ending by
Oct 23 12:00 (19); test windows = those starting at or after it (18). No drifter hour is in both.

**Finding 1: the 50%/90% containment measure from Lab 3 breaks for wide forecasts.** It counted 500 particles on a
0.01 deg grid; for a wide cloud most cells are empty, so hits are undercounted and the area caps near 500 cells. On a
test Gaussian cloud (10 km spread) it gave a 90% area of 372 km2 vs the true 1447 km2. Replaced (for Lab 4 code) by a
kernel density estimate (`forecast_eval.hdr_kde`), which matches the exact answer in a unit test. Lab 3's clouds were
about 1 km wide, where both methods agree, so Lab 3's "the maps are overconfident" still holds.

**Finding 2: "radar + model" was not radar-else-model.** OpenDrift's default interpolation fills radar holes by
repeatedly copying the largest neighbouring radar value (up to 10 times). Along the drifter (Lab 3 setup, 50 particles,
every 3 h): 56% of particle-hours got real radar, 40% got radar stretched into a hole (median 4.7 km, max 30 km from
real radar), 3% got HYCOM. `src/blend.py` fixes this: with a blend file, 57% radar / 43% model, and OpenDrift's current
matches the file to 0.0002 m/s. **Lab 3 wording ("radar where it has data, else the model") needs revisiting; we
agreed to leave Lab 3 alone for now.**

**Finding 3: wind barely matters for this drifter; Stokes drift hurts.** Tuned on the early windows (K = 10):
model only is best with no wind (mean 24 h miss 21.6 km; 3% windage gives 24.8 km); radar + model is best at 1%
windage, 0.1 km better than none. Every setup with Stokes drift was worse. Raw data: over the 11 days the drifter
moved north (about 9 cm/s) while HYCOM's current at the drifter pointed south (about 7 cm/s) and the wind blew
toward the southeast. The likely cause is a HYCOM current error here; not proven (one drifter; drogue status is from
the GDP file).

**Finding 4: honest search areas are very large, and random-walk spread is the wrong shape.** With the KDE measure,
hitting 50%/90% on the tuning windows needs diffusivity K = 1600-3200 m2/s, i.e. 90% areas of 3,500-7,500 km2. The
tuned areas then contain the drifter 100% of the time on the test windows (too wide), and K for radar + model hit the
top of the grid. Random-walk spread grows like sqrt(time) while our error is a steady drift, so it cannot match all
horizons. Better: ensembles of forcing (blends, windage), planned.

**Finding 5: what is best in the first five days is not best in the next five.** Try-everything baseline (50
evaluations on the tuning windows) picked **model only, no wind** (21.6 km); every step toward more radar was worse
(25% radar 23.1 km ... full radar 25.9 km). On the 18 test windows the ranking flips:

| Setup (spread calibrated on tuning windows) | K | median 24 h | mean 24 h | skill | in50 | in90 | 90% area km2 |
|---|---|---|---|---|---|---|---|
| model only (= baseline's pick) | 1600 | 15.3 | 15.4 | 0.19 | 61% | 100% | 3568 |
| hard_switch blend | 3200 | 12.5 | 13.2 | 0.25 | 100% | 100% | 7573 |
| reader priority (Lab 3, stretched radar) | 3200 | 11.4 | 11.8 | 0.28 | 100% | 100% | 7616 |
| no motion | - | 12.6 | 12.2 | 0.25 | - | - | - |

`blend_tools diagnose` on the tuning windows shows why: forecasts that spent most time on radar missed by 30.3 km vs
19.7 km off radar; Oct 19-20 radar windows missed by 30-41 km in near-calm wind; on Oct 18 the drifter ended about
21 km upwind of every forecast in 7-9 m/s northwest wind. The conditions changed between halves (light winds Oct 19-22,
storm Oct 24-25). **Takeaway: one fixed choice per period is the wrong kind of decision here; deciding when to trust
the radar as conditions change (Phase D) is where an agent could add value.** Needs more drifters to generalize.

**Also note:** these are hindcasts (radar, HYCOM and ERA5 describe the forecast period itself), the standard way to
test against drifters but a best case.

## 5. How the Phase B pieces fit
- `src/blend.py --radar --model --out --spec '{"method": ...}'`: menu = `hard_switch`, `edge_taper` (taper_km 2-50),
  `weighted` (radar_weight 0-1), plus `gap_fill_hours` 0-6, `trust_min_coverage`, `trust_max_rms`. Refuses unless the
  frozen `check` passes; never overwrites.
- `src/make_blend_case.py`: case folder with the forcing files and ONLY the tuning part of the drifter.
- `src/blend_tools.py`: the agent's tool. `menu`, `build`, `evaluate` (budget 12 per run folder; identical evals free),
  `diagnose`, `list`. Refuses a case whose drifter file runs past the split.
- `src/blend_baseline.py --case runs/blend_cases/oct21 --out runs/results/blend/baseline` (about 25 min).
- `src/blend_score.py --decision name=path ... --out <dir>`: calibrates spread on tuning windows by a fixed rule,
  scores on test windows, adds references (model only, hard_switch, Lab 3 reader priority, no motion). About 3 min per
  setup. Refuses to overwrite `summary.json`, so score the agent into a new `--out` (or re-run with all decisions).
- Agent decision format: see `.claude/skills/blend-tools/SKILL.md` (currents, windage, stokes, evaluations_used,
  hypotheses, evidence, reason, menu_gap).

## 6. What's left, in order
1. **Run the blend agent once on oct21.** Needs a small runner like `scripts/run_agent_batch.py`:
   `python scripts/make_eval_workspace.py --run-id blend_rep1 --blend-case runs/blend_cases/oct21`, then inside the
   printed folder run `claude -p "<prompt>" --permission-mode dontAsk --settings .claude/settings.json
   --disallowedTools "Edit(memory/**)" --output-format stream-json --verbose`. Prompt along the lines of: "Use the
   blend-tools skill to choose the forcing setup for runs/blend_cases/oct21. Run folder: runs/results/blend/agent_rep1.
   Write your decision to runs/results/blend/agent_rep1/decision.json." The `python` on PATH must have the project
   venv (xarray, OpenDrift). Copy `decision.json` and the transcript back into the repo, then
   `python src/blend_score.py --decision baseline=runs/results/blend/baseline/decision.json --decision
   agent=runs/results/blend/agent_rep1/decision.json --out runs/results/blend/score_agent`. Expect a few dollars and
   15-30 min. Most likely it also picks model only; the interesting part is whether its written hypotheses notice
   that radar windows behave differently.
2. **Spread/ensembles:** replace random-walk spread with a forcing ensemble (several blends x windages) and re-check
   50/90% containment at 6, 12 and 24 h.
3. **Phase C:** `src/search.py`: POC grid from weighted particles, detection probability 1 - exp(-coverage),
   IAMSAR-style rectangle vs greedy highest-probability allocation, scored by the probability of having found the
   real drifter. 2x2: (model only vs best setup) x (IAMSAR vs greedy).
4. **Phase D:** `src/sar_loop.py`: 6 h periods, Bayes update after an unsuccessful search, radar arriving hour by hour
   with mid-stream faults (extend `src/inject_faults.py`), agent vs scripted loop.
5. **Phase E:** November 2021 drifters (`…390370`, `…391260`, `…399930`, about Nov 8-Dec 13, 32-33.3 N, 121-119.3 W).
   First a small radar test (`scripts/list_ncei.py --month 202111 --save data/ncei_listing_202111.txt`, then
   `src/data/pull_hfr_ncei.py` for 2 days) to check radar coverage along their tracks, then the full pull, HYCOM and
   ERA5. Drogue status for these is not stated in the file.
6. **Lab 3 wording** (Finding 2) and whether to swap Lab 3's containment numbers to the KDE method: owners' decision.

## 7. Small things to know
- `src/drifter_eval.py` defaults to `data/hfr_20211018.nc` / `data/hycom_20211018.nc` (same as `fixtures/`).
- `xarray`'s `interpolate_na(max_gap=...)` needs `bottleneck`, which is not installed; `blend.py` does its own gap fill.
- `runs/work/blend/` holds rebuildable blend files and evaluation JSONs (not committed).
