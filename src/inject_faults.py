"""Plant seeded faults in the HF-radar fixture for the SAR forcing benchmark.

Writes runs/cases/caseNN/{radar.nc, model.nc} (neutral names, case order shuffled, no fault hint in
names, attributes or encoding) and the answer key heldout/labels.json (gitignored, never committed).
Deterministic for a given SEED and fixtures. Prints nothing about which case holds which fault.
Run from the repository root:  python src/inject_faults.py
"""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
HFR = REPO / "fixtures" / "hfr_2km_slice.nc"
MODEL = REPO / "fixtures" / "hycom_uv_slice.nc"
CASES = REPO / "runs" / "cases"
LABELS = REPO / "heldout" / "labels.json"
RADAR_NAME, MODEL_NAME = "radar.nc", "model.nc"

# ---- EDIT THESE ------------------------------------------------------
SEED = 295
N_CLEAN = 4
BLANK_HOURS_FRACTION = 0.60      # whole hours set to NaN
DROPOUT_FRACTION = 0.25          # of radar-covered cells, one contiguous block of columns, every hour
FILL_CELLS_FRACTION = 0.05       # of valid cells, for both fill-value faults
OFF_LIST_SENTINEL = -777.0       # must NOT be on qc.fill_values
# ----------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location("forcing_tools", REPO / ".claude/skills/forcing-tools/forcing_tools.py")
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)


def fill_value_from_config():
    try:
        fv = ft.load_thresholds().get("qc.fill_values", ft.TODO)
    except ft.ToolError as e:
        sys.exit(str(e))
    if fv == ft.TODO:
        sys.exit("qc.fill_values is TODO in configs/thresholds.yaml; the owners must set it first.")
    fv = fv if isinstance(fv, list) else [fv]
    if not fv:
        sys.exit("qc.fill_values is empty in configs/thresholds.yaml.")
    if OFF_LIST_SENTINEL in fv:
        sys.exit(f"OFF_LIST_SENTINEL {OFF_LIST_SENTINEL} is on qc.fill_values; pick another.")
    return float(fv[0])


def _set(ds, c, data):
    ds[c] = ds[c].copy(data=data)


def _valid(ds):
    return np.isfinite(ds["u"].values) & np.isfinite(ds["v"].values)


# ---- faults: each takes (ds, rng) and returns (ds, detail dict for the answer key) ----
def rescale(k):
    def f(ds, rng):
        for c in ("u", "v"):
            _set(ds, c, ds[c].values * k)
        return ds, {"factor": k}
    return f


def shift(hours):
    def f(ds, rng):
        shifted = ds["time"].values + np.timedelta64(hours, "h")
        return ds.assign_coords(time=("time", shifted, ds["time"].attrs)), {"hours": hours}
    return f


def flip_v(ds, rng):
    _set(ds, "v", -ds["v"].values)
    return ds, {}


def swap(ds, rng):
    u = ds["u"].values.copy()
    _set(ds, "u", ds["v"].values)
    _set(ds, "v", u)
    return ds, {}


def blank_hours(ds, rng):
    nt = ds.sizes["time"]
    hours = np.sort(rng.choice(nt, round(BLANK_HOURS_FRACTION * nt), replace=False))
    for c in ("u", "v"):
        a = ds[c].values.copy()
        a[hours] = np.nan
        _set(ds, c, a)
    return ds, {"hours_blanked": hours.tolist(), "n_time": nt}


def partial_dropout(ds, rng):
    """NaN a contiguous block of lon columns holding ~DROPOUT_FRACTION of the radar-covered cells."""
    covered = _valid(ds).any(axis=0)                      # (lat, lon)
    col = covered.sum(axis=0)
    csum = np.concatenate([[0], np.cumsum(col)])
    target = DROPOUT_FRACTION * covered.sum()
    spans = [(s, int(np.searchsorted(csum, csum[s] + target))) for s in range(col.size) if col[s] > 0]
    spans = [(s, e) for s, e in spans if e < csum.size]
    s, e = spans[rng.integers(len(spans))]
    for c in ("u", "v"):
        a = ds[c].values.copy()
        a[:, :, s:e] = np.nan
        _set(ds, c, a)
    return ds, {"lon_index_start": s, "lon_index_stop": e,
                "covered_cells_dropped_fraction": round(float(col[s:e].sum() / covered.sum()), 4)}


