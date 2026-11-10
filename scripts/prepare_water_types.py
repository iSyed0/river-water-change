"""Sort the water in the JRC record into likely types, from where it lies and how often it is there.

Satellites record that a pixel was wet, not why. These types are inferred, with no dates involved: they use
all of 1984-2024 at once, so "seasonal" water here includes floods, high river water, rain ponding and flooded
fields together. Each water pixel gets the first type that fits:

  2 lake or reservoir   inside an OpenStreetMap lake or reservoir outline, unless the outline is river-shaped
  5 coast, tidal        within COAST_KM of the sea, or in a mapped tidal flat, salt marsh or mangrove
  1 river or canal      in a mapped riverbank; or wet at least REGULAR_OCC of the time and joined to a mapped
                        river, canal, stream or drain
  3 seasonal, near a river   in the Indus floodplain or within NEAR_RIVER_KM of a mapped river
  4 seasonal, away from rivers   everything else
  regular water that matches none of these (a farm pond, say) counts as type 2

Outputs:
  watertype/watertype_*_v1_5_2024.tif   source of the "watertype" map layer (build_tiles.py --layers watertype)
  portal/data/water_types.json          km2 of each type: national, per province, per district

    python scripts/prepare_water_types.py     (after prepare_boundaries.py, prepare_rivers.py, analyse_floodplain.py)
"""
import json
import math
import os
import time
import zipfile
import zlib
from concurrent.futures import ProcessPoolExecutor

from floodplain_common import mosaic

import numpy as np
import pyogrio
import rasterio
import shapely
from affine import Affine
from rasterio import features
from rasterio.windows import Window
from scipy.ndimage import binary_dilation, distance_transform_edt, generate_binary_structure, label
from shapely.geometry import box, shape

from common import DATA_OUT, GRANULE, GRANULES, LAT0, LON0, RES, ROOT, granule_path, national_boundary, national_outline

CHUNK = 2000            # JRC pixels, 0.5 degrees
PAD = 1000              # read around a chunk: a river is judged over about 100 km, not one chunk
REGULAR_OCC = 50        # wet at least this share of the time, the same test as the floodplain analysis
NEAR_RIVER_KM = 5
COAST_KM = 10
COAST_LAT = 25.75       # the coast lies south of this latitude
RIVER_SHAPED = 0.02     # an OSM "water" outline this thin (4 pi area / perimeter^2) is a river, not a lake
TYPES = ["River or canal", "Lake or reservoir", "Seasonal water near a river", "Seasonal water away from rivers", "Coast and tidal water"]
RIVER, LAKE, NEAR, AWAY, COAST = 1, 2, 3, 4, 5
TIDAL = ["wetland_tidalflat", "wetland_saltmarsh", "wetland_mangrove"]
OSM_ZIP = ROOT / "osm_src" / "pakistan-latest-free.shp.zip"
OSM_WATER = ROOT / "osm_src" / "infra" / "gis_osm_water_a_free_1.shp"

_W = {}


def _init(wkb, district_ids):
    _W.update({k: shapely.from_wkb(v) for k, v in wkb.items()})
    _W["trees"] = {k: shapely.STRtree(_W[k]) for k in ("lines", "rivers", "lakes", "banks", "tidal", "districts")}
    for geom in (_W["floodplain"], _W["coast"], _W["pakistan"]):
        shapely.prepare(geom)
    _W["district_ids"] = district_ids


def burn(name, cell, tf, shape_, all_touched=False):
    """Rasterise the features of one group that touch this window."""
    hits = _W["trees"][name].query(cell)
    if not len(hits):
        return np.zeros(shape_, bool)
    return features.rasterize(_W[name][hits], out_shape=shape_, transform=tf, fill=0, dtype="uint8", all_touched=all_touched).astype(bool)


