"""Baseline vs agent pictures for the faulty October cases, against the real drifter. Offline; does not read the key.

1. decision_summary_oct.png - the drifter-summary layout, comparing No QC (radar as delivered), the scripted baseline,
   the Claude agent and the perfect repair, over every case and every 24 h window:
   (a) one case: the real drifter's path in each window and each policy's forecast-centre path
   (b) distance from forecast centre to the real drifter by hour, over all cases and windows (median, middle 50%)
   (c) every case: mean 24 h miss over the windows, per policy
2. baseline_vs_agent_oct.png - decisions side by side, per-case miss baseline vs agent, metrics side by side
3. decision_oct_<case>.gif - one 24 h forecast per policy, animated, with the perfect repair and the real drifter

  python src/decision_viz.py --agent-run oct_rep1
  python src/decision_viz.py --agent-run oct_rep1 --cases case14 case06 --window "2021-10-24 12:00"
"""
import argparse
import importlib.util
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


de = _load("drifter_eval", "src/drifter_eval.py")
sc = _load("score", "src/score.py")
dre = _load("decision_reality", "src/decision_reality.py")
ps = _load("plot_style", "src/plot_style.py")
CHOICE_COL = {"fused": "#c6dbef", "repair": "#c7e9c0", "model_only": "#fdd0a2", "escalate": "#dadaeb", None: "white"}


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


# ---- 1. decision summary ---------------------------------------------------------------------
def summary_runs(pols, ids, track, starts, clean, model, cases_dir, k):
    """{policy: {case: [(clon, clat, sep_by_hour) per window]}}; identical forcings are run once."""
    cache, out = {}, {}
    for name, (decs, work) in pols.items():
        out[name] = {}
        for c in ids:
            if name == "clean_radar":
                readers = [clean, model]
            else:
                readers, note = dre.forcing_for(decs.get(c), c, work, cases_dir, model)
                if readers is None:
                    if note == "escalated":            # handed to a person: no forecast to draw
                        out[name][c] = []
                        continue
                    sys.exit(f"{name} {c}: repaired file missing")
            h = dre.dr.forcing_hash(readers[0] if len(readers) == 2 else None, readers[-1])
            if h not in cache:
                per = []
                for s in starts:
                    obs = track.loc[s:s + pd.Timedelta(hours=24)]
                    plon, plat = de.run(readers, obs["lon"].iloc[0], obs["lat"].iloc[0], s, 24, k)
                    clon, clat = np.nanmean(plon, 0), np.nanmean(plat, 0)
                    per.append((clon, clat, de.haversine_km(clon, clat, obs["lon"].values, obs["lat"].values)))
                cache[h] = per
            out[name][c] = cache[h]
        print(f"  summary runs: {name} done ({len(cache)} distinct forcings)")
    return out


