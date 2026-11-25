"""Bank-erosion outlook: how likely each piece of dry floodplain land is to be lost to the Indus within HORIZON years.

Not a river model. It learns from the yearly water record how often land in a given situation was lost, and applies
those rates to the land as it is now ("if the river keeps behaving as it has"). For every dry 30 m pixel in the Indus
floodplain, in a start year t:

  distance to permanent water   how far the river's all-year channel is (m), in year t
  distance to any water          how far the nearest water is, seasonal included: side channels the river may take.
                                 Only water seen in at least 2 of the last 3 years counts, so a flood year (2010)
                                 does not make all the land look close to the river
  recent activity                of the land within ~1 km that was dry ACT_YEARS years before t, the share the
                                 river has since taken: where the bank is moving now

(Tried and dropped, 2026-11-23: requiring the channel and the activity to last 2 of 3 years. It fitted the total
better but ranked the land worse, AUC 0.83-0.84 against 0.86.)

Each pixel falls in one cell of these three features (BINS). "Lost" means the same as land lost in the reports: dry at
t, under water all year at t + HORIZON and under water the year before too (so one wet year does not count). The share
of land lost in each cell, over the training years, is that cell's chance of loss. A cell with little land borrows
from the overall rate (SMOOTH).

Hindcast (the test): learn from start year TRAIN_TEST (outcome 10 years on), apply at VALID_START and compare with what
the river actually took by VALID_START + HORIZON. Reported: how well the chances rank the land that was lost (AUC), the
acres expected against the acres lost, overall and by district, and how much of the lost land fell in the high and
medium zones.

Inputs: data/earth_engine/yearly/ (yearly_water_export.js: JRC Yearly History 2000-2021; recent_water_yearly_export.js:
Landsat + Sentinel-2 2021-2025, when present), the JRC maximum extent (pixels never seen wet come through the yearly
export as 0, the same as "no data", and are dry), frontend/data/floodplain.geojson, districts.geojson.

Outputs:
  data/cache/outlook/counts.npz        land and lost land per start year, district and feature cell
  data/cache/outlook/validation.json   the hindcast
  data/derived/outlook/cells_<year>.tif the feature cell of every dry pixel in the latest year (0 = not dry land)

    python backend/pipeline/prepare_erosion_outlook.py
"""
import glob
import json
import math
import re
import time
from concurrent.futures import ProcessPoolExecutor

import floodplain_common  # noqa: F401  (clears a conflicting PROJ path before rasterio loads)
from floodplain_common import mosaic

import numpy as np
import rasterio
import shapely
from affine import Affine
from rasterio import features
from rasterio.windows import Window
from scipy.ndimage import distance_transform_edt, uniform_filter
from shapely.geometry import shape

from common import CACHE_ROOT, DATA_OUT, DERIVED_DIR, EARTH_ENGINE, LAT0, LON0, RES

YEARLY = EARTH_ENGINE / "yearly"
CACHE = CACHE_ROOT / "outlook"
OUT = DERIVED_DIR / "outlook"
HORIZON = 10
ACT_YEARS = 3
ACT_PX = 67                 # window for recent activity, pixels: about 2 km across
TRAIN_TEST = (2003,)        # hindcast: learn from these start years (outcomes up to 2013) ...
VALID_START = 2011          # ... and test from this one (outcome 2021)
CHUNK, PAD = 2000, 110      # PAD covers the farthest distance cell (3 km)
BINS = {
    "dperm": [60, 120, 250, 500, 1000, 2000, 3000],    # m to permanent water: 8 cells
    "dwet": [60, 250, 1000],                           # m to any water: 4 cells
    "act": [0.002, 0.01, 0.03, 0.08],                  # recent share taken: 5 cells
}
NB = (len(BINS["dperm"]) + 1) * (len(BINS["dwet"]) + 1) * (len(BINS["act"]) + 1)
SMOOTH = 400                # pixels of the overall rate added to every cell
ZONES = {"high": 0.12, "medium": 0.06}  # calibrated chance of loss within HORIZON years
DRY, SEAS, PERM = 1, 2, 3


# ---------------------------------------------------------------- yearly water
def yearly_files():
    """year -> (path, band, row offset, col offset) on the JRC mosaic; JRC up to 2021, the recent export after it."""
    out, overlap = {}, {}
    for p in sorted(glob.glob(str(YEARLY / "*.tif"))):
        recent = "recent" in p.lower()
        with rasterio.open(p) as d:
            r0, c0 = round((LAT0 - d.transform.f) / RES), round((d.transform.c - LON0) / RES)
            for b, name in enumerate(d.descriptions, 1):
                y = int(re.sub(r"\D", "", name or ""))
                entry = (p, b, r0, c0, d.height, d.width)
                if recent and y <= 2021:
                    overlap.setdefault(y, []).append(entry)    # kept for the comparison with JRC
                elif not recent or y not in out:
                    out.setdefault(y, []).append(entry)
    return out, overlap


