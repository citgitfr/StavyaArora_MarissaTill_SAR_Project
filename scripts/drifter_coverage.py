"""Offline check: at each drifter's hourly positions, does a radar file have data? (No network.)

For every drifter position inside the radar file's time span and box, look up the radar value at the same hour in
the nearest 2 km cell and in any cell within --near-km (radar gaps are patchy). Prints per drifter and overall.

  python scripts/drifter_coverage.py --radar data/hfr_wide_20210810.nc \\
      --drifters data/drifters/gdp_hourly_2018-01-01_2025-01-01.csv
"""
import argparse

import numpy as np
import pandas as pd
import xarray as xr


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radar", required=True)
    ap.add_argument("--drifters", required=True, help="CSV saved by scripts/find_drifters.py")
    ap.add_argument("--near-km", type=float, default=5.0)
    a = ap.parse_args(argv)

    with xr.open_dataset(a.radar) as ds:
        ds = ds[["u", "v"]].load()
    t, lat, lon = ds["time"].values, ds["lat"].values, ds["lon"].values
    valid = np.isfinite(ds["u"].values) & np.isfinite(ds["v"].values)
    print(f"radar {a.radar}: {t[0]} -> {t[-1]}, lat {lat.min():.2f}..{lat.max():.2f}, lon {lon.min():.2f}..{lon.max():.2f}")

    df = pd.read_csv(a.drifters, parse_dates=["time"])
    df = df[(df["time"] >= t[0]) & (df["time"] <= t[-1]) & df["lat"].between(lat.min(), lat.max())
            & df["lon"].between(lon.min(), lon.max())]
    if df.empty:
        print("no drifter positions inside this radar file's time span and box")
        return
    dlat = abs(np.diff(lat).mean())
    dlon = abs(np.diff(lon).mean())
    rows = []
    for _, r in df.iterrows():
        it = int(np.argmin(np.abs(t - np.datetime64(r["time"]))))
        iy, ix = int(np.argmin(np.abs(lat - r["lat"]))), int(np.argmin(np.abs(lon - r["lon"])))
        ny = int(np.ceil(a.near_km / 111.2 / dlat))
        nx = int(np.ceil(a.near_km / (111.2 * np.cos(np.radians(r["lat"]))) / dlon))
        win = valid[it, max(iy - ny, 0):iy + ny + 1, max(ix - nx, 0):ix + nx + 1]
        rows.append({"id": r["id"], "time": r["time"], "here": bool(valid[it, iy, ix]), "near": bool(win.any())})
    res = pd.DataFrame(rows)
    print(f"\n{res['id'].nunique()} drifter(s), {len(res)} positions inside the radar box and hours")
    for i, g in res.groupby("id"):
        print(f"  {i}: {len(g):4d} positions, radar data in the same cell {g['here'].mean():.0%}, "
              f"within {a.near_km:g} km {g['near'].mean():.0%}")
    print(f"overall: same cell {res['here'].mean():.0%}, within {a.near_km:g} km {res['near'].mean():.0%}")


if __name__ == "__main__":
    main()
