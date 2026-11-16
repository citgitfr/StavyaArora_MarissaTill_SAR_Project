"""Plant seeded faults in the HF-radar fixture for the SAR forcing benchmark.

Writes runs/cases/caseNN/{radar.nc, model.nc} (neutral names, case order shuffled, no fault hint in
names, attributes or encoding) and the answer key heldout/labels.json (gitignored, never committed).
Deterministic for a given SEED and fixtures. Prints nothing about which case holds which fault.
Run from the repository root:
  python src/inject_faults.py                     # original benchmark (August 2021 fixtures, seed 295)
  python src/inject_faults.py --radar data/hfr_20211018.nc --model data/hycom_20211018.nc \\
      --cases runs/cases_oct --labels ~/sar_heldout/labels_oct.json --seed 1018
A --labels path outside the repository writes the key straight to where it is kept during agent runs.
  --plan compound   8 harder cases: two radar faults at once, or a faulty MODEL with clean radar (see plan_compound)
"""
import argparse
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


def flip_u(ds, rng):
    _set(ds, "u", -ds["u"].values)
    return ds, {}


def compose(*faults):
    """Apply several faults in order; the answer-key detail lists each step."""
    def f(ds, rng):
        steps = []
        for g in faults:
            ds, d = g(ds, rng)
            steps.append({"fault": getattr(g, "__name__", "fault"), **d})
        return ds, {"steps": steps}
    return f


def choice(name, action=None, param=None):
    return {"choice": name, "repair": None if action is None else {"action": action, "param": param}}


def repairs(*steps):
    """An acceptable choice made of several repairs in this order: repairs(("rescale", 0.01), ("shift_time", -3.0))."""
    return {"choice": "repair", "repair": [{"action": a, "param": p} for a, p in steps]}


def plan_compound(fill):
    """Harder cases where the scripted baseline's first-failing-test rule is expected to go wrong, plus controls.
    Entries: (fault_type, radar fault, detectable, acceptable_choices, model fault or None). Owners review the key."""
    safe, esc = choice("model_only"), choice("escalate")
    clean = ("clean", None, True, [safe, choice("fused")], None)
    return [clean] * 2 + [
        # discriminating: both components negated (a 180-degree rotation) looks like a swap to a fixed rule
        ("radar_rotated_180", compose(flip_u, flip_v), True,
         [repairs(("flip_sign", "u"), ("flip_sign", "v")), repairs(("flip_sign", "v"), ("flip_sign", "u")), safe], None),
        # discriminating: a 90-degree rotation (u' = -v, v' = u) needs a swap and one sign flip
        ("radar_rotated_90", compose(swap, flip_u), True,
         [repairs(("swap_components", None), ("flip_sign", "v")), repairs(("flip_sign", "u"), ("swap_components", None)),
          safe], None),
        # discriminating: the MODEL is wrong (x100); the radar is clean. Repairing the radar or falling back to the
        # broken model are both wrong; a person must look at it
        ("model_x100", None, True, [esc], rescale(100.0)),
        # controls: two independent radar faults that a first-failing-test rule should fix one at a time
        ("x100_plus_shift_+3h", compose(rescale(100.0), shift(3)), True,
         [repairs(("rescale", 0.01), ("shift_time", -3.0)), repairs(("shift_time", -3.0), ("rescale", 0.01)), safe], None),
        ("swap_plus_x0.01", compose(swap, rescale(0.01)), True,
         [repairs(("rescale", 100.0), ("swap_components", None)), repairs(("swap_components", None), ("rescale", 100.0)),
          safe], None),
        ("spikes_plus_v_flip", compose(set_cells(OFF_LIST_SENTINEL), flip_v), True,
         [repairs(("mask_fill", abs(OFF_LIST_SENTINEL)), ("flip_sign", "v")),
          repairs(("flip_sign", "v"), ("mask_fill", abs(OFF_LIST_SENTINEL))), safe], None),
    ]


