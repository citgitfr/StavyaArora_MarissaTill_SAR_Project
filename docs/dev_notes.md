# Dev notes for Claude Code (SAR forcing agent)
Read CLAUDE.md and PERMISSIONS.md first. No network, no commits or pushes, no installs, do not read
heldout/. Never invent a value, threshold, or variable name; say so if something is missing.
Work in small steps, show code before running, stop after each step.

## Real data (frozen, never edit fixtures/)
- fixtures/hfr_2km_slice.nc: u, v (standard_name surface_eastward/northward_sea_water_velocity,
  units 'm s-1'), dims time 48, lat 78, lon 120; 1 h step, 2021-08-10 00:00 to 08-11 23:00;
  ~0.018 deg lat, 0.021 deg lon; NaN where no radar.
- fixtures/hycom_uv_slice.nc: u, v (standard_name eastward/northward_sea_water_velocity, units 'm/s'),
  dims time 17, lat 34, lon 32; 3 h step, 08-10 00:00 to 08-12 00:00; surface level; 0.04 x 0.08 deg;
  NaN over land (valid fraction 0.78). Its time coordinate has no standard_name.
- Unit strings differ but both are m/s: compare normalized units, never string equality.

## Measured on the clean data (reference, not thresholds)
- Speed median / 95th / max: HFR 0.192 / 0.424 / 1.000; HYCOM 0.174 / 0.412 / 0.633 m/s.
- HFR valid fraction per hour: mean 0.33, min 0.28, max 0.35 of all box cells (measure coverage
  against ocean cells from the HYCOM mask, not the whole box).
- HYCOM interpolated to HFR grid and hours: n=130635; u RMS 0.176, corr 0.50, bias +0.020;
  v RMS 0.167, corr 0.39, bias -0.011.

## Tools: .claude/skills/forcing-tools/forcing_tools.py (numpy/xarray only; every output is JSON
## and includes its input file names)
- qc --file F --source hfr|model: units_attr, units_normalized, n_time, time_step_s, largest_gap_s,
  valid_fraction, speed_median/p95/max, fill_value_hits, flags.
- compare --hfr F --model F: n_pairs, rms_u/v, corr_u/v, bias_u/v, speed_ratio_median (HFR/HYCOM),
  best_lag_hours (integer lags -6..+6), corr_swapped, flags.
- repair --file F --action rescale|shift_time|flip_sign|swap_components|mask_fill [--param X]
  --out runs/<case>/...: writes only under runs/, refuses a 3rd repair per case.
- check --hfr F --model F: pass/fail with per-test results (speed bound, time axis, fill values,
  coverage, speed ratio, correlation sign, lag).
- Thresholds live in configs/thresholds.yaml with a date. Leave placeholders marked TODO; the
  owners set them from physics and the clean reference above before any agent run.

## Planted faults: src/inject_faults.py (seeded). Writes cases to runs/cases/caseNN/ with
## neutral names, and the answer key to heldout/labels.json (never committed)
- clean (several), HFR x100, HFR x0.01, HFR time shift +1 h and +3 h, v sign flip, u/v swapped,
  60% of hours blanked, 5% of cells set to 9999.
- Label per case: fault type and the acceptable choices. Clean: model_only or fused.

## Decision (agent output, one JSON per case)
{case_id, choice: model_only|fused|repair|escalate, repair: {action, param}|null,
 evidence: [numbers copied verbatim from tool output], reason: <=2 sentences}

## Baseline: plain if-then script using the same tools, fixed order, thresholds from the yaml, no LLM.

## Order: Step 1 forcing_tools.py + unit tests. Step 2 inject_faults.py. Step 3 baseline + score script.
## Step 4 SKILL.md.