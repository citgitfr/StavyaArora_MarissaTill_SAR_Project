"""First look: HF radar vs HYCOM. Prints units, grids, coverage, and disagreement; saves a figure."""
import numpy as np
import xarray as xr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

h = xr.open_dataset("fixtures/hfr_2km_slice.nc")
y = xr.open_dataset("fixtures/hycom_uv_slice.nc")

print("units as written: HFR", repr(h["u"].attrs.get("units")), "| HYCOM", repr(y["u"].attrs.get("units")))
print("grid spacing (deg): HFR lat %.3f lon %.3f | HYCOM lat %.3f lon %.3f" % (
    abs(np.diff(h["lat"].values).mean()), abs(np.diff(h["lon"].values).mean()),
    abs(np.diff(y["lat"].values).mean()), abs(np.diff(y["lon"].values).mean())))
print("time step: HFR", np.diff(h["time"].values)[0], "| HYCOM", np.diff(y["time"].values)[0])

sp_h, sp_y = np.hypot(h["u"], h["v"]), np.hypot(y["u"], y["v"])
print("HFR speed   median/95th/max:", [round(float(sp_h.quantile(q)), 3) for q in (0.5, 0.95, 1.0)])
print("HYCOM speed median/95th/max:", [round(float(sp_y.quantile(q)), 3) for q in (0.5, 0.95, 1.0)])

cov_t = np.isfinite(h["u"]).mean(("lat", "lon"))
print("HFR valid fraction per hour: mean %.2f  min %.2f  max %.2f"
      % (float(cov_t.mean()), float(cov_t.min()), float(cov_t.max())))
print("HYCOM valid fraction: %.2f" % float(np.isfinite(y["u"]).mean()))

yi = y[["u", "v"]].interp(time=h["time"], lat=h["lat"], lon=h["lon"])
for c in ("u", "v"):
    a_all, b_all = h[c].values, yi[c].values
    m = np.isfinite(a_all) & np.isfinite(b_all)
    a, b = a_all[m], b_all[m]
    print(f"{c}: n={m.sum()}  RMS diff={np.sqrt(np.mean((a - b) ** 2)):.3f} m/s  "
          f"corr={np.corrcoef(a, b)[0, 1]:.2f}  bias(HFR-HYCOM)={np.mean(a - b):.3f}")

k = int(np.argmax(cov_t.values))
t = h["time"].values[k]
yk = y.sel(time=t, method="nearest")
fig, axs = plt.subplots(1, 2, figsize=(11, 5), sharex=True, sharey=True)
axs[0].quiver(h["lon"].values[::2], h["lat"].values[::2],
              h["u"].isel(time=k).values[::2, ::2], h["v"].isel(time=k).values[::2, ::2], scale=4)
axs[0].set_title(f"HF radar {str(t)[:16]}")
axs[1].quiver(y["lon"].values, y["lat"].values, yk["u"].values, yk["v"].values, scale=4)
axs[1].set_title(f"HYCOM {str(yk['time'].values)[:16]}")
axs[0].set_ylabel("lat")
for ax in axs:
    ax.set_xlabel("lon")
fig.savefig("docs/figures/first_look_hfr_vs_hycom.png", dpi=150, bbox_inches="tight")
print("saved docs/figures/first_look_hfr_vs_hycom.png")