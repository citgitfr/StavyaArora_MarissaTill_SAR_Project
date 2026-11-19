"""Blend HF-radar and model currents into ONE gap-free current file, from a fixed menu. LLM-free and deterministic.

Why: with reader priority [radar, model], OpenDrift's default interpolation fills radar holes by repeatedly copying
the largest neighbouring radar value (up to 10 times), so "radar + model" in practice stretched radar about 5 km
(median) and up to 30 km into gaps and used the model for only ~3% of particle-hours along the October drifter.
Here we decide every cell ourselves: the output has a value in every ocean cell of the radar grid, so OpenDrift has
no holes to fill. Use it as the first reader, with the model second (it covers the area outside the radar box).

Menu (a spec is a dict; unknown keys or out-of-range values are refused):
  method        hard_switch   radar where it has data, model elsewhere (what "fused" was meant to be)
                edge_taper    like hard_switch, but the radar weight fades from 1 to 0 over taper_km towards the
                              edge of the radar's coverage that hour (gaps and the box edge; not the coast)
                weighted      radar_weight * radar + (1 - radar_weight) * model where radar has data
  taper_km            edge_taper only, 2..50 km
  radar_weight        weighted only, 0..1
  gap_fill_hours      0..6: first fill radar holes of at most this many consecutive missing hours by linear
                      interpolation in time (per cell; only between two real radar values)
  trust_min_coverage  0..1 or None: drop a whole radar hour if it covers less than this share of ocean cells
  trust_max_rms       0.05..1 m/s or None: drop a whole radar hour if its RMS vector difference from the model
                      (over cells where both exist) is above this
Order: gap fill -> hourly trust -> weights. Blend: u = w * radar + (1 - w) * model, w in [0, 1] (w = 0 where the
radar has no value). Ocean = cells where the model (interpolated to the radar grid) or the radar ever has a value;
model holes at the coast are filled from the nearest model cell; land stays NaN.

Output netCDF (on the radar grid and radar hours): u, v (m s-1, CF surface_*_sea_water_velocity), weight (radar
weight w), source (int8: 1 radar, 2 mixed, 0 model, -1 neither); attribute blend_spec (JSON). A JSON summary
(shares of ocean cell-hours by source, hours dropped, cells gap-filled) is printed and returned.
Refuses unless the frozen `check` (forcing_tools.py) passes on the radar/model pair, unless require_check=False
(harness and tests only). Never overwrites an output file.

  python src/blend.py --radar fixtures/hfr_20211018.nc --model fixtures/hycom_20211018.nc \\
      --out runs/work/blend/hard.nc --spec '{"method": "hard_switch"}'
  python src/blend.py ... --spec '{"method": "edge_taper", "taper_km": 10, "gap_fill_hours": 2}'
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from scipy import ndimage

REPO = Path(__file__).resolve().parents[1]
FT_PATH = REPO / ".claude" / "skills" / "forcing-tools" / "forcing_tools.py"
KM_PER_DEG = 111.195

METHODS = ("hard_switch", "edge_taper", "weighted")
DEFAULTS = {"method": "hard_switch", "taper_km": None, "radar_weight": None, "gap_fill_hours": 0,
            "trust_min_coverage": None, "trust_max_rms": None}
RANGES = {"taper_km": (2.0, 50.0), "radar_weight": (0.0, 1.0), "gap_fill_hours": (0, 6),
          "trust_min_coverage": (0.0, 1.0), "trust_max_rms": (0.05, 1.0)}


class BlendError(Exception):
    pass


def _ft():
    spec = importlib.util.spec_from_file_location("forcing_tools", FT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate(spec):
    """Return a complete, checked spec (defaults filled in). Raises BlendError on anything off the menu."""
    if not isinstance(spec, dict):
        raise BlendError("spec must be a JSON object")
    unknown = set(spec) - set(DEFAULTS)
    if unknown:
        raise BlendError(f"unknown spec keys {sorted(unknown)}; allowed {sorted(DEFAULTS)}")
    s = {**DEFAULTS, **spec}
    if s["method"] not in METHODS:
        raise BlendError(f"method must be one of {METHODS}")
    need = {"edge_taper": "taper_km", "weighted": "radar_weight"}
    for m, key in need.items():
        if (s["method"] == m) != (s[key] is not None):
            raise BlendError(f"{key} is required for method {m} and only for it")
    for key, (lo, hi) in RANGES.items():
        v = s[key]
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= v <= hi):
            raise BlendError(f"{key} must be a number in [{lo}, {hi}], got {v!r}")
    if not float(s["gap_fill_hours"]).is_integer():
        raise BlendError("gap_fill_hours must be a whole number of hours")
    s["gap_fill_hours"] = int(s["gap_fill_hours"])
    return s


def _load(path, ft):
    ds = ft._open(str(path))
    nu = ft.normalize_units(ds["u"].attrs.get("units"))
    if nu is None:
        raise BlendError(f"{path}: unrecognized velocity units {ds['u'].attrs.get('units')!r}")
    return ds * nu[1]


def _nearest_fill(a, where):
    """Fill NaNs of 2-D a at cells `where` from the nearest finite cell of a."""
    bad = ~np.isfinite(a)
    if not bad.any() or bad.all():
        return a
    idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
    out = a.copy()
    fill = bad & where
    out[fill] = a[tuple(i[fill] for i in idx)]
    return out


def gap_fill(u, v, hours):
    """Linear interpolation in time across radar holes of <= `hours` consecutive missing hours, per cell.
    Only holes with a real value on both sides are filled. Returns (u, v, cells_filled)."""
    if hours <= 0:
        return u, v, 0
    ua, va = u.values.copy(), v.values.copy()
    ok = np.isfinite(ua) & np.isfinite(va)
    filled = 0
    for i, j in zip(*np.nonzero(ok.any(axis=0) & ~ok.all(axis=0))):
        good = np.flatnonzero(ok[:, i, j])
        for a, b in zip(good[:-1], good[1:]):            # consecutive real values with a hole between them
            n = b - a - 1
            if 0 < n <= hours:
                f = np.arange(1, n + 1) / (n + 1)
                ua[a + 1:b, i, j] = ua[a, i, j] + f * (ua[b, i, j] - ua[a, i, j])
                va[a + 1:b, i, j] = va[a, i, j] + f * (va[b, i, j] - va[a, i, j])
                filled += n
    return u.copy(data=ua), v.copy(data=va), int(filled)


def edge_weight(valid, ocean, dy_km, dx_km, taper_km):
    """Per hour: radar weight = distance (km) to the nearest ocean cell WITHOUT radar, or to the box edge, divided
    by taper_km, clipped to [0, 1]; 0 where the radar has no value. Land does not count as an edge."""
    w = np.zeros(valid.shape, float)
    for t in range(valid.shape[0]):
        edge = ocean & ~valid[t]
        free = np.pad(~edge, 1, constant_values=False)          # outside the box = edge (model takes over there)
        d = ndimage.distance_transform_edt(free, sampling=(dy_km, dx_km))[1:-1, 1:-1]
        w[t] = np.clip(d / taper_km, 0, 1) * valid[t]
    return w


def blend(radar_path, model_path, out_path, spec, require_check=True, thresholds=None):
    s = validate(spec)
    out_path = Path(out_path)
    if out_path.exists():
        raise BlendError(f"{out_path} already exists; never overwritten")
    ft = _ft()
    check_result = "not run (require_check=False)"
    if require_check:
        chk = ft.check(str(radar_path), str(model_path), thresholds or ft.load_thresholds())
        if chk["result"] != "pass":
            failed = [n for n, t in chk["tests"].items() if not t["pass"]]
            raise BlendError(f"check fails on this radar/model pair ({failed}); refusing to blend")
        check_result = "pass"

    r = _load(radar_path, ft)
    m = _load(model_path, ft)
    if pd.Timestamp(m.time.values[0]) > pd.Timestamp(r.time.values[0]) or \
            pd.Timestamp(m.time.values[-1]) < pd.Timestamp(r.time.values[-1]):
        raise BlendError("the model's time range must cover the radar's")
    mi = m.interp(time=r.time, lat=r.lat, lon=r.lon)          # linear in time and space
    ur, vr = r["u"].where(np.isfinite(r["v"])), r["v"].where(np.isfinite(r["u"]))
    um, vm = mi["u"].values.copy(), mi["v"].values.copy()

    ever_model = np.isfinite(um).any(axis=0) & np.isfinite(vm).any(axis=0)
    ever_radar = (np.isfinite(ur.values) & np.isfinite(vr.values)).any(axis=0)
    ocean = ever_model | ever_radar
    for t in range(um.shape[0]):                               # model holes at the coast: nearest model cell
        um[t] = _nearest_fill(um[t], ocean)
        vm[t] = _nearest_fill(vm[t], ocean)

    ur, vr, n_filled = gap_fill(ur, vr, s["gap_fill_hours"])
    ur, vr = ur.values.copy(), vr.values.copy()
    valid = np.isfinite(ur) & np.isfinite(vr) & ocean

    dropped = []
    n_ocean = int(ocean.sum())
    for t in range(valid.shape[0]):
        cov = valid[t].sum() / n_ocean if n_ocean else 0.0
        both = valid[t] & np.isfinite(um[t]) & np.isfinite(vm[t])
        rms = float(np.sqrt(np.mean((ur[t][both] - um[t][both]) ** 2 + (vr[t][both] - vm[t][both]) ** 2))) \
            if both.any() else np.inf
        low = s["trust_min_coverage"] is not None and cov < s["trust_min_coverage"]
        far = s["trust_max_rms"] is not None and rms > s["trust_max_rms"]
        if low or far:
            valid[t] = False
            dropped.append({"time": str(pd.Timestamp(r.time.values[t])), "coverage": round(float(cov), 4),
                            "rms_m_s": round(rms, 4) if np.isfinite(rms) else None})

    if s["method"] == "hard_switch":
        w = valid.astype(float)
    elif s["method"] == "weighted":
        w = valid * float(s["radar_weight"])
    else:
        lat = r.lat.values
        dy_km = float(np.mean(np.diff(lat))) * KM_PER_DEG
        dx_km = float(np.mean(np.diff(r.lon.values))) * KM_PER_DEG * np.cos(np.radians(float(np.mean(lat))))
        w = edge_weight(valid, ocean, dy_km, dx_km, float(s["taper_km"]))

    ur0, vr0 = np.where(valid, ur, 0.0), np.where(valid, vr, 0.0)
    u = w * ur0 + (1 - w) * um
    v = w * vr0 + (1 - w) * vm
    land = np.broadcast_to(~ocean, u.shape)
    u[land], v[land] = np.nan, np.nan
    have = np.isfinite(u) & np.isfinite(v)
    source = np.full(u.shape, -1, np.int8)
    source[have & (w >= 1)] = 1
    source[have & (w > 0) & (w < 1)] = 2
    source[have & (w <= 0)] = 0

    dims = ("time", "lat", "lon")
    out = xr.Dataset(
        {"u": (dims, u.astype("float32"), {"standard_name": "surface_eastward_sea_water_velocity", "units": "m s-1"}),
         "v": (dims, v.astype("float32"), {"standard_name": "surface_northward_sea_water_velocity", "units": "m s-1"}),
         "weight": (dims, np.where(have, w, np.nan).astype("float32"),
                    {"long_name": "radar weight w in u = w*radar + (1-w)*model", "units": "1"}),
         "source": (dims, source, {"long_name": "1 radar, 2 mixed, 0 model, -1 neither (land)"})},
        coords={"time": r.time.values, "lat": r.lat.values, "lon": r.lon.values})
    out["lat"].attrs.update(standard_name="latitude", units="degrees_north")
    out["lon"].attrs.update(standard_name="longitude", units="degrees_east")
    out.attrs.update(blend_spec=json.dumps(s, sort_keys=True), radar=str(radar_path), model=str(model_path),
                     check=check_result, made_by="src/blend.py")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_netcdf(out_path)

    oc = np.broadcast_to(ocean, u.shape)
    n = int(oc.sum())
    summary = {"out": str(out_path), "spec": s, "check": check_result,
               "ocean_cells": n_ocean, "hours": int(u.shape[0]),
               "share_radar": round(float((source[oc] == 1).sum() / n), 4),
               "share_mixed": round(float((source[oc] == 2).sum() / n), 4),
               "share_model": round(float((source[oc] == 0).sum() / n), 4),
               "share_neither": round(float((source[oc] == -1).sum() / n), 4),
               "mean_radar_weight": round(float(np.mean(w[oc])), 4),
               "cells_gap_filled": n_filled, "hours_dropped": len(dropped), "dropped": dropped}
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radar", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", default='{"method": "hard_switch"}', help="JSON object, see the menu above")
    a = ap.parse_args(argv)
    try:
        spec = json.loads(a.spec)
        print(json.dumps(blend(a.radar, a.model, a.out, spec), indent=2))
    except (BlendError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
