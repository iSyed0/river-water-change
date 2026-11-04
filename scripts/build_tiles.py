"""Render JRC surface-water GeoTIFFs into XYZ (Web Mercator) PNG tiles for the portal.

Three stages:
  1. Max zoom (~30 m) is sampled straight from the source mosaic, clipped to the official national outline.
  2. A "raw" pyramid (build/raw) collapses 2x2 blocks of child tiles into parents.
     Raw tiles are 8-bit palette PNGs whose pixel index is the JRC value, so parents
     rebuild losslessly. A coverage pyramid tracks the share of each pixel that is water.
  3. Display tiles (portal/tiles) below max zoom are RGBA: colour from the reduced value,
     opacity from coverage. Thin rivers stay visible while partial cover looks lighter
     than open water. Max-zoom display tiles keep the palette encoding, so the
     browser can read a JRC value back from a pixel colour.

    python scripts/build_tiles.py                  # all layers, z0-12
    python scripts/build_tiles.py --layers extent  # one layer
"""
import argparse
import atexit
import json
import math
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor

os.environ.setdefault("GDAL_CACHEMAX", "96")  # MB per worker; source strips are rarely re-read

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from rasterio import features
from rasterio.windows import Window
from shapely.ops import transform as shp_transform

from common import (DATA_OUT, GRANULE, LAT0, LAYERS, LON0, MAX_ZOOM, MOSAIC,
                    NODATA, RES, ROOT, TILE, TILES, granule_path, national_outline)

EARTH_R = 6378137.0
ORIGIN = math.pi * EARTH_R
RAW = ROOT / "build" / "raw"
COVER = "_coverage"
MIN_ALPHA = 0.3  # opacity of a pixel with a trace of water; full cover is opaque


def lut_visible(layer):
    v = np.zeros(256, bool)
    v[list(LAYERS[layer]["colors"])] = True
    return v


def palette(layer):
    pal = np.zeros((256, 3), np.uint8)
    for k, rgb in LAYERS[layer]["colors"].items():
        pal[k] = rgb
    alpha = bytes(255 if k in LAYERS[layer]["colors"] else 0 for k in range(256))
    return pal, alpha


VISIBLE = {k: lut_visible(k) for k in LAYERS}
PALETTES = {k: palette(k) for k in LAYERS}
ALPHA_LUT = np.round(255 * (MIN_ALPHA + (1 - MIN_ALPHA) * np.sqrt(np.arange(256) / 255))).astype(np.uint8)


def tile_path(root, layer, z, x, y):
    return root / layer / str(z) / str(x) / f"{y}.png"


def save_png(img, root, layer, z, x, y, **kw):
    p = tile_path(root, layer, z, x, y)
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p, compress_level=6, **kw)


def save_palette(root, layer, z, x, y, arr):
    img = Image.fromarray(arr, "P")
    pal, alpha = PALETTES[layer]
    img.putpalette(pal.flatten().tolist())
    save_png(img, root, layer, z, x, y, transparency=alpha)


def load_tile(layer, z, x, y):
    p = tile_path(RAW, layer, z, x, y)
    if not p.exists():
        return None
    return np.asarray(Image.open(p))


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def to_mercator(geom):
    def f(lon, lat, z=None):
        lon, lat = np.asarray(lon), np.asarray(lat)
        return EARTH_R * np.radians(lon), EARTH_R * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    return shp_transform(f, geom)


# ---------------------------------------------------------------- worker state
_W = {}


def _init(clip_geom_merc, layers):
    _W["clip"] = clip_geom_merc
    _W["layers"] = layers
    _W["ds"] = {}
    atexit.register(lambda: [d.close() for d in _W["ds"].values()])


def _ds(layer, key):
    k = (layer, key)
    if k not in _W["ds"]:
        _W["ds"][k] = rasterio.open(granule_path(layer, key))
    return _W["ds"][k]