def read_years(files, years, r0, r1, c0, c1):
    """(years, rows, cols) of raw yearly classes, 0 where no file covers the pixel."""
    out = np.zeros((len(years), r1 - r0, c1 - c0), np.uint8)
    for i, y in enumerate(years):
        for p, b, fr, fc, h, w in files[y]:
            ra, rz, ca, cz = max(r0, fr), min(r1, fr + h), max(c0, fc), min(c1, fc + w)
            if ra >= rz or ca >= cz:
                continue
            with rasterio.open(p) as d:
                a = d.read(b, window=Window(ca - fc, ra - fr, cz - ca, rz - ra))
            out[i, ra - r0:rz - r0, ca - c0:cz - c0] = np.where(a == 255, 0, a)
    return out


def clean(raw, ever_wet):
    """Dry, seasonal or permanent every year: never-wet pixels are dry; a year with no clear image takes the year
    before it (or, at the start of the record, the first year seen)."""
    s = np.where((raw == 0) & ~ever_wet, DRY, raw).astype(np.uint8)
    for i in range(1, len(s)):
        s[i] = np.where(s[i] == 0, s[i - 1], s[i])
    for i in range(len(s) - 2, -1, -1):
        s[i] = np.where(s[i] == 0, s[i + 1], s[i])
    return s


# ---------------------------------------------------------------- features
def cells(state, t_idx, sampling):
    """Feature cell (1..NB) of every pixel dry in year t_idx, 0 elsewhere."""
    s = state[t_idx]
    dperm = distance_transform_edt(s != PERM, sampling=sampling)
    wet_years = (state[t_idx - 2:t_idx + 1] >= SEAS).sum(0)
    dwet = distance_transform_edt(wet_years < 2, sampling=sampling)
    before = state[t_idx - ACT_YEARS]
    taken = uniform_filter(((before == DRY) & (s == PERM)).astype(np.float32), ACT_PX)
    land = uniform_filter((before == DRY).astype(np.float32), ACT_PX)
    act = np.where(land > 0.02, taken / np.maximum(land, 1e-6), 0)
    b = (np.digitize(dperm, BINS["dperm"]) * (len(BINS["dwet"]) + 1) + np.digitize(dwet, BINS["dwet"])) * (len(BINS["act"]) + 1) \
        + np.digitize(act, BINS["act"])
    return np.where(s == DRY, b + 1, 0).astype(np.uint8)


def lost(state, end_idx):
    return (state[end_idx] == PERM) & (state[end_idx - 1] >= SEAS)


# ---------------------------------------------------------------- one chunk
def work(job):
    (rb, cb), years, starts, apply_year, files, nd = job
    r0, c0 = rb * CHUNK, cb * CHUNK
    pr0, pc0 = r0 - PAD, c0 - PAD
    shp = (CHUNK + 2 * PAD, CHUNK + 2 * PAD)
    tf = Affine(RES, 0, LON0 + pc0 * RES, 0, -RES, LAT0 - pr0 * RES)
    fp = shapely.union_all([shape(f["geometry"]) for f in json.loads((DATA_OUT / "floodplain.geojson").read_text(encoding="utf-8"))["features"]])
    in_fp = features.rasterize([(fp, 1)], out_shape=shp, transform=tf, fill=0, dtype="uint8").astype(bool)
    core = (slice(PAD, PAD + CHUNK), slice(PAD, PAD + CHUNK))
    if not in_fp[core].any():
        return None
    raw = read_years(files, years, pr0, pr0 + shp[0], pc0, pc0 + shp[1])
    ever = mosaic("extent", pr0, pr0 + shp[0], pc0, pc0 + shp[1], fill=0) == 1
    state = clean(raw, ever)
    # Outside the floodplain counts as dry ground for distances, but is never counted itself
    dists = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    box = shapely.box(LON0 + c0 * RES, LAT0 - (r0 + CHUNK) * RES, LON0 + (c0 + CHUNK) * RES, LAT0 - r0 * RES)
    near = [(shape(f["geometry"]), f["properties"]["id"]) for f in dists if shape(f["geometry"]).intersects(box)]
    dist = features.rasterize(near, out_shape=(CHUNK, CHUNK), transform=Affine(RES, 0, LON0 + c0 * RES, 0, -RES, LAT0 - r0 * RES),
                              fill=0, dtype="int32") if near else np.zeros((CHUNK, CHUNK), np.int32)
    lat = LAT0 - (r0 + np.arange(CHUNK) + 0.5) * RES
    px_km2 = ((RES * 110.574) * (RES * 111.320 * np.cos(np.radians(lat))))[:, None] * np.ones((1, CHUNK))
    mid = math.radians(LAT0 - (r0 + CHUNK / 2) * RES)
    sampling = (RES * 110574, RES * 111320 * math.cos(mid))
    yi = {y: i for i, y in enumerate(years)}
    inside = in_fp[core]
    counts = np.zeros((len(starts), nd + 1, NB + 1, 4))   # land px, lost px, land km2, lost km2
    for k, t in enumerate(starts):
        c = cells(state, yi[t], sampling)[core]
        sel = inside & (c > 0)
        ev = lost(state, yi[t + HORIZON])[core] & sel
        key = dist[sel] * (NB + 1) + c[sel]
        n = (nd + 1) * (NB + 1)
        counts[k, :, :, 0] = np.bincount(key, minlength=n).reshape(nd + 1, NB + 1)
        counts[k, :, :, 2] = np.bincount(key, weights=px_km2[sel], minlength=n).reshape(nd + 1, NB + 1)
        kev = dist[ev] * (NB + 1) + c[ev]
        counts[k, :, :, 1] = np.bincount(kev, minlength=n).reshape(nd + 1, NB + 1)
        counts[k, :, :, 3] = np.bincount(kev, weights=px_km2[ev], minlength=n).reshape(nd + 1, NB + 1)
    now = cells(state, yi[apply_year], sampling)[core] * inside
    return (rb, cb), counts, now


