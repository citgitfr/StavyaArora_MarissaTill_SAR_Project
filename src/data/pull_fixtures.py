"""Pull a HYCOM surface-current slice (u, v) over OPeNDAP into one netCDF file. HF radar comes from
src/data/pull_hfr_ncei.py. Run this yourself (the agent never touches the network). Defaults reproduce the original
fixture.

  python src/data/pull_fixtures.py                                  # original: 2021-08-10..12, SoCal box
  python src/data/pull_fixtures.py --box 31.5 35.5 -123.5 -117 --out data/hycom_wide_20210810.nc

HYCOM is 3-hourly, so let --end run one step past the radar's last hour. The model box should be LARGER than the
radar box: particles that leave every reader stop in OpenDrift. The default URL is GLBy0.08 expt_93.0 for the
start year; a window spanning two years is refused. Never overwrites --out.
"""
import argparse
import os
import sys

import pandas as pd
import xarray as xr

URL = "https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0/uv3z/{year}"


def describe(name, ds):
    print(f"\n===== {name} =====")
    print(ds)
    for v in ds.variables:
        print(f"  {v}: standard_name={ds[v].attrs.get('standard_name')!r} "
              f"units={ds[v].attrs.get('units')!r} dims={ds[v].dims}")
    t = ds["time"].values
    step = f" (step {t[1] - t[0]})" if len(t) > 1 else ""
    print(f"  times: {len(t)} from {t[0]} to {t[-1]}{step}")


def find_var(ds, names, std):
    for v in ds.data_vars:
        if v in names or ds[v].attrs.get("standard_name") == std:
            return v
    sys.exit(f"Could not find {std}. Variables: {list(ds.data_vars)}")


def pull_hycom(t0, t1, box, url, out):
    if os.path.exists(out):
        sys.exit(f"{out} already exists; not overwriting. Remove it yourself to rebuild.")
    lat0, lat1, lon0, lon1 = box
    ds = xr.open_dataset(url, drop_variables=["tau"])
    print("HYCOM raw dims:", dict(ds.sizes), "| variables:", list(ds.data_vars))
    print("HYCOM time range:", ds["time"].values[0], "->", ds["time"].values[-1])
    un = find_var(ds, ("water_u", "u"), "eastward_sea_water_velocity")
    vn = find_var(ds, ("water_v", "v"), "northward_sea_water_velocity")
    lo0, lo1 = lon0, lon1
    if float(ds["lon"].max()) > 180:          # global HYCOM grids often use 0..360
        lo0, lo1 = lon0 % 360, lon1 % 360
    sub = ds[[un, vn]].sel(time=slice(t0, t1), lat=slice(lat0, lat1), lon=slice(lo0, lo1))
    if "depth" in sub.dims:
        print("HYCOM depth levels (first 3):", ds["depth"].values[:3])
        sub = sub.isel(depth=0)
    sub = sub.rename({un: "u", vn: "v"})
    sub = sub.assign_coords(lon=(((sub["lon"] + 180) % 360) - 180)).sortby("lon")
    if min(sub.sizes.values()) == 0:
        sys.exit(f"HYCOM subset empty: {dict(sub.sizes)}")
    sub = sub.load()
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    sub.to_netcdf(out)
    describe("HYCOM (surface level)", sub)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2021-08-10 00:00")
    ap.add_argument("--end", default="2021-08-12 00:00")
    ap.add_argument("--box", nargs=4, type=float, default=[32.8, 34.2, -120.0, -117.5],
                    metavar=("LAT0", "LAT1", "LON0", "LON1"), help="degrees, longitudes -180..180")
    ap.add_argument("--url", help="OPeNDAP URL (default: GLBy0.08 expt_93.0 uv3z for the start year)")
    ap.add_argument("--out", default="fixtures/hycom_uv_slice.nc")
    a = ap.parse_args(argv)
    y0, y1 = pd.Timestamp(a.start).year, pd.Timestamp(a.end).year
    if a.url is None and y0 != y1:
        sys.exit("window spans two years; pull each year separately or pass --url")
    pull_hycom(a.start, a.end, a.box, a.url or URL.format(year=y0), a.out)


if __name__ == "__main__":
    main()