def render_row(z, y, xmin, xmax):
    """Sample one row of max-zoom tiles from the source mosaic."""
    n = 2 ** z * TILE
    width = (xmax - xmin + 1) * TILE
    py = y * TILE + np.arange(TILE) + 0.5
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * py / n))))
    px = xmin * TILE + np.arange(width) + 0.5
    lon = px / n * 360 - 180
    rows = np.floor((LAT0 - lat) / RES).astype(np.int64)
    cols = np.floor((lon - LON0) / RES).astype(np.int64)

    tile_m = 2 * ORIGIN / 2 ** z
    if _W["clip"] is not None:
        tf = Affine(tile_m / TILE, 0, -ORIGIN + xmin * tile_m, 0, -tile_m / TILE, ORIGIN - y * tile_m)
        mask = features.rasterize([(_W["clip"], 1)], out_shape=(TILE, width), transform=tf,
                                  fill=0, dtype="uint8").astype(bool)
    else:
        mask = np.ones((TILE, width), bool)
    mask &= ((rows >= 0) & (rows < MOSAIC))[:, None]
    mask &= ((cols >= 0) & (cols < MOSAIC))[None, :]
    if not mask.any():
        return 0
    need_rows, need_cols = mask.any(1), mask.any(0)

    def sample(layer):
        band = np.full((TILE, width), NODATA, np.uint8)
        for rb in (0, 1):
            rsel = need_rows & (rows // GRANULE == rb)
            if not rsel.any():
                continue
            lr = rows[rsel] - rb * GRANULE
            r0, r1 = lr.min(), lr.max()
            for cb in (0, 1):
                csel = need_cols & (cols // GRANULE == cb)
                if not csel.any():
                    continue
                lc = cols[csel] - cb * GRANULE
                c0, c1 = lc.min(), lc.max()
                ds = _ds(layer, (cb, rb))
                ur, inv = np.unique(lr, return_inverse=True)
                if r1 - r0 + 1 <= 3 * len(ur):
                    win = ds.read(1, window=Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1))[ur - r0]
                else:  # sparse rows (low max zoom): read only the rows that are sampled
                    win = np.stack([ds.read(1, window=Window(c0, r, c1 - c0 + 1, 1))[0] for r in ur])
                band[np.ix_(rsel, csel)] = win[np.ix_(inv, lc - c0)]
        band[~mask] = NODATA
        return band

    def tiles(band):
        for i, x in enumerate(range(xmin, xmax + 1)):
            yield x, np.ascontiguousarray(band[:, i * TILE:(i + 1) * TILE])

    extent = sample("extent")
    cover = np.where(extent == 1, 255, 0).astype(np.uint8)
    for x, t in tiles(cover):
        if t.any():
            save_png(Image.fromarray(t, "L"), RAW, COVER, z, x, y)

    written = 0
    for layer in _W["layers"]:
        band = extent if layer == "extent" else sample(layer)
        for x, t in tiles(band):
            if VISIBLE[layer][t].any():
                save_palette(RAW, layer, z, x, y, t)
                save_palette(TILES, layer, z, x, y, t)
                written += 1
    return written


def reduce_block(arr, layer):
    """Collapse a (512, 512) array to (256, 256) using the layer's reduce rule."""
    v = arr.reshape(TILE, 2, TILE, 2).transpose(0, 2, 1, 3).reshape(TILE, TILE, 4)
    if layer == COVER:
        return np.round(v.astype(np.uint16).sum(-1) / 4).astype(np.uint8)
    valid = VISIBLE[layer][v]
    count = valid.sum(-1)
    out = np.full((TILE, TILE), NODATA, np.uint8)
    has = count > 0
    rule = LAYERS[layer]["reduce"]
    if rule == "any":
        out[has] = np.where(valid, v, 0).max(-1)[has]
    elif rule == "mean":
        s = np.where(valid, v, 0).astype(np.uint16).sum(-1)
        out[has] = np.round(s[has] / count[has]).astype(np.uint8)
    elif rule == "mode":
        eq = (v[..., :, None] == v[..., None, :]) & valid[..., None, :]
        score = np.where(valid, eq.sum(-1), -1)
        pick = np.take_along_axis(v, score.argmax(-1)[..., None], -1)[..., 0]
        out[has] = pick[has]
    return out


def build_parent(args):
    layer, z, x, y = args
    fill = 0 if layer == COVER else NODATA
    big = np.full((2 * TILE, 2 * TILE), fill, np.uint8)
    for dx in (0, 1):
        for dy in (0, 1):
            t = load_tile(layer, z + 1, 2 * x + dx, 2 * y + dy)
            if t is not None:
                big[dy * TILE:(dy + 1) * TILE, dx * TILE:(dx + 1) * TILE] = t
    out = reduce_block(big, layer)
    if layer == COVER:
        if out.any():
            save_png(Image.fromarray(out, "L"), RAW, COVER, z, x, y)
            return 1
        return 0
    if VISIBLE[layer][out].any():
        save_palette(RAW, layer, z, x, y, out)
        return 1
    return 0


def build_display(args):
    """RGBA display tile: palette colour, opacity scaled by water coverage."""
    layer, z, x, y = args
    raw = load_tile(layer, z, x, y)
    cover = load_tile(COVER, z, x, y)
    if cover is None:
        cover = np.zeros_like(raw)
    pal, _ = PALETTES[layer]
    rgba = np.zeros((TILE, TILE, 4), np.uint8)
    rgba[..., :3] = pal[raw]
    rgba[..., 3] = np.where(VISIBLE[layer][raw], ALPHA_LUT[np.maximum(cover, 1)], 0)
    save_png(Image.fromarray(rgba, "RGBA"), TILES, layer, z, x, y)
    return 1


def existing_tiles(layer, z):
    base = RAW / layer / str(z)
    if not base.exists():
        return []
    out = []
    for xd in os.scandir(base):
        for f in os.scandir(xd.path):
            out.append((int(xd.name), int(f.name[:-4])))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layers", nargs="+", default=list(LAYERS), choices=list(LAYERS))
    ap.add_argument("--maxzoom", type=int, default=MAX_ZOOM)
    ap.add_argument("--minzoom", type=int, default=0)
    ap.add_argument("--workers", type=int, default=min(8, max(1, (os.cpu_count() or 2) - 2)))
    ap.add_argument("--no-clip", action="store_true", help="keep water outside Pakistan's boundary")
    args = ap.parse_args()

    for layer in args.layers + [COVER]:
        shutil.rmtree(RAW / layer, ignore_errors=True)
        shutil.rmtree(TILES / layer, ignore_errors=True)

    geom = national_outline()  # clip to the full official outline, disputed territory included
    lon_min, lat_min, lon_max, lat_max = geom.bounds
    clip = None if args.no_clip else to_mercator(geom)

    z = args.maxzoom
    x0, y0 = lonlat_to_tile(lon_min, lat_max, z)
    x1, y1 = lonlat_to_tile(lon_max, lat_min, z)
    t = time.time()
    print(f"z{z}: rows {y0}-{y1}, cols {x0}-{x1}, layers {args.layers}, {args.workers} workers")
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(clip, args.layers)) as ex:
        futs = [ex.submit(render_row, z, y, x0, x1) for y in range(y0, y1 + 1)]
        total = 0
        for i, f in enumerate(futs, 1):
            total += f.result()
            if i % 20 == 0 or i == len(futs):
                print(f"  row {i}/{len(futs)}  tiles {total}  {time.time() - t:.0f}s", flush=True)

        display = []
        for z in range(args.maxzoom - 1, args.minzoom - 1, -1):
            jobs = []
            for layer in args.layers + [COVER]:
                parents = {(cx // 2, cy // 2) for cx, cy in existing_tiles(layer, z + 1)}
                jobs += [(layer, z, px, py) for px, py in parents]
            n = sum(ex.map(build_parent, jobs, chunksize=64))
            display += [(layer, z, px, py) for layer in args.layers for px, py in existing_tiles(layer, z)]
            print(f"z{z}: {n} raw tiles  {time.time() - t:.0f}s", flush=True)

        n = sum(ex.map(build_display, display, chunksize=64))
        print(f"display: {n} tiles  {time.time() - t:.0f}s", flush=True)

    DATA_OUT.mkdir(parents=True, exist_ok=True)
    pal = {k: {str(v): list(rgb) for v, rgb in LAYERS[k]["colors"].items()} for k in LAYERS}
    (DATA_OUT / "palettes.json").write_text(json.dumps(pal, separators=(",", ":")), encoding="utf-8")
    (TILES / "manifest.json").write_text(json.dumps({
        "minzoom": args.minzoom, "maxzoom": args.maxzoom,
        "bounds": [lon_min, lat_min, lon_max, lat_max], "layers": list(LAYERS),
    }, indent=2), encoding="utf-8")
    print("done", f"{time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
