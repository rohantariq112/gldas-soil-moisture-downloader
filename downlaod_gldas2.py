import os
import re
from pathlib import Path

import earthaccess
import geopandas as gpd
import requests
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from tqdm import tqdm

# ==============================================================================
# CONFIGURATION
# ==============================================================================
SHORT_NAME = "GLDAS_NOAH025_M"
VERSION = "2.1"
START_DATE = "2000-01-01"
END_DATE = "2025-12-31"

# Your area of interest (.shp, or .geojson / .gpkg also work)
SHAPEFILE = Path(__file__).parent / "JSON" / "Punjab_JSON.json"

SOIL_VARS = [
    "SoilMoi0_10cm_inst",
    "SoilMoi10_40cm_inst",
    "SoilMoi40_100cm_inst",
    "SoilMoi100_200cm_inst",
    "SWE_inst",
    "CanopInt_inst",
]
COORD_VARS = ["time", "lat", "lon"]

OUTPUT_DIR = Path("./gldas_soil_moisture_nc4")
TEMP_DIR = Path("./gldas_temp_raw")
MERGE_INTO_SINGLE_FILE = True
MERGED_FILENAME = OUTPUT_DIR / "GLDAS_NOAH025_M_SoilMoisture_2001_2025_clipped.nc4"

# GLDAS NOAH 0.25 deg grid (cell centres): lat -59.875..89.875, lon -179.875..179.875
RES = 0.25
LAT0, LON0 = -59.875, -179.875
NLAT, NLON = 600, 1440


# ==============================================================================
# SHAPEFILE -> INDEX WINDOW
# ==============================================================================
def load_shape(path):
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise ValueError("Shapefile has no CRS defined (.prj missing?).")
    return gdf.to_crs("EPSG:4326")