def set_cells(value):
    def f(ds, rng):
        flat = np.flatnonzero(_valid(ds))
        idx = rng.choice(flat, round(FILL_CELLS_FRACTION * flat.size), replace=False)
        for c in ("u", "v"):
            a = ds[c].values.copy()
            a.flat[idx] = value
            _set(ds, c, a)
        return ds, {"value": value, "n_cells": int(idx.size), "of_valid_cells": int(flat.size)}
    return f


def choice(name, action=None, param=None):
    return {"choice": name, "repair": None if action is None else {"action": action, "param": param}}


def plan(fill):
    """(fault_type, fault fn, detectable, acceptable_choices). PROPOSED answer key: owners to review."""
    safe = choice("model_only")
    clean = ("clean", None, True, [safe, choice("fused")])
    return [clean] * N_CLEAN + [
        ("hfr_x100", rescale(100.0), True, [choice("repair", "rescale", 0.01), safe]),
        ("hfr_x0.01", rescale(0.01), True, [choice("repair", "rescale", 100.0), safe]),
        ("time_shift_+1h", shift(1), False, [choice("repair", "shift_time", -1.0), safe, choice("fused")]),
        ("time_shift_+3h", shift(3), True, [choice("repair", "shift_time", -3.0), safe]),
        ("v_sign_flip", flip_v, True, [choice("repair", "flip_sign", "v"), safe]),
        ("uv_swapped", swap, True, [choice("repair", "swap_components"), safe]),
        ("hours_blanked_60pct", blank_hours, True, [safe, choice("escalate")]),
        ("partial_dropout_25pct", partial_dropout, False, [safe, choice("fused")]),
        ("fill_value_on_list", set_cells(fill), True, [choice("repair", "mask_fill", abs(fill)), safe]),
        ("sentinel_off_list", set_cells(OFF_LIST_SENTINEL), True,
         [choice("repair", "mask_fill", abs(OFF_LIST_SENTINEL)), safe]),
    ]


def write_radar(ds, path):
    """Same encoding for every case so dtype or packing cannot hint at the fault."""
    for c in ("u", "v", "time"):
        ds[c].encoding = {}
    ds.to_netcdf(path, encoding={"u": {"_FillValue": np.nan}, "v": {"_FillValue": np.nan},
                                 "time": {"units": "seconds since 1970-01-01 00:00:00", "dtype": "int64"}})


def main():
    for p in (CASES, LABELS):
        if p.exists():
            sys.exit(f"{p} already exists; not overwriting. Remove it yourself to rebuild.")
    fill = fill_value_from_config()
    rng = np.random.default_rng(SEED)
    cases = plan(fill)
    order = rng.permutation(len(cases))
    with xr.open_dataset(HFR) as src:
        base = src.load()

    labels = {}
    for n, i in enumerate(order, 1):
        fault_type, fault, detectable, acceptable = cases[i]
        case_id = f"case{n:02d}"
        ds = base.copy(deep=True)
        ds, detail = fault(ds, rng) if fault else (ds, {})
        out = CASES / case_id
        out.mkdir(parents=True)
        write_radar(ds, out / RADAR_NAME)
        shutil.copyfile(MODEL, out / MODEL_NAME)
        labels[case_id] = {"fault_type": fault_type, "detectable": detectable,
                           "acceptable_choices": acceptable, "detail": detail}

    LABELS.parent.mkdir(exist_ok=True)
    LABELS.write_text(json.dumps({"seed": SEED, "radar_fixture": HFR.name, "model_fixture": MODEL.name,
                                  "fill_value_used": fill, "off_list_sentinel": OFF_LIST_SENTINEL,
                                  "cases": labels}, indent=2), encoding="utf-8")
    print(f"wrote {len(labels)} cases to {CASES.relative_to(REPO)} and the answer key to {LABELS.relative_to(REPO)}")


if __name__ == "__main__":
    main()
