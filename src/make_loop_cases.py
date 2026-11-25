"""Build the data stream for the multi-period SAR loop (Lab 4, Phase D). Harness code; people run it.

A scenario starts at a TEST-window time t0 (the drifter's real position is the last known position) and runs
PERIODS periods of PERIOD_H hours. At the end of each period k (t_k = t0 + k * PERIOD_H) the radar provider delivers
the latest DELIVERY_H hours of radar (72 h: on 24 h slices the frozen check fails clean data too often; on 72 h it
passed 19 of 21 clean October slices and caught x100, flip_v, a 3 h shift and swap in 21 of 21). From the onset
period on, every delivery carries the scenario's fault (a feed fault: the provider re-sends the whole slice wrong).
Each delivery is one case folder, exactly like the cleaning benchmark: radar.nc + model.nc (model sliced with 6 h of
margin). Case names are shuffled caseNN, with no hint of scenario, period or fault.

Writes:
  runs/cases_loop/caseNN/{radar.nc, model.nc}
  runs/cases_loop/mapping.json        case -> scenario, period, t_end (no fault information; read by src/sar_loop.py)
No answer key is written (owner decision 2026-11-25): the scorer decides whether delivered radar was faulty by
comparing it with the clean fixture, and the fault plan is SCENARIOS below. The agent never sees this file or
mapping.json: an evaluation workspace copies only the case folders.

  python src/make_loop_cases.py
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("inject_faults", REPO / "src" / "inject_faults.py")
inj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inj)

RADAR = REPO / "fixtures" / "hfr_20211018.nc"
MODEL = REPO / "fixtures" / "hycom_20211018.nc"
OUT = REPO / "runs" / "cases_loop"
SEED = 2511
PERIODS, PERIOD_H, DELIVERY_H, MODEL_MARGIN_H = 4, 6, 72, 6

# t0 (UTC), onset period (None = clean throughout), fault
SCENARIOS = [
    ("S1", "2021-10-23 12:00", None, None),
    ("S2", "2021-10-24 00:00", 2, ("rescale_x100", lambda: inj.rescale(100))),
    ("S3", "2021-10-24 12:00", 3, ("flip_v", lambda: inj.flip_v)),
    ("S4", "2021-10-25 00:00", 2, ("shift_+3h", lambda: inj.shift(3))),
    ("S5", "2021-10-25 12:00", 3, ("rescale_x100+shift_+3h", lambda: inj.compose(inj.rescale(100), inj.shift(3)))),
    ("S6", "2021-10-26 00:00", 2, ("swap", lambda: inj.swap)),
]


def deliveries(scenarios=SCENARIOS):
    """[(scenario, period, t_end, fault_name or None, fault_fn or None)] for every delivery."""
    out = []
    for sid, t0, onset, fault in scenarios:
        for k in range(1, PERIODS + 1):
            t_end = pd.Timestamp(t0) + pd.Timedelta(hours=PERIOD_H * k)
            bad = onset is not None and k >= onset
            out.append((sid, k, t_end, fault[0] if bad else None, fault[1]() if bad else None))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        sys.exit(f"{out} already exists; not overwriting")
    r = xr.open_dataset(RADAR).load()
    m = xr.open_dataset(MODEL).load()
    rng = np.random.default_rng(SEED)
    dl = deliveries()
    order = rng.permutation(len(dl))
    mapping = {}
    for n, i in enumerate(order, 1):
        sid, k, t_end, fname, f = dl[i]
        case = f"case{n:02d}"
        t_start = t_end - pd.Timedelta(hours=DELIVERY_H - 1)
        rc = r.sel(time=slice(t_start, t_end)).copy(deep=True)
        if rc.sizes["time"] != DELIVERY_H:
            sys.exit(f"{sid} period {k}: radar slice has {rc.sizes['time']} hours, expected {DELIVERY_H}")
        if f is not None:
            rc, _ = f(rc, rng)
        mc = m.sel(time=slice(t_start - pd.Timedelta(hours=MODEL_MARGIN_H), t_end + pd.Timedelta(hours=MODEL_MARGIN_H)))
        d = out / case
        d.mkdir(parents=True)
        inj.write_radar(rc, d / "radar.nc")
        inj.write_model(mc.copy(deep=True), d / "model.nc")
        mapping[case] = {"scenario": sid, "period": k, "t_end": str(t_end)}
    scen = {s[0]: {"t0": s[1], "periods": PERIODS, "period_h": PERIOD_H} for s in SCENARIOS}
    (out / "mapping.json").write_text(json.dumps({"scenarios": scen, "delivery_h": DELIVERY_H, "cases": mapping},
                                                 indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(dl)} cases and mapping.json to {out}")


if __name__ == "__main__":
    sys.exit(main())
