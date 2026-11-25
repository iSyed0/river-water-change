"""River reports: land each river took, and gave back, since the 1980s, district by district along it.

The Indus report adds up the district reports (prepare_district_reports.py), which measure inside the Indus floodplain.
Every other river is measured in a corridor: the ground within CORRIDOR_KM of its mapped line (WaterWays_Pakistan, as on
the map), the same reach the portal's "River system" water source uses. Ground in the Indus floodplain belongs to the
Indus, and where two corridors overlap each pixel goes to the nearer river. The lower Jhelum has no name in the waterways
map, so its line is traced along the satellite water record from where its name ends to the Chenab (EXTEND).

The same rules as the district reports (analyse_floodplain.erosion_classes, JRC GSW 1984-2024, 30 m):
  land lost        class 1: dry in the 1980s, under water all year now, and a rise of 25 points or more in the
                   Occurrence Change layer. The user's rule of 2026-11-20.
  part of the year class 2: now flooded part of the year; shown beside it, not counted as lost
  gave back        class 3: under water all year in the 1980s, dry now
Left out of a river's figures:
  reservoirs       pixels of water type 2 (prepare_water_types.py: OSM lake or reservoir outlines), e.g. Mangla.
                   Land a reservoir took is reported on its own, not as land lost to the river.
  canals           ground within CANAL_M of a named canal in the waterways map

Buildings come from NDMA's building server for the ground the rivers took (z15 tiles holding class 1 or 2 pixels),
cached in data/cache/rivers/buildings.npz. If the server cannot be reached and nothing is cached, building counts are
left out (null) rather than guessed. Schools, health facilities and
police, rescue and railway stations: NDMA DSDI registers (dsdi_data.py).

Output: frontend/data/river_reports.json

    python backend/pipeline/prepare_river_reports.py   (after prepare_district_reports.py, prepare_water_types.py)
"""
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import dsdi_data
from analyse_floodplain import TILE_SERVER, erosion_classes, tile_footprints
from floodplain_common import mosaic

import numpy as np
import shapely
from affine import Affine
from rasterio import features
from scipy import sparse
from scipy.ndimage import binary_dilation, distance_transform_edt
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import LineString, shape

from common import CACHE_ROOT, DATA_OUT, LAT0, LON0, MOSAIC, RES

CORRIDOR_KM = 5
CANAL_M = 90
CHUNK = 2000                # JRC pixels, 0.5 degrees
PAD = 200                   # pixels read around a chunk: more than CORRIDOR_KM
PLACE_KM = 3
KX, KY = 96.4, 110.574      # km per degree, for distances to places
ACRE = 247.105              # acres per km2
MIN_ACRES = 1
TOP_VILLAGES = 25
BUILDING_ZOOM = 15
CACHE = CACHE_ROOT / "rivers"

# Map name, name in the reports, flow direction (east, north) used to order its districts upstream to downstream
RIVERS = [
    ("Chenab River", "Chenab", (-0.7, -0.7)),
    ("Jhelum River", "Jhelum", (-0.35, -0.94)),
    ("Ravi River", "Ravi", (-0.85, -0.55)),
    ("Sutlej River", "Sutlej", (-0.9, -0.45)),
    ("Kabul River", "Kabul", (1, 0)),
    ("Swat River", "Swat", (-0.5, -0.87)),
    ("Panjkora River", "Panjkora", (0, -1)),
    ("Kunar River", "Chitral (Kunar)", (-0.45, -0.9)),
    ("Mastuj River", "Mastuj", (-0.8, -0.6)),
    ("Gilgit River", "Gilgit", (1, -0.2)),
    ("Hunza River", "Hunza", (0, -1)),
    ("Shyok River", "Shyok", (-1, 0)),
    ("Shigar River", "Shigar", (0, -1)),
    ("Neelum River", "Neelum", (-0.9, -0.4)),
    ("Kunhar River", "Kunhar", (0, -1)),
    ("Poonch River", "Poonch", (-0.8, -0.6)),
    ("Sawan River", "Soan", (-0.9, -0.4)),
    ("Kurram River", "Kurram", (0.7, -0.7)),
    ("Tochi River", "Tochi", (1, 0)),
    ("Gomal River", "Gomal", (1, 0)),
    ("Dasht River", "Dasht", (0, -1)),
    ("Hub River", "Hub", (0, -1)),
]
# Rivers whose mapped name stops short: traced on along the water to this point (lon, lat), from the named end lying
# furthest downstream. The Jhelum joins the Chenab just above Trimmu Barrage.
EXTEND = {"Jhelum River": (72.147, 31.16)}


