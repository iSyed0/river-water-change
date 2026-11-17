"""District reports: what the Indus took from each district's villages, for the district popup and report window.

There are no official village (mouza) boundaries in this project, so a "village" here is a named place from
GeoNames / OpenStreetMap (portal/data/places.json), and its ground is the land within PLACE_KM that is closer to it than
to any other named place. Everything else comes from data already in the project:

  Land lost       the erosion raster of analyse_floodplain.py (JRC GSW 1984-2024, 30 m, sustained change only):
                  class 1 land now wet all year, class 2 land now wet part of the year. Each pixel goes to the nearest
                  named place within PLACE_KM.
  Year by year    the cleaned JRC Monthly History shares of prepare_timeline.py (build/timeline/share_YYYY.tif, cells of
                  0.0025 degrees, ~275 m). A cell's river share for a year is the median of its seen months. Land cut in
                  year Y is new river share that was not river in Y-1 and stays river in Y+1, counted inside the Indus
                  floodplain only. Cells are coarse, so this is an estimate of the trend, not of exact acres.
  Buildings       build/floodplain/buildings.npz (NDMA building server, or OSM when it was offline).
  Places in path  named places, and OSM schools and health facilities, whose point is on (or one pixel from) ground the
                  river took, or on ground that was water in both periods of the record.

Risk of losing more land combines the share of the village's ground already lost since the 1980s and the share cut in
the last five years of the monthly record (RECENT). It is a guide from the satellite record, not a forecast.

Output: portal/data/district_reports.json

    python scripts/prepare_district_reports.py   (after analyse_floodplain.py, prepare_places.py, prepare_timeline.py)
"""
import json
import time
from datetime import date

from analyse_floodplain import CHUNK, EDU, HEALTH, osm_read
from floodplain_common import CACHE, to_rc

import numpy as np
import pandas as pd
import rasterio
import shapely
from affine import Affine
from rasterio import features
from rasterio.windows import Window, from_bounds
from scipy.spatial import cKDTree
from shapely.geometry import box, shape

from common import DATA_OUT, GRANULE, GRANULES, LAT0, LON0, RES, ROOT

PLACE_KM = 3
KX, KY = 96.4, 110.574      # km per degree along the Indus plains, for distances
ACRE = 247.105              # acres per km2
GRID_DEG = 0.001            # sampling step for each village's ground
YEARS = list(range(2000, 2022))
RECENT = (2016, 2020)       # "the last five years" of the monthly record whose cut can be confirmed by the year after
SEEN_MIN_MONTHS = 3
TIMELINE = ROOT / "build" / "timeline"
UNSEEN = 253
RISK = ["Very high", "High", "Moderate", "Low", "Little or none"]
# Shares of the village's ground, in %: lost since the 1980s, cut in the RECENT years. Very high needs both: much already
# lost and the river still cutting there. Plain words for the portal in RISK_TEXT.
RISK_RULES = [
    lambda lost, recent: (lost >= 25) & (recent >= 10),
    lambda lost, recent: ((lost >= 15) & (recent >= 5)) | (lost >= 40) | (recent >= 30),
    lambda lost, recent: (lost >= 5) | (recent >= 5),
]
RISK_TEXT = [
    "a quarter or more of its ground lost since the 1980s, and a tenth or more cut again in the last five years",
    "15% lost and 5% cut recently, or 40% lost, or 30% cut recently",
    "5% or more lost, or 5% or more cut recently",
    "some land lost or cut, less than the above",
    "no land lost or cut",
]
MIN_ACRES = 1               # less than this counts as no loss
WHERE = {1: "On land the river took: now under water all year",
         2: "On land the river took: now flooded part of the year",
         5: "In the river's wet belt since the 1980s"}
WHERE_ORDER = [1, 2, 5]


def km_xy(lon, lat):
    return np.column_stack([np.asarray(lon) * KX, np.asarray(lat) * KY])


