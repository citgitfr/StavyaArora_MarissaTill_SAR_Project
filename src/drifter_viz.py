"""Pictures for the drifter reality check (src/drifter_eval.py). Offline, about a minute.

1. An animation of ONE forecast window, hour by hour: the real drifter's path grows in black; the model-only
   particles (orange) and radar+model particles (blue) move; light grey shows where the radar has data that hour.
   -> docs/figures/drifter_<id>_window_<MMDD_HH>.gif (one per --window)
2. A summary of ALL windows:
   (a) map: for every window, the drifter's real 24 h path (black) and each forecast's cloud-centre path
   (b) distance from forecast centre to the real drifter, by hour after release (median and middle 50% of windows)
   (c) the 24 h miss for every window, model only vs radar+model, with the share of the drifter's 25 hourly
       positions where the radar had data
   -> docs/figures/drifter_<id>_summary.png

  python src/drifter_viz.py
  python src/drifter_viz.py --window "2021-10-22 12:00" --k 10
"""
import argparse
import importlib.util
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("drifter_eval", REPO / "src" / "drifter_eval.py")
de = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(de)
_ps = importlib.util.spec_from_file_location("plot_style", REPO / "src" / "plot_style.py")
ps = importlib.util.module_from_spec(_ps)
_ps.loader.exec_module(ps)
COL = {k: ps.color(k) for k in ("model_only", "radar+model")}


def radar_hits(radar, obs):
    """Per hour: does the radar have data in the drifter's cell?"""
    t, lat, lon = radar["time"].values, radar["lat"].values, radar["lon"].values
    ok = np.isfinite(radar["u"].values) & np.isfinite(radar["v"].values)
    out = []
    for when, r in obs.iterrows():
        it = int(np.argmin(np.abs(t - np.datetime64(when))))
        out.append(bool(ok[it, int(np.argmin(np.abs(lat - r["lat"]))), int(np.argmin(np.abs(lon - r["lon"])))]))
    return np.array(out)


