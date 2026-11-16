"""Find Global Drifter Program (GDP) hourly drifter tracks inside the radar box and window. Run this yourself
(the agent never touches the network). Makes at most three small requests to NOAA AOML's ERDDAP server:
  1. search the server for hourly drifter datasets and print them,
  2. read the chosen dataset's variable list (nothing is guessed),
  3. download only ID/time/position (+ drogue and position-error fields if the dataset has them) inside the box.
It prints a per-drifter summary and, when the window overlaps the radar fixture, how much of each track lies where
the radar has data. Raw rows are saved to data/drifters/ (gitignored); nothing in fixtures/ is touched.

Run from the repository root:
  python scripts/find_drifters.py                                    # fixture box and window (2021-08-10..12)
  python scripts/find_drifters.py --start 2021-06-01 --end 2021-10-01 # wider window, to look for another period
  python scripts/find_drifters.py --dataset <id>                     # if the search shows a different id
"""
import argparse
import io
import sys
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[1]
SERVER = "https://erddap.aoml.noaa.gov/gdp/erddap"
DEFAULT_DATASET = "drifter_hourly_qc"
RADAR = REPO / "fixtures" / "hfr_2km_slice.nc"
TIMEOUT = 120
CANDIDATES = {                 # first name the dataset actually has wins
    "id": ["ID", "id", "drifter_id"],
    "time": ["time"],
    "lat": ["latitude", "lat"],
    "lon": ["longitude", "lon"],
}
OPTIONAL = ["drogue_status", "drogue_lost_date", "DrogueLostDate", "drogue_off_date", "err_lat", "err_lon",
            "location_type", "typebuoy", "WMO"]


def get_csv(url):
    r = requests.get(url, timeout=TIMEOUT)
    print(f"GET {url[:150]}{'...' if len(url) > 150 else ''} -> HTTP {r.status_code}, {len(r.content)} bytes")
    if r.status_code != 200:
        print(r.text[:500])
        return None
    return pd.read_csv(io.StringIO(r.text), skiprows=[1])         # ERDDAP's second CSV row holds units


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2021-08-10T00:00:00Z")
    ap.add_argument("--end", default="2021-08-12T00:00:00Z")
    ap.add_argument("--box", nargs=4, type=float, default=[32.8, 34.2, -120.0, -117.5], metavar=("LAT0", "LAT1", "LON0", "LON1"))
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--skip-search", action="store_true")
    a = ap.parse_args(argv)
    lat0, lat1, lon0, lon1 = a.box

    if not a.skip_search:
        print("== 1. hourly drifter datasets on the server")
        s = get_csv(f"{SERVER}/search/index.csv?page=1&itemsPerPage=50&searchFor=drifter+hourly")
        if s is not None:
            cols = [c for c in s.columns if c in ("Dataset ID", "Title")]
            print(s[cols].to_string(index=False))

    print(f"\n== 2. variables in {a.dataset}")
    info = get_csv(f"{SERVER}/info/{a.dataset}/index.csv")
    if info is None:
        sys.exit("Could not read the dataset info. Pick an id from the search list and pass --dataset.")
    names = set(info.loc[info["Row Type"] == "variable", "Variable Name"])
    print(sorted(names))
    pick = {}
    for k, opts in CANDIDATES.items():
        hit = [o for o in opts if o in names]
        if not hit:
            sys.exit(f"no variable for {k} (tried {opts}); stopping rather than guessing")
        pick[k] = hit[0]
    extra = [o for o in OPTIONAL if o in names]
    print(f"using {pick} + optional {extra}")

    print(f"\n== 3. rows inside lat {lat0}..{lat1}, lon {lon0}..{lon1}, {a.start} .. {a.end}")
    vars_ = ",".join([pick["id"], pick["time"], pick["lat"], pick["lon"], *extra])
    cons = (f"{vars_}&{pick['time']}>={a.start}&{pick['time']}<={a.end}"
            f"&{pick['lat']}>={lat0}&{pick['lat']}<={lat1}&{pick['lon']}>={lon0}&{pick['lon']}<={lon1}")
    q = f"{SERVER}/tabledap/{a.dataset}.csv?" + quote(cons, safe=",&=_.-:")      # encodes < and >
    df = get_csv(q)
    if df is None or df.empty:
        print("\nNo drifter positions in this box and window. Try a wider window with --start/--end to find another period.")
        return
    df = df.rename(columns={pick["id"]: "id", pick["time"]: "time", pick["lat"]: "lat", pick["lon"]: "lon"})
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)

    out = REPO / "data" / "drifters"
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{a.start[:10]}_{a.end[:10]}"
    raw = out / f"gdp_hourly_{tag}.csv"
    df.to_csv(raw, index=False)

    cover = None
    if RADAR.exists():
        import xarray as xr
        with xr.open_dataset(RADAR) as ds:
            if df["time"].min() <= pd.Timestamp(ds.time.values[-1]) and df["time"].max() >= pd.Timestamp(ds.time.values[0]):
                cover = ds[["u", "v"]].load()

    print(f"\n{df['id'].nunique()} drifter(s), {len(df)} hourly positions; saved {raw.relative_to(REPO)}")
    if "drogue_lost_date" in df:
        lost = pd.to_datetime(df["drogue_lost_date"], utc=True, errors="coerce").dt.tz_localize(None)
        df["drogued"] = np.where(lost.isna(), np.nan, (df["time"] < lost).astype(float))   # NaN = date unknown
    print("\nvisits (a new visit starts after a gap of more than 6 h), sorted by start; >= 24 h visits marked *")
    seg = []
    for i, g in df.sort_values("time").groupby("id"):
        k = (g["time"].diff() > pd.Timedelta(hours=6)).cumsum()
        for _, s in g.groupby(k):
            seg.append((s["time"].min(), s["time"].max(), i, len(s),
                        None if "drogued" not in s else s["drogued"].mean(skipna=True)))
    for t0, t1, i, n, dr in sorted(seg):
        hrs = (t1 - t0) / pd.Timedelta(hours=1) + 1
        d = "" if dr is None or pd.isna(dr) else f", drogued {dr:.0%} of the time"
        print(f"  {'*' if hrs >= 24 else ' '} {t0:%Y-%m-%d %H:%M} -> {t1:%Y-%m-%d %H:%M} ({hrs:.0f} h, {n} positions)  id {i}{d}")
    if len(seg) > 40:
        return
    print("\nper drifter:")
    for i, g in df.groupby("id"):
        line = (f"  id {i}: {len(g)} positions, {g['time'].min()} -> {g['time'].max()}, "
                f"lat {g['lat'].min():.2f}..{g['lat'].max():.2f}, lon {g['lon'].min():.2f}..{g['lon'].max():.2f}")
        for c in extra:
            if c.startswith("drogue"):
                line += f", {c}={g[c].iloc[0]}"
        t_r = cover["time"].values if cover is not None else None
        if cover is not None and g["time"].min() <= pd.Timestamp(t_r[-1]) and g["time"].max() >= pd.Timestamp(t_r[0]):
            hit = []                       # only when this drifter's own times overlap the radar fixture's
            for _, r in g.iterrows():
                try:
                    p = cover.sel(time=r["time"], lat=r["lat"], lon=r["lon"], method="nearest")
                    hit.append(bool(np.isfinite(p["u"].item())))
                except (KeyError, ValueError):
                    hit.append(False)
            line += f", radar has data at {np.mean(hit):.0%} of its positions"
        print(line)


if __name__ == "__main__":
    main()