def read_chunk(sources, r0, c0):
    key = (c0 // GRANULE, r0 // GRANULE)
    return sources[key].read(1, window=Window(c0 - key[0] * GRANULE, r0 - key[1] * GRANULE, CHUNK, CHUNK))


def where_class(cls, r, c):
    """Erosion class at points, looking one pixel around each: land taken by the river first, then always-wet ground."""
    h, w = cls.shape
    best = np.zeros(len(r), np.uint8)
    for want in reversed(WHERE_ORDER):
        hit = np.zeros(len(r), bool)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                hit |= cls[np.clip(r + dr, 0, h - 1), np.clip(c + dc, 0, w - 1)] == want
        best[hit] = want
    return best


def polygon_index(feats, lon, lat):
    """Index of the feature each point falls in, -1 for none."""
    geoms = [shape(f["geometry"]) for f in feats]
    tree = shapely.STRtree(geoms)
    out = np.full(len(lon), -1, int)
    p, g = tree.query(shapely.points(np.column_stack([lon, lat])), predicate="within")
    out[p] = g
    return out


def main():
    t0 = time.time()
    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    tehsils = json.loads((DATA_OUT / "admin_units.geojson").read_text(encoding="utf-8"))["features"]
    uc_path = DATA_OUT / "union_councils.geojson"   # optional, from prepare_admin_units.py
    ucs = json.loads(uc_path.read_text(encoding="utf-8"))["features"] if uc_path.exists() else []
    fp = shapely.union_all([shape(f["geometry"]) for f in json.loads((DATA_OUT / "floodplain.geojson").read_text(encoding="utf-8"))["features"]])
    fpj = json.loads((DATA_OUT / "floodplain.json").read_text(encoding="utf-8"))
    route = json.loads((DATA_OUT / "indus_route.json").read_text(encoding="utf-8"))
    tehsil_fx = json.loads((DATA_OUT / "unit_effects.json").read_text(encoding="utf-8"))
    places = json.loads((DATA_OUT / "places.json").read_text(encoding="utf-8"))
    kinds = places["kinds"]

    # ---------------------------------------------------------------- villages near the floodplain
    pl_lon, pl_lat = np.array(places["x"]) / 1e5, np.array(places["y"]) / 1e5
    near_fp = fp.buffer(PLACE_KM / KX + 0.002)
    wide_fp = fp.buffer(2 * PLACE_KM / KX + 0.004)
    shapely.prepare(near_fp)
    shapely.prepare(wide_fp)
    pts = shapely.points(np.column_stack([pl_lon, pl_lat]))
    wide = np.nonzero(shapely.contains(wide_fp, pts))[0]           # every place that can be nearest to ground below
    cand = wide[shapely.contains(near_fp, pts[wide])]                # places whose ground can reach the floodplain
    pos_in_wide = {int(p): i for i, p in enumerate(wide)}
    cand_set = np.zeros(len(wide), bool)
    cand_set[[pos_in_wide[int(p)] for p in cand]] = True
    tree = cKDTree(km_xy(pl_lon[wide], pl_lat[wide]))
    print(f"places: {len(cand):,} within {PLACE_KM} km of the floodplain, {len(wide):,} considered; {time.time() - t0:.0f}s", flush=True)

    n = len(wide)
    V = {k: np.zeros(n) for k in ("area", "lost_perm", "lost_seas", "recent", "bld", "bld_perm")}

    def nearest(lon, lat):
        """Index into `wide` of the nearest named place within PLACE_KM, -1 for none."""
        d, i = tree.query(km_xy(lon, lat), distance_upper_bound=PLACE_KM)
        return np.where(np.isfinite(d), i, -1)

    # Each village's ground: points on a grid in a disc around it that are nearest to it
    step_lat = GRID_DEG
    off = np.arange(-PLACE_KM, PLACE_KM + 1e-9, GRID_DEG * KY)
    oy, ox = np.meshgrid(off, off, indexing="ij")
    disc = (ox ** 2 + oy ** 2) <= PLACE_KM ** 2
    ox, oy = ox[disc], oy[disc]
    for i in np.nonzero(cand_set)[0]:
        lon, lat = pl_lon[wide[i]], pl_lat[wide[i]]
        kx = KX  # constant along the plains, fine at village scale
        g_lon, g_lat = lon + ox / kx, lat + oy / KY
        own = nearest(g_lon, g_lat) == i
        cell_km2 = (GRID_DEG * KY) ** 2
        V["area"][i] = own.sum() * cell_km2
    print(f"village ground done; {time.time() - t0:.0f}s", flush=True)

    # ---------------------------------------------------------------- erosion raster: land lost, buildings, places in path
    bld = np.load(CACHE / "buildings.npz")["xy"]
    bld = bld[shapely.contains(near_fp, shapely.points(bld))] if len(bld) else bld
    print(f"buildings near the floodplain: {len(bld):,}", flush=True)

    fp_bbox = near_fp.bounds
    pois = []
    for layer in ("pois", "pois_a"):
        df = osm_read(layer, fp_bbox, ["fclass", "name"])
        df = df[df.fclass.isin(EDU | HEALTH)]
        c = shapely.centroid(df.geometry.values)
        keep = shapely.contains(near_fp, c)
        pois.append(df[keep].assign(geometry=c[keep]))
    pois = pd.concat(pois, ignore_index=True)
    poi_xy = shapely.get_coordinates(pois.geometry.values)
    poi_kind = np.where(pois.fclass.isin(HEALTH), "Health facility", "School")
    poi_name = [str(v).strip() if isinstance(v, str) and v.strip() else ("Unnamed health facility" if k == "Health facility" else "Unnamed school")
                for v, k in zip(pois.name, poi_kind)]
    print(f"OSM schools and health facilities near the floodplain: {len(pois):,}", flush=True)

    cand_xy = np.column_stack([pl_lon[cand], pl_lat[cand]])
    pt_xy = np.vstack([cand_xy, poi_xy]) if len(poi_xy) else cand_xy
    pt_where = np.zeros(len(pt_xy), np.uint8)

    def by_chunk(xy):
        r, c = to_rc(xy[:, 0], xy[:, 1])
        ids = r // CHUNK * 100000 + c // CHUNK
        order = np.argsort(ids)
        vals, first = np.unique(ids[order], return_index=True)
        return {int(v): (idx, r[idx] % CHUNK, c[idx] % CHUNK) for v, idx in zip(vals, np.split(order, first[1:]))}

    bld_chunks, pt_chunks = by_chunk(bld), by_chunk(pt_xy)
    sources = {key: rasterio.open(ROOT / "erosion" / f"erosion_{name}_v1_5_2024.tif") for key, name in GRANULES.items()}
    w, s, e, nn = fp.bounds
    r_lo, c_lo = to_rc(w, nn)
    r_hi, c_hi = to_rc(e, s)
    n_chunks = 0
    for cr in range(int(r_lo) // CHUNK, int(r_hi) // CHUNK + 1):
        for cc in range(int(c_lo) // CHUNK, int(c_hi) // CHUNK + 1):
            r0, c0 = cr * CHUNK, cc * CHUNK
            bb = box(LON0 + c0 * RES, LAT0 - (r0 + CHUNK) * RES, LON0 + (c0 + CHUNK) * RES, LAT0 - r0 * RES)
            if not fp.intersects(bb):
                continue
            cls = read_chunk(sources, r0, c0)
            n_chunks += 1
            cid = cr * 100000 + cc
            rows, cols = np.nonzero((cls == 1) | (cls == 2))
            if len(rows):
                lat = LAT0 - (r0 + rows + 0.5) * RES
                lon = LON0 + (c0 + cols + 0.5) * RES
                km2 = (RES * 111.320 * np.cos(np.radians(lat))) * (RES * 110.574)
                who = nearest(lon, lat)
                ok = who >= 0
                perm = cls[rows, cols] == 1
                np.add.at(V["lost_perm"], who[ok & perm], km2[ok & perm])
                np.add.at(V["lost_seas"], who[ok & ~perm], km2[ok & ~perm])
            if cid in bld_chunks:
                idx, r, c = bld_chunks[cid]
                k = cls[r, c]
                on = (k == 1) | (k == 2)
                if on.any():
                    who = nearest(bld[idx[on], 0], bld[idx[on], 1])
                    ok = who >= 0
                    np.add.at(V["bld"], who[ok], 1)
                    np.add.at(V["bld_perm"], who[ok & (k[on] == 1)], 1)
            if cid in pt_chunks:
                idx, r, c = pt_chunks[cid]
                pt_where[idx] = where_class(cls, r, c)
    for src in sources.values():
        src.close()
    print(f"erosion raster: {n_chunks} chunks; {time.time() - t0:.0f}s", flush=True)

    # ---------------------------------------------------------------- year by year, from the monthly record
    with rasterio.open(TIMELINE / f"share_{YEARS[0]}.tif") as src:
        win = from_bounds(*fp.bounds, transform=src.transform).round_offsets().round_lengths()
        tf = src.window_transform(win)
        shape_ = (int(win.height), int(win.width))
    in_fp = features.rasterize([(fp, 1)], out_shape=shape_, transform=tf, fill=0, dtype="uint8").astype(bool)
    rr, cc = np.nonzero(in_fp)
    c_lon = tf.c + (cc + 0.5) * tf.a
    c_lat = tf.f + (rr + 0.5) * tf.e
    c_km2 = (abs(tf.a) * 111.320 * np.cos(np.radians(c_lat))) * (abs(tf.e) * 110.574)
    c_dist = polygon_index(districts, c_lon, c_lat)
    c_who = nearest(c_lon, c_lat)
    river = {}
    for y in YEARS:
        with rasterio.open(TIMELINE / f"share_{y}.tif") as src:
            a = src.read(window=win)[:, rr, cc].astype(np.float32)
        a[a > 100] = np.nan
        seen = np.isfinite(a).sum(0)
        med = np.full(a.shape[1], np.nan, np.float32)
        ok = seen >= SEEN_MIN_MONTHS
        med[ok] = np.nanmedian(a[:, ok], axis=0)
        river[y] = med / 100
    cut = {}
    for y in YEARS[1:]:
        after = river.get(y + 1)
        held = np.minimum(river[y], after) if after is not None else river[y]
        gain = np.nan_to_num(held - river[y - 1], nan=0.0)
        cut[y] = np.clip(gain, 0, None) * c_km2
    for y in range(RECENT[0], RECENT[1] + 1):
        ok = c_who >= 0
        np.add.at(V["recent"], c_who[ok], cut[y][ok])
    year_by_district = {}
    for y, v in cut.items():
        sums = np.bincount(c_dist[c_dist >= 0], weights=v[c_dist >= 0], minlength=len(districts))
        for d in np.nonzero(sums)[0]:
            year_by_district.setdefault(int(d), {})[y] = float(sums[d])
    print(f"monthly record: {len(rr):,} floodplain cells, {YEARS[0]}-{YEARS[-1]}; {time.time() - t0:.0f}s", flush=True)

    # ---------------------------------------------------------------- per village
    v_dist = polygon_index(districts, pl_lon[wide], pl_lat[wide])
    v_teh = polygon_index(tehsils, pl_lon[wide], pl_lat[wide])
    v_uc = polygon_index(ucs, pl_lon[wide], pl_lat[wide]) if ucs else np.full(n, -1)
    uc_name = lambda k: ucs[k]["properties"]["name"] if k >= 0 else ""
    lost = V["lost_perm"] + V["lost_seas"]
    with np.errstate(all="ignore"):
        lost_pct = np.where(V["area"] > 0, 100 * lost / V["area"], 0)
        recent_pct = np.where(V["area"] > 0, 100 * V["recent"] / V["area"], 0)
    risk = np.full(n, 4)
    has = (lost * ACRE >= MIN_ACRES) | (V["recent"] * ACRE >= MIN_ACRES)
    risk[has] = 3
    for level, rule in reversed(list(enumerate(RISK_RULES))):
        risk[has & rule(lost_pct, recent_pct)] = level

    # ---------------------------------------------------------------- per district
    fp_metrics = fpj["meta"]["metrics"]
    FM = {m: i for i, m in enumerate(fp_metrics)}
    TM = {m: i for i, m in enumerate(tehsil_fx["metrics"])}
    stops = sorted(route["stops"], key=lambda s: s["km"])
    all_xy = km_xy(pl_lon, pl_lat)
    all_tree = cKDTree(all_xy)
    dist_id = [f["properties"]["id"] for f in districts]
    out = {}
    for a in fpj["areas"]:
        if a["id"] not in dist_id:      # floodplain outside the district files
            continue
        di = dist_id.index(a["id"])
        v = a["v"]
        ero = v[FM["ero_perm"]] + v[FM["ero_seas"]]
        if ero <= 0:
            continue
        mine = np.nonzero(cand_set & (v_dist == di))[0]
        rows = []
        for i in mine:
            if not has[i]:
                continue
            p = wide[i]
            teh = tehsils[v_teh[i]]["properties"]["name"] if v_teh[i] >= 0 else ""
            rows.append([places["n"][p], teh, kinds[places["k"][p]], round(V["area"][i] * ACRE), round(lost[i] * ACRE), round(V["lost_perm"][i] * ACRE),
                         round(V["recent"][i] * ACRE), int(V["bld"][i]), int(V["bld_perm"][i]), int(risk[i]),
                         round(float(pl_lon[p]), 5), round(float(pl_lat[p]), 5), uc_name(v_uc[i])])
        rows.sort(key=lambda r: (r[9], -r[4], -r[6]))
        risk_counts = [int(((risk[mine] == k)).sum()) for k in range(5)]

        # Tehsils: polygon figures from the floodplain analysis, village counts from above
        teh_rows = []
        for f in tehsils:
            P = f["properties"]
            if P["district"] != a["name"] or str(P["id"]) not in tehsil_fx["units"]:
                continue
            t = tehsil_fx["units"][str(P["id"])]
            sel = mine[v_teh[mine] == tehsils.index(f)]
            acres = (t[TM["ero_perm"]] + t[TM["ero_seas"]]) * ACRE
            if not len(sel) and acres < MIN_ACRES:
                continue
            teh_rows.append([P["name"], int(len(sel)), int(has[sel].sum()), round(acres), int((risk[sel] <= 1).sum()), int(t[TM["bldg_ero"]])])
        teh_rows.sort(key=lambda r: -r[3])

        # Places in the river's path: settlements first by their own point, then schools and health facilities
        path = []
        for j in range(len(pt_xy)):
            k = int(pt_where[j])
            if k not in WHERE:
                continue
            if j < len(cand):
                p = cand[j]
                if v_dist[pos_in_wide[int(p)]] != di:
                    continue
                teh = v_teh[pos_in_wide[int(p)]]
                path.append([places["n"][p], "Settlement", tehsils[teh]["properties"]["name"] if teh >= 0 else "", k,
                             round(float(pl_lon[p]), 5), round(float(pl_lat[p]), 5), uc_name(v_uc[pos_in_wide[int(p)]])])
            else:
                q = j - len(cand)
                lon, lat = poi_xy[q]
                if not shape(districts[di]["geometry"]).contains(shapely.Point(lon, lat)):
                    continue
                _, near = all_tree.query(km_xy([lon], [lat])[0])
                uc = polygon_index(ucs, [lon], [lat])[0] if ucs else -1
                path.append([poi_name[q], str(poi_kind[q]), f"near {places['n'][near]}", k, round(float(lon), 5), round(float(lat), 5), uc_name(uc)])
        kind_rank = {"Health facility": 0, "School": 1, "Settlement": 2}
        path.sort(key=lambda r: (WHERE_ORDER.index(r[3]), kind_rank[r[1]], r[0]))

        tol = fpj["meta"]["reach_km"]  # district spans are whole 20 km reaches
        before = [s["name"] for s in stops if s["km"] <= a["km0"] + tol]
        after = [s["name"] for s in stops if s["km"] >= a["km1"] - tol]
        yd = year_by_district.get(di, {})
        out[str(a["id"])] = {
            "name": a["name"], "province": a["province"], "km": [a["km0"], a["km1"]],
            "stretch": [before[-1] if before else None, after[0] if after else None],
            "totals": {
                "acres": round(ero * ACRE), "acres_perm": round(v[FM["ero_perm"]] * ACRE),
                "acres_back": round((v[FM["acc_perm"]] + v[FM["acc_seas"]]) * ACRE),
                "fp_acres": round(v[FM["fp"]] * ACRE),
                "buildings": int(v[FM["bldg_ero"]]), "buildings_near": int(v[FM["bldg_near"]]),
                "crop_acres": round(v[FM["crop_ero"]] * ACRE),
                "villages_near": int(len(mine)), "villages_lost": int((has[mine] & (lost[mine] * ACRE >= MIN_ACRES)).sum()),
                "villages_high": int((risk[mine] <= 1).sum()),
                "recent_acres": round(sum(yd.get(y, 0) for y in range(RECENT[0], RECENT[1] + 1)) * ACRE),
                "in_path": len(path), "in_path_taken": sum(1 for r in path if r[3] in (1, 2)),
            },
            "years": [round(yd.get(y, 0) * ACRE) for y in YEARS[1:]],
            "risk": risk_counts,
            "tehsils": teh_rows,
            "villages": rows,
            "path": path,
        }

    meta = {
        "built": date.today().isoformat(),
        "period": "1984–2024",
        "years": YEARS[1:], "provisional_from": YEARS[-1], "recent": list(RECENT),
        "place_km": PLACE_KM, "min_acres": MIN_ACRES,
        "risk": RISK, "risk_text": RISK_TEXT,
        "where": {str(k): v for k, v in WHERE.items()},
        "buildings": fpj["meta"].get("buildings"),
        "village_fields": ["name", "tehsil", "kind", "area_ac", "lost_ac", "lost_perm_ac", "recent_ac", "bld", "bld_perm", "risk", "lon", "lat", "union_council"],
        "tehsil_fields": ["name", "villages_near", "villages_lost", "lost_ac", "high_risk", "bld"],
        "path_fields": ["name", "kind", "village", "where", "lon", "lat", "union_council"],
        "union_councils": bool(ucs),
    }
    path = DATA_OUT / "district_reports.json"
    path.write_text(json.dumps({"meta": meta, "districts": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path.name}: {len(out)} districts, {path.stat().st_size / 1e3:.0f} KB; {time.time() - t0:.0f}s")
    for d in out.values():
        T = d["totals"]
        print(f"  {d['name']:<22} {T['acres']:>8,} ac  villages near {T['villages_near']:>4}  lost {T['villages_lost']:>4}  high {T['villages_high']:>3}  "
              f"risk {d['risk']}  path {T['in_path']:>3}  recent {T['recent_acres']:>6,} ac")


if __name__ == "__main__":
    main()
