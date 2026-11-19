"""Pull ERA5 hourly 10 m wind (and, optionally, wave Stokes drift) for a box and window, and write files OpenDrift
can read directly. Run this yourself (the agent never touches the network).

ERA5 comes from the Copernicus Climate Data Store (CDS), dataset "reanalysis-era5-single-levels". It needs a free
CDS account and the `cdsapi` package:
  1. Register at https://cds.climate.copernicus.eu and accept the ERA5 licence on the dataset's page.
  2. Put your key in ~/.cdsapirc (your home folder, NEVER in this repository):
         url: https://cds.climate.copernicus.eu/api
         key: <your-personal-access-token>
  3. pip install cdsapi   (or: uv pip install cdsapi)

Defaults are the October 2021 drifter window, over the HYCOM box plus a 0.5 degree margin (ERA5 is a 0.25 degree
grid, so a margin keeps edge particles inside the wind field):

  python src/data/pull_era5.py                     # wind  -> data/era5_wind_20211018.nc
  python src/data/pull_era5.py --stokes            # also Stokes drift -> data/era5_stokes_20211018.nc

Wind and Stokes drift are separate requests and separate files: ERA5 wave fields are on a coarser grid and are
missing near the coast. Raw CDS downloads are kept in --raw-dir and reused if present. Never overwrites an output.

Output contract (what OpenDrift's generic reader needs):
  wind:   variables x_wind, y_wind (m s-1), standard_name eastward_wind / northward_wind, dims (time, lat, lon)
  stokes: variables x_stokes, y_stokes (m s-1), standard_name sea_surface_wave_stokes_drift_x/y_velocity
  lat ascending, lon in -180..180 ascending, time in UTC, exactly --start..--end (inclusive, hourly).
"""
import argparse
import os
import sys
import zipfile

import numpy as np
import pandas as pd
import xarray as xr

DATASET = "reanalysis-era5-single-levels"
WIND_VARS = ["10m_u_component_of_wind", "10m_v_component_of_wind"]
STOKES_VARS = ["u_component_stokes_drift", "v_component_stokes_drift"]
# names CDS uses inside the netCDF (first match wins) -> our name, CF standard name
WIND_MAP = {"x_wind": (["u10"], "eastward_wind"), "y_wind": (["v10"], "northward_wind")}
STOKES_MAP = {"x_stokes": (["ust"], "sea_surface_wave_stokes_drift_x_velocity"),
              "y_stokes": (["vst"], "sea_surface_wave_stokes_drift_y_velocity")}


def request_for(t0, t1, box, variables):
    """One CDS request per calendar month in [t0, t1]. Returns a list of (tag, request dict)."""
    lat0, lat1, lon0, lon1 = box
    hours = pd.date_range(pd.Timestamp(t0).floor("h"), pd.Timestamp(t1).floor("h"), freq="h")
    if len(hours) == 0:
        sys.exit("empty window: --end is before --start")
    out = []
    for (y, m), g in pd.Series(hours, index=hours).groupby([hours.year, hours.month]):
        out.append((f"{y:04d}{m:02d}", {
            "product_type": ["reanalysis"],
            "variable": variables,
            "year": [f"{y:04d}"],
            "month": [f"{m:02d}"],
            "day": sorted({f"{d:02d}" for d in g.index.day}),
            "time": [f"{h:02d}:00" for h in range(24)],
            "area": [lat1, lon0, lat0, lon1],          # CDS order: North, West, South, East
            "data_format": "netcdf",
            "download_format": "unarchived",
        }))
    return out


def unpack(path):
    """CDS sometimes returns a zip even when asked for one file. Return the netCDF path(s) inside."""
    if not zipfile.is_zipfile(path):
        return [path]
    d = path + "_unzipped"
    with zipfile.ZipFile(path) as z:
        z.extractall(d)
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".nc"))