def process_chunk(args):
    r0, c0 = args
    win = (r0 - PAD, r0 + CHUNK + PAD, c0 - PAD, c0 + CHUNK + PAD)
    ext = mosaic("extent", *win) == 1
    inner = (slice(PAD, -PAD), slice(PAD, -PAD))
    if not ext[inner].any():
        return r0, c0, None, np.zeros((0, len(TYPES) + 1)), np.zeros(len(TYPES) + 1)
    occ = mosaic("occurrence", *win)
    regular = ext & (occ >= REGULAR_OCC) & (occ <= 100)

    size = ext.shape
    tf = Affine(RES, 0, LON0 + win[2] * RES, 0, -RES, LAT0 - win[0] * RES)
    cell = box(LON0 + win[2] * RES, LAT0 - win[1] * RES, LON0 + win[3] * RES, LAT0 - win[0] * RES)

    # Regular water joined to any mapped line, however far it spreads from the line inside this window
    lines = burn("lines", cell, tf, size, all_touched=True)
    joined = np.zeros(size, bool)
    if lines.any() and regular.any():
        labels, _ = label(regular, structure=generate_binary_structure(2, 2))
        touching = np.unique(labels[binary_dilation(lines, iterations=2) & regular])
        joined = np.isin(labels, touching[touching > 0])

    river_lines = burn("rivers", cell, tf, size, all_touched=True)
    near = np.zeros(size, bool)
    if river_lines.any():
        lat = LAT0 - (win[0] + CHUNK) * RES
        sampling = (RES * 110574, RES * 111320 * math.cos(math.radians(lat)))
        near = distance_transform_edt(~river_lines, sampling=sampling) <= NEAR_RIVER_KM * 1000
    near |= features.rasterize([(_W["floodplain"], 1)], out_shape=size, transform=tf, fill=0, dtype="uint8").astype(bool)
    coast = burn("tidal", cell, tf, size) | features.rasterize([(_W["coast"], 1)], out_shape=size, transform=tf, fill=0, dtype="uint8").astype(bool)

    # Later lines win
    out = np.full(size, 255, np.uint8)
    out[ext] = AWAY
    out[ext & near] = NEAR
    out[regular] = LAKE
    out[joined & regular] = RIVER
    out[ext & burn("banks", cell, tf, size)] = RIVER
    out[ext & coast] = COAST
    out[ext & burn("lakes", cell, tf, size)] = LAKE
    out = out[inner]

    # Areas per district, and nationally
    rows = np.arange(CHUNK)
    # The same pixel area as compute_stats.py, so the types add up to the extent in the Insights panel
    px_km2 = (math.radians(RES) * 6371.0088) ** 2 * np.cos(np.radians(LAT0 - (r0 + rows + 0.5) * RES))
    area = np.broadcast_to(px_km2[:, None], (CHUNK, CHUNK))
    tf_in = Affine(RES, 0, LON0 + c0 * RES, 0, -RES, LAT0 - r0 * RES)
    cell_in = box(LON0 + c0 * RES, LAT0 - (r0 + CHUNK) * RES, LON0 + (c0 + CHUNK) * RES, LAT0 - r0 * RES)
    hits = _W["trees"]["districts"].query(cell_in)
    dist = np.zeros((CHUNK, CHUNK), np.int32)
    if len(hits):
        dist = features.rasterize([(_W["districts"][h], int(_W["district_ids"][h])) for h in hits], out_shape=(CHUNK, CHUNK), transform=tf_in, fill=0, dtype="int32")
    wet = out != 255
    per_district = np.column_stack([dist[wet], out[wet], area[wet]])
    national_mask = features.rasterize([(_W["pakistan"], 1)], out_shape=(CHUNK, CHUNK), transform=tf_in, fill=0, dtype="uint8").astype(bool)
    national = np.bincount(out[wet & national_mask], weights=area[wet & national_mask], minlength=len(TYPES) + 1)
    return r0, c0, zlib.compress(out.tobytes(), 6), per_district, national


def coast_zone(outline):
    """The sea edge of the national outline, widened by COAST_KM."""
    lines = []
    for poly in (outline.geoms if outline.geom_type == "MultiPolygon" else [outline]):
        xy = shapely.get_coordinates(poly.exterior)
        keep = np.nonzero((xy[:, 1] < COAST_LAT) & (xy[:, 0] > 61.63) & (xy[:, 0] < 68.2))[0]
        for run in np.split(keep, np.nonzero(np.diff(keep) > 1)[0] + 1):
            if len(run) > 1:
                lines.append(shapely.linestrings(xy[run]))
    return shapely.buffer(shapely.simplify(shapely.multilinestrings(lines), 0.005), COAST_KM / 100, quad_segs=4)