def km_xy(lon, lat):
    return np.column_stack([np.asarray(lon) * KX, np.asarray(lat) * KY])


def to_rc(lon, lat):
    return (np.floor((LAT0 - np.asarray(lat)) / RES).astype(np.int64), np.floor((np.asarray(lon) - LON0) / RES).astype(np.int64))


# ---------------------------------------------------------------- lines
def river_lines():
    fc = json.loads((DATA_OUT / "waterways_major.geojson").read_text(encoding="utf-8"))["features"]
    lines = {name: [] for name, _, _ in RIVERS}
    canals = []
    for f in fc:
        p = f["properties"]
        if p.get("name") in lines and p.get("kind") == "river":
            lines[p["name"]].append(shape(f["geometry"]))
        elif p.get("kind") == "canal":
            canals.append(shape(f["geometry"]))
    out = {}
    for name, _, flow in RIVERS:
        g = shapely.union_all(lines[name])
        if name in EXTEND:
            g = shapely.union_all([g, trace(g, flow, EXTEND[name])])
        out[name] = g
    return out, shapely.union_all(canals)


def trace(line, flow, end):
    """A path from the downstream end of `line` to `end` along the wettest ground, on 0.0025 degree cells of the
    maximum JRC occurrence: the route the river itself takes where the map has no name for it."""
    pts = shapely.get_coordinates(line)
    start = pts[np.argmax(pts @ np.array(flow))]
    w, e = min(start[0], end[0]) - 0.25, max(start[0], end[0]) + 0.25
    s, n = min(start[1], end[1]) - 0.25, max(start[1], end[1]) + 0.25
    r0, c0 = to_rc(w, n)
    r1, c1 = to_rc(e, s)
    k = 10
    r1, c1 = r0 + (r1 - r0) // k * k, c0 + (c1 - c0) // k * k
    occ = mosaic("occurrence", int(r0), int(r1), int(c0), int(c1))
    occ = np.where(occ <= 100, occ, 0).reshape((r1 - r0) // k, k, (c1 - c0) // k, k).max(axis=(1, 3)).astype(float)
    h, wd = occ.shape
    cost = 1 + 40 * (1 - occ / 100) ** 2            # wet cells are cheap; a dry gap costs 41 times as much
    idx = np.arange(h * wd).reshape(h, wd)
    rows, cols, vals = [], [], []
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = idx[0:h - dr, max(0, -dc):wd - max(0, dc)]
        b = idx[dr:h, max(0, dc):wd - max(0, -dc)]
        step = math.hypot(dr, dc)
        wgt = (cost.ravel()[a.ravel()] + cost.ravel()[b.ravel()]) / 2 * step
        rows += [a.ravel(), b.ravel()]
        cols += [b.ravel(), a.ravel()]
        vals += [wgt, wgt]
    graph = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(h * wd, h * wd))
    cell = lambda lon, lat: int((lat_to_row(lat, r0) // k) * wd + (lon_to_col(lon, c0) // k))
    a, b = cell(*start), cell(*end)
    _, pred = dijkstra(graph, indices=a, return_predecessors=True)
    path = [b]
    while path[-1] != a and pred[path[-1]] >= 0:
        path.append(pred[path[-1]])
    path = np.array(path[::-1])
    lon = LON0 + (c0 + (path % wd) * k + k / 2) * RES
    lat = LAT0 - (r0 + (path // wd) * k + k / 2) * RES
    coords = np.column_stack([lon, lat])
    print(f"  traced {len(path)} cells, about {LineString(coords).length * 100:.0f} km, from {start.round(3)} to {end}", flush=True)
    return LineString(np.vstack([start, coords, end]))


def lat_to_row(lat, r0):
    return int(math.floor((LAT0 - lat) / RES)) - int(r0)


def lon_to_col(lon, c0):
    return int(math.floor((lon - LON0) / RES)) - int(c0)


# ---------------------------------------------------------------- buildings
def fetch_buildings(keys):
    """(lon, lat) of footprints in the z15 tiles holding these pixels, from NDMA's building server; None if unreachable."""
    path = CACHE / "buildings.npz"
    if path.exists():
        return np.load(path)["xy"]
    try:
        catalog = json.load(urllib.request.urlopen(f"{TILE_SERVER}/data.json", timeout=10))
    except OSError as err:
        print(f"  building server unreachable ({err}): building counts left out", flush=True)
        return None
    z, n = BUILDING_ZOOM, 2 ** BUILDING_ZOOM
    r, c = keys // MOSAIC, keys % MOSAIC
    lon = LON0 + (c + 0.5) * RES
    lat = LAT0 - (r + 0.5) * RES
    tx = ((lon + 180) / 360 * n).astype(int)
    ty = ((1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n).astype(int)
    tiles = sorted(set(zip(tx.tolist(), ty.tolist())))
    jobs = []
    for x, y in tiles:
        lon0, lon1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
        lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
        lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
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
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return tile_footprints(resp.read(), z, x, y) if resp.status == 200 else []
            except OSError:
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"tile failed: {tid}/{z}/{x}/{y}")

    seen = {}
    with ThreadPoolExecutor(12) as ex:
        for feats in ex.map(fetch, jobs):
            for fid, lon_, lat_ in feats:
                seen.setdefault(fid if fid is not None else (lon_, lat_), (lon_, lat_))
    xy = np.array(list(seen.values()), float).reshape(-1, 2)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(path, xy=xy)
    return xy


# ---------------------------------------------------------------- the Indus, from the district reports
def indus_report(reports):
    ds = sorted(reports["districts"].items(), key=lambda kv: kv[1]["km"][0])
    T = {k: 0 for k in ("acres", "acres_part", "acres_back", "buildings", "buildings_part", "villages_lost", "path_schools", "path_health", "emb_n")}
    villages = []
    for did, R in ds:
        for k in T:
            T[k] += R["totals"].get(k) or 0
        for v in R["villages"]:
            if v[4] >= MIN_ACRES:
                villages.append([v[0], R["name"], v[2], v[4], v[5], v[10], v[11]])
    villages.sort(key=lambda v: (-v[3], -v[4]))
    return {
        "id": "indus", "name": "Indus", "map_name": "Indus River", "method": "floodplain",
        "km": max(R["km"][1] for _, R in ds),
        "totals": {**T, "acres_reservoir": None, "emb_km": round(sum(R["totals"].get("emb_km") or 0 for _, R in ds), 1),
                   "path_stations": sum(R["totals"].get("path_stations") or 0 for _, R in ds)},
        "districts": [[int(did), R["name"], R["province"], R["totals"]["acres"], R["totals"]["acres_part"], R["totals"]["acres_back"],
                       R["totals"]["buildings"], R["totals"]["buildings_part"], R["km"]] for did, R in ds],
        "villages": villages[:TOP_VILLAGES],
        "path": [],
    }


# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    reports = json.loads((DATA_OUT / "district_reports.json").read_text(encoding="utf-8"))
    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    dgeoms = [shapely.make_valid(shape(f["geometry"])) for f in districts]
    dtree = shapely.STRtree(dgeoms)
    fp = shapely.union_all([shape(f["geometry"]) for f in json.loads((DATA_OUT / "floodplain.geojson").read_text(encoding="utf-8"))["features"]])
    places = json.loads((DATA_OUT / "places.json").read_text(encoding="utf-8"))
    pl_xy = np.column_stack([np.array(places["x"]) / 1e5, np.array(places["y"]) / 1e5])
    pl_tree = cKDTree(km_xy(pl_xy[:, 0], pl_xy[:, 1]))

    print("River lines", flush=True)
    lines, canals = river_lines()
    names = [name for name, _, _ in RIVERS]
    deg = CORRIDOR_KM / KX * 1.3
    corridors = [lines[n].buffer(deg) for n in names]
    for c in corridors:
        shapely.prepare(c)

    # Chunks of the mosaic any corridor reaches
    chunks = set()
    for c in corridors:
        w, s, e, n = c.bounds
        for r in range(int((LAT0 - n) / RES) // CHUNK, int((LAT0 - s) / RES) // CHUNK + 1):
            for col in range(int((w - LON0) / RES) // CHUNK, int((e - LON0) / RES) // CHUNK + 1):
                box = shapely.box(LON0 + col * CHUNK * RES, LAT0 - (r + 1) * CHUNK * RES, LON0 + (col + 1) * CHUNK * RES, LAT0 - r * CHUNK * RES)
                if shapely.intersects(c, box):
                    chunks.add((r, col))
    print(f"  {len(chunks)} chunks of 0.5 degrees; {time.time() - t0:.0f} s", flush=True)

    nr, nd = len(names), len(districts)
    # per river and district: km2 lost, part-year, gave back, reservoir, corridor land; and the flow position sums
    tab = np.zeros((nr, nd + 1, 5))
    flow_sum = np.zeros((nr, nd + 1))
    keep_keys, keep_river, keep_cls, keep_dist = [], [], [], []

    for i, (rb, cb) in enumerate(sorted(chunks)):
        r0, c0 = rb * CHUNK - PAD, cb * CHUNK - PAD
        r1, c1 = (rb + 1) * CHUNK + PAD, (cb + 1) * CHUNK + PAD
        west, north = LON0 + c0 * RES, LAT0 - r0 * RES
        tf = Affine(RES, 0, west, 0, -RES, north)
        shp = (r1 - r0, c1 - c0)
        bbox = shapely.box(west, LAT0 - r1 * RES, LON0 + c1 * RES, north)
        here = [k for k, c in enumerate(corridors) if shapely.intersects(c, bbox)]
        if not here:
            continue
        lat_mid = (north + LAT0 - r1 * RES) / 2
        sampling = (RES * KY * 1000, RES * 111320 * math.cos(math.radians(lat_mid)))
        dist = np.full((len(here),) + shp, np.inf, np.float32)
        for j, k in enumerate(here):
            line = shapely.clip_by_rect(lines[names[k]], *bbox.bounds)
            if line.is_empty:
                continue
            on = features.rasterize([(line, 1)], out_shape=shp, transform=tf, fill=0, dtype="uint8", all_touched=True)
            dist[j] = distance_transform_edt(on == 0, sampling=sampling)
        best = dist.argmin(0)
        near = np.take_along_axis(dist, best[None], 0)[0] <= CORRIDOR_KM * 1000
        sl = (slice(PAD, PAD + CHUNK), slice(PAD, PAD + CHUNK))
        near, best = near[sl], best[sl]
        if not near.any():
            continue
        r0i, c0i = rb * CHUNK, cb * CHUNK
        ctf = Affine(RES, 0, LON0 + c0i * RES, 0, -RES, LAT0 - r0i * RES)
        cbox = shapely.box(LON0 + c0i * RES, LAT0 - (r0i + CHUNK) * RES, LON0 + (c0i + CHUNK) * RES, LAT0 - r0i * RES)
        # Not the Indus floodplain, not canals, inside a district of the map
        none = np.zeros((CHUNK, CHUNK), bool)
        in_fp = features.rasterize([(fp, 1)], out_shape=(CHUNK, CHUNK), transform=ctf, fill=0, dtype="uint8").astype(bool) if fp.intersects(cbox) else none
        canal = shapely.clip_by_rect(canals, *cbox.bounds)
        if not canal.is_empty:
            canal = features.rasterize([(canal, 1)], out_shape=(CHUNK, CHUNK), transform=ctf, fill=0, dtype="uint8", all_touched=True).astype(bool)
            canal = binary_dilation(canal, iterations=max(1, round(CANAL_M / 30)))
        else:
            canal = none
        idx = dtree.query(cbox, predicate="intersects")
        dist_id = features.rasterize([(dgeoms[j], j + 1) for j in idx], out_shape=(CHUNK, CHUNK), transform=ctf, fill=0, dtype="int32") if len(idx) else np.zeros((CHUNK, CHUNK), np.int32)
        use = near & ~in_fp & (dist_id > 0)
        if not use.any():
            continue
        trans = mosaic("transitions", r0i, r0i + CHUNK, c0i, c0i + CHUNK, fill=0)
        chg = mosaic("change", r0i, r0i + CHUNK, c0i, c0i + CHUNK)
        d = np.where(chg <= 200, chg.astype(np.int16) - 100, 0)
        cls = erosion_classes(trans, d)
        wt = mosaic("watertype", r0i, r0i + CHUNK, c0i, c0i + CHUNK, fill=0)
        river = np.array(here)[best]
        lat = LAT0 - (r0i + np.arange(CHUNK) + 0.5) * RES
        px_km2 = (RES * KY) * (RES * 111.320 * np.cos(np.radians(lat)))[:, None] * np.ones((1, CHUNK))
        lon = LON0 + (c0i + np.arange(CHUNK) + 0.5) * RES
        for k in np.unique(river[use]):
            m = use & (river == k)
            rv = m & ~canal & np.isin(wt, (1, 3))
            dx, dy = RIVERS[k][2]
            for col, sel in enumerate((rv & (cls == 1), rv & (cls == 2), rv & (cls == 3), m & ~canal & (wt == 2) & (cls == 1), m)):
                tab[k, :, col] += np.bincount(dist_id[sel], weights=px_km2[sel], minlength=nd + 1)
            proj = (lon[None, :] * dx * KX + lat[:, None] * dy * KY)
            flow_sum[k] += np.bincount(dist_id[m], weights=(proj * px_km2)[m], minlength=nd + 1)
            for cl in (1, 2):
                rr, cc = np.nonzero(rv & (cls == cl))
                keep_keys.append((r0i + rr).astype(np.int64) * MOSAIC + c0i + cc)
                keep_river.append(np.full(len(rr), k, np.int16))
                keep_cls.append(np.full(len(rr), cl, np.uint8))
                keep_dist.append(dist_id[rr, cc])
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(chunks)} chunks; {time.time() - t0:.0f} s", flush=True)

    keys = np.concatenate(keep_keys)
    k_river, k_cls, k_dist = np.concatenate(keep_river), np.concatenate(keep_cls), np.concatenate(keep_dist)
    order = np.argsort(keys)
    keys, k_river, k_cls, k_dist = keys[order], k_river[order], k_cls[order], k_dist[order]
    print(f"pixels lost or newly wet along the rivers: {len(keys):,}; {time.time() - t0:.0f} s", flush=True)

    def lookup(xy):
        """Index into keys of the pixel under each point, -1 where it is not land lost or newly wet."""
        r, c = to_rc(xy[:, 0], xy[:, 1])
        k = r * MOSAIC + c
        pos = np.clip(np.searchsorted(keys, k), 0, len(keys) - 1)
        return np.where(keys[pos] == k, pos, -1)

    # Villages: each pixel to the nearest named place within PLACE_KM
    lon = LON0 + (keys % MOSAIC + 0.5) * RES
    lat = LAT0 - (keys // MOSAIC + 0.5) * RES
    px_km2 = (RES * KY) * (RES * 111.320 * np.cos(np.radians(lat)))
    dd, pl = pl_tree.query(km_xy(lon, lat), distance_upper_bound=PLACE_KM)
    ok = np.isfinite(dd)
    vill = {}
    for k, p, cl, a, did in zip(k_river[ok], pl[ok], k_cls[ok], px_km2[ok], k_dist[ok]):
        v = vill.setdefault((int(k), int(p)), [0.0, 0.0, int(did)])
        v[cl - 1] += a

    # Buildings and DSDI schools and health facilities on that ground
    bxy = fetch_buildings(keys)
    bld = np.zeros((nr, nd + 1, 2))
    if bxy is not None and len(bxy):
        at = lookup(bxy)
        hit = at >= 0
        np.add.at(bld, (k_river[at[hit]], k_dist[at[hit]], k_cls[at[hit]] - 1), 1)
    path = {k: [] for k in range(nr)}
    for key, kind in dsdi_data.POINT_KINDS.items():
        got = dsdi_data.points(key)
        if got is None:
            continue
        xy, pnames, details = got
        at = lookup(xy)
        seen = set()  # the registers list some facilities twice
        for j in np.nonzero(at >= 0)[0]:
            q = at[j]
            dup = (pnames[j], round(float(xy[j, 0]), 4), round(float(xy[j, 1]), 4))
            if dup in seen:
                continue
            seen.add(dup)
            path[int(k_river[q])].append([pnames[j] + (f" ({details[j]})" if details[j] else ""), kind,
                                          districts[k_dist[q] - 1]["properties"]["name"], int(k_cls[q]),
                                          round(float(xy[j, 0]), 5), round(float(xy[j, 1]), 5)])
    print(f"buildings: {'not available' if bxy is None else f'{int(bld.sum()):,} on this ground'}; {time.time() - t0:.0f} s", flush=True)

    whole = lambda km2: int(round(km2 * ACRE)) if km2 * ACRE >= MIN_ACRES else 0
    out = [indus_report(reports)]
    for k, (map_name, label, _) in enumerate(RIVERS):
        rows = []
        for did in np.nonzero(tab[k, 1:, 4] > 0)[0] + 1:
            f = districts[did - 1]["properties"]
            t = tab[k, did]
            if t[4] * ACRE < 50 and not t[:3].any():   # the corridor only grazes this district
                continue
            rows.append([int(f["id"]), f["name"], f["province"], whole(t[0]), whole(t[1]), whole(t[2]),
                         None if bxy is None else int(bld[k, did, 0]), None if bxy is None else int(bld[k, did, 1]),
                         flow_sum[k, did] / t[4]])
        rows.sort(key=lambda r: r[-1])
        for r in rows:
            r[-1] = None
        T = tab[k].sum(0)
        vs = [[places["n"][p], districts[did - 1]["properties"]["name"] if did else "", places["kinds"][places["k"][p]],
               whole(v[0]), whole(v[1]), round(float(pl_xy[p, 0]), 5), round(float(pl_xy[p, 1]), 5)]
              for (kk, p), (*v, did) in vill.items() if kk == k]
        vs = [v for v in vs if v[3] or v[4]]
        vs.sort(key=lambda v: (-v[3], -v[4]))
        g = lines[map_name]
        out.append({
            "id": label.split()[0].lower(), "name": label, "map_name": map_name, "method": "corridor",
            "bbox": [round(v, 3) for v in g.bounds],
            "totals": {"acres": whole(T[0]), "acres_part": whole(T[1]), "acres_back": whole(T[2]), "acres_reservoir": whole(T[3]),
                       "corridor_acres": whole(T[4]),
                       "buildings": None if bxy is None else int(bld[k, :, 0].sum()),
                       "buildings_part": None if bxy is None else int(bld[k, :, 1].sum()),
                       "villages_lost": sum(1 for v in vs if v[3] >= MIN_ACRES),
                       "path_schools": sum(1 for p in path[k] if p[1] == "School"),
                       "path_health": sum(1 for p in path[k] if p[1] == "Health facility"),
                       "path_stations": sum(1 for p in path[k] if p[1].endswith("station"))},
            "districts": rows,
            "villages": vs[:TOP_VILLAGES],
            "path": sorted(path[k], key=lambda p: (p[3], p[2], p[0])),
            "traced": map_name in EXTEND,
        })
        print(f"  {label:16} lost {out[-1]['totals']['acres']:>8,}  part-year {out[-1]['totals']['acres_part']:>8,}  "
              f"gave back {out[-1]['totals']['acres_back']:>7,}  reservoir {out[-1]['totals']['acres_reservoir']:>6,}  districts {len(rows)}", flush=True)

    meta = {
        "built": date.today().isoformat(), "period": reports["meta"]["period"], "corridor_km": CORRIDOR_KM, "canal_m": CANAL_M,
        "place_km": PLACE_KM, "min_acres": MIN_ACRES,
        "buildings": None if bxy is None else "ndma", "facilities": "dsdi",
        "district_fields": ["id", "name", "province", "acres_lost", "acres_part_year", "acres_gave_back", "buildings_lost", "buildings_part_year", "km_along (Indus only)"],
        "village_fields": ["name", "district", "kind", "acres_lost", "acres_part_year", "lon", "lat"],
        "path_fields": ["name", "kind", "district", "class (1 land lost, 2 flooded part of the year)", "lon", "lat"],
        "traced": {k: list(v) for k, v in EXTEND.items()},
    }
    path_out = DATA_OUT / "river_reports.json"
    path_out.write_text(json.dumps({"meta": meta, "rivers": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path_out.name}: {len(out)} rivers, {path_out.stat().st_size / 1e3:.0f} KB; {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
