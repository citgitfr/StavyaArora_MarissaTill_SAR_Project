import numpy as np
import xarray as xr

h = xr.open_dataset("fixtures/hfr_2km_slice.nc")
y = xr.open_dataset("fixtures/hycom_uv_slice.nc")
ocean = np.isfinite(y["u"]).any("time")
ocean_h = ocean.sel(lat=h["lat"].values, lon=h["lon"].values, method="nearest").values  # (lat, lon)
valid = np.isfinite(h["u"]).values                                                       # (time, lat, lon)
frac = (valid & ocean_h).sum(axis=(1, 2)) / ocean_h.sum()
print(int(ocean_h.sum()), "ocean cells on the radar grid")
print("radar coverage of ocean per hour: min %.2f mean %.2f max %.2f" % (frac.min(), frac.mean(), frac.max()))