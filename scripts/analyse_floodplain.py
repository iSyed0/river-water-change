"""Erosion and exposure along the Indus floodplain, per 20 km reach and per district.

Water history: JRC Global Surface Water, Landsat 1984-2024. The transitions layer alone overstates new
               water: the first year of the record is thinly observed, so its seasonal water is undercounted
               (a quarter of 2024's on the Sindh plains). Change here therefore has to be sustained: the JRC
               occurrence-change layer, which compares the same calendar months in 1984-99 and 2000-24, must
               have moved by at least SUSTAINED points. Pixels that fail show as "no lasting change".
Land cover:    ESA WorldCover 2021, 10 m, read in 0.25 degree chunks and cached.
Infrastructure: OpenStreetMap roads, railways, bridges, schools, health facilities.
Buildings:     the NDMA tile server when it can be reached, otherwise OSM footprints.

Zones used in every figure:
  floodplain  see prepare_floodplain.py
  eroded      land that became water with a sustained change (transitions 2, 5 and change >= +SUSTAINED)
  regained    water that became land with a sustained change (transitions 3, 6 and change <= -SUSTAINED)
  near bank   land within NEAR_M of the river: water wet at least RIVER_OCC of the time in 2000-24, without
              features narrower than ~150 m, so canals and ponds do not count

Water "then" and "now" are 1984-99 and 2000-24, estimated from overall occurrence and the change layer
(occurrence -/+ half the change) and counted as wet at WET_OCC or more.

Outputs:
  portal/data/floodplain.json        reaches, districts, totals, camera track
  portal/data/fp_sections.json       one record per 5 km cross-section
  portal/data/floodplain.geojson     outline
  portal/data/fp_transects.geojson   cross-section lines
  portal/data/fp_infra.geojson       roads, rail, bridges, schools, health facilities
  erosion/erosion_*_v1_5_2024.tif    source of the "erosion" map layer (build_tiles.py --layers erosion)
  build/floodplain/erosion_cells.npz the same figures per grid cell, read by prepare_erosion_dots.py

    python scripts/analyse_floodplain.py     (after prepare_floodplain.py, prepare_places.py)
"""
import gzip
import json
import os
import re
import time
import urllib.request
import zipfile
import zlib
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from floodplain_common import CACHE, mosaic, sample_dem, sample_mosaic, to_rc, worldcover

import numpy as np
import pyogrio
import rasterio
import shapely
from affine import Affine
from rasterio import features
from rasterio.windows import Window
from scipy.ndimage import binary_opening, distance_transform_edt, gaussian_filter1d
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.ops import unary_union

from common import DATA_OUT, GRANULE, GRANULES, LAT0, LON0, RES, ROOT, national_outline
from prepare_floodplain import main_belt

REACH_KM = 20
NEAR_M = 1000
SUSTAINED = 25          # occurrence points the change layer must have moved
WET_OCC = 25            # a pixel is water in an epoch at this occurrence or more
RIVER_OCC = 50          # regularly wet: what "near bank" measures from
CHUNK = 1000            # JRC pixels, 0.25 degrees
CELL = 100              # JRC pixels, 0.025 degrees: the grid behind the erosion dots; divides CHUNK
PAD = 45                # pixels read around a chunk so distances to water cross chunk edges
SEC_MARGIN_M = 2000     # cross-section charts show this much ground beyond the floodplain
BUILDING_ZOOM = 15      # complete footprints; lower zooms drop dense buildings
TILE_SERVER = "http://172.18.1.142:8081"

OSM_ZIP = ROOT / "osm_src" / "pakistan-latest-free.shp.zip"
OSM_DIR = ROOT / "osm_src" / "infra"
OSM_LAYERS = ["roads", "railways", "pois", "pois_a", "traffic", "traffic_a", "buildings_a"]

MAJOR = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link"}
NOT_ROADS = {"footway", "path", "steps", "cycleway", "bridleway", "pedestrian", "unknown"}
EDU = {"school", "college", "university", "kindergarten"}
HEALTH = {"hospital", "clinic", "doctors"}
DAMS = {"dam", "weir"}

# WorldCover classes -> groups
LC_GROUPS = ["crop", "built", "veg", "bare", "water"]
LC_MAP = np.zeros(256, np.uint8)  # 0 = no data
for cls, g in {40: 1, 50: 2, 10: 3, 20: 3, 30: 3, 90: 3, 95: 3, 100: 3, 60: 4, 70: 4, 80: 5}.items():
    LC_MAP[cls] = g

SEG_MAJOR, SEG_MINOR, SEG_RAIL = 0, 1, 2
PT_BRIDGE, PT_EDU, PT_HEALTH, PT_PLACE, PT_BLDG, PT_DAM = 0, 1, 2, 3, 4, 5
ZONES = ["fp", "ero", "near"]

METRICS = (["fp", "water_then", "water_now", "perm_now", "seas_now", "ero_perm", "ero_seas", "acc_perm", "acc_seas", "passing"]
           + [f"{g}_{z}" for z in ZONES for g in LC_GROUPS]
           + [f"{k}_{z}" for z in ZONES for k in ("major", "minor", "rail")]
           + [f"{k}_{z}" for z in ZONES for k in ("bridge", "edu", "health", "place", "bldg", "dam")])
MI = {k: i for i, k in enumerate(METRICS)}



def epochs(occ, chg):
    """Change in occurrence points, and estimated occurrence in 1984-99 and 2000-24, from the JRC layers."""
    d = np.where(chg <= 200, chg.astype(np.int16) - 100, 0)
    o = np.where(occ <= 100, occ, 0).astype(np.int16)
    return d, o - d / 2, o + d / 2