def decision_summary(res, ids, track, starts, map_case, decs_by_policy, path):
    plt = _plt()
    order = ["always_fused", "baseline", "agent", "clean_radar"]
    style = {"always_fused": dict(lw=1.2), "baseline": dict(lw=2.2), "agent": dict(lw=1.4, ls="--"),
             "clean_radar": dict(lw=1.0, ls=":")}
    fig = plt.figure(figsize=(18, 6.2))
    gs = fig.add_gridspec(1, 3, width_ratios=[1, 1, 1.35])

    a = fig.add_subplot(gs[0])
    a.plot(track["lon"], track["lat"], color="0.85", lw=1, label="Drifter, whole 10-day track")
    for i, s in enumerate(starts):
        obs = track.loc[s:s + pd.Timedelta(hours=24)]
        a.plot(obs["lon"], obs["lat"], color="k", lw=1, label="Real drifter, each 24 h window" if i == 0 else None)
        a.plot(obs["lon"].iloc[0], obs["lat"].iloc[0], "o", color="k", ms=2.5)
    for p in order:
        for i, (clon, clat, _) in enumerate(res[p][map_case]):
            a.plot(clon, clat, color=ps.color(p), alpha=0.85, label=ps.name(p) if i == 0 else None, **style[p])
    lons = np.concatenate([track["lon"].values] + [r[0] for p in ("baseline", "agent") for r in res[p][map_case]])
    lats = np.concatenate([track["lat"].values] + [r[1] for p in ("baseline", "agent") for r in res[p][map_case]])
    a.set_xlim(np.nanmin(lons) - 0.05, np.nanmax(lons) + 0.05)
    a.set_ylim(np.nanmin(lats) - 0.05, np.nanmax(lats) + 0.05)
    a.set_xlabel("Longitude", fontsize=ps.LABEL_SIZE)
    a.set_ylabel("Latitude", fontsize=ps.LABEL_SIZE)
    dec_txt = "  |  ".join(f"{ps.name(p).split(' (')[0]}: {ps.decision_text(decs_by_policy[p].get(map_case))}"
                           for p in ("baseline", "agent"))
    a.set_title(f"(a) {ps.case_label(map_case)}: forecast-centre paths vs the real drifter\n{dec_txt}",
                fontsize=ps.SUBTITLE_SIZE - 1)
    a.legend(fontsize=ps.LEGEND_SIZE - 1, loc="lower left")

    b = fig.add_subplot(gs[1])
    hours = np.arange(25)
    for p in order:
        d = np.array([r[2] for c in ids for r in res[p][c]])
        b.plot(hours, np.median(d, 0), color=ps.color(p), label=f"{ps.name(p)} (median)", **style[p])
        if p != "clean_radar":
            b.fill_between(hours, np.percentile(d, 25, 0), np.percentile(d, 75, 0), color=ps.color(p), alpha=0.12)
    b.set_xlabel("Hours after release", fontsize=ps.LABEL_SIZE)
    b.set_ylabel("Distance from forecast centre to real drifter (km)", fontsize=ps.LABEL_SIZE)
    b.set_title(f"(b) How fast each policy's forecast drifts away from reality\n"
                f"all {len(ids)} cases x {len(starts)} windows; line = median, band = middle 50%",
                fontsize=ps.SUBTITLE_SIZE - 1)
    b.legend(fontsize=ps.LEGEND_SIZE, loc="upper left")
    b.grid(alpha=0.3)

    c = fig.add_subplot(gs[2])
    x = np.arange(len(ids))
    bars = ["always_fused", "baseline", "agent"]
    w = 0.27
    for j, p in enumerate(bars):
        vals = [np.mean([r[2][-1] for r in res[p][cid]]) for cid in ids]
        c.bar(x + (j - 1) * w, vals, w, color=ps.color(p), label=ps.name(p),
              hatch="//" if p == "agent" else None, edgecolor="white" if p == "agent" else None)
    clean_vals = [np.mean([r[2][-1] for r in res["clean_radar"][cid]]) for cid in ids]
    c.plot(x, clean_vals, "_", color=ps.color("clean_radar"), ms=22, mew=2.5, label=ps.name("clean_radar"))
    c.set_xticks(x)
    c.set_xticklabels([str(int(cid.replace("case", ""))) for cid in ids], fontsize=ps.LABEL_SIZE)
    c.set_xlabel("Case", fontsize=ps.LABEL_SIZE)
    c.set_ylabel("Mean 24 h miss vs real drifter (km)", fontsize=ps.LABEL_SIZE)
    c.set_title("(c) Every faulty case: 24 h miss, averaged over the windows", fontsize=ps.SUBTITLE_SIZE - 1)
    c.legend(fontsize=ps.LEGEND_SIZE, loc="upper left")
    c.grid(axis="y", alpha=0.3)
    fig.suptitle(f"No QC vs scripted baseline vs Claude agent on the faulty October 2021 cases, judged against real "
                 f"drifter {de.DEF_ID}", fontsize=ps.TITLE_SIZE)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---- 2. baseline vs agent --------------------------------------------------------------------
