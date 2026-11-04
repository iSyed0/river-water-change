"""Surface-water statistics per district / province from the 30 m JRC rasters.

Areas come from pixel counts weighted by the true area of each 0.00025 deg pixel at
its latitude. National totals use the official national boundary (common.NATIONAL);
province and district totals use geoBoundaries. Output: portal/data/stats.json

    python scripts/compute_stats.py
"""
import argparse
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor

os.environ.setdefault("GDAL_CACHEMAX", "64")

import numpy as np
import rasterio
from affine import Affine
from pyproj import Geod
from rasterio import features
from rasterio.windows import Window
from shapely.geometry import box, shape

from common import (BOUNDARIES, DATA_OUT, GRANULE, GRANULES, LAT0, LON0, RES, TRANSITION_CLASSES, granule_path,
                    national_boundary)

EARTH_R_KM = 6371.0088
NCLS = 16
CHUNK = 250

# Derived indicators from the transition classes (first year vs last year of the record)
DERIVED = {
    "permanent": [1, 2, 7],   # permanent water today
    "seasonal": [4, 5, 8],    # seasonal water today
    "gained": [2, 5],         # new permanent + new seasonal
    "lost": [3, 6],           # lost permanent + lost seasonal
    "ephemeral": [9, 10],
}

_W = {}


def _init(shapes, national, nd):
    _W["shapes"] = shapes
    _W["nd"] = nd
    _W["national"] = national


def chunk_stats(args):
    key, row0, nrows = args
    cb, rb = key
    lon_left = LON0 + cb * GRANULE * RES
    lat_top = LAT0 - (rb * GRANULE + row0) * RES
    tf = Affine(RES, 0, lon_left, 0, -RES, lat_top)
    ids = features.rasterize(_W["shapes"], out_shape=(nrows, GRANULE), transform=tf, fill=0, dtype="uint16")
    nat = features.rasterize([(_W["national"], 1)], out_shape=(nrows, GRANULE), transform=tf, fill=0,
                             dtype="uint8").astype(bool)
    nd = _W["nd"]
    if not ids.any() and not nat.any():
        return np.zeros((nd, NCLS)), np.zeros(nd), np.zeros(NCLS), 0.0
    win = Window(0, row0, GRANULE, nrows)
    with rasterio.open(granule_path("transitions", key)) as ds:
        tr = ds.read(1, window=win)
    with rasterio.open(granule_path("extent", key)) as ds:
        ex = ds.read(1, window=win)
    tr = np.where(tr <= 10, tr, 0).astype(np.uint16)
    sel = ids > 0
    idv = ids[sel]
    cls = np.bincount(idv * NCLS + tr[sel], minlength=nd * NCLS).reshape(nd, NCLS)
    ext = np.bincount(idv[ex[sel] == 1], minlength=nd)
    nat_cls = np.bincount(tr[nat], minlength=NCLS)
    nat_ext = np.count_nonzero(ex[nat] == 1)
    lat_c = lat_top - nrows * RES / 2
    px_km2 = (math.radians(RES) * EARTH_R_KM) ** 2 * math.cos(math.radians(lat_c))
    return cls * px_km2, ext * px_km2, nat_cls * px_km2, nat_ext * px_km2


def summarise(cls, ext):
    out = {
        "extent_km2": round(float(ext), 2),
        "classes_km2": {str(k): round(float(cls[k]), 2) for k in TRANSITION_CLASSES},
    }
    for name, ks in DERIVED.items():
        out[f"{name}_km2"] = round(float(sum(cls[k] for k in ks)), 2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    t = time.time()

    feats = sorted(json.loads((BOUNDARIES / "pak_ADM2_full.geojson").read_text(encoding="utf-8"))["features"],
                   key=lambda f: f["properties"]["shapeName"])
    web = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    province_of = {f["properties"]["id"]: f["properties"]["province"] for f in web}
    national_geom, _ = national_boundary()
    # geoBoundaries units run offshore; trim them to the official national boundary
    geoms = [shape(f["geometry"]).intersection(national_geom) for f in feats]
    shapes = [(g, i) for i, g in enumerate(geoms, start=1) if not g.is_empty]
    b = np.array([g.bounds for g in geoms] + [national_geom.bounds])
    union_bounds = box(b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max())

    jobs = []
    for (cb, rb) in GRANULES:
        for row0 in range(0, GRANULE, CHUNK):
            lon_l = LON0 + cb * GRANULE * RES
            lat_t = LAT0 - (rb * GRANULE + row0) * RES
            if box(lon_l, lat_t - CHUNK * RES, lon_l + GRANULE * RES, lat_t).intersects(union_bounds):
                jobs.append(((cb, rb), row0, CHUNK))
    print(f"{len(jobs)} chunks, {args.workers} workers")

    nd = len(geoms) + 1
    cls, ext = np.zeros((nd, NCLS)), np.zeros(nd)
    nat_cls, nat_ext = np.zeros(NCLS), 0.0
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(shapes, national_geom, nd)) as ex:
        for i, (c, e, nc, ne) in enumerate(ex.map(chunk_stats, jobs), 1):
            cls += c
            ext += e
            nat_cls += nc
            nat_ext += ne
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)}  {time.time() - t:.0f}s", flush=True)

    geod = Geod(ellps="WGS84")
    districts, provinces = [], {}
    for i, (f, g) in enumerate(zip(feats, geoms), start=1):
        land = abs(geod.geometry_area_perimeter(g)[0]) / 1e6
        prov = province_of[i]
        districts.append({"id": i, "name": f["properties"]["shapeName"], "province": prov,
                          "area_km2": round(land, 1), **summarise(cls[i], ext[i])})
        p = provinces.setdefault(prov, {"cls": np.zeros(NCLS), "ext": 0.0, "area": 0.0, "n": 0})
        p["cls"] += cls[i]
        p["ext"] += ext[i]
        p["area"] += land
        p["n"] += 1

    prov_out = [{"name": k, "districts": v["n"], "area_km2": round(v["area"], 1), **summarise(v["cls"], v["ext"])}
                for k, v in sorted(provinces.items())]
    national = {"name": "Pakistan", "districts": len(districts),
                "area_km2": round(abs(geod.geometry_area_perimeter(national_geom)[0]) / 1e6, 1),
                **summarise(nat_cls, nat_ext)}

    out = {
        "source": "JRC Global Surface Water v1.5 (1984-2024), 30 m",
        "boundaries": "National: official boundary (Pakistan_National.geojson); provinces/districts: geoBoundaries gbOpen",
        "classes": {str(k): v[0] for k, v in TRANSITION_CLASSES.items()},
        "derived": {k: v for k, v in DERIVED.items()},
        "national": national,
        "provinces": prov_out,
        "districts": districts,
    }
    (DATA_OUT / "stats.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"done {time.time() - t:.0f}s  extent {national['extent_km2']:.0f} km2, "
          f"permanent {national['permanent_km2']:.0f} km2, seasonal {national['seasonal_km2']:.0f} km2")


if __name__ == "__main__":
    main()