def erosion_classes(trans, d):
    """1, 2 land became permanent, seasonal water; 3, 4 permanent, seasonal water became land;
    5 water in both years; 6 wet at times with no lasting change; 255 dry."""
    out = np.full(trans.shape, 255, np.uint8)
    out[np.isin(trans, (1, 4, 7, 8))] = 5
    out[np.isin(trans, (2, 3, 5, 6, 9, 10))] = 6
    out[(trans == 2) & (d >= SUSTAINED)] = 1
    out[(trans == 5) & (d >= SUSTAINED)] = 2
    out[(trans == 3) & (d <= -SUSTAINED)] = 3
    out[(trans == 6) & (d <= -SUSTAINED)] = 4
    return out


_W = {}


def _init(fp_wkb, dist_wkb, dist_ids, route_xy, unit_wkb):
    _W["fp"] = shapely.from_wkb(fp_wkb)
    _W["districts"] = [(shapely.from_wkb(w), i) for w, i in zip(dist_wkb, dist_ids)]
    _W["tree"] = cKDTree(route_xy)
    _W["units"] = [shapely.from_wkb(w) for w in unit_wkb]
    _W["unit_tree"] = shapely.STRtree(_W["units"]) if _W["units"] else None


def lc_counts(r0, c0):
    """Share of each land-cover group per JRC pixel of a chunk: uint8 array (group, row, col), 0-9."""
    path = CACHE / "wc" / f"{r0}_{c0}.npz"
    if path.exists():
        return np.load(path)["c"]
    wc = LC_MAP[worldcover(r0, r0 + CHUNK, c0, c0 + CHUNK)]
    blocks = wc.reshape(CHUNK, 3, CHUNK, 3)
    out = np.stack([(blocks == g + 1).sum((1, 3)) for g in range(len(LC_GROUPS))]).astype(np.uint8)
    tmp = path.with_suffix(f".{os.getpid()}.npz")
    np.savez_compressed(tmp, c=out)
    os.replace(tmp, path)
    return out


def process_chunk(args):
    r0, c0, segs, pts = args
    tf = Affine(RES, 0, LON0 + c0 * RES, 0, -RES, LAT0 - r0 * RES)
    fp = features.rasterize([(_W["fp"], 1)], out_shape=(CHUNK, CHUNK), transform=tf, fill=0, dtype="uint8").astype(bool)
    lc = lc_counts(r0, c0)      # also caches ground around the floodplain for the cross-section charts
    if not fp.any():
        return {}, {}, {}, None, r0, c0

    lat = LAT0 - (r0 + np.arange(CHUNK) + 0.5) * RES
    cell_km2 = (RES * 111.320 * np.cos(np.radians(lat))) * (RES * 110.574)
    px_m = np.array([RES * 110574, RES * 111320 * np.cos(np.radians(lat.mean()))])

    win = (r0 - PAD, r0 + CHUNK + PAD, c0 - PAD, c0 + CHUNK + PAD)
    trans = mosaic("transitions", *win)
    d, o_then, o_now = epochs(mosaic("occurrence", *win), mosaic("change", *win))
    river = binary_opening(o_now >= RIVER_OCC, iterations=3)
    near = (distance_transform_edt(~river, sampling=px_m) <= NEAR_M) & ~river if river.any() else np.zeros_like(river)
    crop = lambda a: a[PAD:-PAD, PAD:-PAD]
    trans, d, o_then, o_now, near = crop(trans), crop(d), crop(o_then), crop(o_now), crop(near) & fp
    classes = erosion_classes(trans, d)
    ero = np.isin(classes, (1, 2)) & fp

    # Each pixel: nearest route km -> reach; district from the boundary file
    dist = features.rasterize([(g, i + 1) for g, i in _W["districts"] if g.intersects(box(*rasterio.transform.array_bounds(CHUNK, CHUNK, tf)))] or [(box(0, 0, 0, 0), 0)],
                              out_shape=(CHUNK, CHUNK), transform=tf, fill=0, dtype="int32")

    def assign(lon, lat_):
        _, km = _W["tree"].query(np.column_stack([np.asarray(lon) * 96.4, np.asarray(lat_) * 110.574]))
        return km // REACH_KM

    rows, cols = np.nonzero(fp)
    area = cell_km2[rows]
    t, c = trans[rows, cols], classes[rows, cols]
    zones = {"fp": np.ones(len(rows), bool), "ero": ero[rows, cols], "near": near[rows, cols]}

    def tabulate(pixel_keys, point_key):
        """All metrics grouped by a key: pixel_keys for the floodplain pixels, point_key(lon, lat, r, c) for features."""
        uk, inv = np.unique(pixel_keys, return_inverse=True)
        table = np.zeros((len(uk), len(METRICS)))

        def add(name, sel):
            table[:, MI[name]] = np.bincount(inv, weights=area * sel, minlength=len(uk))

        add("fp", np.ones(len(rows)))
        add("water_then", o_then[rows, cols] >= WET_OCC)
        add("water_now", o_now[rows, cols] >= WET_OCC)
        add("perm_now", np.isin(t, (1, 2, 7)))
        add("seas_now", np.isin(t, (4, 5, 8)))
        add("ero_perm", c == 1)
        add("ero_seas", c == 2)
        add("acc_perm", c == 3)
        add("acc_seas", c == 4)
        add("passing", c == 6)

        for g, name in enumerate(LC_GROUPS):
            share = lc[g][rows, cols] / 9
            for z, sel in zones.items():
                table[:, MI[f"{name}_{z}"]] = np.bincount(inv, weights=area * share * sel, minlength=len(uk))

        index = {int(k): i for i, k in enumerate(uk)}

        def lookup(lon, lat_):
            """Zone flags and table row for points; -1 where a point is outside the floodplain."""
            pr, pc = to_rc(lon, lat_)
            pr, pc = pr - r0, pc - c0
            ok = (pr >= 0) & (pr < CHUNK) & (pc >= 0) & (pc < CHUNK)
            pr, pc = np.clip(pr, 0, CHUNK - 1), np.clip(pc, 0, CHUNK - 1)
            ok &= fp[pr, pc]
            slot = np.array([index.get(int(v), -1) for v in point_key(lon, lat_, pr, pc)], int)
            slot[~ok] = -1
            return slot, ero[pr, pc], near[pr, pc]

        if len(segs):
            slot, e, n = lookup(segs[:, 0], segs[:, 1])
            for cls_, name in ((SEG_MAJOR, "major"), (SEG_MINOR, "minor"), (SEG_RAIL, "rail")):
                s = segs[:, 3] == cls_
                for z, flag in (("fp", np.ones(len(slot), bool)), ("ero", e), ("near", n)):
                    sel = s & flag & (slot >= 0)
                    np.add.at(table[:, MI[f"{name}_{z}"]], slot[sel], segs[sel, 2])
        if len(pts):
            slot, e, n = lookup(pts[:, 0], pts[:, 1])
            for cls_, name in ((PT_BRIDGE, "bridge"), (PT_EDU, "edu"), (PT_HEALTH, "health"), (PT_PLACE, "place"), (PT_BLDG, "bldg"), (PT_DAM, "dam")):
                s = pts[:, 2] == cls_
                for z, flag in (("fp", np.ones(len(slot), bool)), ("ero", e), ("near", n)):
                    sel = s & flag & (slot >= 0)
                    np.add.at(table[:, MI[f"{name}_{z}"]], slot[sel], 1)
        return {int(k): table[i] for i, k in enumerate(uk)}

    reach = assign(LON0 + (c0 + cols + 0.5) * RES, LAT0 - (r0 + rows + 0.5) * RES)
    by_reach = tabulate(reach.astype(np.int64) * 100000 + dist[rows, cols],
                        lambda lon, lat_, pr, pc: assign(lon, lat_).astype(np.int64) * 100000 + dist[pr, pc])

    # The same figures per union council or tehsil, for the map popup
    by_unit = {}
    if _W["unit_tree"] is not None:
        hits = _W["unit_tree"].query(box(*rasterio.transform.array_bounds(CHUNK, CHUNK, tf)))
        if len(hits):
            units = features.rasterize([(_W["units"][i], int(i) + 1) for i in hits], out_shape=(CHUNK, CHUNK), transform=tf, fill=0, dtype="int32")
            by_unit = tabulate(units[rows, cols].astype(np.int64), lambda lon, lat_, pr, pc: units[pr, pc])
            by_unit.pop(0, None)

    # And per grid cell with eroded ground, for the erosion dots
    cell = lambda r, c: (r0 + r) // CELL * 100000 + (c0 + c) // CELL
    by_cell = tabulate(cell(rows, cols).astype(np.int64), lambda lon, lat_, pr, pc: cell(pr, pc))
    by_cell = {k: v for k, v in by_cell.items() if v[MI["ero_perm"]] + v[MI["ero_seas"]] > 0}

    raster = np.where(fp, classes, 255).astype(np.uint8)
    return by_reach, by_unit, by_cell, zlib.compress(raster.tobytes(), 6), r0, c0


