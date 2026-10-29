"""Pull the HYCOM half of the SAR forcing demo (HF radar comes from NCEI via pull_hfr_ncei.py).
Run this yourself in a terminal (the agent never touches the network)."""
import os
import sys
import xarray as xr

# ---- EDIT THESE ------------------------------------------------------
T0, T1 = "2021-08-10 00:00", "2021-08-12 00:00"   # HYCOM is 3-hourly; the extra step covers the radar's last hours
LAT0, LAT1 = 32.8, 34.2
LON0, LON1 = -120.0, -117.5              # degrees east, -180..180
HYCOM_URL = "https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0/uv3z/2021"
OUT = "fixtures/hycom_uv_slice.nc"
# ----------------------------------------------------------------------
os.makedirs("fixtures", exist_ok=True)


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


def pull_hycom():
    if os.path.exists(OUT):
        sys.exit(f"{OUT} already exists; not overwriting. Remove it yourself to rebuild.")
    ds = xr.open_dataset(HYCOM_URL, drop_variables=["tau"])
    print("HYCOM raw dims:", dict(ds.sizes), "| variables:", list(ds.data_vars))
    print("HYCOM time range:", ds["time"].values[0], "->", ds["time"].values[-1])
    un = find_var(ds, ("water_u", "u"), "eastward_sea_water_velocity")
    vn = find_var(ds, ("water_v", "v"), "northward_sea_water_velocity")
    lo0, lo1 = LON0, LON1
    if float(ds["lon"].max()) > 180:          # global HYCOM grids often use 0..360
        lo0, lo1 = LON0 % 360, LON1 % 360
    sub = ds[[un, vn]].sel(time=slice(T0, T1), lat=slice(LAT0, LAT1), lon=slice(lo0, lo1))
    if "depth" in sub.dims:
        print("HYCOM depth levels (first 3):", ds["depth"].values[:3])
        sub = sub.isel(depth=0)
    sub = sub.rename({un: "u", vn: "v"})
    sub = sub.assign_coords(lon=(((sub["lon"] + 180) % 360) - 180)).sortby("lon")
    if min(sub.sizes.values()) == 0:
        sys.exit(f"HYCOM subset empty: {dict(sub.sizes)}")
    sub = sub.load()
    sub.to_netcdf(OUT)
    describe("HYCOM (surface level)", sub)


if __name__ == "__main__":
    pull_hycom()