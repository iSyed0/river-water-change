"""Indus floodplain geometry: cross-sections every 5 km and the floodplain edges.

The floodplain is the land the satellite record shows the river actually reaching: on each
transect the JRC maximum-extent envelope (1984-2024) is followed outward from the main channel,
tolerating dry gaps of up to GAP_M, until the ground rises RISE_M above the channel or the
HALF_M limit is reached. Edges are then median-filtered along the river so a single canal or
tributary cannot widen the floodplain.

Output: build/floodplain/sections.npz, read by analyse_floodplain.py

    python scripts/prepare_floodplain.py
"""
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter

from common import DATA_OUT
from floodplain_common import CACHE, DEM_DIR, fetch_dem, sample_dem, sample_mosaic

SEC_KM = 5
HALF_M = 30_000     # transects run this far either side of the river
STEP_M = 30
GAP_M = 1_500       # dry ground tolerated inside the floodplain
RISE_M = 15         # ground this far above the channel ends the floodplain
BELT_GAP_M = 300    # gaps smaller than this are inside one water belt
MIN_HALF_M = 400

KM_LON = 111.320
KM_LAT = 110.574


def tangents(xy):
    lat = np.radians(xy[:, 1])
    dx = np.gradient(xy[:, 0]) * KM_LON * np.cos(lat)
    dy = np.gradient(xy[:, 1]) * KM_LAT
    n = np.hypot(dx, dy)
    return dx / n, dy / n


def runs(mask):
    """Start and end indices of True runs, end exclusive."""
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1)


def close_gaps(mask, gap):
    out = mask.copy()
    s, e = runs(mask)
    for a, b in zip(e[:-1], s[1:]):
        if b - a <= gap:
            out[a:b] = True
    return out


def main_belt(water):
    """The water belt the transect is really crossing: wide and close to the route line."""
    s, e = runs(close_gaps(water, BELT_GAP_M // STEP_M))
    if not len(s):
        return None
    mid = len(water) // 2
    score = [(water[a:b].sum() * STEP_M) / (1 + abs((a + b) / 2 - mid) * STEP_M / 4000) for a, b in zip(s, e)]
    i = int(np.argmax(score))
    return int(s[i]), int(e[i])


def edge(ext, ground, start, step, channel):
    """Walk outward from the belt edge along the envelope; returns the last floodplain index."""
    gap, last, i = 0, start, start + step
    limit = GAP_M // STEP_M
    while 0 <= i < len(ext):
        if ground[i] > channel + RISE_M:
            break
        if ext[i]:
            last, gap = i, 0
        else:
            gap += 1
            if gap > limit:
                break
        i += step
    return last


def main():
    route = json.loads((DATA_OUT / "indus_route.json").read_text(encoding="utf-8"))
    R = np.array(route["coords"], float)
    S = np.column_stack([gaussian_filter1d(R[:, i], 4, mode="nearest") for i in (0, 1)])
    kms = np.arange(0, len(R), SEC_KM)
    tx, ty = tangents(S)
    offs = np.arange(-HALF_M, HALF_M + 1, STEP_M, dtype=float)

    # Transect i, sample j. Offsets are negative on the left bank, looking downstream.
    lat_c = S[kms, 1]
    lons = S[kms, 0, None] + (ty[kms, None] * offs / 1000) / (KM_LON * np.cos(np.radians(lat_c))[:, None])
    lats = lat_c[:, None] - (tx[kms, None] * offs / 1000) / KM_LAT

    DEM_DIR.mkdir(parents=True, exist_ok=True)
    tiles = {(int(a), int(b)) for a, b in zip(np.floor(lats).ravel(), np.floor(lons).ravel())}
    print(f"{len(kms)} sections; DEM tiles needed: {len(tiles)}", flush=True)
    with ThreadPoolExecutor(8) as ex:
        for i, _ in enumerate(ex.map(fetch_dem, sorted(tiles)), 1):
            if i % 10 == 0 or i == len(tiles):
                print(f"  DEM {i}/{len(tiles)}", flush=True)

    left, right, chan = [], [], []
    for i in range(len(kms)):
        lon, lat = lons[i], lats[i]
        ext = sample_mosaic("extent", lon, lat) == 1
        tr = sample_mosaic("transitions", lon, lat)
        then, now = np.isin(tr, (1, 3, 4, 6, 7, 8)), np.isin(tr, (1, 2, 4, 5, 7, 8))
        ground = sample_dem(lon, lat)
        ground = np.where(np.isnan(ground), 0, ground)
        # 600 m running median: tree tops and houses in the surface model must not end the floodplain
        ground = median_filter(ground, size=21, mode="nearest")
        b = main_belt(then | now) or main_belt(ext)
        if b is None:   # no water seen on this line at all
            left.append(MIN_HALF_M); right.append(MIN_HALF_M); chan.append(np.nan)
            continue
        channel = float(np.median(ground[b[0]:b[1]]))
        l, r = edge(ext, ground, b[0], -1, channel), edge(ext, ground, b[1] - 1, 1, channel)
        left.append(-offs[l]); right.append(offs[r])    # distance from the route line, positive outward
        chan.append(channel)
        if (i + 1) % 100 == 0:
            print(f"  section {i + 1}/{len(kms)}", flush=True)

    left, right = np.array(left, float), np.array(right, float)
    sm = lambda a: gaussian_filter1d(median_filter(a, size=7, mode="nearest"), 1.5, mode="nearest")
    fl, fr = np.maximum(sm(left), MIN_HALF_M), np.maximum(sm(right), MIN_HALF_M)
    np.savez(CACHE / "sections.npz", kms=kms, lon=S[kms, 0], lat=S[kms, 1], tx=tx[kms], ty=ty[kms],
             raw_left=left, raw_right=right, left=fl, right=fr, channel=np.array(chan))

    width = (fl + fr) / 1000
    print("  km  width  channel      L      R")
    for i in range(0, len(kms), 20):
        print(f"{kms[i]:5d} {width[i]:6.1f} {chan[i]:7.0f}  {fl[i] / 1000:5.1f}  {fr[i] / 1000:5.1f}")


if __name__ == "__main__":
    main()