def extract_osm():
    OSM_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OSM_ZIP) as z:
        names = {n for n in z.namelist()}
        for layer in OSM_LAYERS:
            stem = f"gis_osm_{layer.replace('_a', '')}_a_free_1" if layer.endswith("_a") else f"gis_osm_{layer}_free_1"
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                if stem + ext in names and not (OSM_DIR / (stem + ext)).exists():
                    z.extract(stem + ext, OSM_DIR)


def osm_read(layer, bbox, columns):
    stem = f"gis_osm_{layer.replace('_a', '')}_a_free_1" if layer.endswith("_a") else f"gis_osm_{layer}_free_1"
    return pyogrio.read_dataframe(OSM_DIR / f"{stem}.shp", bbox=bbox, columns=columns)


# ---------------------------------------------------------------- building tiles
def _varint(b, i):
    v, s = 0, 0
    while True:
        c = b[i]
        i += 1
        v |= (c & 0x7F) << s
        if c < 0x80:
            return v, i
        s += 7


def _fields(b):
    """Protobuf fields of a message as (number, wire type, value)."""
    i = 0
    while i < len(b):
        tag, i = _varint(b, i)
        no, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            n, i = _varint(b, i)
            v = b[i:i + n]
            i += n
        elif wt == 1:
            v = b[i:i + 8]
            i += 8
        elif wt == 5:
            v = b[i:i + 4]
            i += 4
        else:
            raise ValueError(f"wire type {wt}")
        yield no, wt, v


