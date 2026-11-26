# Lab 4 handoff (updated 2026-11-25)

Branch: `claude/lab4` (from `main` at 00745c6). Nothing is merged into `main`; the owners merge.
Every change below was made with Claude Code in one session and reviewed by Stavya as it went.

## 1. Plan we agreed on
Work on the October 2021 window and its one real drifter (300534061399950) first; add more drifters later.

| Phase | Goal | Status |
|---|---|---|
| A | Add wind (and wave Stokes drift) to the forecast; make the 50% / 90% areas honest | done |
| B | Blend menu for radar + model, investigator agent vs a try-everything baseline | done (one agent run) |
| U | Forcing ensemble instead of random-walk spread | done |
| C | Search planner, one period (IAMSAR-style vs highest-probability), scored against the drifter | done |
| D | Multi-period loop: search, update probabilities, new data, re-forecast, re-plan; agent vs scripted loop | done (QC decisions per period; see section 6) |
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
| 987da4e | this handoff (first version) |
| 52f0fb0 | LOG.md, AI_USE.md, docs/pipeline.md (Lab 4 commands), requirements.txt (cdsapi) caught up |
| 59e8ae4 | `scripts/run_blend_agent.py` |
| 32edc7d | blend agent run `blend_rep1` (decision, evaluations, transcript, cost log) |
| 176faf0 | `src/ensemble_eval.py` + results in `runs/results/ensemble/`; `src/search.py`, `src/search_eval.py` + `tests/test_search.py` |
| 12cc971 | Phase C results in `runs/results/search/` |
| 946d8dd | `src/make_loop_cases.py` + `runs/cases_loop/mapping.json` (the .nc cases are gitignored; rebuild with the script) |
| 3e58c8b | `src/sar_loop.py` + `tests/test_sar_loop.py`; scripted decisions on the 24 deliveries |
| 8f2a20e | cleaning-agent batch `loop_rep1` on the deliveries; loop results in `runs/results/loop/` |

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

**Finding 6: the blend agent matched the try-everything baseline with a tenth of the effort.** Run `blend_rep1`
(20 turns, $0.56, 4 min) used 5 of its 12 evaluations and chose model only, no wind, Stokes off, the same as the
baseline's 50 evaluations, so the same test score (15.4 km). Its hypotheses (decision.json): dropping the radar's
worst hours (RMS vs model > 0.3 m/s) helped but did not reach model only; the miss shrank as radar weight fell;
windage made the windy windows worse. Sound for the tuning period, which is not like the test period (Finding 5).

**Finding 7: a forcing ensemble does not explain the error.** 9 forcings (model only, hard_switch, 50/50 blend x
windage 0/1/2%), 100 particles each: member centroids only 5.5 km apart at 24 h vs about 20 km misses, so with no
extra spread the 90% area held the drifter in 11% of test windows. With K = 1600 (chosen on tuning windows) the 24 h
hit rates are 44% / 83% (target 50% / 90%), the closest of all setups, but 89% / 100% at 6 h: still too wide early.

**Finding 8: in one search period, the forecast matters more than the planner.** Mean probability that the plan
would have found the drifter, 18 test windows, assumed effort per asset 1 km sweep width x 27.8 km/h x 4 h:

| Search at | Assets | model only IAMSAR / greedy | hard_switch IAMSAR / greedy | ensemble IAMSAR / greedy |
|---|---|---|---|---|
| 12 h | 1 | 0.19 / 0.16 | 0.19 / 0.21 | 0.14 / 0.19 |
| 12 h | 3 | 0.39 / 0.37 | 0.32 / 0.38 | 0.28 / 0.36 |
| 24 h | 1 | 0.07 / 0.05 | 0.11 / 0.13 | 0.09 / 0.06 |
| 24 h | 3 | 0.16 / 0.14 | 0.25 / 0.24 | 0.14 / 0.14 |

