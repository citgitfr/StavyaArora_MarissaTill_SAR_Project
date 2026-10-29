"""Download NCEI HF-radar 2 km hourly files for the SAR fixture and cut a slice.
File list comes from data/ncei_listing.txt (output of scripts/list_ncei.py); the
catalog is not requested again. Run this yourself (the agent never touches the network)."""
import os
import re
import sys
import time
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import requests
import xarray as xr

# ---- EDIT THESE ------------------------------------------------------
T0, T1 = "2021-08-10 00:00", "2021-08-11 23:00"   # inclusive, by file-name timestamp
LAT0, LAT1 = 32.8, 34.2
LON0, LON1 = -120.0, -117.5              # degrees east, -180..180
LISTING = "data/ncei_listing.txt"
RAW_DIR = "data/hfr_raw"
OUT = "fixtures/hfr_2km_slice.nc"
# ----------------------------------------------------------------------
HEADERS = {"User-Agent": "caltech-me295-student"}
NAME = re.compile(r"(\d{12})_hfr_uswc_2km_rtv_uwls_SIO\.nc")   # excludes NDBC and 25hr_average
U_STD = "surface_eastward_sea_water_velocity"
V_STD = "surface_northward_sea_water_velocity"


def build_list():
    expected = [t.strftime("%Y%m%d%H%M") for t in pd.date_range(T0, T1, freq="h")]
    found = {}
    with open(LISTING, encoding="utf-8-sig") as f:
        for line in f:
            parts = [p.strip() for p in line.split(" | ")]
            if len(parts) != 3:
                continue
            name, url = parts[0].lstrip("*? ").strip(), parts[2]
            m = NAME.fullmatch(name)
            if not m or m.group(1) not in expected:
                continue
            u = urlparse(url)
            if (u.scheme != "https" or u.netloc != "www.ncei.noaa.gov"
                    or os.path.basename(u.path) != name):
                sys.exit(f"Unexpected URL for {name}: {url}")
            found[m.group(1)] = (name, url)
    missing = [t for t in expected if t not in found]
    print(f"Files in window: {len(found)} of {len(expected)} expected")
    print(f"Missing hours: {missing if missing else 'none'}")
    if len(found) != 48:
        sys.exit("Count is not 48; stopping before any download.")
    return [found[t] for t in expected]


def download(files):
    os.makedirs(RAW_DIR, exist_ok=True)
    first = True
    for name, url in files:
        path = os.path.join(RAW_DIR, name)
        if os.path.exists(path):
            print(f"  skip (present) {name}")
            continue
        if not first:
            time.sleep(2)
        first = False
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            sys.exit(f"HTTP {r.status_code} for {url}; stopping (no retry).")
        with open(path + ".part", "wb") as f:      # rename only once complete
            f.write(r.content)
        os.replace(path + ".part", path)
        print(f"  got {name} ({len(r.content)} bytes)")
    return [os.path.join(RAW_DIR, name) for name, _ in files]


def find_by_std(ds, std, coords=False):
    pool = ds.variables if coords else ds.data_vars
    hits = [v for v in pool if ds[v].attrs.get("standard_name") == std]
    if len(hits) != 1:
        sys.exit(f"Expected one variable with standard_name={std!r}, found {hits}. "
                 f"Variables: {list(ds.variables)}")
    return hits[0]


def describe(name, ds, tn):
    print(f"\n===== {name} =====")
    print("dimensions:", dict(ds.sizes))
    for v in ds.variables:
        print(f"  {v}: standard_name={ds[v].attrs.get('standard_name')!r} "
              f"units={ds[v].attrs.get('units')!r} dims={ds[v].dims}")
    t = ds[tn].values
    steps = np.unique(np.diff(t)) if len(t) > 1 else []
    print(f"  times: {len(t)} from {t[0]} to {t[-1]} | step(s): {list(steps)}")


def merge_and_cut(paths):
    if os.path.exists(OUT):
        sys.exit(f"{OUT} already exists; not overwriting. Remove it yourself to rebuild.")
    parts = [xr.open_dataset(p) for p in paths]
    ds0 = parts[0]
    un, vn = find_by_std(ds0, U_STD), find_by_std(ds0, V_STD)
    tn = find_by_std(ds0, "time", coords=True)
    yn = find_by_std(ds0, "latitude", coords=True)
    xn = find_by_std(ds0, "longitude", coords=True)
    print(f"velocity: u={un!r} v={vn!r} | coords: time={tn!r} lat={yn!r} lon={xn!r}")
    ds = xr.concat([p[[un, vn]] for p in parts], dim=tn).sortby(tn)
    if float(ds[xn].max()) > 180:
        sys.exit("Longitude is 0..360 in this product; stopping rather than converting silently.")
    lat_slice = slice(LAT0, LAT1) if ds[yn][0] < ds[yn][-1] else slice(LAT1, LAT0)
    sub = ds.sel({yn: lat_slice, xn: slice(LON0, LON1)})
    if min(sub.sizes.values()) == 0:
        sys.exit(f"HFR subset empty: {dict(sub.sizes)}")
    sub = sub.load()
    for p in parts:
        p.close()
    os.makedirs("fixtures", exist_ok=True)
    sub.to_netcdf(OUT)
    describe(f"HF radar 2 km -> {OUT}", sub, tn)


if __name__ == "__main__":
    files = build_list()
    paths = download(files)
    merge_and_cut(paths)
