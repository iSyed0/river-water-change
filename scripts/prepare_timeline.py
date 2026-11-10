"""Monthly water, March 1984 to December 2021, from the Earth Engine exports in "ndma water monthly/".

The exports come from "GEE monthly water/monthly_water_export.js": JRC Monthly Water History v1.4 (Landsat, 30 m)
summed into 0.0025 degree cells (about 280 m), with two bands per month: w_YYYY_MM, the share of the cell seen as water,
and s_YYYY_MM, the share the satellite could see at all. Earth Engine split most exports into an east and a west file.

Stages:
  clean    join the halves, crop to Pakistan, and keep one value per cell and month: the water share of the part that
           was seen, or UNSEEN where under SEEN_MIN of the cell was visible (cloud, or no image). Unseen is not dry.
           -> build/timeline/share_YYYY.tif (12 bands; a month outside the record is unseen)
  normal   each cell's usual water share for each calendar month: the median over NORMAL_YEARS of the months it was seen
           -> build/timeline/normal.tif (12 bands)
  publish  map tiles and figures. A month's water counts as above usual where its share beats the usual share for that
           calendar month by UNUSUAL_POINTS or more: the likely flood signal, though rain ponding and flooded fields
           can do the same. -> portal/tiles/monthly/YYYY_Qn/{z}/{x}/{y}.png, portal/data/timeline.json

  yearly   each year's water the way JRC's yearly classification sorts it: permanent (wet in nearly every month it was
           seen: rivers, canals, lakes), seasonal (wet in some months: floods, rain ponding, irrigation), and seasonal
           water well above usual in two or more months of that year, the likely flood.
           -> portal/tiles/yearly/YYYY/{z}/{x}/{y}.png, portal/data/timeline_years.json

Monthly tiles hold three months each, one per colour channel (Q1 = Jan, Feb, Mar), so the browser picks a month by
channel. Each channel value: 1 not seen, 2 dry, 2 + share for usual water (3-102), 102 + share for water above usual
(103-202). Alpha is 0 outside Pakistan.

Yearly tiles are palette images: 1 not seen, then 10 levels each of permanent (10-19), seasonal (20-29) and seasonal
above usual (30-39), by the typical share of the cell under water in its wet months.

    python scripts/prepare_timeline.py                 (all stages)
    python scripts/prepare_timeline.py --stage publish
"""
import argparse
import glob
import json
import math
import os
import re
import shutil
import time
import warnings
from concurrent.futures import ProcessPoolExecutor

import floodplain_common  # noqa: F401  (clears a conflicting PROJ path before rasterio loads)
import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from rasterio import features
from rasterio.windows import Window
from shapely.geometry import shape

from common import DATA_OUT, ROOT, TILES, national_boundary, national_outline

SRC = ROOT / "ndma water monthly"
WORK = ROOT / "build" / "timeline"
OUT_TILES = TILES / "monthly"
OUT_YEARS = TILES / "yearly"
CELL = 0.0025
FIRST, LAST = (1984, 3), (2021, 12)
SEEN_MIN = 50           # % of a cell that must be visible for its month to count
NORMAL_YEARS = (2000, 2021)
NORMAL_MIN_YEARS = 5
UNUSUAL_POINTS = 25     # share above the usual share for that calendar month
UNSEEN, OUTSIDE = 253, 255
WET_MIN = 10            # % of a cell under water for its month to count as wet (a canal fills part of a cell)
PERMANENT_SHARE = 0.9   # wet in this share of the months a cell was seen: permanent for the year
YEAR_SEEN_MIN = 3       # months a cell must be seen in a year to be classed
# A year's seasonal water counts as above usual when it beats the usual share for its calendar month by this much, in
# this many months of the year. Looser rules marked most years: in 2010 this finds 5.7 times the area it finds in 2014.
YEAR_UNUSUAL_POINTS, YEAR_UNUSUAL_MONTHS = 40, 2
PERM, SEAS, ABOVE = 10, 20, 30
# Permanent water strong; seasonal pale and light; seasonal above usual orange. (colour, alpha at level 1, at level 10)
YEAR_COLORS = {PERM: ((37, 140, 255), 150, 255), SEAS: ((165, 180, 220), 60, 165), ABOVE: ((249, 115, 22), 110, 255)}
ZOOMS = range(4, 10)    # z9 is about the cells' own size
STRIP = 512