# ---------------------------------------------------------------- the model and its test
def model(counts):
    """Chance of loss per feature cell from (cells, 4) counts."""
    land, ev = counts[..., 0], counts[..., 1]
    p0 = ev.sum() / max(land.sum(), 1)
    return (ev + SMOOTH * p0) / (land + SMOOTH)


def calibrate(p, land_km2, rate):
    """Chances scaled so the land expected to be lost is `rate` of the dry land given."""
    return np.clip(p * rate * land_km2.sum() / max((p * land_km2).sum(), 1e-12), 0, 1)


def auc(p, land, ev):
    """Area under the ROC curve from per-cell chances and counts (ties within a cell count half)."""
    order = np.argsort(-p)
    pos, neg = ev[order], (land - ev)[order]
    P, N = pos.sum(), neg.sum()
    if not P or not N:
        return float("nan")
    neg_above = np.cumsum(neg) - neg          # negatives with a higher chance than this cell
    return float((pos * (N - neg_above - neg / 2)).sum() / (P * N))


def zone_stats(p, land_km2, ev_km2):
    """Per zone: its acres, the acres lost inside it, the share of all lost land it caught, the share of it lost."""
    out = {}
    for name, cut in ZONES.items():
        z = p >= cut
        out[name] = {"zone_acres": round(float(land_km2[z].sum()) * 247.105), "lost_acres_in_zone": round(float(ev_km2[z].sum()) * 247.105),
                     "share_of_lost": round(float(ev_km2[z].sum() / max(ev_km2.sum(), 1e-9)), 3),
                     "share_of_zone_lost": round(float(ev_km2[z].sum() / max(land_km2[z].sum(), 1e-9)), 3)}
    return out