Every plan found the drifter more often than it predicted (spread too wide). The greedy plan is optimal only for the
map it is given; when the map is off, it gains little over the square.

**Finding 9: the frozen check needs 72 h of radar.** On 24 h slices of clean October radar it often fails (lag,
correlation); on 72 h slices 19 of 21 clean slices pass and x100, flip_v, a +3 h shift and swap are caught 21 of 21.

**Finding 10: in the loop, the agent's QC kept the search as good as perfect radar.** 6 scenarios x 4 periods of 6 h,
3 assets per period, radar delivered every period as the latest 72 h, feed faults starting mid-scenario:

| Policy | mean P(found by 24 h) | periods on radar | silent errors | false alarms | cost |
|---|---|---|---|---|---|
| perfect radar | 0.866 | 24 | 0 | 0 | - |
| agent (loop_rep1) | 0.867 | 23 | 1 | 1 | $5.21 |
| scripted (baseline.py) | 0.852 | 21 | 1 | 1 | $0 |
| no QC | 0.824 | 24 | 13 | 0 | $0 |
| model only | 0.806 | 0 | 0 | 0 | $0 |

The two differ on 2 deliveries, both a u/v swap: the script's first-failing-test rule tried flip_sign u twice and
fell back to the model; the agent read the swapped correlation and chose swap_components (faithful), which lifted
that scenario from 0.674 to 0.761 (= perfect). Both made the same false alarm (a clean slice failing speed_ratio) and
the same silent error (x100 + 3 h shift repaired with a -4 h shift: the lag bias known since 2026-11-15).
Small differences, 6 overlapping scenarios, one drifter: no confidence intervals yet.

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
1. **Phase E, more drifters:** November 2021 (`…390370`, `…391260`, `…399930`, about Nov 8-Dec 13, 32-33.3 N,
   121-119.3 W). First a small radar test (`scripts/list_ncei.py --month 202111 --save data/ncei_listing_202111.txt`,
   then `src/data/pull_hfr_ncei.py` for 2 days) to check radar coverage along their tracks, then the full pull, HYCOM
   and ERA5. Drogue status for these is not stated in the file. Then re-run L2-L12 in docs/pipeline.md.
2. **Statistics:** several fault seeds for the loop, a second agent repeat (blend and loop), and bootstrap confidence
   intervals; today's differences are small.
3. **Agent choices beyond QC in the loop:** today every policy re-forecasts each period with a fixed spread and the
   agent only makes the cleaning decision. Still open: when to re-forecast vs only update the probabilities, how much
   to trust the radar this period (the blend menu), and escalating on contradictory evidence. Finding 5 says this is
   where a fixed rule is weakest.
4. **Spread:** neither random-walk nor the forcing ensemble is honest at every horizon; try a spread that grows with
   time (e.g. a per-particle constant velocity error) calibrated at 6, 12 and 24 h.
5. **Lag bias** (Finding 10 silent error): fix in a versioned SKILL/baseline v2 with fresh cases, not in place.
6. **Lab 3 wording** (Finding 2) and whether to swap Lab 3's containment numbers to the KDE method: owners' decision.
7. **Lab 4 report:** an honest failure-mode list (containment bug, radar stretching, choices not carrying over between
   periods, short-slice false alarms, the lag bias, one drifter, hindcasts).

## 7. Small things to know
- `src/drifter_eval.py` defaults to `data/hfr_20211018.nc` / `data/hycom_20211018.nc` (same as `fixtures/`).
- `xarray`'s `interpolate_na(max_gap=...)` needs `bottleneck`, which is not installed; `blend.py` does its own gap fill.
- `runs/work/blend/` and `runs/work/loop/` hold rebuildable blend files and evaluation JSONs (not committed).
- Loop cases (`runs/cases_loop/case*/`) are gitignored .nc files; `python src/make_loop_cases.py` rebuilds them
  identically (seeded). There is no answer key for them by design.