# Pakistan's outline, IIOJK included as on the other water layers, in whole cells
W0, N0 = 60.75, 37.25   # export grid origin
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = 60.85, 23.8, 79.32, 37.1
C0, C1 = round((LON_MIN - W0) / CELL), round((LON_MAX - W0) / CELL)
R0, R1 = round((N0 - LAT_MAX) / CELL), round((N0 - LAT_MIN) / CELL)
WIDTH, HEIGHT = C1 - C0, R1 - R0
TRANSFORM = Affine(CELL, 0, W0 + C0 * CELL, 0, -CELL, N0 - R0 * CELL)
PROFILE = dict(driver="GTiff", width=WIDTH, height=HEIGHT, count=12, dtype="uint8", nodata=OUTSIDE, crs="EPSG:4326",
               transform=TRANSFORM, tiled=True, blockxsize=512, blockysize=512, compress="deflate", predictor=2)


def months():
    return [(y, m) for y in range(FIRST[0], LAST[0] + 1) for m in range(1, 13) if FIRST <= (y, m) <= LAST]


def outline_mask():
    return features.rasterize([(national_outline(), 1)], out_shape=(HEIGHT, WIDTH), transform=TRANSFORM, fill=0, dtype="uint8").astype(bool)


# ---------------------------------------------------------------- clean
def clean_block(prefix):
    """One export block (a few years): join its files, crop, and write each year's share."""
    t0 = time.time()
    parts = sorted(glob.glob(str(SRC / f"{prefix}*.tif")))
    srcs = [rasterio.open(p) for p in parts]
    names = list(srcs[0].descriptions)
    assert all(list(s.descriptions) == names for s in srcs), prefix
    years = sorted({int(n[2:6]) for n in names})
    mask = outline_mask()
    outs = {y: rasterio.open(WORK / f"share_{y}.tif", "w", **PROFILE) for y in years}
    for row in range(0, HEIGHT, STRIP):
        h = min(STRIP, HEIGHT - row)
        full = np.zeros((len(names), h, WIDTH), np.uint8)
        for s in srcs:
            # This file's columns, in the cropped grid
            c_start = round((s.transform.c - W0) / CELL)
            a, b = max(c_start, C0), min(c_start + s.width, C1)
            if a >= b:
                continue
            full[:, :, a - C0:b - C0] = s.read(window=Window(a - c_start, R0 + row, b - a, h))
        m = mask[row:row + h]
        by_year = {y: np.full((12, h, WIDTH), UNSEEN, np.uint8) for y in years}
        for i, n in enumerate(names):
            if not n.startswith("w_"):
                continue
            y, mo = int(n[2:6]), int(n[7:9])
            w = full[i].astype(np.float32)
            s = full[names.index("s" + n[1:])].astype(np.float32)
            share = np.where(s >= SEEN_MIN, np.round(100 * w / np.maximum(s, 1)), UNSEEN).astype(np.uint8)
            share[~m] = OUTSIDE
            by_year[y][mo - 1] = share
        for y in years:
            by_year[y][:, ~m] = OUTSIDE
            outs[y].write(by_year[y], window=Window(0, row, WIDTH, h))
    for o in list(outs.values()) + srcs:
        o.close()
    return f"{prefix}: years {years[0]}-{years[-1]}, {time.time() - t0:.0f}s"