def main():
    t0 = time.time()
    files, overlap = yearly_files()
    years = sorted(files)
    last = years[-1]
    apply_year = last
    starts = sorted({*TRAIN_TEST, VALID_START, *range(TRAIN_TEST[0], last - HORIZON + 1, 2)} & set(range(years[0] + ACT_YEARS, last - HORIZON + 1)))
    print(f"yearly water {years[0]}-{last}; start years {starts}; outlook from {apply_year} to {apply_year + HORIZON}", flush=True)
    if overlap:
        print(f"  the recent export also has {sorted(overlap)}, kept out: compare it with JRC before trusting the join", flush=True)

    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    nd = max(f["properties"]["id"] for f in districts)
    fp = shapely.union_all([shape(f["geometry"]) for f in json.loads((DATA_OUT / "floodplain.geojson").read_text(encoding="utf-8"))["features"]])
    w, s, e, n = fp.bounds
    jobs = []
    for rb in range(int((LAT0 - n) / RES) // CHUNK, int((LAT0 - s) / RES) // CHUNK + 1):
        for cb in range(int((w - LON0) / RES) // CHUNK, int((e - LON0) / RES) // CHUNK + 1):
            b = shapely.box(LON0 + cb * CHUNK * RES, LAT0 - (rb + 1) * CHUNK * RES, LON0 + (cb + 1) * CHUNK * RES, LAT0 - rb * CHUNK * RES)
            if fp.intersects(b):
                jobs.append(((rb, cb), years, starts, apply_year, files, nd))
    print(f"  {len(jobs)} chunks", flush=True)

    counts = np.zeros((len(starts), nd + 1, NB + 1, 4))
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    rows = [j[0][0] for j in jobs]
    cols = [j[0][1] for j in jobs]
    R0, C0 = min(rows) * CHUNK, min(cols) * CHUNK
    H, W = (max(rows) + 1) * CHUNK - R0, (max(cols) + 1) * CHUNK - C0
    out_path = OUT / f"cells_{apply_year}.tif"
    with rasterio.open(out_path, "w", driver="GTiff", width=W, height=H, count=1, dtype="uint8", nodata=0, crs="EPSG:4326",
                       transform=Affine(RES, 0, LON0 + C0 * RES, 0, -RES, LAT0 - R0 * RES), tiled=True, compress="deflate",
                       BIGTIFF="IF_SAFER", SPARSE_OK=True) as dst:
        dst.update_tags(year=apply_year, horizon=HORIZON, bins=json.dumps(BINS), act_years=ACT_YEARS)
        with ProcessPoolExecutor(3) as ex:
            for i, res in enumerate(ex.map(work, jobs), 1):
                if res is not None:
                    (rb, cb), cn, now = res
                    counts += cn
                    dst.write(now, 1, window=Window(cb * CHUNK - C0, rb * CHUNK - R0, CHUNK, CHUNK))
                if i % 10 == 0:
                    print(f"  {i}/{len(jobs)} chunks; {time.time() - t0:.0f} s", flush=True)
    np.savez(CACHE / "counts.npz", counts=counts, starts=np.array(starts), bins=json.dumps(BINS), horizon=HORIZON)

    # ---- hindcast
    si = {t: i for i, t in enumerate(starts)}
    train = counts[[si[t] for t in TRAIN_TEST]].sum(axis=(0, 1))     # all districts
    p_raw = model(train)
    v = counts[si[VALID_START]]                                        # (districts, cells, 4)
    land, ev, land_km2, ev_km2 = (v[..., i] for i in range(4))
    # How much land goes in a decade is steady (2.3-2.5% of the dry floodplain), but the features of a single start year
    # are not (2011 still held the 2010 flood). So the chances are scaled to give the training years' rate of loss on
    # the land as it is at the start: the model says where, the long-run rate says how much.
    rate = train[:, 3].sum() / train[:, 2].sum()
    p = calibrate(p_raw, land_km2.sum(0), rate)
    tot = lambda a: float(a.sum())
    expected = (p[None, :] * land_km2).sum(1)
    observed = ev_km2.sum(1)
    by_d = []
    names = {f["properties"]["id"]: (f["properties"]["name"], f["properties"]["province"]) for f in districts}
    for d in np.argsort(-observed):
        if observed[d] * 247.105 < 200 and expected[d] * 247.105 < 200:
            continue
        a = auc(p, land[d], ev[d])
        by_d.append({"id": int(d), "name": names.get(int(d), ("?", ""))[0], "lost_acres": round(observed[d] * 247.105),
                     "expected_acres": round(expected[d] * 247.105), "auc": None if math.isnan(a) else round(a, 3)})
    # The same test with distance to permanent water alone, to show what the other two features add
    nd_, nw, na = len(BINS["dperm"]) + 1, len(BINS["dwet"]) + 1, len(BINS["act"]) + 1
    tr_d = train[1:].reshape(nd_, nw * na, 4).sum(1)
    p_d = model(tr_d)
    v_all = v.sum(0)[1:].reshape(nd_, nw * na, 4).sum(1)
    result = {
        "built": time.strftime("%Y-%m-%d"), "horizon": HORIZON, "train_starts": list(TRAIN_TEST), "valid_start": VALID_START,
        "valid_end": VALID_START + HORIZON, "years": [years[0], last],
        "auc": round(auc(p, land.sum(0), ev.sum(0)), 3),
        "auc_distance_only": round(auc(p_d, v_all[:, 0], v_all[:, 1]), 3),
        "lost_acres": round(tot(ev_km2) * 247.105), "expected_acres": round(tot(p[None, :] * land_km2) * 247.105),
        "expected_acres_uncalibrated": round(tot(p_raw[None, :] * land_km2) * 247.105), "decade_rate": round(float(rate), 4),
        "dry_acres": round(tot(land_km2) * 247.105),
        "zones": zone_stats(p, land_km2.sum(0), ev_km2.sum(0)),
        "districts": by_d,
    }
    (CACHE / "validation.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "districts"}, indent=1))
    print(f"{'district':22} {'lost':>8} {'expected':>9}  auc")
    for d in by_d[:30]:
        print(f"{d['name']:22} {d['lost_acres']:>8,} {d['expected_acres']:>9,}  {d['auc']}")
    print(f"done; {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
