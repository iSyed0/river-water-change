"""District reports: what the Indus took from each district's villages, for the district popup and report window.

There are no official village (mouza) boundaries in this project, so a "village" here is a named place from
GeoNames / OpenStreetMap (frontend/data/places.json), and its ground is the land within PLACE_KM that is closer to it than
to any other named place. Everything else comes from data already in the project:

  Land lost       the erosion raster of analyse_floodplain.py (JRC GSW 1984-2024, 30 m, sustained change only):
                  class 1, land that was dry in the 1980s and is now under water all year (JRC transitions "new
                  permanent" and a rise of SUSTAINED or more in the occurrence-change layer, i.e. green there). Class 2,
                  land now flooded only part of the year, is reported beside it and is NOT counted as lost (the user's
                  decision, 2026-11-20: part-year ground is often still farmed). Each pixel goes to the nearest named
                  place within PLACE_KM; tehsils and union councils are measured inside their boundaries.
  Cropland        ESA WorldCover shares per JRC pixel, cached by analyse_floodplain.py (lc_counts).
  Year by year    the cleaned JRC Monthly History shares of prepare_timeline.py (data/cache/timeline/share_YYYY.tif, cells of
                  0.0025 degrees, ~275 m). A cell's river share for a year is the lowest share of its seen months, i.e.
                  the part under water all year, matching "land lost" above (it was the median until 2026-11-20). Land cut in
                  year Y is new river share that was not river in Y-1 and stays river in Y+1, counted inside the Indus
                  floodplain only. Cells are coarse, so this is an estimate of the trend, not of exact acres. The years
                  can't be added up: ground the river leaves and retakes counts in each year it is retaken. The RECENT
                  figure is therefore the net change over the period, cell by cell (river at its end, not before it).
  Buildings       data/cache/floodplain/buildings.npz (NDMA building server, or OSM when it was offline).
  Places in path  named places, and OSM schools and health facilities, whose point is on (or one pixel from) ground the
                  river took, or on ground that was water in both periods of the record.

Risk of losing more land combines the share of the village's ground already lost since the 1980s and the share cut in
the last five years of the monthly record (RECENT). It is a guide from the satellite record, not a forecast.

Output: frontend/data/district_reports.json

    python backend/pipeline/prepare_district_reports.py   (after analyse_floodplain.py, prepare_places.py, prepare_timeline.py)
"""
import json
import re
import time
from datetime import date

import dsdi_data
from analyse_floodplain import CHUNK, EDU, HEALTH, lc_counts, osm_read
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

from common import CACHE_ROOT, DATA_OUT, GRANULE, GRANULES, LAT0, LON0, RES, ROOT, granule_path

PLACE_KM = 3
KX, KY = 96.4, 110.574      # km per degree along the Indus plains, for distances
ACRE = 247.105              # acres per km2
GRID_DEG = 0.001            # sampling step for each village's ground
YEARS = list(range(2000, 2022))
RECENT = (2016, 2020)       # "the last five years" of the monthly record whose cut can be confirmed by the year after
SEEN_MIN_MONTHS = 3
TIMELINE = CACHE_ROOT / "timeline"
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
         2: "On ground now flooded part of the year (not counted as lost)",
         5: "In the river's wet belt since the 1980s"}
WHERE_ORDER = [1, 2, 5]
OUTSIDE_TEHSILS = "Outside this district's tehsils (the tehsil and district maps differ at their edges)"
OUTSIDE_UCS = "Outside the union council map"
EMB_STEP = 0.00015          # embankments are measured in steps of ~15 m
EMB_MIN_KM = 0.03           # a stretch counts as reached by the river from one pixel (~30 m) of taken land


RIVER_NAMES = re.compile(r"^(upper |lower )?(indus|chenab|jhelum|ravi|sutlej|kabul|swat)( river)?$", re.I)