def animate(track, obs, clouds, radar, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter
    lons = np.concatenate([obs["lon"].values] + [c[0].ravel() for c in clouds.values()])
    lats = np.concatenate([obs["lat"].values] + [c[1].ravel() for c in clouds.values()])
    x0, x1 = np.nanmin(lons) - 0.05, np.nanmax(lons) + 0.05
    y0, y1 = np.nanmin(lats) - 0.05, np.nanmax(lats) + 0.05
    sub = radar.sel(lat=slice(y0, y1), lon=slice(x0, x1))
    valid = (np.isfinite(sub["u"]) & np.isfinite(sub["v"])).values
    rt = sub["time"].values

    fig, ax = plt.subplots(figsize=(8.5, 7))
    fig.subplots_adjust(top=0.80)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_xlabel("Longitude", fontsize=ps.LABEL_SIZE)
    ax.set_ylabel("Latitude", fontsize=ps.LABEL_SIZE)
    ax.plot(track["lon"], track["lat"], color="0.85", lw=1, zorder=1)
    mesh = ax.pcolormesh(sub["lon"], sub["lat"], valid[0], cmap="Greys", vmin=0, vmax=4, shading="auto", zorder=0)
    path_line, = ax.plot([], [], color="k", lw=2, zorder=5, label="Real drifter, path so far")
    head, = ax.plot([], [], "o", color="k", ms=6, zorder=6)
    dots = {k: ax.plot([], [], ".", ms=2, color=COL[k], alpha=0.6, zorder=3, label=f"Forecast particles: {ps.name(k)}")[0] for k in clouds}
    ax.plot(obs["lon"].iloc[0], obs["lat"].iloc[0], "s", color="k", ms=6, zorder=6, label="Release point (drifter at 0 h)")
    ax.plot([], [], "s", color="0.75", label="HF radar has data this hour")
    leg = ax.legend(loc="lower left", fontsize=8)
    for h in leg.legend_handles:
        if hasattr(h, "set_markersize"):
            h.set_markersize(6)
            h.set_alpha(1)
    stamp = fig.suptitle("", fontsize=ps.SUBTITLE_SIZE)
    t0 = obs.index[0]

    def frame(h):
        it = int(np.argmin(np.abs(rt - np.datetime64(t0 + pd.Timedelta(hours=h)))))
        mesh.set_array(valid[it].ravel())
        path_line.set_data(obs["lon"].values[:h + 1], obs["lat"].values[:h + 1])
        head.set_data([obs["lon"].values[h]], [obs["lat"].values[h]])
        txt = []
        for k, (plon, plat) in clouds.items():
            dots[k].set_data(plon[:, h], plat[:, h])
            d = de.haversine_km(np.nanmean(plon[:, h]), np.nanmean(plat[:, h]), obs["lon"].values[h], obs["lat"].values[h])
            txt.append(f"{ps.name(k)} {d:.1f} km")
        stamp.set_text(f"{title}\nHour +{h:02d} ({t0 + pd.Timedelta(hours=h):%Y-%m-%d %H:%M} UTC)\n"
                       "Distance from forecast centre to real drifter: " + "  |  ".join(txt))
        return [mesh, path_line, head, *dots.values(), stamp]

    anim = FuncAnimation(fig, frame, frames=len(obs), interval=400, blit=False)
    anim.save(path, writer=PillowWriter(fps=3))
    plt.close(fig)


def summary(track, results, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(15, 5.2))
    a = fig.add_subplot(1, 3, 1)
    b = fig.add_subplot(1, 3, 2)
    c = fig.add_subplot(1, 3, 3)

    a.plot(track["lon"], track["lat"], color="0.85", lw=1)
    for r in results:
        a.plot(r["obs"]["lon"], r["obs"]["lat"], color="k", lw=1)
        for k in COL:
            a.plot(r[k]["clon"], r[k]["clat"], color=COL[k], lw=0.8, alpha=0.8)
        a.plot(r["obs"]["lon"].iloc[0], r["obs"]["lat"].iloc[0], "o", color="k", ms=2.5)
    a.plot([], [], color="k", label="Real drifter, each 24 h window")
    for k in COL:
        a.plot([], [], color=COL[k], label=f"Forecast centre: {ps.name(k)}")
    a.legend(fontsize=7, loc="lower left")
    a.set_title("(a) Every window: the real path vs the forecast-centre paths", fontsize=ps.SUBTITLE_SIZE - 1)
    a.set_xlabel("Longitude", fontsize=ps.LABEL_SIZE)
    a.set_ylabel("Latitude", fontsize=ps.LABEL_SIZE)

    hours = np.arange(len(results[0]["obs"]))
    for k in COL:
        d = np.array([r[k]["sep"] for r in results])
        b.plot(hours, np.median(d, 0), color=COL[k], lw=2, label=f"{ps.name(k)} (median)")
        b.fill_between(hours, np.percentile(d, 25, 0), np.percentile(d, 75, 0), color=COL[k], alpha=0.2)
    b.set_xlabel("Hours after release", fontsize=ps.LABEL_SIZE)
    b.set_ylabel("Distance from forecast centre to real drifter (km)", fontsize=ps.LABEL_SIZE)
    b.set_title("(b) How fast each forecast drifts away from reality\nline = median over windows, band = middle 50%", fontsize=ps.SUBTITLE_SIZE - 1)
    b.legend(fontsize=8)
    b.grid(alpha=0.3)

    x = np.arange(len(results))
    w = 0.4
    for j, k in enumerate(COL):
        c.bar(x + (j - 0.5) * w, [r[k]["sep"][-1] for r in results], w, color=COL[k], label=ps.name(k))
    c2 = c.twinx()
    c2.plot(x, [100 * r["radar_share"] for r in results], "k.--", lw=0.8, label="HF radar data at the drifter (%)")
    c2.set_ylim(0, 105)
    c2.set_ylabel("Share of the drifter's hours with HF radar data (%, dots)", fontsize=ps.LABEL_SIZE)
    c.set_xticks(x)
    c.set_xticklabels([f"{r['start']:%d %Hh}" for r in results], rotation=90, fontsize=7)
    c.set_ylabel("Miss after 24 h (km)", fontsize=ps.LABEL_SIZE)
    c.set_xlabel("Window start (day, hour UTC)", fontsize=ps.LABEL_SIZE)
    c.set_title("(c) The 24 h miss of every window", fontsize=ps.SUBTITLE_SIZE - 1)
    h1, l1 = c.get_legend_handles_labels()
    h2, l2 = c2.get_legend_handles_labels()
    c.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper left")
    fig.suptitle(title, fontsize=ps.TITLE_SIZE)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main(argv=None):
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radar", default=str(de.DEF_RADAR))
    ap.add_argument("--model", default=str(de.DEF_MODEL))
    ap.add_argument("--drifters", default=str(de.DEF_DRIFTERS))
    ap.add_argument("--id", type=int, default=de.DEF_ID)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--every", type=int, default=12)
    ap.add_argument("--k", type=float, default=10.0, help="diffusivity m2/s")
    ap.add_argument("--window", nargs="+", default=["2021-10-24 12:00", "2021-10-22 12:00"],
                    help="start(s) of the animated window(s); defaults: one where radar helps, one where it does not")
    a = ap.parse_args(argv)

    radar_p, model_p = Path(a.radar), Path(a.model)
    with xr.open_dataset(radar_p) as r:
        radar = r[["u", "v"]].load()
    with xr.open_dataset(model_p) as m:
        t1 = min(pd.Timestamp(radar.time.values[-1]), pd.Timestamp(m.time.values[-1]))
    t0 = pd.Timestamp(radar.time.values[0])
    track = de.drifter_track(a.drifters, a.id, t0, t1)
    starts = de.windows(track, a.hours, a.every)
    forcings = {"model_only": [model_p], "radar+model": [radar_p, model_p]}

    wanted = {pd.Timestamp(w) for w in a.window}
    results, anim_clouds = [], {}
    for s in starts:
        obs = track.loc[s:s + pd.Timedelta(hours=a.hours)]
        r = {"start": s, "obs": obs, "radar_share": float(radar_hits(radar, obs).mean())}
        clouds = {}
        for k, readers in forcings.items():
            plon, plat = de.run(readers, obs["lon"].iloc[0], obs["lat"].iloc[0], s, a.hours, a.k)
            clon, clat = np.nanmean(plon, 0), np.nanmean(plat, 0)
            r[k] = {"clon": clon, "clat": clat, "sep": de.haversine_km(clon, clat, obs["lon"].values, obs["lat"].values)}
            clouds[k] = (plon, plat)
        if s in wanted:
            anim_clouds[s] = (obs, clouds)
        results.append(r)
        print(f"  {s} done")

    fig_dir = REPO / "docs" / "figures"
    title = (f"Clean forecasts vs real drifter {a.id}, {starts[0]:%Y-%m-%d} to "
             f"{starts[-1] + pd.Timedelta(hours=a.hours):%Y-%m-%d}: {len(starts)} forecasts of {a.hours} h "
             f"(OceanDrift, no wind, spread {a.k:g} m²/s)")
    summary(track, results, fig_dir / f"drifter_{a.id}_summary.png", title)
    print(f"wrote docs/figures/drifter_{a.id}_summary.png")
    missing = wanted - set(anim_clouds)
    if missing:
        sys.exit(f"--window {sorted(map(str, missing))} not among the window starts: {[str(s) for s in starts]}")
    for s, (obs, clouds) in sorted(anim_clouds.items()):
        name = f"drifter_{a.id}_window_{s:%m%d_%H}.gif"
        animate(track, obs, clouds, radar, fig_dir / name,
                f"Clean forecasts vs real drifter {a.id}\none 24 h forecast released at the drifter, "
                f"{obs.index[0]:%Y-%m-%d %H:%M} UTC")
        print(f"wrote docs/figures/{name}")


if __name__ == "__main__":
    sys.exit(main())