def tile_footprints(data, z, x, y):
    """Decode a vector tile: [(footprint id, lon, lat of first ring centre)]."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    out = []
    for no, _, layer in _fields(data):
        if no != 3:
            continue
        extent, keys, values, feats = 4096, [], [], []
        for n, _, v in _fields(layer):
            if n == 5:
                extent = v
            elif n == 3:
                keys.append(v.decode())
            elif n == 4:
                values.append(next((u.decode() for f, _, u in _fields(v) if f == 1), None))
            elif n == 2:
                feats.append(v)
        id_key = keys.index("id") if "id" in keys else -1
        for f in feats:
            tags, geom = [], b""
            for n, _, v in _fields(f):
                if n == 2:
                    i = 0
                    while i < len(v):
                        t, i = _varint(v, i)
                        tags.append(t)
                elif n == 4:
                    geom = v
            fid = None
            for k, val in zip(tags[::2], tags[1::2]):
                if k == id_key:
                    fid = values[val]
            i, px, py, xs, ys = 0, 0, 0, [], []
            while i < len(geom):         # first ring only
                cmd, i = _varint(geom, i)
                op, count = cmd & 7, cmd >> 3
                if op == 7:
                    break
                for _ in range(count):
                    dx, i = _varint(geom, i)
                    dy, i = _varint(geom, i)
                    px += (dx >> 1) ^ -(dx & 1)
                    py += (dy >> 1) ^ -(dy & 1)
                    xs.append(px)
                    ys.append(py)
            if not xs:
                continue
            n = 2 ** z
            lon = (x + sum(xs) / len(xs) / extent) / n * 360 - 180
            yy = (y + sum(ys) / len(ys) / extent) / n
            lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * yy))))
            out.append((fid, lon, float(lat)))
    return out


def building_points(fp):
    """(lon, lat) of building footprints in the floodplain, and where they came from."""
    cache = CACHE / "buildings.npz"
    if cache.exists():
        d = np.load(cache)
        return d["xy"], str(d["source"])
    try:
        catalog = json.load(urllib.request.urlopen(f"{TILE_SERVER}/data.json", timeout=10))
    except OSError as err:
        print(f"  tile server unreachable ({err}); using OSM footprints", flush=True)
        df = osm_read("buildings_a", fp.bounds, ["osm_id"])
        c = shapely.centroid(df.geometry.values)
        shapely.prepare(fp)
        c = c[shapely.contains(fp, c)]
        # Not cached: the next run should try the fuller NDMA data again
        return shapely.get_coordinates(c), "osm"

    z = BUILDING_ZOOM
    n = 2 ** z
    w, s, e, nn = fp.bounds
    tx = lambda lon: int((lon + 180) / 360 * n)
    ty = lambda lat: int((1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n)
    shapely.prepare(fp)
    jobs = []
    for x in range(tx(w), tx(e) + 1):
        col = shapely.box(x / n * 360 - 180, s, (x + 1) / n * 360 - 180, nn)
        if not shapely.intersects(fp, col):
            continue
        for y in range(ty(nn), ty(s) + 1):
            lon0, lon1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
            lat1 = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * y / n))))
            lat0 = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (y + 1) / n))))
            if not shapely.intersects(fp, shapely.box(lon0, lat0, lon1, lat1)):
                continue
            for t in catalog:
                bw, bs, be, bn = t["bounds"]
                if t["format"] == "pbf" and bw <= lon1 and be >= lon0 and bs <= lat1 and bn >= lat0:
                    jobs.append((t["id"], x, y))
    print(f"  building tiles to read: {len(jobs):,}", flush=True)

    def fetch(job):
        tid, x, y = job
        req = urllib.request.Request(f"{TILE_SERVER}/data/{tid}/{z}/{x}/{y}.pbf", headers={"Accept-Encoding": "gzip"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return tile_footprints(r.read(), z, x, y) if r.status == 200 else []
            except OSError:
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"tile failed: {tid}/{z}/{x}/{y}")

    seen = {}
    with ThreadPoolExecutor(12) as ex:
        for i, feats in enumerate(ex.map(fetch, jobs), 1):
            for fid, lon, lat in feats:
                seen.setdefault(fid if fid is not None else (lon, lat), (lon, lat))
            if i % 5000 == 0:
                print(f"    {i:,}/{len(jobs):,}  {len(seen):,} footprints", flush=True)
    xy = np.array(list(seen.values()), float)
    pts = shapely.points(xy)
    xy = xy[shapely.contains(fp, pts)]
    np.savez(cache, xy=xy, source="ndma")
    return xy, "ndma"


# ---------------------------------------------------------------- inputs
def outline(sec):
    """Floodplain polygon from the section edges, plus each section's edge points."""
    kx = 111.320 * np.cos(np.radians(sec["lat"]))
    l_lon = sec["lon"] - sec["ty"] * sec["left"] / 1000 / kx
    l_lat = sec["lat"] + sec["tx"] * sec["left"] / 1000 / 110.574
    r_lon = sec["lon"] + sec["ty"] * sec["right"] / 1000 / kx
    r_lat = sec["lat"] - sec["tx"] * sec["right"] / 1000 / 110.574
    quads = []
    for i in range(len(l_lon) - 1):
        q = Polygon([(l_lon[i], l_lat[i]), (r_lon[i], r_lat[i]), (r_lon[i + 1], r_lat[i + 1]), (l_lon[i + 1], l_lat[i + 1])])
        quads.append(q if q.is_valid else q.buffer(0))
    poly = unary_union(quads).buffer(0.002).buffer(-0.002)
    return poly.intersection(national_outline()), (l_lon, l_lat), (r_lon, r_lat)


def sample_segments(geoms, cls_):
    """Mid-point (lon, lat), length (km) and class of ~30 m pieces of lines."""
    if not len(geoms):
        return np.zeros((0, 4))
    coords, idx = shapely.get_coordinates(shapely.segmentize(geoms, 0.0003), return_index=True)
    same = idx[1:] == idx[:-1]
    a, b = coords[:-1][same], coords[1:][same]
    mid = (a + b) / 2
    km = np.hypot((b[:, 0] - a[:, 0]) * 111.320 * np.cos(np.radians(mid[:, 1])), (b[:, 1] - a[:, 1]) * 110.574)
    c = cls_[idx[:-1][same]] if isinstance(cls_, np.ndarray) else np.full(len(km), cls_)
    return np.column_stack([mid, km, c])


def line_features(gdf, fp_near, kind_of):
    out = []
    keep = gdf[shapely.intersects(fp_near, gdf.geometry.values)]
    clipped = shapely.intersection(keep.geometry.values, fp_near)
    simp = shapely.simplify(clipped, 0.0002)
    for g, (_, r) in zip(simp, keep.iterrows()):
        if g.is_empty or g.geom_type not in ("LineString", "MultiLineString"):
            continue
        name = latin(r.get("name")) or latin(r.get("ref"))
        out.append({"type": "Feature", "geometry": rounded(mapping(g)), "properties": {"kind": kind_of(r), "name": name}})
    return out


