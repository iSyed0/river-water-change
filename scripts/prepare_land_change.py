"""Detailed land loss and deposition from the Earth Engine exports in "GEE indus erosion/".

The three rasters come from indus_land_change.js in that folder: Sentinel-2 median composites of
Nov-Jan in 2018-19 and 2025-26, with water at MNDWI > 0.05. They are 20 m yes/no masks in one UTM
grid: land lost to the river, land deposited, and water in 2025-26. Only the floodplain counts, so fields and ponds elsewhere in the window are
ignored, and patches under MIN_PATCH pixels are dropped as speckle. Any window can be exported:
this script works from the rasters' own extent.

Land use on the lost land uses ESA WorldCover 2021, so land the river took before 2021 shows as water.

Outputs:
  portal/tiles/landloss/{z}/{x}/{y}.png   z5-13, values readable by the pixel inspector
  portal/data/land_change.json            per district, per 20 km reach, and on each cross-section

    python scripts/prepare_land_change.py     (after analyse_floodplain.py)
"""
import json
import math
import shutil

from analyse_floodplain import (EDU, HEALTH, MAJOR, NOT_ROADS, REACH_KM, osm_read, rle, sample_segments)
from floodplain_common import CACHE

import numpy as np
import rasterio
import shapely
from PIL import Image
from pyproj import Transformer
from rasterio import features
from rasterio.warp import Resampling, reproject
from scipy.ndimage import distance_transform_edt, label
from scipy.spatial import cKDTree
from shapely.geometry import box, shape
from shapely.ops import transform as shp_transform, unary_union

from common import DATA_OUT, LAYERS, ROOT, TILES, TILE
from floodplain_common import to_rc

SRC = ROOT / "GEE indus erosion"
MIN_PATCH = 5           # pixels; 0.2 ha
NEAR_M = 200            # "close to lost land"
LOSS, DEPOSIT, WATER, NONE = 1, 2, 3, 255
MIN_ZOOM, MAX_ZOOM = 5, 13
EARTH = 20037508.342789244
PX_KM2 = 0.0004

METRICS = ["loss", "deposit", "water", "crop", "built", "veg", "bare", "water21",
           "major", "minor", "rail", "bldg", "place", "edu", "health",
           "bldg_near", "place_near", "major_near", "minor_near", "rail_near"]
MI = {k: i for i, k in enumerate(METRICS)}


def read(name):
    with rasterio.open(SRC / name) as d:
        return d.read(1) == 1, d.transform, d.crs, d.bounds


def despeckle(mask):
    lab, _ = label(mask, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel())
    keep = sizes >= MIN_PATCH
    keep[0] = False
    return keep[lab]


def rowcol(tf, x, y, shape_):
    c = np.floor((np.asarray(x) - tf.c) / tf.a).astype(np.int64)
    r = np.floor((np.asarray(y) - tf.f) / tf.e).astype(np.int64)
    return r, c, (r >= 0) & (r < shape_[0]) & (c >= 0) & (c < shape_[1])


def write_tile(z, x, y, arr, colors):
    pal = np.zeros((256, 3), np.uint8)
    for k, rgb in colors.items():
        pal[k] = rgb
    img = Image.fromarray(arr, "P")
    img.putpalette(pal.flatten().tolist())
    p = TILES / "landloss" / str(z) / str(x) / f"{y}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p, compress_level=6, transparency=bytes(255 if k in colors else 0 for k in range(256)))


