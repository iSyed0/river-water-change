"""Shared readers for the Indus floodplain analysis: the JRC mosaic, Copernicus DEM and ESA WorldCover."""
import math
import os
import urllib.error
import urllib.request

# A PostGIS install can point these at an older PROJ database that rasterio cannot use
for _v in ("PROJ_LIB", "PROJ_DATA"):
    os.environ.pop(_v, None)

os.environ.setdefault("GDAL_CACHEMAX", "128")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")

import numpy as np
import rasterio
from rasterio.windows import Window

from common import GRANULE, LAT0, LON0, RES, ROOT, granule_path

CACHE = ROOT / "build" / "floodplain"
DEM_DIR = CACHE / "dem"
DEM_RES = 1 / 1800  # Copernicus GLO-30, first overview: 2 arc-seconds (~60 m)
DEM_URL = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
           "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
WC_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N{lat:02d}E{lon:03d}_Map.tif"
WC_PER_JRC = 3  # WorldCover (1/12000 deg) nests exactly 3x3 inside a JRC pixel (0.00025 deg)

_open = {}


def _ds(path):
    if path not in _open:
        _open[path] = rasterio.open(path)
    return _open[path]


def mosaic(layer, r0, r1, c0, c1, fill=255):
    """Read JRC rows r0..r1, cols c0..c1 (mosaic pixels, end exclusive) across granule edges."""
    out = np.full((r1 - r0, c1 - c0), fill, np.uint8)
    for rb in range(max(0, r0 // GRANULE), min(1, (r1 - 1) // GRANULE) + 1):
        for cb in range(max(0, c0 // GRANULE), min(1, (c1 - 1) // GRANULE) + 1):
            ra, rz = max(r0, rb * GRANULE), min(r1, (rb + 1) * GRANULE)
            ca, cz = max(c0, cb * GRANULE), min(c1, (cb + 1) * GRANULE)
            if ra >= rz or ca >= cz:
                continue
            w = Window(ca - cb * GRANULE, ra - rb * GRANULE, cz - ca, rz - ra)
            out[ra - r0:rz - r0, ca - c0:cz - c0] = _ds(granule_path(layer, (cb, rb))).read(1, window=w)
    return out


def to_rc(lon, lat):
    return (np.floor((LAT0 - np.asarray(lat)) / RES).astype(np.int64),
            np.floor((np.asarray(lon) - LON0) / RES).astype(np.int64))


def sample_mosaic(layer, lon, lat):
    rows, cols = to_rc(lon, lat)
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    return mosaic(layer, r0, r1, c0, c1)[rows - r0, cols - c0]


def dem_key(lat, lon):
    return f"N{lat:02d}E{lon:03d}"


def fetch_dem(tile):
    """Download one 1-degree DEM tile at ~60 m into the build cache. Sea tiles do not exist."""
    lat, lon = tile
    path = DEM_DIR / f"{dem_key(lat, lon)}.npy"
    if path.exists():
        return path
    url = DEM_URL.format(ns="N", lat=lat, ew="E", lon=lon)
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            np.save(path, np.zeros((0, 0), np.float32))
            return path
        raise
    with rasterio.open(f"/vsicurl/{url}", overview_level=0) as d:
        np.save(path, d.read(1).astype(np.float32))
    return path


_dem = {}


def sample_dem(lon, lat):
    """Ground (surface model) height in metres; NaN over the sea."""
    lon, lat = np.asarray(lon), np.asarray(lat)
    out = np.full(lon.shape, np.nan, np.float32)
    lat_i, lon_i = np.floor(lat).astype(int), np.floor(lon).astype(int)
    for tile in {(int(a), int(b)) for a, b in zip(lat_i.ravel(), lon_i.ravel())}:
        if tile not in _dem:
            p = DEM_DIR / f"{dem_key(*tile)}.npy"
            _dem[tile] = np.load(p, mmap_mode="r") if p.exists() else np.zeros((0, 0), np.float32)
        a = _dem[tile]
        if not a.size:
            continue
        sel = (lat_i == tile[0]) & (lon_i == tile[1])
        r = np.clip(((tile[0] + 1 - lat[sel]) / DEM_RES).astype(int), 0, a.shape[0] - 1)
        c = np.clip(((lon[sel] - tile[1]) / DEM_RES).astype(int), 0, a.shape[1] - 1)
        out[sel] = a[r, c]
    return out


def worldcover(r0, r1, c0, c1):
    """WorldCover classes for JRC rows/cols, at 3x the JRC resolution."""
    k = WC_PER_JRC
    out = np.zeros(((r1 - r0) * k, (c1 - c0) * k), np.uint8)
    top, bottom = LAT0 - r0 * RES, LAT0 - r1 * RES
    left, right = LON0 + c0 * RES, LON0 + c1 * RES
    for lat in range(math.floor(bottom / 3) * 3, math.ceil(top / 3) * 3, 3):
        for lon in range(math.floor(left / 3) * 3, math.ceil(right / 3) * 3, 3):
            url = "/vsicurl/" + WC_URL.format(lat=lat, lon=lon)
            # tile origin in WorldCover pixels from the mosaic corner
            tr, tc = round((LAT0 - (lat + 3)) / RES) * k, round((lon - LON0) / RES) * k
            ra, rz = max(r0 * k, tr), min(r1 * k, tr + 36000)
            ca, cz = max(c0 * k, tc), min(c1 * k, tc + 36000)
            if ra >= rz or ca >= cz:
                continue
            try:
                d = _ds(url)
            except rasterio.errors.RasterioIOError:  # tile over the sea
                continue
            out[ra - r0 * k:rz - r0 * k, ca - c0 * k:cz - c0 * k] = d.read(1, window=Window(ca - tc, ra - tr, cz - ca, rz - ra))
    return out
