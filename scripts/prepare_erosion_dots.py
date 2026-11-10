"""Eroded and newly wet ground as points, each saying what stands on it, for the blinking dots on the map.

Single pixels of the erosion raster vanish when zoomed out. The analysis sums every figure per grid cell of
about 2.5 km; this script merges cells into dots for each map zoom, the way the map would cluster them, and
names the settlements: each building on eroded ground goes to the nearest named place within PLACE_KM. The
building may not belong to that place, but it is what the ground can be called. Buildings are also split by the
ground under them: now wet all year (erosion class 1) or wet for part of the year (class 2).

Every zoom holds all the eroded ground once, so the dots of a zoom add up to the floodplain totals.

Output: portal/data/erosion_dots.geojson

    python scripts/prepare_erosion_dots.py     (after analyse_floodplain.py)
"""
import json
from collections import Counter

from analyse_floodplain import CELL, CHUNK
from floodplain_common import CACHE, to_rc

import numpy as np
import rasterio
import shapely
from rasterio.windows import Window
from scipy.spatial import cKDTree
from shapely.geometry import shape

from common import DATA_OUT, GRANULE, GRANULES, LAT0, LON0, RES, ROOT

ZOOMS = range(4, 10)    # dots are redrawn at each whole zoom; they are gone by zoom 10
RADIUS_PX = 36          # cells closer than this on screen become one dot
PLACE_KM = 3
TOP_PLACES = 8
KX = 98.0               # km per degree of longitude along the Indus plains, for distances only
KY = 110.574
SUMS = {"km2": None, "perm": "ero_perm", "bld": "bldg_ero", "crop": "crop_ero", "built": "built_ero", "major": "major_ero",
        "minor": "minor_ero", "rail": "rail_ero", "edu": "edu_ero", "health": "health_ero", "bridge": "bridge_ero"}