def latin(name):
    """A label the map can show next to the rest: OSM names in Urdu script only are skipped."""
    return name if isinstance(name, str) and re.search(r"[A-Za-z]", name) else ""


def rounded(g, nd=5):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def dumps(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  {path.name}: {path.stat().st_size / 1e6:.2f} MB", flush=True)


def rle(a):
    change = np.flatnonzero(np.diff(a)) + 1
    starts = np.concatenate(([0], change))
    lengths = np.diff(np.concatenate((starts, [len(a)])))
    out = []
    for s, n in zip(starts, lengths):
        out += [int(n), int(a[s])]
    return out


def main():
    t0 = time.time()
    (CACHE / "wc").mkdir(parents=True, exist_ok=True)
    sec = dict(np.load(CACHE / "sections.npz"))
    route = json.loads((DATA_OUT / "indus_route.json").read_text(encoding="utf-8"))
    R = np.array(route["coords"], float)
    fp, L, Rr = outline(sec)
    print(f"floodplain {shapely.area(fp):.2f} sq deg; {shapely.get_num_coordinates(fp):,} vertices", flush=True)
    fp_near = fp.buffer(0.015)  # ~1.5 km of context around it on the map

    print("Reading OpenStreetMap", flush=True)
    extract_osm()
    bbox = fp_near.bounds
    shapely.prepare(fp_near)
    roads = osm_read("roads", bbox, ["fclass", "name", "ref", "bridge"])
    roads = roads[~roads.fclass.isin(NOT_ROADS)]
    roads = roads[shapely.intersects(fp_near, roads.geometry.values)].reset_index(drop=True)
    rail = osm_read("railways", bbox, ["fclass", "name", "bridge"])
    rail = rail[rail.fclass.isin(["rail", "narrow_gauge"])]
    rail = rail[shapely.intersects(fp_near, rail.geometry.values)].reset_index(drop=True)
    print(f"  roads {len(roads):,}, rail {len(rail):,}", flush=True)

    def points_of(layer):
        df = osm_read(layer, bbox, ["fclass", "name"])
        c = shapely.centroid(df.geometry.values)
        keep = shapely.intersects(fp_near, c)
        return df[keep].assign(geometry=c[keep])

    pois = [points_of("pois"), points_of("pois_a")]
    traffic = [points_of("traffic"), points_of("traffic_a")]
    import pandas as pd
    pois, traffic = pd.concat(pois, ignore_index=True), pd.concat(traffic, ignore_index=True)
    edu, health = pois[pois.fclass.isin(EDU)], pois[pois.fclass.isin(HEALTH)]
    dams = traffic[traffic.fclass.isin(DAMS)]

    # A bridge is mapped as several pieces, one per carriageway: merge pieces within ~300 m
    def bridge_points(gdf):
        c = shapely.get_coordinates(shapely.line_interpolate_point(gdf.geometry.values, 0.5, normalized=True))
        return c
    road_b = roads[roads.bridge == "T"]
    rail_b = rail[rail.bridge == "T"]
    bxy = np.vstack([bridge_points(road_b), bridge_points(rail_b)]) if len(road_b) + len(rail_b) else np.zeros((0, 2))
    if len(bxy):
        _, first = np.unique(np.round(bxy / 0.003).astype(np.int64), axis=0, return_index=True)
        bxy = bxy[first]

    places = json.loads((DATA_OUT / "places.json").read_text(encoding="utf-8"))
    pl_xy = np.column_stack([np.array(places["x"]) / 1e5, np.array(places["y"]) / 1e5])
    pl_rank = np.array(places["k"])
    pl_names = places["n"]

    print("Buildings", flush=True)
    bld_xy, bld_source = building_points(fp)
    print(f"  {len(bld_xy):,} footprints from {bld_source}", flush=True)

    seg_cls = np.where(roads.fclass.isin(MAJOR), SEG_MAJOR, SEG_MINOR)
    segs = np.vstack([sample_segments(roads.geometry.values, seg_cls), sample_segments(rail.geometry.values, SEG_RAIL)])
    pts = np.vstack([
        np.column_stack([xy, np.full(len(xy), c)]) for xy, c in (
            (bxy, PT_BRIDGE), (shapely.get_coordinates(edu.geometry.values), PT_EDU),
            (shapely.get_coordinates(health.geometry.values), PT_HEALTH), (pl_xy, PT_PLACE), (bld_xy, PT_BLDG),
            (shapely.get_coordinates(dams.geometry.values), PT_DAM))])
    print(f"  {len(segs):,} line pieces, {len(pts):,} points; {time.time() - t0:.0f}s", flush=True)

    # ------------------------------------------------------------ chunks
    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    dgeoms = [shape(f["geometry"]) for f in districts]
    chunk_of = lambda arr: (np.floor((LAT0 - arr[:, 1]) / RES / CHUNK).astype(int), np.floor((arr[:, 0] - LON0) / RES / CHUNK).astype(int))
    fp_ctx = fp.buffer(SEC_MARGIN_M / 96000)
    shapely.prepare(fp_ctx)
    jobs = []
    sr, sc = chunk_of(segs)
    pr, pc = chunk_of(pts)
    w, s, e, n = fp_ctx.bounds
    for cr in range(int((LAT0 - n) / RES / CHUNK), int((LAT0 - s) / RES / CHUNK) + 1):
        for cc in range(int((w - LON0) / RES / CHUNK), int((e - LON0) / RES / CHUNK) + 1):
            r0, c0 = cr * CHUNK, cc * CHUNK
            cell = box(LON0 + c0 * RES, LAT0 - (r0 + CHUNK) * RES, LON0 + (c0 + CHUNK) * RES, LAT0 - r0 * RES)
            if shapely.intersects(fp_ctx, cell):
                jobs.append((r0, c0, segs[(sr == cr) & (sc == cc)], pts[(pr == cr) & (pc == cc)]))
    print(f"{len(jobs)} chunks", flush=True)

    out_dir = ROOT / "erosion"
    out_dir.mkdir(exist_ok=True)
    profile = dict(driver="GTiff", width=GRANULE, height=GRANULE, count=1, dtype="uint8", nodata=255, crs="EPSG:4326",
                   tiled=True, blockxsize=512, blockysize=512, compress="deflate", sparse_ok=True)
    sinks = {}
    for key, name in GRANULES.items():
        tf = Affine(RES, 0, LON0 + key[0] * GRANULE * RES, 0, -RES, LAT0 - key[1] * GRANULE * RES)
        sinks[key] = rasterio.open(out_dir / f"erosion_{name}_v1_5_2024.tif", "w", transform=tf, **profile)

    table = {}
    route_xy = np.column_stack([R[:, 0] * 96.4, R[:, 1] * 110.574])
    units_path = DATA_OUT / "admin_units.geojson"
    units_fc = json.loads(units_path.read_text(encoding="utf-8")) if units_path.exists() else {"features": [], "level": None}
    init = (shapely.to_wkb(fp), [shapely.to_wkb(g) for g in dgeoms], list(range(len(dgeoms))), route_xy,
            [shapely.to_wkb(shape(f["geometry"])) for f in units_fc["features"]])
    unit_table = {}
    cell_table = {}
    with ProcessPoolExecutor(max_workers=min(8, max(2, (os.cpu_count() or 4) - 2)), initializer=_init, initargs=init) as ex:
        for i, (rows, unit_rows, cell_rows, raster, r0, c0) in enumerate(ex.map(process_chunk, jobs, chunksize=1), 1):
            for k, v in rows.items():
                table[k] = table.get(k, 0) + v
            for k, v in unit_rows.items():
                unit_table[k] = unit_table.get(k, 0) + v
            cell_table.update(cell_rows)
            if raster is not None:
                key = (c0 // GRANULE, r0 // GRANULE)
                win = Window(c0 - key[0] * GRANULE, r0 - key[1] * GRANULE, CHUNK, CHUNK)
                sinks[key].write(np.frombuffer(zlib.decompress(raster), np.uint8).reshape(CHUNK, CHUNK), 1, window=win)
            if i % 20 == 0 or i == len(jobs):
                print(f"  chunk {i}/{len(jobs)}  {time.time() - t0:.0f}s", flush=True)
    for d in sinks.values():
        d.close()
    np.savez(CACHE / "erosion_cells.npz", keys=np.array(list(cell_table), np.int64), values=np.array(list(cell_table.values())), metrics=METRICS)

    # ------------------------------------------------------------ tables
    reach_total, area_total = {}, {}
    for k, v in table.items():
        reach, did = k // 100000, k % 100000
        reach_total[reach] = reach_total.get(reach, 0) + v
        area_total[did] = area_total.get(did, 0) + v
    total = sum(reach_total.values())
    assert abs(total[MI["fp"]] - sum(area_total.values())[MI["fp"]]) < 1e-6

    props = [f["properties"] for f in districts]
    reach_rows = []
    for r in sorted(reach_total):
        parts = sorted(((table[k][MI["fp"]], k % 100000) for k in table if k // 100000 == r), reverse=True)
        names = [props[d - 1]["id"] for a, d in parts if d > 0 and a >= 0.02 * reach_total[r][MI["fp"]]]
        reach_rows.append({"r": int(r), "km0": int(r) * REACH_KM, "km1": min((int(r) + 1) * REACH_KM, len(R) - 1), "d": names,
                           "v": [round(float(x), 3) for x in reach_total[r]]})
    area_rows = []
    for d in sorted(area_total):
        span = [k // 100000 for k in table if k % 100000 == d]
        p = props[d - 1] if d > 0 else {"id": -1, "name": "Outside district data", "province": ""}
        area_rows.append({"id": p["id"], "name": p["name"], "province": p["province"], "km0": int(min(span)) * REACH_KM,
                          "km1": (int(max(span)) + 1) * REACH_KM, "v": [round(float(x), 3) for x in area_total[d]]})
    area_rows.sort(key=lambda a: (a["km0"], a["km1"]))

    # ------------------------------------------------------------ cross-sections
    print("Cross-sections", flush=True)
    major = roads[roads.fclass.isin(MAJOR)]
    cross_geoms = list(major.geometry.values) + list(rail.geometry.values)
    cross_kind = ["road"] * len(major) + ["rail"] * len(rail)
    cross_name = [latin(a) or latin(b) for a, b in zip(major.name, major.ref)] + [latin(a) for a in rail.name]
    cross_tree = shapely.STRtree(cross_geoms)
    pl_tree = cKDTree(np.column_stack([pl_xy[:, 0] * 96.4, pl_xy[:, 1] * 110.574]))
    d_tree = shapely.STRtree(dgeoms)

    def district_at(lon, lat):
        for i in d_tree.query(Point(lon, lat)):
            if dgeoms[i].contains(Point(lon, lat)):
                return props[i]["id"]
        return -1

    sections, transects = [], []
    for i, km in enumerate(sec["kms"]):
        lf, rg = float(sec["left"][i]), float(sec["right"][i])
        kx = 111.320 * np.cos(np.radians(sec["lat"][i]))
        offs = np.arange(-lf - SEC_MARGIN_M, rg + SEC_MARGIN_M + 1, 30.0)
        lon = sec["lon"][i] + sec["ty"][i] * offs / 1000 / kx
        lat = sec["lat"][i] - sec["tx"][i] * offs / 1000 / 110.574
        tr = sample_mosaic("transitions", lon, lat)
        dd, o_then, o_now = epochs(sample_mosaic("occurrence", lon, lat), sample_mosaic("change", lon, lat))
        classes = erosion_classes(tr, dd)
        ec = classes.astype(int)
        ec[ec == 255] = 0
        then, now = o_then >= WET_OCC, o_now >= WET_OCC

        lc = np.zeros(len(offs), int)
        rr, cc = to_rc(lon, lat)
        for key in {(int(a), int(b)) for a, b in zip(rr // CHUNK, cc // CHUNK)}:
            p = CACHE / "wc" / f"{key[0] * CHUNK}_{key[1] * CHUNK}.npz"
            if not p.exists():
                continue
            arr = np.load(p)["c"]
            sel = (rr // CHUNK == key[0]) & (cc // CHUNK == key[1])
            shares = arr[:, rr[sel] - key[0] * CHUNK, cc[sel] - key[1] * CHUNK]
            lc[sel] = np.where(shares.sum(0) > 0, shares.argmax(0) + 1, 0)
        # 150 m majority so the land-cover strip stays readable
        blocks = np.array_split(np.arange(len(lc)), max(1, len(lc) // 5))
        lc_s = lc.copy()
        for b in blocks:
            vals = np.bincount(lc[b], minlength=6)
            lc_s[b] = vals.argmax()

        z = sample_dem(lon[::5], lat[::5])
        z = [None if np.isnan(v) else int(round(float(v))) for v in z]
        inside = (offs >= -lf) & (offs <= rg)
        # Outer edges of the main water belt in each epoch, inside the belt of the two together
        bt, bn = (np.nan, np.nan), (np.nan, np.nan)
        whole = main_belt((then | now) & inside)
        if whole:
            idx = np.arange(len(offs))
            within = (idx >= whole[0]) & (idx < whole[1])
            a, b = main_belt(then & within), main_belt(now & within)
            bt = (offs[a[0]], offs[a[1] - 1]) if a else bt
            bn = (offs[b[0]], offs[b[1] - 1]) if b else bn
        # Bank movement: positive = the bank moved outward, i.e. into the land
        sh_l = float(bt[0] - bn[0]) if not np.isnan(bt[0] + bn[0]) else None
        sh_r = float(bn[1] - bt[1]) if not np.isnan(bt[1] + bn[1]) else None
        ero = np.isin(classes, (1, 2))
        acc = np.isin(classes, (3, 4))
        centre = 0.5 * (bn[0] + bn[1]) if not np.isnan(bn[0] + bn[1]) else 0.0
        side = offs < centre

        line = LineString(np.column_stack([lon, lat])[[0, -1]])
        xs = []
        for j in cross_tree.query(line):
            hit = cross_geoms[j].intersection(line)
            if hit.is_empty:
                continue
            for pt in (hit.geoms if hasattr(hit, "geoms") else [hit]):
                if pt.geom_type == "Point":
                    xs.append([int(round(offs[0] + line.project(pt) / line.length * (offs[-1] - offs[0]))), cross_kind[j], cross_name[j]])
        xs.sort()
        near = pl_tree.query_ball_point(np.column_stack([lon[::17] * 96.4, lat[::17] * 110.574]), 0.35)
        ids = sorted({j for group in near for j in group}, key=lambda j: pl_rank[j])[:6]
        pl = []
        for j in ids:
            # position along the line
            d2 = (lon - pl_xy[j, 0]) ** 2 + (lat - pl_xy[j, 1]) ** 2
            pl.append([int(offs[int(d2.argmin())]), pl_names[j], int(pl_rank[j])])
        pl.sort()

        sections.append({
            "km": int(km), "x0": int(offs[0]), "cls": rle(ec), "wet": rle(then.astype(int) + 2 * now.astype(int)), "lc": rle(lc_s), "z": z,
            "fpl": round(lf), "fpr": round(rg),
            "tw": round(float(then[inside].sum()) * 0.03, 2), "nw": round(float(now[inside].sum()) * 0.03, 2),
            "bt": None if np.isnan(bt[0]) else [round(float(bt[0])), round(float(bt[1]))],
            "bn": None if np.isnan(bn[0]) else [round(float(bn[0])), round(float(bn[1]))],
            "shl": None if sh_l is None else round(sh_l), "shr": None if sh_r is None else round(sh_r),
            "el": round(float((ero & inside & side).sum()) * 0.03, 2), "er": round(float((ero & inside & ~side).sum()) * 0.03, 2),
            "al": round(float((acc & inside & side).sum()) * 0.03, 2), "ar": round(float((acc & inside & ~side).sum()) * 0.03, 2),
            "xs": xs, "pl": pl,
            "ld": district_at(sec["lon"][i] + sec["ty"][i] * (-lf / 2) / 1000 / kx, sec["lat"][i] - sec["tx"][i] * (-lf / 2) / 1000 / 110.574),
            "rd": district_at(sec["lon"][i] + sec["ty"][i] * (rg / 2) / 1000 / kx, sec["lat"][i] - sec["tx"][i] * (rg / 2) / 1000 / 110.574),
            "ch": None if np.isnan(sec["channel"][i]) else int(round(float(sec["channel"][i]))),
        })
        transects.append({"type": "Feature", "properties": {"km": int(km), "label": f"km {int(km):,}"},
                          "geometry": {"type": "LineString", "coordinates": [[round(float(L[0][i]), 5), round(float(L[1][i]), 5)],
                                                                             [round(float(Rr[0][i]), 5), round(float(Rr[1][i]), 5)]]}})
        if (i + 1) % 100 == 0:
            print(f"  section {i + 1}/{len(sec['kms'])}  {time.time() - t0:.0f}s", flush=True)

    # ------------------------------------------------------------ camera track
    n_km = len(R)
    offs_mid = np.interp(np.arange(n_km), sec["kms"], (sec["right"] - sec["left"]) / 2)
    width = np.interp(np.arange(n_km), sec["kms"], sec["right"] + sec["left"])
    tx = np.interp(np.arange(n_km), sec["kms"], sec["tx"])
    ty = np.interp(np.arange(n_km), sec["kms"], sec["ty"])
    S = np.column_stack([gaussian_filter1d(R[:, i], 4, mode="nearest") for i in (0, 1)])
    kx = 111.320 * np.cos(np.radians(S[:, 1]))
    mid = np.column_stack([S[:, 0] + ty * offs_mid / 1000 / kx, S[:, 1] - tx * offs_mid / 1000 / 110.574])
    cam = np.column_stack([gaussian_filter1d(mid[:, i], 14, mode="nearest") for i in (0, 1)])

    # Highest ground near each km, smoothed: the camera looks at a steady height instead of following every ridge
    spread = np.arange(-6, 7, 3.0)
    samples = []
    for off in spread:
        samples.append(sample_dem(cam[:, 0] + ty * off / kx, cam[:, 1] - tx * off / 110.574))
    grid = np.nan_to_num(np.array(samples), nan=0.0)
    alt = gaussian_filter1d(np.percentile(grid, 80, axis=0), 15, mode="nearest")

    # Frame the floodplain: wide belts pull the camera back
    W = gaussian_filter1d(width, 20, mode="nearest") / 1000
    mpp = np.maximum(W, 1) * 1000 / 760
    zoom = np.clip(np.log2(78271.5 * np.cos(np.radians(cam[:, 1])) / mpp), 10.2, 11.5)
    zoom = gaussian_filter1d(zoom, 15, mode="nearest")

    data = {
        "meta": {"reach_km": REACH_KM, "sec_km": 5, "near_m": NEAR_M, "metrics": METRICS, "sustained": SUSTAINED,
                 "lc_groups": LC_GROUPS, "buildings": bld_source,
                 "built": time.strftime("%Y-%m-%d")},
        "total": [round(float(x), 3) for x in total],
        "reaches": reach_rows, "areas": area_rows,
        "camera": {"coords": [[round(float(a), 5), round(float(b), 5)] for a, b in cam],
                   "alt": [round(float(a)) for a in alt], "zoom": [round(float(z), 3) for z in zoom]},
    }
    print("Writing", flush=True)
    dumps(DATA_OUT / "floodplain.json", data)
    dumps(DATA_OUT / "fp_sections.json", {"sections": sections})
    # Unit ids are 1-based positions in admin_units.geojson
    dumps(DATA_OUT / "unit_effects.json", {
        "level": units_fc.get("level"), "metrics": METRICS, "buildings": bld_source,
        "units": {str(k): [round(float(x), 3) for x in v] for k, v in sorted(unit_table.items())},
    })
    dumps(DATA_OUT / "fp_transects.geojson", {"type": "FeatureCollection", "features": transects})
    dumps(DATA_OUT / "floodplain.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": rounded(mapping(shapely.simplify(fp, 0.0005)))}]})

    infra = []
    infra += line_features(major, fp_near, lambda r: "motorway" if r["fclass"].startswith("motorway") else "trunk" if r["fclass"].startswith("trunk")
                           else "primary" if r["fclass"].startswith("primary") else "secondary")
    infra += line_features(rail, fp_near, lambda r: "rail")
    pt = lambda xy, kind, name="": {"type": "Feature", "properties": {"kind": kind, "name": name}, "geometry": {"type": "Point", "coordinates": [round(float(xy[0]), 5), round(float(xy[1]), 5)]}}
    for gdf, kind in ((edu, "school"), (health, "health"), (dams, "dam")):
        for g, name in zip(gdf.geometry.values, gdf.name):
            infra.append(pt((g.x, g.y), kind, latin(name)))
    mb = major[major.bridge == "T"]
    if len(mb):
        c = bridge_points(mb)
        # A bridge piece is usually named after its road, which the road already shows
        for xy, name in zip(c, mb.name):
            n = latin(name)
            infra.append(pt(xy, "bridge", n if re.search(r"bridge|\bpul\b", n, re.I) else ""))
    dumps(DATA_OUT / "fp_infra.geojson", {"type": "FeatureCollection", "features": infra})

    v = dict(zip(METRICS, total))
    if unit_table:
        print(f"{units_fc.get('level')}s touching the floodplain: {len(unit_table)}; floodplain area they cover "
              f"{sum(u[MI['fp']] for u in unit_table.values()):,.0f} of {v['fp']:,.0f} km2; buildings {sum(u[MI['bldg_fp']] for u in unit_table.values()):,.0f} of {v['bldg_fp']:,.0f}")
    print(f"\nFloodplain {v['fp']:,.0f} km2  water then {v['water_then']:,.0f}  now {v['water_now']:,.0f}  "
          f"eroded to permanent {v['ero_perm']:,.0f}, to seasonal {v['ero_seas']:,.0f}  regained {v['acc_perm'] + v['acc_seas']:,.0f}  no lasting change {v['passing']:,.0f}")
    print(f"Cropland: fp {v['crop_fp']:,.0f}, eroded zone {v['crop_ero']:,.0f}, near bank {v['crop_near']:,.0f} km2")
    print(f"Built-up: fp {v['built_fp']:,.1f}, eroded zone {v['built_ero']:,.1f}, near bank {v['built_near']:,.1f} km2")
    print(f"Roads km: major fp {v['major_fp']:,.0f}, near {v['major_near']:,.0f}, ero {v['major_ero']:,.0f}; rail fp {v['rail_fp']:,.0f}")
    print(f"Counts: bridges {v['bridge_fp']:.0f}, schools {v['edu_fp']:.0f}, health {v['health_fp']:.0f}, places {v['place_fp']:.0f}, buildings {v['bldg_fp']:.0f} ({bld_source})")
    print(f"Done {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