def tidy(ds, mapping, t0, t1):
    """Rename an ERA5/CDS dataset to the output contract and cut it to [t0, t1]. Pure function (tested offline)."""
    ren = {}
    for a, b in (("valid_time", "time"), ("latitude", "lat"), ("longitude", "lon")):
        if a in ds.dims or a in ds.coords:
            ren[a] = b
    ds = ds.rename(ren)
    keep = {}
    for new, (names, std) in mapping.items():
        src = next((n for n in names if n in ds.data_vars), None)
        if src is None:
            sys.exit(f"could not find {names} for {new}; variables are {list(ds.data_vars)}")
        da = ds[src]
        for extra in ("number", "expver"):              # ensemble/version dims CDS sometimes adds
            if extra in da.dims:
                da = da.isel({extra: 0})
        da = da.drop_vars([c for c in da.coords if c not in ("time", "lat", "lon")])
        keep[new] = da.astype("float32").assign_attrs(standard_name=std, units="m s-1",
                                                      long_name=da.attrs.get("long_name", new))
    out = xr.Dataset(keep)
    out = out.assign_coords(lon=(((out["lon"] + 180) % 360) - 180)).sortby("lon").sortby("lat").sortby("time")
    out = out.sel(time=slice(pd.Timestamp(t0), pd.Timestamp(t1)))
    want = pd.date_range(pd.Timestamp(t0).floor("h"), pd.Timestamp(t1).floor("h"), freq="h")
    missing = want.difference(pd.DatetimeIndex(out["time"].values))
    if len(missing):
        sys.exit(f"{len(missing)} hours missing from the download, first {missing[0]}")
    out["lat"].attrs.update(standard_name="latitude", units="degrees_north")
    out["lon"].attrs.update(standard_name="longitude", units="degrees_east")
    out.attrs.update(source=f"ERA5 hourly single levels ({DATASET}), Copernicus Climate Change Service",
                     note="Generated using Copernicus Climate Change Service information; tidied by src/data/pull_era5.py")
    return out


def describe(name, ds):
    print(f"\n===== {name} =====")
    print(f"  dims {dict(ds.sizes)}  lat {float(ds.lat.min()):.2f}..{float(ds.lat.max()):.2f}"
          f"  lon {float(ds.lon.min()):.2f}..{float(ds.lon.max()):.2f}")
    print(f"  time {ds.time.values[0]} .. {ds.time.values[-1]} ({ds.sizes['time']} hours)")
    for v in ds.data_vars:
        a = ds[v].values
        print(f"  {v}: standard_name={ds[v].attrs['standard_name']!r} NaN share {np.isnan(a).mean():.2f}"
              f"  |value| median {np.nanmedian(np.abs(a)):.2f} max {np.nanmax(np.abs(a)):.2f} m/s")


def pull(kind, variables, mapping, a):
    out = a.out_stokes if kind == "stokes" else a.out
    if os.path.exists(out):
        print(f"{out} already exists; skipping (never overwritten). Remove it yourself to rebuild.")
        return
    os.makedirs(a.raw_dir, exist_ok=True)
    files = []
    for tag, req in request_for(a.start, a.end, a.box, variables):
        raw = os.path.join(a.raw_dir, f"era5_{kind}_{tag}_{'_'.join(str(x) for x in req['area'])}"
                                      f"_{req['day'][0]}-{req['day'][-1]}.nc")
        if os.path.exists(raw):
            print(f"reusing {raw}")
        else:
            try:
                import cdsapi
            except ImportError:
                sys.exit("cdsapi is not installed: pip install cdsapi (see the instructions at the top of this file)")
            print(f"requesting {kind} {tag} days {req['day'][0]}..{req['day'][-1]} (CDS queues requests; this can"
                  " take a few minutes)")
            cdsapi.Client().retrieve(DATASET, req, raw)
        files += unpack(raw)
    ds = xr.open_mfdataset(files, combine="by_coords") if len(files) > 1 else xr.open_dataset(files[0])
    res = tidy(ds, mapping, a.start, a.end).load()
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    res.to_netcdf(out)
    describe(f"{kind} -> {out}", res)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2021-10-18 00:00", help="first hour, UTC, inclusive")
    ap.add_argument("--end", default="2021-10-29 00:00", help="last hour, UTC, inclusive")
    ap.add_argument("--box", nargs=4, type=float, default=[32.5, 37.0, -123.5, -118.5],
                    metavar=("LAT0", "LAT1", "LON0", "LON1"), help="degrees, longitudes -180..180")
    ap.add_argument("--out", default="data/era5_wind_20211018.nc")
    ap.add_argument("--stokes", action="store_true", help="also pull wave Stokes drift")
    ap.add_argument("--out-stokes", default="data/era5_stokes_20211018.nc")
    ap.add_argument("--raw-dir", default="data/era5_raw")
    a = ap.parse_args(argv)
    pull("wind", WIND_VARS, WIND_MAP, a)
    if a.stokes:
        pull("stokes", STOKES_VARS, STOKES_MAP, a)


if __name__ == "__main__":
    main()