def read_chunk(sources, r0, c0):
    key = (c0 // GRANULE, r0 // GRANULE)
    return sources[key].read(1, window=Window(c0 - key[0] * GRANULE, r0 - key[1] * GRANULE, CHUNK, CHUNK))


def by_chunk(xy):
    """Indices of points per chunk, with their pixel position inside it."""
    r, c = to_rc(xy[:, 0], xy[:, 1])
    ids = r // CHUNK * 100000 + c // CHUNK
    order = np.argsort(ids)
    values, first = np.unique(ids[order], return_index=True)
    return {int(v): (idx, r[idx] % CHUNK, c[idx] % CHUNK) for v, idx in zip(values, np.split(order, first[1:]))}


def main():
    data = np.load(CACHE / "erosion_cells.npz")
    metrics = [str(m) for m in data["metrics"]]
    cells = {int(k): {"values": v, "lon": 0.0, "lat": 0.0, "places": Counter(), "bld_perm": 0, "bld_seen": 0} for k, v in zip(data["keys"], data["values"])}
    MI = {m: i for i, m in enumerate(metrics)}

    bld = np.load(CACHE / "buildings.npz")["xy"]
    places = json.loads((DATA_OUT / "places.json").read_text(encoding="utf-8"))
    pl_xy = np.column_stack([np.array(places["x"]) / 1e5, np.array(places["y"]) / 1e5])
    pl_tree = cKDTree(pl_xy * [KX, KY])
    bld_chunks = by_chunk(bld)

    sources = {key: rasterio.open(ROOT / "erosion" / f"erosion_{name}_v1_5_2024.tif") for key, name in GRANULES.items()}
    no_place = 0
    for cid in sorted({k // 100000 // (CHUNK // CELL) * 100000 + (k % 100000) // (CHUNK // CELL) for k in cells}):
        r0, c0 = cid // 100000 * CHUNK, cid % 100000 * CHUNK
        cls = read_chunk(sources, r0, c0)
        ero = np.isin(cls, (1, 2))
        cell_of = lambda r, c: (r0 + r) // CELL * 100000 + (c0 + c) // CELL

        # Each dot sits at the middle of its changed pixels
        rows, cols = np.nonzero(ero)
        keys = cell_of(rows, cols)
        lon = LON0 + (c0 + cols + 0.5) * RES
        lat = LAT0 - (r0 + rows + 0.5) * RES
        for k in np.unique(keys):
            sel = keys == k
            if int(k) in cells:
                cells[int(k)]["lon"], cells[int(k)]["lat"] = float(lon[sel].mean()), float(lat[sel].mean())

        if cid in bld_chunks:
            idx, r, c = bld_chunks[cid]
            on = ero[r, c]
            if on.any():
                perm = cls[r[on], c[on]] == 1
                for k, p in zip(cell_of(r[on], c[on]), perm):
                    if int(k) in cells:
                        cells[int(k)]["bld_seen"] += 1
                        cells[int(k)]["bld_perm"] += int(p)
                dist, near = pl_tree.query(bld[idx[on]] * [KX, KY])
                for k, d, p in zip(cell_of(r[on], c[on]), dist, near):
                    if d <= PLACE_KM:
                        cells[int(k)]["places"][int(p)] += 1
                    else:
                        no_place += 1
    for src in sources.values():
        src.close()

    # District of each cell, for the labels when zoomed far out
    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    dgeoms = [shape(f["geometry"]) for f in districts]
    tree = shapely.STRtree(dgeoms)
    ids = list(cells)
    xy = np.array([[cells[k]["lon"], cells[k]["lat"]] for k in ids])
    at_point, at_geom = tree.query(shapely.points(xy), predicate="within")
    for i, g in zip(at_point, at_geom):
        cells[ids[i]]["district"] = districts[g]["properties"]["name"]

    def total(group, name):
        return sum(cells[k]["values"][MI["ero_perm"]] + cells[k]["values"][MI["ero_seas"]] if name is None else cells[k]["values"][MI[name]] for k in group)

    def as_feature(fid, z, group):
        sums = {key: total(group, name) for key, name in SUMS.items()}
        weights = np.array([cells[k]["values"][MI["ero_perm"]] + cells[k]["values"][MI["ero_seas"]] for k in group])
        pts = np.array([[cells[k]["lon"], cells[k]["lat"]] for k in group])
        lon, lat = (pts * weights[:, None]).sum(0) / weights.sum()
        named = Counter()
        area_by_district = Counter()
        for k, w in zip(group, weights):
            named.update(cells[k]["places"])
            if "district" in cells[k]:
                area_by_district[cells[k]["district"]] += float(w)
        dists = [d for d, _ in area_by_district.most_common(4)]
        top = [[places["n"][p], round(float(pl_xy[p, 0]), 5), round(float(pl_xy[p, 1]), 5), n] for p, n in named.most_common(TOP_PLACES)]
        far_out = z <= 6 or not top
        name = f"{dists[0]} district" if far_out and dists else top[0][0] if top else "Indus floodplain"
        bld_n = round(sums["bld"])
        sub = f"{bld_n:,} building{'' if bld_n == 1 else 's'}" if bld_n else "No buildings"
        if sums["crop"] >= 0.5:
            sub += f" · {sums['crop']:,.0f} km² cropland"
        props = {"id": fid, "z": z, "name": name, "sub": sub, "districts": dists, "places": top, "n_places": len(named),
                 "bld_named": sum(named.values()), "bld_perm": sum(cells[k]["bld_perm"] for k in group),
                 "bbox": [round(float(v), 4) for v in (pts[:, 0].min() - 0.02, pts[:, 1].min() - 0.02, pts[:, 0].max() + 0.02, pts[:, 1].max() + 0.02)]}
        props.update({key: round(float(v), 3) for key, v in sums.items()})
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(lon), 4), round(float(lat), 4)]}, "properties": props}

    # Largest ground first; each takes the unclaimed cells around it
    order = sorted(ids, key=lambda k: -(cells[k]["values"][MI["ero_perm"]] + cells[k]["values"][MI["ero_seas"]]))
    pos = np.array([[cells[k]["lon"] * KX / KY, cells[k]["lat"]] for k in order])
    cell_tree = cKDTree(pos)
    feats = []
    for z in ZOOMS:
        radius = RADIUS_PX * 360 / (512 * 2 ** z)
        used = np.zeros(len(order), bool)
        n0 = len(feats)
        for i in range(len(order)):
            if used[i]:
                continue
            near = [j for j in cell_tree.query_ball_point(pos[i], radius) if not used[j]]
            used[near] = True
            feats.append(as_feature(len(feats) + 1, z, [order[j] for j in near]))
        got = feats[n0:]
        print(f"z{z}: {len(got)} dots, {sum(f['properties']['km2'] for f in got):,.1f} km2, {sum(f['properties']['bld'] for f in got):,.0f} buildings")

    path = DATA_OUT / "erosion_dots.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    v = data["values"].sum(0)
    named = sum(sum(c["places"].values()) for c in cells.values())
    print(f"{path.name}: {len(feats)} dots, {path.stat().st_size / 1e3:.0f} KB")
    print(f"analysis totals: {v[MI['ero_perm']] + v[MI['ero_seas']]:,.1f} km2, {v[MI['bldg_ero']]:,.0f} buildings")
    print(f"named here: {named:,} buildings near a named place, {no_place:,} with none within {PLACE_KM} km")
    seen = sum(c["bld_seen"] for c in cells.values())
    perm = sum(c["bld_perm"] for c in cells.values())
    print(f"buildings on the erosion raster: {seen:,} ({perm:,} on ground now wet all year, {seen - perm:,} wet part of the year)")


if __name__ == "__main__":
    main()