def bbox_to_index_window(gdf):
    """Convert the shapefile bounding box to inclusive lat/lon index ranges,
    padded by one cell so edge cells are never lost."""
    minx, miny, maxx, maxy = gdf.total_bounds
    lat_i0 = max(int((miny - LAT0) // RES) - 1, 0)
    lat_i1 = min(int((maxy - LAT0) // RES) + 1, NLAT - 1)
    lon_i0 = max(int((minx - LON0) // RES) - 1, 0)
    lon_i1 = min(int((maxx - LON0) // RES) + 1, NLON - 1)
    if lat_i0 > lat_i1 or lon_i0 > lon_i1:
        raise ValueError(
            "Shapefile lies outside the GLDAS NOAH025 domain "
            "(lat -60..90, lon -180..180)."
        )
    return (lat_i0, lat_i1), (lon_i0, lon_i1)


# ==============================================================================
# HELPERS
# ==============================================================================
def is_netcdf(data: bytes) -> bool:
    """True for NetCDF-4 (HDF5) or classic NetCDF content."""
    return data[:4] == b"\x89HDF" or data[:3] == b"CDF"


def file_ok(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 200:
        return False
    with open(path, "rb") as f:
        return is_netcdf(f.read(8))


def get_opendap_url(granule):
    service_links = granule._filter_related_links("USE SERVICE API")
    for link in service_links:
        if "opendap" in link.lower():
            return link
    data_links = granule.data_links(access="external")
    for link in data_links:
        if "hydro1.gesdisc.eosdis.nasa.gov/data/" in link:
            return link.replace("/data/", "/opendap/")
    return None


def build_subset_url(opendap_url, lat_rng, lon_rng):
    """Constraint expression with a lat/lon index window.
    Soil variables are (time, lat, lon); monthly files have time length 1."""
    la0, la1 = lat_rng
    lo0, lo1 = lon_rng
    if "opendap.earthdata.nasa.gov" in opendap_url:
        # Cloud OPeNDAP (Hyrax DAP4): inclusive index ranges
        parts = ["/time", f"/lat[{la0}:{la1}]", f"/lon[{lo0}:{lo1}]"]
        parts += [f"/{v}[0][{la0}:{la1}][{lo0}:{lo1}]" for v in SOIL_VARS]
        return f"{opendap_url}.dap.nc4?dap4.ce={';'.join(parts)}"
    # On-prem GES DISC OPeNDAP (DAP2)
    parts = ["time", f"lat[{la0}:{la1}]", f"lon[{lo0}:{lo1}]"]
    parts += [f"{v}[0:0][{la0}:{la1}][{lo0}:{lo1}]" for v in SOIL_VARS]
    return f"{opendap_url}.nc4?{','.join(parts)}"


def extract_year_month(granule_url):
    match = re.search(r"\.A(\d{6})\.", granule_url)
    return match.group(1) if match else os.path.basename(granule_url)


def clip_to_polygon(ds, gdf):
    """Mask everything outside the polygon; all_touched keeps cells the
    boundary touches (important for small areas on a 0.25 deg grid)."""
    ds = ds.rio.set_spatial_dims(x_dim="lon", y_dim="lat")
    ds = ds.rio.write_crs("EPSG:4326")
    return ds.rio.clip(gdf.geometry, gdf.crs, all_touched=True, drop=True)


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    gdf = load_shape(SHAPEFILE)
    lat_rng, lon_rng = bbox_to_index_window(gdf)
    print(
        f"Bounding box index window: lat[{lat_rng[0]}:{lat_rng[1]}] "
        f"lon[{lon_rng[0]}:{lon_rng[1]}] "
        f"({(lat_rng[1]-lat_rng[0]+1)} x {(lon_rng[1]-lon_rng[0]+1)} cells)"
    )
    lat_min, lat_max = LAT0 + lat_rng[0] * RES, LAT0 + lat_rng[1] * RES
    lon_min, lon_max = LON0 + lon_rng[0] * RES, LON0 + lon_rng[1] * RES

    print("Authenticating with NASA Earthdata...")
    earthaccess.login(strategy="interactive", persist=True)
    session = earthaccess.get_requests_https_session()

    print(f"Searching {SHORT_NAME} v{VERSION} from {START_DATE} to {END_DATE}...")
    granules = earthaccess.search_data(
        short_name=SHORT_NAME,
        version=VERSION,
        temporal=(START_DATE, END_DATE),
    )
    print(f"Found {len(granules)} monthly granules.")

    for granule in tqdm(granules, desc="Downloading monthly netCDF files"):
        data_link = granule.data_links(access="external")[0]
        ym = extract_year_month(data_link)
        out_file = OUTPUT_DIR / f"GLDAS_NOAH025_M_SoilMoi_{ym}.nc4"

        if file_ok(out_file):
            continue

        opendap_url = get_opendap_url(granule)
        subset_success = False

        # Attempt 1: server-side subsetting to the bounding box
        if opendap_url:
            subset_url = build_subset_url(opendap_url, lat_rng, lon_rng)
            try:
                resp = session.get(
                    subset_url,
                    headers={"Accept-Encoding": "identity"},
                    timeout=120,
                )
                if resp.ok and is_netcdf(resp.content):
                    with open(out_file, "wb") as f:
                        f.write(resp.content)
                    subset_success = True
            except requests.RequestException:
                pass

        # Attempt 2: download full granule, subset to the bounding box locally
        if not subset_success:
            raw_files = earthaccess.download([granule], local_path=str(TEMP_DIR))
            if raw_files:
                raw_path = Path(raw_files[0])
                with xr.open_dataset(raw_path) as ds:
                    available = [v for v in SOIL_VARS if v in ds.data_vars]
                    ds_sub = ds[available].sel(
                        lat=slice(lat_min, lat_max), lon=slice(lon_min, lon_max)
                    )
                    ds_sub.load().to_netcdf(out_file)
                raw_path.unlink(missing_ok=True)

    print(f"\nMonthly bbox-subset files saved to: {OUTPUT_DIR.resolve()}")

    if MERGE_INTO_SINGLE_FILE:
        print("Merging and clipping to the exact polygon...")
        monthly_files = sorted(OUTPUT_DIR.glob("GLDAS_NOAH025_M_SoilMoi_*.nc4"))
        if monthly_files:
            with xr.open_mfdataset(
                monthly_files, combine="by_coords", engine="netcdf4"
            ) as ds_merged:
                ds_clipped = clip_to_polygon(ds_merged, gdf).load()
            encoding = {v: {"zlib": True, "complevel": 4} for v in SOIL_VARS}
            ds_clipped.to_netcdf(MERGED_FILENAME, encoding=encoding)
            print(f"Merged, clipped dataset saved to: {MERGED_FILENAME.resolve()}")


if __name__ == "__main__":
    main()