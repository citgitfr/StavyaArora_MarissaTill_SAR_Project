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

### Access problems found
- hfrnet-tds.ucsd.edu timed out from the WorkSpace (port 443), and NOAA's ERDDAP mirror returned
  403 (IP blacklisted); the NCEI archive worked. See LOG.md.