def stage_clean():
    WORK.mkdir(parents=True, exist_ok=True)
    prefixes = sorted({re.sub(r"(-\d+-\d+)?\.tif$", "", os.path.basename(p)) for p in glob.glob(str(SRC / "*.tif"))})
    print(f"clean: {len(prefixes)} export blocks, grid {WIDTH} x {HEIGHT} cells", flush=True)
    with ProcessPoolExecutor(3) as ex:
        for msg in ex.map(clean_block, prefixes):
            print("  " + msg, flush=True)


# ---------------------------------------------------------------- normal
def normal_month(m):
    years = range(NORMAL_YEARS[0], NORMAL_YEARS[1] + 1)
    srcs = [rasterio.open(WORK / f"share_{y}.tif") for y in years]
    out = np.full((HEIGHT, WIDTH), UNSEEN, np.uint8)
    for row in range(0, HEIGHT, STRIP):
        h = min(STRIP, HEIGHT - row)
        stack = np.stack([s.read(m, window=Window(0, row, WIDTH, h)) for s in srcs]).astype(np.float32)
        outside = stack[0] == OUTSIDE
        stack[stack > 100] = np.nan
        n = np.isfinite(stack).sum(0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(stack, axis=0)
        block = np.where(n >= NORMAL_MIN_YEARS, np.round(med), UNSEEN)
        block[outside] = OUTSIDE
        out[row:row + h] = np.nan_to_num(block, nan=UNSEEN).astype(np.uint8)
    for s in srcs:
        s.close()
    return m, out


def stage_normal():
    print(f"normal: median of seen months, {NORMAL_YEARS[0]}-{NORMAL_YEARS[1]}", flush=True)
    with rasterio.open(WORK / "normal.tif", "w", **PROFILE) as dst:
        with ProcessPoolExecutor(3) as ex:
            for m, arr in ex.map(normal_month, range(1, 13)):
                dst.write(arr, m)
                known = arr[arr <= 100]
                print(f"  month {m:2d}: usual share known for {known.size / (arr != OUTSIDE).sum() * 100:.0f}% of cells", flush=True)


# ---------------------------------------------------------------- publish
EARTH = 20037508.342789244


def mercator_grid(z):
    """Tile range and, for each mosaic pixel row and column, the grid row and column it samples."""
    n = 2 ** z
    tx0 = int((LON_MIN + 180) / 360 * n)
    tx1 = int((LON_MAX + 180) / 360 * n)
    ty = lambda lat: int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    ty0, ty1 = ty(LAT_MAX), ty(LAT_MIN)
    px = (np.arange(tx0 * 256, (tx1 + 1) * 256) + 0.5) / (n * 256)
    py = (np.arange(ty0 * 256, (ty1 + 1) * 256) + 0.5) / (n * 256)
    lon = px * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * py))))
    cols = np.floor((lon - TRANSFORM.c) / CELL).astype(int)
    rows = np.floor((TRANSFORM.f - lat) / CELL).astype(int)
    return (tx0, ty0, tx1, ty1), rows, cols


def encode(share, normal):
    """Channel values: 1 not seen, 2 dry, 3-102 usual water, 103-202 water above usual, 0 outside."""
    v = np.full(share.shape, 2, np.uint8)
    seen = share <= 100
    wet = seen & (share > 0)
    unusual = wet & (normal <= 100) & (share.astype(np.int16) - normal >= UNUSUAL_POINTS)
    v[~seen] = 1
    v[wet] = 2 + share[wet]
    v[unusual] = 102 + share[unusual]
    v[share == OUTSIDE] = 0
    return v


def reduce2(v):
    """Halve a channel: unseen where 3 or 4 children are; else the mean share of the seen ones, above usual if most of
    that water was."""
    h, w = v.shape[0] // 2, v.shape[1] // 2
    c = v[:h * 2, :w * 2].reshape(h, 2, w, 2).transpose(0, 2, 1, 3).reshape(h, w, 4).astype(np.int16)
    inside = c > 0
    seen = c >= 2
    share = np.where(c >= 103, c - 102, np.where(c >= 3, c - 2, 0))
    unusual = np.where(c >= 103, share, 0).sum(-1)
    total = share.sum(-1)
    n_seen = seen.sum(-1)
    mean = np.where(n_seen > 0, np.round(total / np.maximum(n_seen, 1)), 0).astype(np.int16)
    out = np.where(mean > 0, np.where(unusual * 2 > total, 102 + mean, 2 + mean), 2)
    out = np.where((inside.sum(-1) - n_seen) >= 3, 1, out)
    out = np.where(inside.any(-1), out, 0)
    return out.astype(np.uint8)