PLANS = {"standard": None, "compound": plan_compound}


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
    """Same encoding for every case so dtype or packing cannot hint at the fault. Lossless zlib compression
    (values unchanged; added 2026-11-15 because the 264 h October cases were 25 MB each uncompressed)."""
    for c in ("u", "v", "time"):
        ds[c].encoding = {}
    z = {"_FillValue": np.nan, "zlib": True, "complevel": 4}
    ds.to_netcdf(path, encoding={"u": dict(z), "v": dict(z),
                                 "time": {"units": "seconds since 1970-01-01 00:00:00", "dtype": "int64"}})


def write_model(ds, path):
    """Rewrite a model file with one fixed encoding (used for every case of a plan that alters any model file,
    so a changed model cannot be spotted by its encoding)."""
    for c in ds.variables:
        ds[c].encoding = {}
    z = {"_FillValue": np.nan, "zlib": True, "complevel": 4}
    ds.to_netcdf(path, encoding={"u": dict(z), "v": dict(z),
                                 "time": {"units": "seconds since 1970-01-01 00:00:00", "dtype": "int64"}})


def _show(p):
    p = Path(p).resolve()
    return p.relative_to(REPO) if REPO in p.parents else p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radar", default=str(HFR), help="clean radar file to plant faults in")
    ap.add_argument("--model", default=str(MODEL), help="model file copied unchanged into every case")
    ap.add_argument("--cases", default=str(CASES), help="output folder for caseNN/")
    ap.add_argument("--labels", default=str(LABELS), help="answer-key path (outside the repo is safest)")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--plan", choices=list(PLANS), default="standard",
                    help="standard: the 14-case benchmark; compound: 8 harder cases (two faults, or a faulty model)")
    a = ap.parse_args(argv)
    hfr, model = Path(a.radar), Path(a.model)
    cases_dir, labels_path = Path(a.cases), Path(a.labels).expanduser()
    for p in (cases_dir, labels_path):
        if p.exists():
            sys.exit(f"{p} already exists; not overwriting. Remove it yourself to rebuild.")
    for p in (hfr, model):
        if not p.exists():
            sys.exit(f"{p} not found")
    fill = fill_value_from_config()
    rng = np.random.default_rng(a.seed)
    compound = a.plan == "compound"
    cases = plan_compound(fill) if compound else [(*c, None) for c in plan(fill)]
    order = rng.permutation(len(cases))
    with xr.open_dataset(hfr) as src:
        base = src.load()
    model_base = None
    if compound:
        with xr.open_dataset(model) as src:
            model_base = src.load()

    labels = {}
    for n, i in enumerate(order, 1):
        fault_type, fault, detectable, acceptable, model_fault = cases[i]
        case_id = f"case{n:02d}"
        ds = base.copy(deep=True)
        ds, detail = fault(ds, rng) if fault else (ds, {})
        out = cases_dir / case_id
        out.mkdir(parents=True)
        write_radar(ds, out / RADAR_NAME)
        if compound:                              # every model rewritten the same way, faulty or not
            mds = model_base.copy(deep=True)
            if model_fault:
                mds, mdetail = model_fault(mds, rng)
                detail = {**detail, "model_fault": mdetail}
            write_model(mds, out / MODEL_NAME)
        else:
            shutil.copyfile(model, out / MODEL_NAME)
        labels[case_id] = {"fault_type": fault_type, "detectable": detectable,
                           "acceptable_choices": acceptable, "detail": detail}

    labels_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.write_text(json.dumps({"seed": a.seed, "plan": a.plan, "radar_fixture": hfr.name,
                                       "model_fixture": model.name,
                                       "radar_path": str(_show(hfr)), "model_path": str(_show(model)),
                                       "fill_value_used": fill, "off_list_sentinel": OFF_LIST_SENTINEL,
                                       "cases": labels}, indent=2), encoding="utf-8")
    print(f"wrote {len(labels)} cases to {_show(cases_dir)} and the answer key to {_show(labels_path)}")


if __name__ == "__main__":
    main()