def embankment_label(P):
    """(name, river, kind) of a DSDI protection band. The layer stores the river in `name` for many records and puts
    a note before some descriptions, e.g. "Encroached (Nara Loop Bund Mile 0/0 to 0/6)"."""
    text = lambda f: " ".join(str(P.get(f) or "").split())
    kind = (text("type") or "embankment").capitalize()
    name_field = text("name")
    river = text("river") or (name_field if RIVER_NAMES.match(name_field) else "")
    label = text("descrption")
    note = ""
    m = re.match(r"^(encroached|washed out|damaged|breached)\s*(?:\((.+)\))?\s*$", label, re.I)
    if m:
        label, note = m.group(2) or "", m.group(1).lower()
    label = label or text("name_str") or ("" if RIVER_NAMES.match(name_field) else name_field)
    if not label:
        label = f"Unnamed {kind.lower()}" + (f" {text('inven_code')}" if text("inven_code") else "")
    return label + (f" (marked {note})" if note else ""), river, kind


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
    # lost_perm, bld: land lost (class 1) and buildings on it; lost_part, bld_part: ground now flooded part of the year
    V = {k: np.zeros(n) for k in ("area", "lost_perm", "lost_part", "recent", "bld", "bld_part")}

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

    # Schools and health facilities: NDMA DSDI registers (dsdi_data.py), far fuller than OpenStreetMap on the Indus plains
    got_s, got_h = dsdi_data.points("schools", near_fp), dsdi_data.points("health", near_fp)
    if got_s is not None and got_h is not None:
        poi_source = "dsdi"
        # Police, rescue and railway stations too, when they have been fetched (dsdi_data.py)
        got = {"schools": got_s, "health": got_h, **{k: dsdi_data.points(k, near_fp) for k in ("police", "rescue", "railway")}}
        got = {k: v for k, v in got.items() if v is not None}
        poi_xy = np.vstack([v[0] for v in got.values()])
        poi_kind = np.array([dsdi_data.POINT_KINDS[k] for k, v in got.items() for _ in range(len(v[0]))])
        poi_name = [f"{n} ({d})" if d else n for v in got.values() for n, d in zip(v[1], v[2])]
    else:
        poi_source = "osm"
        pois = []
        for layer in ("pois", "pois_a"):
            df = osm_read(layer, near_fp.bounds, ["fclass", "name"])
            df = df[df.fclass.isin(EDU | HEALTH)]
            c = shapely.centroid(df.geometry.values)
            keep = shapely.contains(near_fp, c)
            pois.append(df[keep].assign(geometry=c[keep]))
        pois = pd.concat(pois, ignore_index=True)
        poi_xy = shapely.get_coordinates(pois.geometry.values)
        poi_kind = np.where(pois.fclass.isin(HEALTH), "Health facility", "School")
        poi_name = [str(v).strip() if isinstance(v, str) and v.strip() else ("Unnamed health facility" if k == "Health facility" else "Unnamed school")
                    for v, k in zip(pois.name, poi_kind)]
    print(f"schools, health facilities and stations near the floodplain ({poi_source}): {len(poi_xy):,}", flush=True)

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
    # Land lost by union council and by tehsil: every class 1/2 pixel, keyed by its district and the unit it falls in.
    # Districts are burnt in chunk by chunk exactly as analyse_floodplain.py does, so each district's rows add up to
    # its headline acres.
    dgeoms = [shape(f["geometry"]) for f in districts]
    ugeoms = [shape(f["geometry"]) for f in ucs]
    utree = shapely.STRtree(ugeoms) if ugeoms else None
    tgeoms = [shape(f["geometry"]) for f in tehsils]
    ttree = shapely.STRtree(tgeoms)
    # (district index + 1) * 100000 + unit index + 1 (0 outside every unit) ->
    #   [km2 lost, km2 flooded part of the year, buildings on land lost, buildings on part-year ground, km2 cropland lost]
    by_uc, by_teh = {}, {}
    # The same land under DSDI's district lines: where a border follows the river, the line decides which district
    # the land the river took is counted in
    dsdi_d = dsdi_data.districts() or []
    by_dsdi = np.zeros(len(dsdi_d) + 1)

    def tally(store, keys, *cols):
        uk, inv = np.unique(keys, return_inverse=True)
        sums = np.column_stack([np.bincount(inv, c, len(uk)) for c in cols])
        for j in range(len(uk)):
            store.setdefault(int(uk[j]), np.zeros(len(cols)))[:] += sums[j]

    def burn(geoms, tree, bb, tf):
        hits = tree.query(bb) if tree is not None else []
        return (features.rasterize([(geoms[i], int(i) + 1) for i in hits], out_shape=(CHUNK, CHUNK), transform=tf, fill=0, dtype="int32")
                if len(hits) else np.zeros((CHUNK, CHUNK), np.int32))
    sources = {key: rasterio.open(granule_path("erosion", key)) for key in GRANULES}
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
                np.add.at(V["lost_part"], who[ok & ~perm], km2[ok & ~perm])
                crop = lc_counts(r0, c0)[0][rows, cols] / 9 * km2 * perm     # WorldCover cropland share of each lost pixel
            tf = Affine(RES, 0, LON0 + c0 * RES, 0, -RES, LAT0 - r0 * RES)
            dl = features.rasterize([(g, i + 1) for i, g in enumerate(dgeoms) if g.intersects(bb)] or [(box(0, 0, 0, 0), 0)],
                                    out_shape=(CHUNK, CHUNK), transform=tf, fill=0, dtype="int32")
            ul, tl = burn(ugeoms, utree, bb, tf), burn(tgeoms, ttree, bb, tf)
            if len(rows):
                dk = dl[rows, cols].astype(np.int64) * 100000
                z = np.zeros(len(rows))
                for store, unit in ((by_uc, ul), (by_teh, tl)):
                    tally(store, dk + unit[rows, cols], km2 * perm, km2 * ~perm, z, z, crop)
                if dsdi_d:
                    xl = features.rasterize([(g, i + 1) for i, (g, _) in enumerate(dsdi_d) if g.intersects(bb)] or [(box(0, 0, 0, 0), 0)],
                                            out_shape=(CHUNK, CHUNK), transform=tf, fill=0, dtype="int32")
                    by_dsdi += np.bincount(xl[rows, cols], weights=km2 * perm, minlength=len(by_dsdi))
            if cid in bld_chunks:
                idx, r, c = bld_chunks[cid]
                k = cls[r, c]
                on = (k == 1) | (k == 2)
                if on.any():
                    p = (k[on] == 1).astype(float)
                    z = np.zeros(len(p))
                    dk = dl[r[on], c[on]].astype(np.int64) * 100000
                    for store, unit in ((by_uc, ul), (by_teh, tl)):
                        tally(store, dk + unit[r[on], c[on]], z, z, p, 1 - p, z)
                    who = nearest(bld[idx[on], 0], bld[idx[on], 1])
                    ok = who >= 0
                    np.add.at(V["bld"], who[ok & (p == 1)], 1)
                    np.add.at(V["bld_part"], who[ok & (p == 0)], 1)
            if cid in pt_chunks:
                idx, r, c = pt_chunks[cid]
                pt_where[idx] = where_class(cls, r, c)
    print(f"erosion raster: {n_chunks} chunks; {time.time() - t0:.0f}s", flush=True)

    # ---------------------------------------------------------------- embankments (DSDI protection bands)
    def erosion_at(xy):
        """Erosion class of the pixel each point is in (255 outside the floodplain), reading each chunk once."""
        r, c = to_rc(xy[:, 0], xy[:, 1])
        out = np.full(len(xy), 255, np.uint8)
        key = (r // CHUNK) * 100000 + c // CHUNK
        for k in np.unique(key):
            m = key == k
            r0, c0 = int(k // 100000) * CHUNK, int(k % 100000) * CHUNK
            out[m] = read_chunk(sources, r0, c0)[r[m] - r0, c[m] - c0]
        return out

    emb_by_district = {}
    emb = dsdi_data.embankments()
    if emb:
        mids, kms, fids = [], [], []
        for k, (g, _) in enumerate(emb):
            if not g.intersects(near_fp):
                continue
            seg = shapely.segmentize(g, EMB_STEP)
            for part in getattr(seg, "geoms", [seg]):
                c = np.asarray(part.coords)[:, :2]
                if len(c) < 2:
                    continue
                mid = (c[1:] + c[:-1]) / 2
                mids.append(mid)
                kms.append(np.hypot((c[1:, 0] - c[:-1, 0]) * 111.32 * np.cos(np.radians(mid[:, 1])), (c[1:, 1] - c[:-1, 1]) * 110.574))
                fids.append(np.full(len(mid), k))
        mids, kms, fids = np.vstack(mids), np.concatenate(kms), np.concatenate(fids)
        ecls = erosion_at(mids)
        e_dist = polygon_index(districts, mids[:, 0], mids[:, 1])
        e_fp = shapely.contains_xy(fp, mids[:, 0], mids[:, 1])
        taken = ecls == 1       # on land lost: now under water all year
        for (k, d) in {(int(a), int(b)) for a, b in zip(fids[taken], e_dist[taken]) if b >= 0}:
            m = (fids == k) & (e_dist == d)
            km_taken = float(kms[m & taken].sum())
            if km_taken < EMB_MIN_KM:
                continue
            label, river, kind = embankment_label(emb[k][1])
            first = mids[m & taken][0]
            emb_by_district.setdefault(d, []).append([
                label, kind, river, round(km_taken, 2), round(float(kms[m & (ecls == 2)].sum()), 2),
                round(float(kms[m & (ecls == 5)].sum()), 2), round(float(kms[m & e_fp].sum()), 2),
                round(float(first[0]), 5), round(float(first[1]), 5)])
        for rows_ in emb_by_district.values():
            rows_.sort(key=lambda r: -r[3])
        print(f"embankments: {sum(len(v) for v in emb_by_district.values())} stretches on land the river took, "
              f"{kms[taken].sum():.1f} km; {time.time() - t0:.0f}s", flush=True)
    for src in sources.values():
        src.close()

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
        med[ok] = np.nanmin(a[:, ok], axis=0)       # under water in every seen month: all year, as land lost is
        river[y] = med / 100
    cut = {}
    for y in YEARS[1:]:
        after = river.get(y + 1)
        held = np.minimum(river[y], after) if after is not None else river[y]
        gain = np.nan_to_num(held - river[y - 1], nan=0.0)
        cut[y] = np.clip(gain, 0, None) * c_km2
    # The recent figure is the net change over the period, cell by cell: river at the end (held the year after) that was
    # not river the year before it began. Adding up the yearly cuts would count again ground the river left and retook.
    end = np.minimum(river[RECENT[1]], river[RECENT[1] + 1])
    recent_cut = np.clip(np.nan_to_num(end - river[RECENT[0] - 1], nan=0.0), 0, None) * c_km2
    ok = c_who >= 0
    np.add.at(V["recent"], c_who[ok], recent_cut[ok])
    recent_by_district = np.bincount(c_dist[c_dist >= 0], weights=recent_cut[c_dist >= 0], minlength=len(districts))
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
    lost = V["lost_perm"]
    with np.errstate(all="ignore"):
        lost_pct = np.where(V["area"] > 0, 100 * lost / V["area"], 0)
        recent_pct = np.where(V["area"] > 0, 100 * V["recent"] / V["area"], 0)
    risk = np.full(n, 4)
    # Under MIN_ACRES counts as no loss, and the tables show it as 0, so every village listed with land lost is also
    # counted as having lost land (a rounded 0.6 acre would otherwise show as 1 without being counted)
    whole = lambda km2: np.where(km2 * ACRE >= MIN_ACRES, np.round(km2 * ACRE), 0)
    lost_ac, recent_ac = whole(lost), whole(V["recent"])
    lost_part_ac = whole(V["lost_part"])
    lost_any = lost_ac >= MIN_ACRES
    has = lost_any | (recent_ac >= MIN_ACRES)
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
            rows.append([places["n"][p], teh, kinds[places["k"][p]], round(V["area"][i] * ACRE), int(lost_ac[i]), int(lost_part_ac[i]),
                         int(recent_ac[i]), int(V["bld"][i]), int(V["bld_part"][i]), int(risk[i]),
                         round(float(pl_lon[p]), 5), round(float(pl_lat[p]), 5), uc_name(v_uc[i])])
        rows.sort(key=lambda r: (r[9], -r[4], -r[6]))
        risk_counts = [int(((risk[mine] == k)).sum()) for k in range(5)]

        # District sums of the unit tallies (buildings and cropland on land lost; every pixel is in one union council row)
        mine_uc = {k: s for k, s in by_uc.items() if k // 100000 == di + 1}
        dsum = sum(mine_uc.values(), np.zeros(5))
        if abs(dsum[0] - v[FM["ero_perm"]]) * ACRE > max(5, 0.001 * v[FM["ero_perm"]] * ACRE):
            print(f"  check {a['name']}: union council rows {dsum[0] * ACRE:,.0f} ac vs floodplain analysis {v[FM['ero_perm']] * ACRE:,.0f} ac")

        # Tehsils: land and buildings measured inside each tehsil's boundary, village counts from above
        teh_rows = []
        own = []
        for ti, f in enumerate(tehsils):
            P = f["properties"]
            if P["district"] != a["name"]:
                continue
            own.append(ti)
            sel = mine[v_teh[mine] == ti]
            tv = by_teh.get((di + 1) * 100000 + ti + 1, np.zeros(5))
            if not len(sel) and tv[0] * ACRE < MIN_ACRES and tv[2] == 0:
                continue
            teh_rows.append([P["name"], int(len(sel)), int(lost_any[sel].sum()), round(tv[0] * ACRE), int((risk[sel] <= 1).sum()), int(tv[2])])
        teh_rows.sort(key=lambda r: -r[3])
        # Ground and villages the district map puts here but the tehsil map puts in another district's tehsil, or in
        # none (the two boundary files differ at their edges). Listed so every column adds up to the district's totals.
        rest = mine[~np.isin(v_teh[mine], own)]
        own_keys = {(di + 1) * 100000 + ti + 1 for ti in own}
        rest_v = sum((s for k, s in by_teh.items() if k // 100000 == di + 1 and k not in own_keys), np.zeros(5))
        if lost_any[rest].any() or (risk[rest] <= 1).any() or rest_v[0] * ACRE >= MIN_ACRES or rest_v[2]:
            teh_rows.append([OUTSIDE_TEHSILS, int(len(rest)), int(lost_any[rest].sum()), round(rest_v[0] * ACRE), int((risk[rest] <= 1).sum()), int(rest_v[2])])

        # Union councils: measured from their boundaries, no nearest-place step. Every class 1/2 pixel of the district is
        # in exactly one row, so the rows add up to the district's acres (to within rounding)
        uc_rows, tail, small = [], [], np.zeros(4)
        for key, (lk, qk, nb, _, _) in mine_uc.items():
            u = key % 100000 - 1
            if u < 0:
                tail.append([OUTSIDE_UCS, "", round(lk * ACRE), round(qk * ACRE), int(nb), None])
            elif lk * ACRE < MIN_ACRES and nb == 0:
                small += (1, lk, qk, nb)
            else:
                P = ucs[u]["properties"]
                uc_rows.append([P["name"], P.get("tehsil", ""), round(lk * ACRE), round(qk * ACRE), int(nb),
                                int((lost_any[mine] & (v_uc[mine] == u)).sum())])
        uc_rows.sort(key=lambda r: (-r[2], -r[3]))
        n_ucs = len(uc_rows)
        if small[0]:
            tail.append([f"{int(small[0])} other union councils, under 1 acre lost each", "", round(small[1] * ACRE), round(small[2] * ACRE), int(small[3]), None])
        uc_rows += tail

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
        kind_rank = {"Health facility": 0, "Rescue station": 1, "Police station": 2, "School": 3, "Railway station": 4, "Settlement": 5}
        path.sort(key=lambda r: (WHERE_ORDER.index(r[3]), kind_rank[r[1]], r[0]))

        emb_rows = emb_by_district.get(di, [])
        border = None
        if dsdi_d:
            A = dgeoms[di]
            over = [(A.intersection(g).area, k) for k, (g, _) in enumerate(dsdi_d) if A.intersects(g)]
            if over:
                _, k = max(over)
                g, name = dsdi_d[k]
                border = {"name": name, "match": round(A.intersection(g).area / A.union(g).area, 3), "acres": round(float(by_dsdi[k + 1]) * ACRE)}

        tol = fpj["meta"]["reach_km"]  # district spans are whole 20 km reaches
        before = [s["name"] for s in stops if s["km"] <= a["km0"] + tol]
        after = [s["name"] for s in stops if s["km"] >= a["km1"] - tol]
        yd = year_by_district.get(di, {})
        out[str(a["id"])] = {
            "name": a["name"], "province": a["province"], "km": [a["km0"], a["km1"]],
            "stretch": [before[-1] if before else None, after[0] if after else None],
            "totals": {
                # Land lost = class 1 only (dry then, under water all year now); part-year ground is reported beside it
                "acres": round(v[FM["ero_perm"]] * ACRE), "acres_part": round(v[FM["ero_seas"]] * ACRE),
                "acres_back": round(v[FM["acc_perm"]] * ACRE),      # the mirror: under water all year then, dry land now
                "fp_acres": round(v[FM["fp"]] * ACRE),
                "buildings": int(dsum[2]), "buildings_part": int(dsum[3]), "buildings_near": int(v[FM["bldg_near"]]),
                "crop_acres": round(dsum[4] * ACRE),
                "villages_near": int(len(mine)), "villages_lost": int(lost_any[mine].sum()),
                "villages_high": int((risk[mine] <= 1).sum()),
                "recent_acres": round(float(recent_by_district[di]) * ACRE),
                "in_path": len(path), "in_path_taken": sum(1 for r in path if r[3] == 1),
                "ucs_lost": n_ucs,
                "path_schools": sum(1 for r in path if r[1] == "School"), "path_health": sum(1 for r in path if r[1] == "Health facility"),
                "path_stations": sum(1 for r in path if r[1].endswith("station")),
                "emb_n": len(emb_rows), "emb_km": round(sum(r[3] for r in emb_rows), 1),
            },
            "ucs": uc_rows,
            "embankments": emb_rows,
            "border": border,
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
        # lost = land lost: dry in the 1980s, under water all year now. part = flooded part of the year, not counted as lost.
        "land_lost": "class 1 only: dry in the 1980s, under water all year in 2024, occurrence change +25 or more",
        "village_fields": ["name", "tehsil", "kind", "area_ac", "lost_ac", "part_ac", "recent_ac", "bld", "bld_part", "risk", "lon", "lat", "union_council"],
        "tehsil_fields": ["name", "villages_near", "villages_lost", "lost_ac", "high_risk", "bld"],
        "path_fields": ["name", "kind", "village", "where", "lon", "lat", "union_council"],
        "uc_fields": ["name", "tehsil", "lost_ac", "part_ac", "bld", "villages_lost"],
        "emb_fields": ["name", "type", "river", "km_on_land_lost", "km_part_year", "km_wet_belt", "km_in_floodplain", "lon", "lat"],
        "facilities": poi_source,           # "dsdi" or "osm": where the schools and health facilities come from
        "embankments": bool(emb), "dsdi_districts": bool(dsdi_d),
        "union_councils": bool(ucs),
    }
    path = DATA_OUT / "district_reports.json"
    path.write_text(json.dumps({"meta": meta, "districts": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path.name}: {len(out)} districts, {path.stat().st_size / 1e3:.0f} KB; {time.time() - t0:.0f}s")
    for d in out.values():
        T = d["totals"]
        print(f"  {d['name']:<22} {T['acres']:>8,} ac lost  {T['acres_part']:>8,} ac part-year  bld {T['buildings']:>5,}/{T['buildings_part']:>5,}  villages near {T['villages_near']:>4}  lost {T['villages_lost']:>4}  high {T['villages_high']:>3}  "
              f"risk {d['risk']}  path {T['in_path']:>3}  recent {T['recent_acres']:>6,} ac")


if __name__ == "__main__":
    main()