def build_tiles(classes, tf, crs, lonlat):
    """Render the top zoom straight from the raster, then keep the strongest signal in each parent
    pixel (loss, then deposition, then water) so thin channels survive when zoomed out."""
    colors = LAYERS["landloss"]["colors"]
    shutil.rmtree(TILES / "landloss", ignore_errors=True)
    src = np.where(classes == 0, NONE, classes).astype(np.uint8)
    w, s, e, n = lonlat
    tile_x = lambda lon, z: int((lon + 180) / 360 * 2 ** z)
    tile_y = lambda lat, z: int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2 ** z)
    z = MAX_ZOOM
    size = 2 * EARTH / 2 ** z
    level = {}
    for x in range(tile_x(w, z), tile_x(e, z) + 1):
        for y in range(tile_y(n, z), tile_y(s, z) + 1):
            dst = np.full((TILE, TILE), NONE, np.uint8)
            dst_tf = rasterio.transform.from_origin(-EARTH + x * size, EARTH - y * size, size / TILE, size / TILE)
            reproject(src, dst, src_transform=tf, src_crs=crs, dst_transform=dst_tf, dst_crs="EPSG:3857",
                      src_nodata=NONE, dst_nodata=NONE, resampling=Resampling.nearest)
            if (dst != NONE).any():
                level[(x, y)] = dst
                write_tile(z, x, y, dst, colors)
    total = len(level)
    for z in range(MAX_ZOOM - 1, MIN_ZOOM - 1, -1):
        parents = {}
        for (x, y), arr in level.items():
            big = parents.setdefault((x // 2, y // 2), np.full((2 * TILE, 2 * TILE), NONE, np.uint8))
            ox, oy = (x % 2) * TILE, (y % 2) * TILE
            big[oy:oy + TILE, ox:ox + TILE] = arr
        level = {}
        for (x, y), big in parents.items():
            # classes are numbered strongest first and NONE is the largest value
            out = big.reshape(TILE, 2, TILE, 2).min((1, 3)).astype(np.uint8)
            level[(x, y)] = out
            write_tile(z, x, y, out, colors)
        total += len(level)
    return total


def main():
    loss, tf, crs, bounds = read("Indus_Land_Loss.tif")
    dep, *_ = read("Indus_Land_Deposition.tif")
    water, *_ = read("Indus_Water_Extent.tif")
    shape_ = loss.shape
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    utm = lambda g: shp_transform(lambda x, y, z=None: to_utm.transform(x, y), g)
    edge = [to_ll.transform(x, y) for x in (bounds.left, bounds.right) for y in (bounds.bottom, bounds.top)]
    lonlat = (min(c[0] for c in edge), min(c[1] for c in edge), max(c[0] for c in edge), max(c[1] for c in edge))

    # Floodplain only, then remove speckle
    fp_ll = unary_union([shape(f["geometry"]) for f in json.loads((DATA_OUT / "floodplain.geojson").read_text(encoding="utf-8"))["features"]])
    fp = features.rasterize([(utm(fp_ll), 1)], out_shape=shape_, transform=tf, fill=0, dtype="uint8").astype(bool)
    print(f"Window {tuple(round(v, 3) for v in lonlat)}; floodplain {fp.sum() * PX_KM2:,.0f} km2 of {fp.size * PX_KM2:,.0f}")
    print(f"Before clipping: loss {loss.sum() * PX_KM2:.1f}, deposition {dep.sum() * PX_KM2:.1f}, water {water.sum() * PX_KM2:.1f} km2")
    print(f"Outside the floodplain, dropped: loss {(loss & ~fp).sum() * PX_KM2:.1f}, deposition {(dep & ~fp).sum() * PX_KM2:.1f} km2")
    loss = despeckle(loss & fp)
    dep = despeckle(dep & fp)
    water = water & fp
    classes = np.zeros(shape_, np.uint8)
    classes[water] = WATER
    classes[dep] = DEPOSIT
    classes[loss] = LOSS

    print("Tiles")
    print(f"  {build_tiles(classes, tf, crs, lonlat)} tiles")
    palettes = json.loads((DATA_OUT / "palettes.json").read_text(encoding="utf-8"))
    palettes["landloss"] = {str(k): list(v) for k, v in LAYERS["landloss"]["colors"].items()}
    (DATA_OUT / "palettes.json").write_text(json.dumps(palettes, separators=(",", ":")), encoding="utf-8")

    # Every pixel and feature gets a district and a 20 km reach
    route = json.loads((DATA_OUT / "indus_route.json").read_text(encoding="utf-8"))
    R = np.array(route["coords"], float)
    tree = cKDTree(np.column_stack([R[:, 0] * 96.4, R[:, 1] * 110.574]))
    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    win = box(*lonlat)
    near_d = [(i, shape(f["geometry"])) for i, f in enumerate(districts) if shape(f["geometry"]).intersects(win)]
    dist = features.rasterize([(utm(g), i + 1) for i, g in near_d], out_shape=shape_, transform=tf, fill=0, dtype="int32")
    table = {}
    units_path = DATA_OUT / "admin_units.geojson"
    unit_feats = json.loads(units_path.read_text(encoding="utf-8"))["features"] if units_path.exists() else []
    near_u = [(f["properties"]["id"], shape(f["geometry"])) for f in unit_feats if box(*f["properties"]["bbox"]).intersects(win)]
    unit_grid = features.rasterize([(utm(g), i) for i, g in near_u], out_shape=shape_, transform=tf, fill=0, dtype="int32") if near_u else np.zeros(shape_, "int32")
    unit_table = {}

    def reach_of(lon, lat):
        _, km = tree.query(np.column_stack([np.asarray(lon) * 96.4, np.asarray(lat) * 110.574]))
        return km // REACH_KM

    def add(metric, keys, weights=None, rows=None, cols=None):
        for store, group in ((table, keys), (unit_table, unit_grid[rows, cols])):
            uk, inv = np.unique(group, return_inverse=True)
            sums = np.bincount(inv, weights=weights, minlength=len(uk))
            for k, v in zip(uk, sums):
                store.setdefault(int(k), np.zeros(len(METRICS)))[MI[metric]] += v

    def keys_of(rows, cols):
        x, y = rasterio.transform.xy(tf, rows, cols)
        lon, lat = to_ll.transform(np.array(x), np.array(y))
        return reach_of(lon, lat).astype(np.int64) * 100000 + dist[rows, cols], lon, lat

    for metric, mask in (("loss", loss), ("deposit", dep), ("water", water)):
        rows, cols = np.nonzero(mask)
        k, lon, lat = keys_of(rows, cols)
        add(metric, k, np.full(len(k), PX_KM2), rows, cols)
        if metric == "loss":
            # WorldCover shares at each lost pixel, from the cache analyse_floodplain.py filled
            rr, cc = to_rc(lon, lat)
            shares = np.zeros((5, len(rr)))
            for key in {(int(a), int(b)) for a, b in zip(rr // 1000, cc // 1000)}:
                p = CACHE / "wc" / f"{key[0] * 1000}_{key[1] * 1000}.npz"
                if p.exists():
                    sel = (rr // 1000 == key[0]) & (cc // 1000 == key[1])
                    shares[:, sel] = np.load(p)["c"][:, rr[sel] - key[0] * 1000, cc[sel] - key[1] * 1000] / 9
            for i, name in enumerate(("crop", "built", "veg", "bare", "water21")):
                add(name, k, shares[i] * PX_KM2, rows, cols)

    near = (distance_transform_edt(~loss) * abs(tf.a) <= NEAR_M) & fp

    def count(metric, near_metric, lon, lat, weights=None):
        x, y = to_utm.transform(np.asarray(lon), np.asarray(lat))
        r, c, ok = rowcol(tf, x, y, shape_)
        r, c, lon, lat = r[ok], c[ok], np.asarray(lon)[ok], np.asarray(lat)[ok]
        w = None if weights is None else np.asarray(weights)[ok]
        for name, hit in ((metric, loss[r, c]), (near_metric, near[r, c])):
            if name and hit.any():
                k = reach_of(lon[hit], lat[hit]).astype(np.int64) * 100000 + dist[r[hit], c[hit]]
                add(name, k, None if w is None else w[hit], r[hit], c[hit])

    print("Infrastructure and buildings")
    bldg = np.load(CACHE / "buildings.npz")
    count("bldg", "bldg_near", bldg["xy"][:, 0], bldg["xy"][:, 1])
    places = json.loads((DATA_OUT / "places.json").read_text(encoding="utf-8"))
    count("place", "place_near", np.array(places["x"]) / 1e5, np.array(places["y"]) / 1e5)
    roads = osm_read("roads", lonlat, ["fclass"])
    roads = roads[~roads.fclass.isin(NOT_ROADS)]
    major = roads.fclass.isin(MAJOR).values
    for mask_, name, near_name in ((major, "major", "major_near"), (~major, "minor", "minor_near")):
        seg = sample_segments(roads.geometry.values[mask_], 0)
        count(name, near_name, seg[:, 0], seg[:, 1], seg[:, 2])
    rail = osm_read("railways", lonlat, ["fclass"])
    rail = rail[rail.fclass.isin(["rail", "narrow_gauge"])]
    seg = sample_segments(rail.geometry.values, 0)
    count("rail", "rail_near", seg[:, 0], seg[:, 1], seg[:, 2])
    for layer in ("pois", "pois_a"):
        df = osm_read(layer, lonlat, ["fclass"])
        c = shapely.get_coordinates(shapely.centroid(df.geometry.values))
        for fset, name in ((EDU, "edu"), (HEALTH, "health")):
            sel = df.fclass.isin(fset).values
            count(name, None, c[sel, 0], c[sel, 1])

    # Tables
    reach_t, area_t = {}, {}
    for k, v in table.items():
        reach_t[k // 100000] = reach_t.get(k // 100000, 0) + v
        area_t[k % 100000] = area_t.get(k % 100000, 0) + v
    total = sum(reach_t.values())
    assert abs(total[MI["loss"]] - loss.sum() * PX_KM2) < 1e-6
    round_v = lambda v: [round(float(x), 3) for x in v]
    props = [f["properties"] for f in districts]
    areas = [{"id": props[d - 1]["id"] if d else -1, "name": props[d - 1]["name"] if d else "Outside district data",
              "province": props[d - 1]["province"] if d else "", "v": round_v(v)} for d, v in sorted(area_t.items())]
    areas.sort(key=lambda a: -a["v"][MI["loss"]])
    unit_table.pop(0, None)
    reaches = [{"r": int(r), "km0": int(r) * REACH_KM, "km1": (int(r) + 1) * REACH_KM, "v": round_v(v)} for r, v in sorted(reach_t.items())]

    # Each cross-section inside the window: the same 30 m samples as its chart
    sec = np.load(CACHE / "sections.npz")
    charts = {s["km"]: s for s in json.loads((DATA_OUT / "fp_sections.json").read_text(encoding="utf-8"))["sections"]}
    lines = {}
    for i, km in enumerate(sec["kms"]):
        s = charts[int(km)]
        n = sum(s["cls"][0::2])
        offs = s["x0"] + np.arange(n) * 30.0
        kx = 111.320 * np.cos(np.radians(sec["lat"][i]))
        lon = sec["lon"][i] + sec["ty"][i] * offs / 1000 / kx
        lat = sec["lat"][i] - sec["tx"][i] * offs / 1000 / 110.574
        x, y = to_utm.transform(lon, lat)
        r, c, ok = rowcol(tf, x, y, shape_)
        if ok.mean() < 0.5 or not (np.abs(offs) < 1e9).all():
            continue
        vals = np.zeros(n, int)
        vals[ok] = classes[r[ok], c[ok]]
        if (vals == 0).all():
            continue
        lines[int(km)] = rle(vals)

    data = {
        "meta": {"metrics": METRICS, "bounds": [round(v, 4) for v in lonlat], "res_m": abs(tf.a), "near_m": NEAR_M,
                 "km0": min(lines), "km1": max(lines), "reach_km": REACH_KM},
        "total": round_v(total), "areas": areas, "reaches": reaches, "sections": lines,
        "units": {str(k): round_v(v) for k, v in sorted(unit_table.items())},
    }
    out = DATA_OUT / "land_change.json"
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{out.name}: {out.stat().st_size / 1e3:.0f} kB; river km {min(lines)}-{max(lines)}")

    v = dict(zip(METRICS, total))
    print(f"\nIn the floodplain: lost {v['loss']:.1f} km2, deposited {v['deposit']:.1f} km2, water 2025 {v['water']:.1f} km2")
    print(f"On lost land (WorldCover 2021): cropland {v['crop']:.1f}, built-up {v['built']:.2f}, vegetation {v['veg']:.1f}, bare {v['bare']:.1f}, water {v['water21']:.1f} km2")
    print(f"Buildings on lost land {v['bldg']:.0f}, within {NEAR_M} m {v['bldg_near']:.0f}; villages {v['place']:.0f} / {v['place_near']:.0f}")
    print(f"Main roads {v['major']:.1f} / {v['major_near']:.1f} km; other roads {v['minor']:.1f} / {v['minor_near']:.1f} km; rail {v['rail']:.2f} / {v['rail_near']:.2f} km")
    print(f"Schools {v['edu']:.0f}, health {v['health']:.0f}")
    for a in areas:
        print(f"  {a['name']:22} lost {a['v'][MI['loss']]:6.1f}  deposited {a['v'][MI['deposit']]:6.1f}  buildings {a['v'][MI['bldg']]:5.0f}")


if __name__ == "__main__":
    main()
