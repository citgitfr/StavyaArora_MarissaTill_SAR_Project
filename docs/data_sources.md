# Data Sources and Software

## Software
- **OpenDrift** 1.14.12 (GPL v2.0): https://github.com/OpenDrift/opendrift
  - Paper: Dagestad et al. (2018), Geosci. Model Dev. 11, 1405–1420, https://doi.org/10.5194/gmd-11-1405-2018
  - Installed from `requirements.txt`. Source cloned only to read its examples: commit d3e6eb550a025ef7b9b9be7c41f51d464ca1ef57 (2026-10-14).
- **Python 3.12** managed with uv, in a project-local `.venv`.

## Data

### HF-radar surface currents (HFRNet total vectors), US West Coast, 2 km, hourly
- Source: NOAA IOOS HF Radar National Network (HFRNet), archived by NOAA NCEI, accession 0241437
  (Aug 2021): https://www.ncei.noaa.gov/archive/accession/0241437 ; collection DOI
  https://doi.org/10.25921/2rkz-8443
- Files: hourly `YYYYMMDDHH00_hfr_uswc_2km_rtv_uwls_SIO.nc` from
  https://www.ncei.noaa.gov/thredds-ocean/catalog/ioos/hfradar/rtv/2021/202108/USWC/catalog.html
  (NDBC copies and 25-hour averages were excluded).
- Used: 2021-08-10 00:00 to 2021-08-11 23:00 UTC (48 files); box lat 32.8 to 34.2, lon -120.0 to -117.5;
  variables u, v (m s-1). Fixture: fixtures/hfr_2km_slice.nc. Raw files stay local (data/hfr_raw/).
- Accessed: 2026-10-28, latest version shown on the landing page (v2.2, published 2026-03-08).
- Notes: velocities represent the upper ~0.3 to 2.5 m. NCEI states no warranty on accuracy.

### HYCOM ocean-model currents
- Source: HYCOM Consortium / NRL, GOFS 3.1 41-layer global 1/12 degree analysis, GLBy0.08 expt_93.0,
  3-hourly u/v (uv3z), 2021 dataset: https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0/uv3z/2021
- Used: surface level (0 m), 2021-08-10 00:00 to 2021-08-12 00:00 UTC, same box; water_u and water_v
  (m/s) renamed u, v. Opened with drop_variables=["tau"] because its units ("hours since analysis")
  are not a date. Fixture: fixtures/hycom_uv_slice.nc.
- Accessed: 2026-10-28.
- Notes: file metadata says "Approved for public release. Distribution unlimited."

### Global Drifter Program hourly drifter tracks (ground truth)
- Source: NOAA AOML GDP, hourly QC dataset `drifter_hourly_qc` on https://erddap.aoml.noaa.gov/gdp/erddap
  (found by scripts/find_drifters.py; the server's search for "drifter hourly" listed only the 6-hour dataset, but the
  hourly dataset's info page and data responded).
- Used: drifter 300534061399950, 2021-10-18 00:00 to 2021-10-28 23:00 UTC, 264 hourly GPS positions, drogue lost
  (drogue_lost_date not set before this period). Scans: none in the original box on 2021-08-10..12; 4 drifters in
  that box in 2018-2024; 38 in 32-38.5 N, 124.5-117 W.
- Accessed: 2026-11-15. Raw CSV in data/drifters/ (not committed).

### Matching radar and model for the drifter window (2021-10-18..29)
- HF radar: same NCEI product as above, 264 hourly SIO files, box 33.8-35.8 N, 122-119.8 W -> data/hfr_20211018.nc.
- HYCOM: same GLBy0.08 expt_93.0 2021 dataset, surface, 3-hourly, box 33-36.5 N, 123-119 W (larger than the radar
  box) -> data/hycom_20211018.nc. Both accessed 2026-11-15, not committed (rebuild with the commands in docs/pipeline.md).

### ERA5 wind and Stokes drift for the drifter window (2021-10-18..29)
- Source: ECMWF ERA5 hourly data on single levels (`reanalysis-era5-single-levels`), Copernicus Climate Change
  Service (C3S) Climate Data Store: https://cds.climate.copernicus.eu . Hersbach et al. (2020), Q. J. R. Meteorol.
  Soc. 146, 1999–2049, https://doi.org/10.1002/qj.3803 . Licence: Licence to use Copernicus Products (accepted by
  the owners on the CDS).
- Pulled with `python src/data/pull_era5.py --stokes` (the owners ran it; the agent has no network).
- Used: 2021-10-18 00:00 to 2021-10-29 00:00 UTC (265 hours); box 32.5-37.0 N, 123.5-118.5 W (HYCOM box plus
  0.5 deg). Wind: 10 m u/v (u10, v10) on a 0.25 deg grid -> data/era5_wind_20211018.nc (x_wind, y_wind). Stokes
  drift: u/v Stokes drift (ust, vst) from the wave model, on a 0.5 deg grid, missing over land and near the coast
  (32% of grid values are NaN) -> data/era5_stokes_20211018.nc (x_stokes, y_stokes). Raw CDS files in
  data/era5_raw/. Not committed.
- Accessed: 2026-11-18.

### Access problems found
- hfrnet-tds.ucsd.edu timed out from the WorkSpace (port 443), and NOAA's ERDDAP mirror returned
  403 (IP blacklisted); the NCEI archive worked. See LOG.md.