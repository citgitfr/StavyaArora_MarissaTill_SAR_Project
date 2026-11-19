"""Offline tests for src/data/pull_era5.py (no network, no CDS account): request building, tidying a file shaped
like a CDS ERA5 download, and that OpenDrift reads the tidied wind file and pushes particles downwind.

Run from the repository root:  python -m unittest tests.test_pull_era5 -v
"""
import importlib.util
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("pull_era5", REPO / "src" / "data" / "pull_era5.py")
pe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pe)


def cds_like(t0, hours, u=5.0, v=0.0, lon360=False, stokes=False):
    """A dataset shaped like a new-CDS ERA5 netCDF: valid_time, descending latitude, number/expver coords."""
    t = pd.date_range(t0, periods=hours, freq="h")
    lat = np.arange(37.0, 32.49, -0.25)                       # ERA5 latitudes run north to south
    lon = np.arange(-123.5, -118.49, 0.25)
    if lon360:
        lon = lon % 360
    shape = (t.size, lat.size, lon.size)
    names = ("ust", "vst") if stokes else ("u10", "v10")
    return xr.Dataset({names[0]: (("valid_time", "latitude", "longitude"), np.full(shape, u, "f4")),
                       names[1]: (("valid_time", "latitude", "longitude"), np.full(shape, v, "f4"))},
                      coords={"valid_time": t, "latitude": lat, "longitude": lon,
                              "number": 0, "expver": ("valid_time", np.array(["0001"] * t.size))})


class TestRequest(unittest.TestCase):
    def test_one_month(self):
        reqs = pe.request_for("2021-10-18 00:00", "2021-10-29 00:00", [32.5, 37.0, -123.5, -118.5], pe.WIND_VARS)
        self.assertEqual(len(reqs), 1)
        tag, r = reqs[0]
        self.assertEqual(tag, "202110")
        self.assertEqual(r["day"], [f"{d}" for d in range(18, 30)])
        self.assertEqual(r["area"], [37.0, -123.5, 32.5, -118.5])     # N, W, S, E
        self.assertEqual(len(r["time"]), 24)

    def test_month_boundary_splits(self):
        reqs = pe.request_for("2021-11-29 00:00", "2021-12-02 05:00", [32, 34, -121, -117], pe.WIND_VARS)
        self.assertEqual([t for t, _ in reqs], ["202111", "202112"])
        self.assertEqual(reqs[0][1]["day"], ["29", "30"])
        self.assertEqual(reqs[1][1]["day"], ["01", "02"])


class TestTidy(unittest.TestCase):
    def test_contract(self):
        out = pe.tidy(cds_like("2021-10-17 22:00", 30), pe.WIND_MAP, "2021-10-18 00:00", "2021-10-19 00:00")
        self.assertEqual(set(out.data_vars), {"x_wind", "y_wind"})
        self.assertEqual(out["x_wind"].dims, ("time", "lat", "lon"))
        self.assertEqual(out["x_wind"].attrs["standard_name"], "eastward_wind")
        self.assertEqual(out["y_wind"].attrs["standard_name"], "northward_wind")
        self.assertEqual(out.sizes["time"], 25)
        self.assertEqual(pd.Timestamp(out.time.values[0]), pd.Timestamp("2021-10-18 00:00"))
        self.assertTrue(np.all(np.diff(out.lat.values) > 0))
        self.assertTrue(np.all(np.diff(out.lon.values) > 0))
        self.assertNotIn("number", out.coords)
        self.assertNotIn("expver", out.coords)

    def test_lon_0_360_is_converted(self):
        out = pe.tidy(cds_like("2021-10-18", 2, lon360=True), pe.WIND_MAP, "2021-10-18 00:00", "2021-10-18 01:00")
        self.assertLess(float(out.lon.max()), 0)
        self.assertAlmostEqual(float(out.lon.min()), -123.5)

    def test_stokes_names(self):
        out = pe.tidy(cds_like("2021-10-18", 2, stokes=True), pe.STOKES_MAP, "2021-10-18 00:00", "2021-10-18 01:00")
        self.assertEqual(out["x_stokes"].attrs["standard_name"], "sea_surface_wave_stokes_drift_x_velocity")

    def test_missing_hours_refused(self):
        with self.assertRaises(SystemExit):
            pe.tidy(cds_like("2021-10-18", 5), pe.WIND_MAP, "2021-10-18 00:00", "2021-10-18 10:00")

    def test_wrong_variables_refused(self):
        with self.assertRaises(SystemExit):
            pe.tidy(cds_like("2021-10-18", 2, stokes=True), pe.WIND_MAP, "2021-10-18 00:00", "2021-10-18 01:00")


class TestOpenDriftReadsIt(unittest.TestCase):
    def test_eastward_wind_moves_particles_east(self):
        from opendrift.models.oceandrift import OceanDrift
        from opendrift.readers import reader_netCDF_CF_generic as R
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "wind.nc"
        pe.tidy(cds_like("2021-10-18", 7, u=10.0, v=0.0), pe.WIND_MAP,
                "2021-10-18 00:00", "2021-10-18 06:00").to_netcdf(path)
        o = OceanDrift(loglevel=50)
        o.add_reader(R.Reader(str(path)))
        o.set_config("environment:fallback:x_sea_water_velocity", 0)
        o.set_config("environment:fallback:y_sea_water_velocity", 0)
        o.set_config("drift:stokes_drift", False)
        o.seed_elements(lon=-121.0, lat=34.5, number=10, radius=0, time=pd.Timestamp("2021-10-18").to_pydatetime(),
                        wind_drift_factor=0.03)
        o.run(duration=timedelta(hours=6), time_step=900, time_step_output=3600)
        lon = o.result["lon"].values
        lat = o.result["lat"].values
        dx_km = (np.nanmean(lon[:, -1]) + 121.0) * 111.32 * np.cos(np.radians(34.5))
        # 3% of 10 m/s for 6 h = 6.48 km east, nothing north
        self.assertAlmostEqual(dx_km, 6.48, delta=0.3)
        self.assertAlmostEqual(float(np.nanmean(lat[:, -1])), 34.5, places=3)


if __name__ == "__main__":
    unittest.main()