def main():
    t0 = time.time()
    if not OSM_WATER.exists():
        with zipfile.ZipFile(OSM_ZIP) as z:
            for n in z.namelist():
                if n.startswith("gis_osm_water_a_free_1"):
                    z.extract(n, OSM_WATER.parent)

    osm = pyogrio.read_dataframe(OSM_WATER, columns=["fclass"])
    geoms = shapely.make_valid(osm.geometry.values)
    round_ = 4 * np.pi * shapely.area(geoms) / np.maximum(shapely.length(geoms), 1e-12) ** 2
    is_water = osm.fclass.values == "water"
    lakes = geoms[(osm.fclass.values == "reservoir") | (is_water & (round_ >= RIVER_SHAPED))]
    banks = geoms[(osm.fclass.values == "riverbank") | (is_water & (round_ < RIVER_SHAPED))]
    # Some inland wetlands are mistagged as tidal: only the coastal belt counts
    bounds = shapely.bounds(geoms)
    tidal = geoms[np.isin(osm.fclass.values, TIDAL) & ((bounds[:, 1] + bounds[:, 3]) / 2 < COAST_LAT)]
    print(f"OSM: {len(lakes):,} lakes and reservoirs, {len(banks):,} river outlines, {len(tidal):,} tidal", flush=True)

    fc = []
    for name in ("waterways_major", "waterways_minor"):
        fc += json.loads((DATA_OUT / f"{name}.geojson").read_text(encoding="utf-8"))["features"]
    fc = [f for f in fc if f["properties"]["kind"] != "qanat"]    # underground channels
    lines = np.array([shape(f["geometry"]) for f in fc])
    rivers = np.array([shape(f["geometry"]) for f in fc if f["properties"]["kind"] == "river"])
    print(f"Waterways: {len(lines):,} lines, {len(rivers):,} of them rivers", flush=True)

    outline = national_outline()
    pakistan, _ = national_boundary()
    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    floodplain = shape(json.loads((DATA_OUT / "floodplain.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"])
    wkb = {
        "lines": shapely.to_wkb(lines), "rivers": shapely.to_wkb(rivers), "lakes": shapely.to_wkb(lakes), "banks": shapely.to_wkb(banks), "tidal": shapely.to_wkb(tidal),
        "districts": shapely.to_wkb(np.array([shape(f["geometry"]) for f in districts])),
        "floodplain": shapely.to_wkb(floodplain), "coast": shapely.to_wkb(coast_zone(outline)), "pakistan": shapely.to_wkb(pakistan),
    }

    w, s, e, n = outline.bounds
    shapely.prepare(outline)
    jobs = []
    for r0 in range(int((LAT0 - n) / RES) // CHUNK * CHUNK, int((LAT0 - s) / RES) + 1, CHUNK):
        for c0 in range(int((w - LON0) / RES) // CHUNK * CHUNK, int((e - LON0) / RES) + 1, CHUNK):
            cell = box(LON0 + c0 * RES, LAT0 - (r0 + CHUNK) * RES, LON0 + (c0 + CHUNK) * RES, LAT0 - r0 * RES)
            if shapely.intersects(outline, cell):
                jobs.append((r0, c0))
    print(f"{len(jobs)} chunks", flush=True)

    profile = dict(driver="GTiff", width=GRANULE, height=GRANULE, count=1, dtype="uint8", nodata=255, crs="EPSG:4326",
                   tiled=True, blockxsize=512, blockysize=512, compress="deflate", sparse_ok=True)
    sinks = {}
    for key in GRANULES:
        path = granule_path("watertype", key)
        path.parent.mkdir(exist_ok=True)
        tf = Affine(RES, 0, LON0 + key[0] * GRANULE * RES, 0, -RES, LAT0 - key[1] * GRANULE * RES)
        sinks[key] = rasterio.open(path, "w", transform=tf, **profile)

    ids = [f["properties"]["id"] for f in districts]
    by_district = np.zeros((max(ids) + 1, len(TYPES) + 1))
    national = np.zeros(len(TYPES) + 1)
    with ProcessPoolExecutor(max_workers=min(5, max(2, (os.cpu_count() or 4) - 2)), initializer=_init, initargs=(wkb, ids)) as ex:
        for i, (r0, c0, raster, per, nat) in enumerate(ex.map(process_chunk, jobs, chunksize=1), 1):
            if raster is not None:
                key = (c0 // GRANULE, r0 // GRANULE)
                win = Window(c0 - key[0] * GRANULE, r0 - key[1] * GRANULE, CHUNK, CHUNK)
                sinks[key].write(np.frombuffer(zlib.decompress(raster), np.uint8).reshape(CHUNK, CHUNK), 1, window=win)
                np.add.at(by_district, (per[:, 0].astype(int), per[:, 1].astype(int)), per[:, 2])
                national += nat
            if i % 50 == 0 or i == len(jobs):
                print(f"  chunk {i}/{len(jobs)}  {time.time() - t0:.0f}s", flush=True)
    for d in sinks.values():
        d.close()

    rnd = lambda row: [round(float(v), 2) for v in row[1:]]
    provinces = {}
    for f in districts:
        p = f["properties"]["province"]
        provinces[p] = provinces.get(p, 0) + by_district[f["properties"]["id"]]
    out = {"types": TYPES, "regular_occurrence": REGULAR_OCC, "near_river_km": NEAR_RIVER_KM, "national": rnd(national),
           "provinces": {k: rnd(v) for k, v in provinces.items()},
           "districts": {str(f["properties"]["id"]): rnd(by_district[f["properties"]["id"]]) for f in districts}}
    (DATA_OUT / "water_types.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    for name, v in zip(TYPES, national[1:]):
        print(f"  {name}: {v:,.0f} km2 ({v / national.sum() * 100:.0f}%)")
    print(f"Total {national.sum():,.0f} km2; done {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