def publish_quarter(args):
    year, q = args
    ms = [m for m in range(3 * q - 2, 3 * q + 1)]
    with rasterio.open(WORK / f"share_{year}.tif") as s, rasterio.open(WORK / "normal.tif") as n:
        share = s.read(ms)
        normal = n.read(ms)
    enc = np.stack([encode(share[i], normal[i]) for i in range(3)])

    # z9 mosaic in Web Mercator, then halve for each lower zoom
    zmax = max(ZOOMS)
    (tx0, ty0, tx1, ty1), rows, cols = mercator_grid(zmax)
    ok_r = (rows >= 0) & (rows < HEIGHT)
    ok_c = (cols >= 0) & (cols < WIDTH)
    mos = np.zeros((3, len(rows), len(cols)), np.uint8)
    mos[np.ix_(range(3), np.nonzero(ok_r)[0], np.nonzero(ok_c)[0])] = enc[:, rows[ok_r]][:, :, cols[ok_c]]
    name = f"{year}_Q{q}"
    count = 0
    z, ox, oy = zmax, tx0, ty0
    while True:
        if z in ZOOMS:
            for j in range(mos.shape[1] // 256):
                for i in range(mos.shape[2] // 256):
                    t = mos[:, j * 256:(j + 1) * 256, i * 256:(i + 1) * 256]
                    inside = t[0] > 0
                    # A tile of dry land only is left out: no tile reads as dry
                    if not inside.any() or not (t[:, inside] != 2).any():
                        continue
                    rgba = np.dstack([t[0], t[1], t[2], np.where(inside, 255, 0).astype(np.uint8)])
                    p = OUT_TILES / name / str(z) / str(ox + i) / f"{oy + j}.png"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(rgba, "RGBA").save(p, optimize=False, compress_level=6)
                    count += 1
        if z == min(ZOOMS):
            break
        # Pad to whole parent tiles before halving
        px0, py0 = ox % 2, oy % 2
        padded = np.zeros((3, mos.shape[1] + 256 * (py0 + (oy + mos.shape[1] // 256) % 2), mos.shape[2] + 256 * (px0 + (ox + mos.shape[2] // 256) % 2)), np.uint8)
        padded[:, py0 * 256:py0 * 256 + mos.shape[1], px0 * 256:px0 * 256 + mos.shape[2]] = mos
        mos = np.stack([reduce2(padded[k]) for k in range(3)])
        ox, oy, z = (ox - px0) // 2, (oy - py0) // 2, z - 1

    # Figures per district for each month: water seen, the usual water on the same seen ground, water above usual
    lat = TRANSFORM.f + (np.arange(HEIGHT) + 0.5) * TRANSFORM.e
    area = (math.radians(CELL) * 6371.0088) ** 2 * np.cos(np.radians(lat))[:, None]
    dist = _districts()
    nd = int(dist.max()) + 1
    out = {}
    for i, m in enumerate(ms):
        if not FIRST <= (year, m) <= LAST:
            continue
        sh, no = share[i], normal[i]
        inside = dist > 0
        seen = inside & (sh <= 100)
        wet = np.where(seen, sh, 0) / 100 * area
        usual = np.where(seen & (no <= 100), no, 0) / 100 * area
        above = np.where(seen & (no <= 100) & (sh.astype(np.int16) - no >= UNUSUAL_POINTS), sh, 0) / 100 * area
        land = np.where(inside, area, 0)
        seen_area = np.where(seen, area, 0)
        key = f"{year}_{m:02d}"
        out[key] = np.stack([np.bincount(dist.ravel(), weights=x.ravel(), minlength=nd) for x in (wet, usual, above, seen_area, land)])
    return name, count, out


_D = {}


def _districts():
    if "d" not in _D:
        fc = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
        _D["d"] = features.rasterize([(shape(f["geometry"]), f["properties"]["id"]) for f in fc], out_shape=(HEIGHT, WIDTH),
                                     transform=TRANSFORM, fill=0, dtype="int32")
    return _D["d"]


def stage_publish():
    shutil.rmtree(OUT_TILES, ignore_errors=True)
    jobs = [(y, q) for y in range(FIRST[0], LAST[0] + 1) for q in range(1, 5)
            if any(FIRST <= (y, m) <= LAST for m in range(3 * q - 2, 3 * q + 1))]
    print(f"publish: {len(jobs)} quarters, zooms {min(ZOOMS)}-{max(ZOOMS)}", flush=True)
    t0 = time.time()
    per_month = {}
    quarters = {}
    with ProcessPoolExecutor(3) as ex:
        for k, (name, count, out) in enumerate(ex.map(publish_quarter, jobs), 1):
            quarters[name] = count
            per_month.update(out)
            if k % 8 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)} quarters, {sum(quarters.values()):,} tiles, {time.time() - t0:.0f}s", flush=True)

    fc = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    keys = sorted(per_month)
    r = lambda v: round(float(v), 1)
    provinces = sorted({f["properties"]["province"] for f in fc})

    def series(ids):
        rows = []
        for k in keys:
            a = per_month[k][:, ids].sum(1)
            # water seen, usual water on the same ground, water above usual, share of the land seen (%)
            rows.append([r(a[0]), r(a[1]), r(a[2]), round(float(a[3] / a[4] * 100), 1) if a[4] else 0])
        return rows

    out = {
        "source": "JRC Monthly Water History v1.4 (Landsat, 30 m), summed to 0.0025 degree cells in Earth Engine",
        "seen_min": SEEN_MIN, "unusual_points": UNUSUAL_POINTS, "normal_years": list(NORMAL_YEARS),
        "fields": ["water_km2", "usual_km2", "above_usual_km2", "seen_pct"],
        "months": keys,
        "national": series([f["properties"]["id"] for f in fc]),
        "provinces": {p: series([f["properties"]["id"] for f in fc if f["properties"]["province"] == p]) for p in provinces},
        "districts": {str(f["properties"]["id"]): series([f["properties"]["id"]]) for f in fc},
        "tiles": {"zooms": [min(ZOOMS), max(ZOOMS)], "quarters": sorted(quarters)},
    }
    path = DATA_OUT / "timeline.json"
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    size = sum(f.stat().st_size for f in OUT_TILES.rglob("*.png"))
    print(f"{path.name}: {path.stat().st_size / 1e6:.1f} MB; tiles {sum(quarters.values()):,}, {size / 1e9:.2f} GB; {time.time() - t0:.0f}s")


# ---------------------------------------------------------------- yearly
def year_palette():
    pal = np.zeros((256, 3), np.uint8)
    alpha = np.zeros(256, np.uint8)
    pal[1], alpha[1] = (148, 163, 184), 120
    for base, (rgb, a0, a1) in YEAR_COLORS.items():
        for k in range(10):
            pal[base + k] = rgb
            alpha[base + k] = round(a0 + (a1 - a0) * k / 9)
    return pal, alpha


def year_classes(year):
    """Palette index per cell for one year; and per cell its class (0 none), typical water share, and whether classed.
    Read in strips: twelve months of a whole year at once do not fit beside other work."""
    idx = np.zeros((HEIGHT, WIDTH), np.uint8)
    cls = np.zeros((HEIGHT, WIDTH), np.uint8)
    typical = np.zeros((HEIGHT, WIDTH), np.float32)
    classed = np.zeros((HEIGHT, WIDTH), bool)
    with rasterio.open(WORK / f"share_{year}.tif") as src, rasterio.open(WORK / "normal.tif") as nrm:
        for row in range(0, HEIGHT, STRIP):
            h = min(STRIP, HEIGHT - row)
            win = Window(0, row, WIDTH, h)
            share = src.read(window=win)
            normal = nrm.read(window=win)
            outside = share[0] == OUTSIDE
            seen = share <= 100
            wet = seen & (share >= WET_MIN)
            n_seen = seen.sum(0)
            n_wet = wet.sum(0)
            typ = np.where(n_wet > 0, np.where(wet, share, 0).sum(0) / np.maximum(n_wet, 1), 0).astype(np.float32)
            above = (wet & (normal <= 100) & (share.astype(np.int16) - normal >= YEAR_UNUSUAL_POINTS)).sum(0) >= YEAR_UNUSUAL_MONTHS
            ok = (n_seen >= YEAR_SEEN_MIN) & ~outside
            perm = ok & (n_wet > 0) & (n_wet >= PERMANENT_SHARE * n_seen)
            seas = ok & (n_wet > 0) & ~perm
            c = np.zeros(outside.shape, np.uint8)
            c[perm] = PERM
            c[seas & ~above] = SEAS
            c[seas & above] = ABOVE
            level = np.clip(np.ceil(typ / 10) - 1, 0, 9).astype(np.uint8)
            ix = np.where(c > 0, c + level, 0).astype(np.uint8)
            ix[~ok & ~outside] = 1
            idx[row:row + h], cls[row:row + h], typical[row:row + h], classed[row:row + h] = ix, c, typ, ok
    return idx, cls, typical, classed


def reduce_year(v):
    """Halve a palette image: not seen where 3 or 4 children are; else the class holding the most water, at the mean
    level of the children that were seen."""
    h, w = v.shape[0] // 2, v.shape[1] // 2
    c = v[:h * 2, :w * 2].reshape(h, 2, w, 2).transpose(0, 2, 1, 3).reshape(h, w, 4).astype(np.int16)
    unseen = (c == 1).sum(-1)
    cls = np.where(c >= 10, c // 10 * 10, 0)
    lvl = np.where(c >= 10, c % 10 + 1, 0)
    weight = {k: np.where(cls == k, lvl, 0).sum(-1) for k in (PERM, SEAS, ABOVE)}
    best = np.select([(weight[PERM] >= weight[SEAS]) & (weight[PERM] >= weight[ABOVE]), weight[ABOVE] >= weight[SEAS]], [PERM, ABOVE], SEAS)
    level = np.round(lvl.sum(-1) / np.maximum(4 - unseen, 1))
    out = np.where(level > 0, best + np.clip(level - 1, 0, 9), 0)
    out = np.where(unseen >= 3, 1, out)
    return out.astype(np.uint8)


def publish_year(year):
    idx, cls, typical, classed = year_classes(year)
    zmax = max(ZOOMS)
    (tx0, ty0, tx1, ty1), rows, cols = mercator_grid(zmax)
    ok_r = (rows >= 0) & (rows < HEIGHT)
    ok_c = (cols >= 0) & (cols < WIDTH)
    mos = np.zeros((len(rows), len(cols)), np.uint8)
    mos[np.ix_(np.nonzero(ok_r)[0], np.nonzero(ok_c)[0])] = idx[rows[ok_r]][:, cols[ok_c]]
    pal, alpha = year_palette()
    count = 0
    z, ox, oy = zmax, tx0, ty0
    while True:
        if z in ZOOMS:
            for j in range(mos.shape[0] // 256):
                for i in range(mos.shape[1] // 256):
                    t = mos[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256]
                    if not t.any():
                        continue
                    img = Image.fromarray(np.ascontiguousarray(t), "P")
                    img.putpalette(pal.flatten().tolist())
                    p = OUT_YEARS / str(year) / str(z) / str(ox + i) / f"{oy + j}.png"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    img.save(p, transparency=alpha.tobytes(), optimize=False, compress_level=6)
                    count += 1
        if z == min(ZOOMS):
            break
        px0, py0 = ox % 2, oy % 2
        padded = np.zeros((mos.shape[0] + 256 * (py0 + (oy + mos.shape[0] // 256) % 2), mos.shape[1] + 256 * (px0 + (ox + mos.shape[1] // 256) % 2)), np.uint8)
        padded[py0 * 256:py0 * 256 + mos.shape[0], px0 * 256:px0 * 256 + mos.shape[1]] = mos
        mos = reduce_year(padded)
        ox, oy, z = (ox - px0) // 2, (oy - py0) // 2, z - 1

    # Per district: permanent, seasonal and above-usual water (cell area x typical share), the land classed, all land
    lat = TRANSFORM.f + (np.arange(HEIGHT) + 0.5) * TRANSFORM.e
    area = np.broadcast_to((math.radians(CELL) * 6371.0088) ** 2 * np.cos(np.radians(lat))[:, None], (HEIGHT, WIDTH))
    dist = _districts().ravel()
    nd = int(dist.max()) + 1
    water = area * typical / 100
    sums = [np.bincount(dist, weights=np.where(cls == k, water, 0).ravel(), minlength=nd) for k in (PERM, SEAS, ABOVE)]
    sums.append(np.bincount(dist, weights=np.where(classed, area, 0).ravel(), minlength=nd))
    sums.append(np.bincount(dist, weights=area.ravel(), minlength=nd))
    return year, count, np.stack(sums)


def stage_yearly():
    shutil.rmtree(OUT_YEARS, ignore_errors=True)
    years = list(range(FIRST[0], LAST[0] + 1))
    print(f"yearly: {len(years)} years", flush=True)
    t0 = time.time()
    per_year, tiles = {}, 0
    with ProcessPoolExecutor(3) as ex:
        for year, count, sums in ex.map(publish_year, years):
            per_year[year] = sums
            tiles += count
            if year % 5 == 0 or year == years[-1]:
                print(f"  {year}: {tiles:,} tiles so far  {time.time() - t0:.0f}s", flush=True)
    fc = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    r = lambda v: round(float(v), 1)

    def series(ids):
        out = []
        for y in years:
            a = per_year[y][:, ids].sum(1)
            out.append([r(a[0]), r(a[1]), r(a[2]), round(float(a[3] / a[4] * 100), 1) if a[4] else 0])
        return out

    provinces = sorted({f["properties"]["province"] for f in fc})
    out = {
        "source": "JRC Monthly Water History v1.4 (Landsat, 30 m), summed to 0.0025 degree cells in Earth Engine",
        "wet_min": WET_MIN, "permanent_share": PERMANENT_SHARE, "seen_min_months": YEAR_SEEN_MIN, "unusual_points": YEAR_UNUSUAL_POINTS, "unusual_months": YEAR_UNUSUAL_MONTHS,
        "fields": ["permanent_km2", "seasonal_km2", "above_usual_km2", "classed_pct"],
        "years": years,
        "national": series([f["properties"]["id"] for f in fc]),
        "provinces": {p: series([f["properties"]["id"] for f in fc if f["properties"]["province"] == p]) for p in provinces},
        "districts": {str(f["properties"]["id"]): series([f["properties"]["id"]]) for f in fc},
    }
    path = DATA_OUT / "timeline_years.json"
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    size = sum(f.stat().st_size for f in OUT_YEARS.rglob("*.png"))
    print(f"{path.name}: {path.stat().st_size / 1e6:.2f} MB; {tiles:,} tiles, {size / 1e6:.0f} MB; {time.time() - t0:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["clean", "normal", "publish", "yearly"], action="append")
    args = ap.parse_args()
    stages = args.stage or ["clean", "normal", "publish", "yearly"]
    for s in ("clean", "normal", "publish", "yearly"):
        if s in stages:
            globals()[f"stage_{s}"]()


if __name__ == "__main__":
    main()
