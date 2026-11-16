# Running the pipeline on other input data (to do)

We plan to test the pipeline on a different region and/or time window. This note lists what the code currently
assumes about the data and what has to change. Status: nothing below is done yet.

## What new data must look like (contract)
Both files must have this shape, or be converted to it by the pull scripts:
- variables `u`, `v` with dims `(time, lat, lon)`; coordinates named `time`, `lat`, `lon`; longitudes in -180..180
- velocity units that `normalize_units` recognizes (m/s or cm/s spellings)
- CF standard names (checked by `qc`, needed by OpenDrift's reader to find the currents):
  radar `surface_eastward_sea_water_velocity` / `surface_northward_sea_water_velocity`;
  model `eastward_sea_water_velocity` / `northward_sea_water_velocity`
- radar hourly (the lag search is in whole hours, -6..+6); model any regular step
- the model's time window covers the radar's, and its box should be LARGER than the radar's (see drift below)
- a window of at least 24 h shared by both, plus slack for the planted time shifts (+3 h)

## Per file: what is tied to the current data
| File | Current assumption | Change for new data |
|---|---|---|
| `src/data/pull_hfr_ncei.py`, `src/data/pull_fixtures.py`, `scripts/list_ncei.py` | DONE 2026-11-15: `--start --end --box --out` (radar also `--listing --raw-dir`; HYCOM `--url`, default expt_93.0 for the start year; listing `--month --save`). Defaults still reproduce the original fixtures | pull the model with a margin around the radar box |
| `configs/thresholds.yaml` | set from THIS window's clean statistics (speed max, coverage, speed ratio, correlation, lag) | re-measure on the new clean pair (`look.py`, `cov_check.py`, `qc`, `compare`), set new values, re-freeze with a new date BEFORE generating cases |
| `src/inject_faults.py` | reads the two fixture paths; +3 h shift must stay inside the model window | take fixture paths as arguments; the key already records `radar_fixture` and `model_fixture` |
| `src/score.py` | `CLEAN = fixtures/hfr_2km_slice.nc` for repair fidelity | read the clean radar name from the key (`labels.json` `radar_fixture`) |
| `src/drift.py` | `--clean`/`--model` default to the fixtures (already arguments); start, release points and coverage are computed from the data; `EDGE_MARGIN_DEG = 0.25` exists because the model box equals the radar box; 24 h run, hourly output | pass `--clean`/`--model`; with a larger model box, reduce or drop the margin; check that 5 points x 200 particles and 0.25 deg suit the new radar footprint |
| `src/data/look.py`, `scripts/cov_check.py` | hard-coded fixture paths | take paths as arguments |
| `memory/MEMORY.md` | region and window line, data-source line | update the facts (and commit before any agent run) |
| `docs/data_sources.md` | current sources and access dates | add the new sources |
| `.claude/skills/forcing-tools/forcing_tools.py` | frozen; standard names above, hourly lags | no change if the contract is met; changing it means a new freeze (SHA in LOG.md) |
| `src/baseline.py`, `scripts/run_agent_batch.py`, `scripts/make_eval_workspace.py` | data-agnostic | none |

## Order of work when the new data arrives
1. Pull the new radar and model (model box larger than radar box) into new fixture files.
2. First look and clean measurements; set and freeze new thresholds (dated).
3. Generate cases and key from the new fixtures; move the key out of the repo.
4. Update MEMORY.md; commit.
5. Baseline, agent batch, `score.py`, `drift.py evaluate --clean <new radar> --model <new model>`.
6. Compare with the current results; record both in LOG.md.