def baseline_vs_agent(ids, base, agent, agent_run, reality, scores, batch, path):
    plt = _plt()
    fig = plt.figure(figsize=(17, 6))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.3, 1, 1.1])

    a = fig.add_subplot(gs[0])
    for i, c in enumerate(ids):
        for j, decs in enumerate((base, agent)):
            d = decs.get(c) or {}
            a.add_patch(plt.Rectangle((j, i), 1, 1, color=CHOICE_COL.get(d.get("choice")), ec="white"))
            a.text(j + 0.5, i + 0.5, ps.decision_text(d), ha="center", va="center", fontsize=7)
        same = (base.get(c, {}).get("choice"), [x.get("action") for x in sc.repairs_of(base.get(c, {}))]) == \
               (agent.get(c, {}).get("choice"), [x.get("action") for x in sc.repairs_of(agent.get(c, {}))])
        a.text(2.08, i + 0.5, "same action" if same else "DIFFERENT", va="center", fontsize=7.5,
               color="#2ca02c" if same else "#d62728")
    a.set_xlim(0, 2.75)
    a.set_ylim(len(ids), 0)
    a.set_xticks([0.5, 1.5])
    a.set_xticklabels([ps.name("baseline"), f"{ps.name('agent')} ({agent_run})"], fontsize=ps.LABEL_SIZE)
    a.set_yticks(np.arange(len(ids)) + 0.5)
    a.set_yticklabels([ps.case_label(c) for c in ids], fontsize=8)
    a.set_title("(a) The decision each made on every faulty case", fontsize=ps.SUBTITLE_SIZE)
    for s in a.spines.values():
        s.set_visible(False)
    for ch, col in CHOICE_COL.items():
        if ch:
            a.add_patch(plt.Rectangle((0, 0), 0, 0, color=col, label={"fused": "use radar as is", "repair": "repair",
                                                                       "model_only": "model only",
                                                                       "escalate": "escalate"}[ch]))
    a.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.4, -0.04), ncol=4, frameon=False)

    b = fig.add_subplot(gs[1])
    r = reality.pivot_table(index="case", columns="policy", values="median_sep_km_24h")
    ag = f"agent_{agent_run}"
    cols = [k for k in ("baseline", ag, "clean_radar") if k in r]
    lim = [float(r[cols].min().min()) - 1, float(r[cols].max().max()) + 1]
    b.plot(lim, lim, color="0.7", lw=1, label="Same outcome (diagonal)")
    b.scatter(r["baseline"], r[ag], color=ps.color("agent"), zorder=3, label="One faulty case")
    if "clean_radar" in r:
        b.axhline(r["clean_radar"].mean(), color=ps.color("clean_radar"), ls=":", lw=1.2,
                  label=f"{ps.name('clean_radar')}, mean")
    for c in r.index:
        b.annotate(str(int(c.replace("case", ""))), (r.loc[c, "baseline"], r.loc[c, ag]), fontsize=7,
                   xytext=(4, 2), textcoords="offset points")
    b.set_xlim(lim)
    b.set_ylim(lim)
    b.set_xlabel(f"{ps.name('baseline')}: median 24 h miss (km)", fontsize=ps.LABEL_SIZE)
    b.set_ylabel(f"{ps.name('agent')}: median 24 h miss (km)", fontsize=ps.LABEL_SIZE)
    b.set_title("(b) Forecast error vs the real drifter, case by case", fontsize=ps.SUBTITLE_SIZE)
    b.legend(fontsize=ps.LEGEND_SIZE, loc="upper left")
    b.grid(alpha=0.3)

    c_ax = fig.add_subplot(gs[2])
    metrics, bv, av = [], [], []
    if scores and ag in scores["policies"]:
        pb, pa = scores["policies"]["baseline"], scores["policies"][ag]
        metrics += ["Acceptable\ndecisions (of 14)", "Silent errors"]
        bv += [pb["acceptable"], pb["silent_errors"]]
        av += [pa["acceptable"], pa["silent_errors"]]
    g = reality.groupby("policy")["median_sep_km_24h"].mean()
    metrics.append("Mean 24 h miss vs\nreal drifter (km)")
    bv.append(round(float(g["baseline"]), 2))
    av.append(round(float(g[ag]), 2))
    metrics.append("Cost (US$)")
    bv.append(0.0)
    av.append(batch.get("total_cost_usd", np.nan) if batch else np.nan)
    x = np.arange(len(metrics))
    c_ax.bar(x - 0.2, bv, 0.4, color=ps.color("baseline"), label=ps.name("baseline"))
    c_ax.bar(x + 0.2, av, 0.4, color=ps.color("agent"), label=ps.name("agent"), hatch="//", edgecolor="white")
    for xi, (u, v) in enumerate(zip(bv, av)):
        c_ax.text(xi - 0.2, u, f"{u:g}", ha="center", va="bottom", fontsize=8)
        c_ax.text(xi + 0.2, v, f"{v:g}", ha="center", va="bottom", fontsize=8)
    c_ax.set_xticks(x)
    c_ax.set_xticklabels(metrics, fontsize=8)
    c_ax.set_title("(c) Side by side", fontsize=ps.SUBTITLE_SIZE)
    c_ax.legend(fontsize=ps.LEGEND_SIZE)
    fig.suptitle("Scripted baseline vs Claude agent on the faulty October 2021 cases: the answer key and a real drifter",
                 fontsize=ps.TITLE_SIZE)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---- 3. animation ----------------------------------------------------------------------------
