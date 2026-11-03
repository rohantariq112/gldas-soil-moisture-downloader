# GLDAS NOAH 0.25° Monthly Soil Moisture Downloader & Clipper

A Python utility to automatically search, subset, download, and clip monthly soil moisture and surface storage variables from NASA's **GLDAS Noah Land Surface Model L4 monthly 0.25° x 0.25° v2.1 (`GLDAS_NOAH025_M`)** dataset for a custom geographical Area of Interest (AOI).

---

## What This Code Does

Downloading global GLDAS granules over multi-decade periods requires substantial bandwidth and disk space. [`downlaod_gldas2.py`](./downlaod_gldas2.py) optimizes this workflow by:

1. **Reading an Area of Interest (AOI)** polygon (e.g., `JSON/Punjab_JSON.json`, or any `.shp`, `.geojson`, or `.gpkg` file).
2. **Server-Side Spatial & Variable Subsetting (OPeNDAP)**: Instead of downloading full global NetCDF granules, it converts the AOI bounding box into GLDAS grid indices (`0.25°` resolution) and queries NASA's OPeNDAP server (supporting both NASA Earthdata Cloud Hyrax DAP4 and on-prem GES DISC DAP2) to download **only** the required variables and grid cells over your study area.
3. **Automatic Local Fallback**: If OPeNDAP server-side subsetting is temporarily unavailable for a granule, the script automatically falls back to downloading the full granule via `earthaccess`, subsets it locally using `xarray`, and deletes the raw global file.
4. **Resume Support**: Previously downloaded and validated monthly NetCDF-4 (`.nc4`) files are automatically skipped on subsequent runs.
5. **Exact Polygon Clipping & Merging**: Combines all monthly bounding-box files across the entire time range (`2000–2025` by default) and masks/clips the dataset to the exact polygon boundary (`all_touched=True` so boundary grid cells are preserved), saving a single compressed NetCDF-4 time-series file.

---

## Extracted Variables

By default, the script extracts the following hydrological variables (configurable via `SOIL_VARS`):

| Variable Name | Description | Depth / Layer |
| :--- | :--- | :--- |
| `SoilMoi0_10cm_inst` | Soil moisture content | 0 – 10 cm underground |
| `SoilMoi10_40cm_inst` | Soil moisture content | 10 – 40 cm underground |
| `SoilMoi40_100cm_inst` | Soil moisture content | 40 – 100 cm underground |
| `SoilMoi100_200cm_inst` | Soil moisture content | 100 – 200 cm underground |
| `SWE_inst` | Snow depth water equivalent | Surface |
| `CanopInt_inst` | Plant canopy surface water | Canopy |

Coordinate dimensions preserved: `time`, `lat`, `lon`.

---

## How It Works (Step-by-Step Pipeline)

1. **AOI Reprojection & Grid Indexing (`load_shape` & `bbox_to_index_window`)**
   - Loads the input boundary file with `geopandas` and reprojects it to `EPSG:4326` (WGS84).
   - Maps the bounding box `(minx, miny, maxx, maxy)` onto the GLDAS 0.25° grid (`600 x 1440` cells, centered from `-59.875°` to `89.875°` latitude and `-179.875°` to `179.875°` longitude), padded by 1 grid cell on each side to prevent edge loss.
2. **NASA Earthdata Authentication (`earthaccess.login`)**
   - Authenticates interactively with NASA Earthdata and persists your credentials locally (`~/.netrc` / `_netrc`), creating an authenticated HTTPS session.
3. **Granule Search (`earthaccess.search_data`)**
   - Queries NASA's Common Metadata Repository (CMR) for `GLDAS_NOAH025_M` (version `2.1`) granules between `START_DATE` and `END_DATE`.
4. **Subset Download Loop**
   - Checks if `GLDAS_NOAH025_M_SoilMoi_<YYYYMM>.nc4` already exists and has a valid HDF5/NetCDF magic header (`is_netcdf`).
   - Constructs an OPeNDAP constraint expression URL (`build_subset_url`) for the exact `[time][lat][lon]` index window and downloads the subset directly.
   - Falls back to full-granule download + local `xarray.Dataset.sel()` slicing if OPeNDAP fails.
5. **Polygon Clipping & Time-Series Merge (`clip_to_polygon`)**
   - Opens all monthly `.nc4` subsets with `xarray.open_mfdataset`, assigns `EPSG:4326` CRS via `rioxarray`, clips the data to the exact AOI geometry (`all_touched=True, drop=True`), and writes a zlib-compressed multi-year NetCDF-4 file.

---

## Prerequisites & Installation

### 1. NASA Earthdata Account
You need a free NASA Earthdata account to download GLDAS data:
1. Register at [https://urs.earthdata.nasa.gov/](https://urs.earthdata.nasa.gov/).
2. Authorize the **NASA GESDISC DATA ARCHIVE** application in your Earthdata profile under *Applications -> Authorized Apps*.

### 2. Python Dependencies
Install the required geospatial and scientific Python packages (recommended via `conda` or `pip`):

```bash
pip install earthaccess geopandas requests rioxarray xarray netCDF4 h5netcdf dask tqdm
```

*(Or using Conda/Mamba)*:
```bash
conda install -c conda-forge earthaccess geopandas requests rioxarray xarray netcdf4 dask tqdm
```

---

## Usage

1. **Configure Settings (Optional)**
   Open [`downlaod_gldas2.py`](./downlaod_gldas2.py) and adjust the parameters at the top of the file if needed:
   ```python
   SHORT_NAME = "GLDAS_NOAH025_M"
   VERSION = "2.1"
   START_DATE = "2000-01-01"
   END_DATE = "2025-12-31"
   SHAPEFILE = Path(__file__).parent / "JSON" / "Punjab_JSON.json"
   MERGE_INTO_SINGLE_FILE = True
   ```

2. **Run the Script**
   ```bash
   python downlaod_gldas2.py
   ```
   On the first run, `earthaccess` will prompt you for your NASA Earthdata username and password.

---

## Outputs

All outputs are saved to `./gldas_soil_moisture_nc4/` (ignored by Git):
- **Monthly Subset Files**: `GLDAS_NOAH025_M_SoilMoi_YYYYMM.nc4` (bounding-box subset for each month).
- **Merged & Clipped File**: `GLDAS_NOAH025_M_SoilMoisture_2001_2025_clipped.nc4` (single compressed file containing the full time series clipped to the exact AOI polygon).