def animate_case(case, clouds, decs, clean_cloud, obs, path):
    plt = _plt()
    from matplotlib.animation import FuncAnimation, PillowWriter
    allx = np.concatenate([obs["lon"].values, clean_cloud[0].ravel()] + [c[0].ravel() for c in clouds.values()])
    ally = np.concatenate([obs["lat"].values, clean_cloud[1].ravel()] + [c[1].ravel() for c in clouds.values()])
    lo, hi = np.nanpercentile(allx, [0.5, 99.5])
    la, ha = np.nanpercentile(ally, [0.5, 99.5])
    pad = 0.03
    n = len(clouds)
    fig, axs = plt.subplots(1, n, figsize=(4.6 * n, 5.6), sharex=True, sharey=True)
    fig.subplots_adjust(top=0.78, bottom=0.27, wspace=0.08, left=0.05, right=0.99)
    art = []
    for ax, (p, (plon, plat)) in zip(axs, clouds.items()):
        ax.set_xlim(lo - pad, hi + pad)
        ax.set_ylim(la - pad, ha + pad)
        ax.set_title(f"{ps.name(p)}\n{ps.decision_text(decs[p])}", fontsize=ps.SUBTITLE_SIZE - 1,
                     color=ps.color(p))
        g, = ax.plot([], [], ".", ms=2, color=ps.color("clean_radar"), alpha=0.3, label=ps.name("clean_radar"))
        dots, = ax.plot([], [], ".", ms=2.5, color=ps.color(p), alpha=0.8, label=f"Forecast particles ({ps.name(p).split(' (')[0]})")
        line, = ax.plot([], [], color="k", lw=1.8, label="Real drifter, path so far")
        head, = ax.plot([], [], "o", color="k", ms=4)
        ax.plot(obs["lon"].iloc[0], obs["lat"].iloc[0], "s", color="k", ms=5, label="Release point (drifter at 0 h)")
        ax.set_xlabel("Longitude", fontsize=8)
        ax.tick_params(labelsize=7)
        leg = ax.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=1, frameon=False)
        for hnd in leg.legend_handles:                                # same readable marker size for every entry
            if hasattr(hnd, "set_markersize"):
                hnd.set_markersize(6)
                hnd.set_alpha(1)
        art.append((p, g, dots, line, head, plon, plat))
    axs[0].set_ylabel("Latitude", fontsize=8)
    stamp = fig.suptitle("", fontsize=ps.SUBTITLE_SIZE)
    t0 = obs.index[0]

    def frame(h):
        misses = []
        for p, g, dots, line, head, plon, plat in art:
            g.set_data(clean_cloud[0][:, h], clean_cloud[1][:, h])
            dots.set_data(plon[:, h], plat[:, h])
            line.set_data(obs["lon"].values[:h + 1], obs["lat"].values[:h + 1])
            head.set_data([obs["lon"].values[h]], [obs["lat"].values[h]])
            d = de.haversine_km(np.nanmean(plon[:, h]), np.nanmean(plat[:, h]), obs["lon"].values[h], obs["lat"].values[h])
            misses.append(f"{ps.name(p).split(' (')[0]} {d:.1f} km")
        stamp.set_text(f"{ps.case_label(case)}: where does each policy's forecast put the object?  24 h forecast released "
                       f"at the real drifter, {t0:%Y-%m-%d %H:%M} UTC\nHour +{h:02d}   distance from forecast centre to "
                       f"real drifter: " + "  |  ".join(misses))
        return []

    FuncAnimation(fig, frame, frames=len(obs), interval=400).save(path, writer=PillowWriter(fps=3))
    plt.close(fig)


def main(argv=None):
    logging.getLogger("opendrift").setLevel(logging.ERROR)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent-run", default="oct_rep1")
    ap.add_argument("--cases", nargs="*", default=["case14", "case06"], help="cases to animate; the first is mapped")
    ap.add_argument("--window", default="2021-10-24 12:00", help="drifter window start for the animations")
    ap.add_argument("--cases-dir", default="runs/cases_oct")
    ap.add_argument("--baseline", default="runs/results/baseline_oct")
    ap.add_argument("--baseline-work", default="runs/work/baseline_oct")
    ap.add_argument("--clean", default=str(de.DEF_RADAR))
    ap.add_argument("--model", default=str(de.DEF_MODEL))
    ap.add_argument("--drifters", default=str(de.DEF_DRIFTERS))
    ap.add_argument("--k", type=float, default=10.0)
    ap.add_argument("--every", type=int, default=12)
    ap.add_argument("--reality", default="runs/results/reality_oct/cases.csv")
    ap.add_argument("--scores", default="runs/results/scores_oct/summary.json")
    ap.add_argument("--skip-summary", action="store_true", help="skip decision_summary_oct.png (saves ~3 min)")
    a = ap.parse_args(argv)

    cases_dir, clean, model = REPO / a.cases_dir, Path(a.clean), Path(a.model)
    ids = sorted(p.name for p in cases_dir.glob("case*") if p.is_dir())
    base = sc.load_decisions(REPO / a.baseline)
    agent = sc.load_decisions(REPO / "runs" / "results" / "agent" / a.agent_run)
    pols = {"always_fused": ({c: {"choice": "fused"} for c in ids}, None),
            "baseline": (base, REPO / a.baseline_work),
            "agent": (agent, REPO / "runs" / "work" / "agent" / a.agent_run),
            "always_model_only": ({c: {"choice": "model_only"} for c in ids}, None)}
    decs_by_policy = {p: v[0] for p, v in pols.items()}
    fig_dir = REPO / "docs" / "figures"

    reality_p, scores_p = REPO / a.reality, REPO / a.scores
    if not reality_p.exists():
        sys.exit(f"{reality_p} not found; run src/decision_reality.py --agent-runs {a.agent_run} first")
    reality = pd.read_csv(reality_p)
    scores = json.loads(scores_p.read_text(encoding="utf-8")) if scores_p.exists() else None
    bpath = REPO / "eval" / "batches" / f"{a.agent_run}.json"
    batch = None
    if bpath.exists():
        bl = json.loads(bpath.read_text(encoding="utf-8"))
        batch = {"total_cost_usd": round(sum(c["cost_usd"] or 0 for c in bl["cases"].values()), 2)}
    baseline_vs_agent(ids, base, agent, a.agent_run, reality, scores, batch, fig_dir / "baseline_vs_agent_oct.png")
    print("wrote docs/figures/baseline_vs_agent_oct.png")

    with __import__("xarray").open_dataset(clean) as rr, __import__("xarray").open_dataset(model) as mm:
        t1 = min(pd.Timestamp(rr.time.values[-1]), pd.Timestamp(mm.time.values[-1]))
        t0 = max(pd.Timestamp(rr.time.values[0]), pd.Timestamp(mm.time.values[0]))
    track = de.drifter_track(a.drifters, de.DEF_ID, t0, t1)
    if not a.skip_summary:
        starts = de.windows(track, 24, a.every)
        sum_pols = {p: pols[p] for p in ("always_fused", "baseline", "agent")}
        sum_pols["clean_radar"] = (None, None)
        res = summary_runs(sum_pols, ids, track, starts, clean, model, cases_dir, a.k)
        decision_summary(res, ids, track, starts, a.cases[0], decs_by_policy, fig_dir / "decision_summary_oct.png")
        print("wrote docs/figures/decision_summary_oct.png")

    s = pd.Timestamp(a.window)
    obs = track.loc[s:s + pd.Timedelta(hours=24)]
    if len(obs) != 25:
        sys.exit(f"window {a.window} has {len(obs)} hourly drifter positions, need 25")
    lon0, lat0 = obs["lon"].iloc[0], obs["lat"].iloc[0]
    clean_cloud = de.run([clean, model], lon0, lat0, s, 24, a.k)
    for c in a.cases:
        clouds, decs = {}, {}
        for p, (dd, work) in pols.items():
            readers, note = dre.forcing_for(dd.get(c), c, work, cases_dir, model)
            if readers is None:
                sys.exit(f"{p} {c}: repaired file missing")
            clouds[p] = de.run(readers, lon0, lat0, s, 24, a.k)
            decs[p] = dd.get(c)
        gif = fig_dir / f"decision_oct_{c}.gif"
        animate_case(c, clouds, decs, clean_cloud, obs, gif)
        print(f"wrote docs/figures/decision_oct_{c}.gif")


if __name__ == "__main__":
    sys.exit(main())
