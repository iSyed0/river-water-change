"""Camera route for the portal's Indus River 3D flythrough.

The Indus in WaterWays_Pakistan comes as ~50 pieces with gaps at barrages and
braided reaches. They are stitched into one main stem by a shortest path from
the upstream end to the sea (gaps bridged at a cost penalty, so side braids are
skipped), smoothed for a steady camera and resampled every kilometre.

Output: portal/data/indus_route.json
  coords  [[lon, lat], ...] one point per km, upstream to sea
  stops   landmarks snapped to the route, with camera zoom/pitch hints

    python scripts/prepare_indus.py      (after prepare_rivers.py)
"""
import heapq
import json
import math

from shapely.geometry import LineString, shape
from shapely.ops import linemerge, unary_union

from common import DATA_OUT
from prepare_rivers import smooth

RIVER = "Indus River"
GAP_PENALTY = 3.0   # bridging a gap costs 3x its length, so real channel is preferred
MAX_GAP = 0.5       # degrees
STEP_KM = 1.0

# Landmarks, upstream to sea: (name, lon, lat, zoom, pitch, note)
STOPS = [
    ("Skardu valley", 75.63, 35.30, 11.0, 68, "Wide braided valley floor in Baltistan; the Shigar River joins just downstream."),
    ("Gilgit confluence", 74.63, 35.77, 11.2, 70, "Junction of the Karakoram, Himalaya and Hindu Kush; the Gilgit River joins near Jaglot."),
    ("Kohistan gorge · Dasu", 73.29, 35.29, 11.3, 70, "Deep gorge through Kohistan; the river drops fast and is prone to landslide dams and GLOF surges."),
    ("Tarbela Dam", 72.70, 34.09, 10.4, 62, "Pakistan's largest reservoir and the main storage on the Indus."),
    ("Attock · Kabul confluence", 72.23, 33.91, 10.8, 64, "The Kabul River joins; the Indus then cuts through the Kala Chitta and Salt ranges."),
    ("Kalabagh", 71.55, 32.96, 10.6, 64, "The Indus leaves the hills and spreads onto the Punjab plain."),
    ("Chashma Barrage", 71.38, 32.43, 10.4, 60, "Barrage and Chashma reservoir; head of the Chashma-Jhelum link."),
    ("Taunsa Barrage", 70.85, 30.51, 10.4, 60, "Wide riverine (kacha) belt that floods in high monsoon years."),
    ("Panjnad confluence · Mithankot", 70.37, 28.95, 10.2, 60, "The Panjnad, carrying the Chenab, Jhelum, Ravi, Beas and Sutlej, joins the Indus."),
    ("Guddu Barrage", 69.72, 28.42, 10.4, 60, "First barrage in Sindh; feeds canals on both banks."),
    ("Sukkur Barrage", 68.85, 27.68, 10.6, 60, "One of the world's largest irrigation systems starts here."),
    ("Sehwan · Manchar Lake", 67.87, 26.43, 10.0, 60, "Right-bank floodplain and Manchar Lake, heavily flooded in 2010 and 2022."),
    ("Kotri Barrage · Hyderabad", 68.37, 25.37, 10.6, 60, "Last barrage before the sea."),
    ("Indus Delta", 67.65, 24.08, 9.8, 58, "Creeks and mangroves where the Indus reaches the Arabian Sea."),
]


def km_between(a, b):
    """Great-circle distance in km between (lon, lat) points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def stitch(parts):
    """Shortest path through the pieces from the upstream end to the sea."""
    nodes = []
    for p in parts:
        nodes += [p.coords[0], p.coords[-1]]
    nodes = list(dict.fromkeys(nodes))
    idx = {n: i for i, n in enumerate(nodes)}
    edges = {i: [] for i in range(len(nodes))}
    for p in parts:
        a, b = idx[p.coords[0]], idx[p.coords[-1]]
        edges[a].append((p.length, b, list(p.coords)))
        edges[b].append((p.length, a, list(p.coords)[::-1]))
    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            d = math.dist(a, b)
            if i != j and d < MAX_GAP:
                edges[i].append((d * GAP_PENALTY, j, [a, b]))

    start = max(range(len(nodes)), key=lambda i: nodes[i][0])   # upstream end lies furthest east
    end = min(range(len(nodes)), key=lambda i: nodes[i][1])     # the mouth lies furthest south
    best, prev, heap = {start: 0}, {}, [(0, start)]
    while heap:
        d, i = heapq.heappop(heap)
        if i == end:
            break
        if d > best[i]:
            continue
        for w, j, coords in edges[i]:
            if d + w < best.get(j, math.inf):
                best[j] = d + w
                prev[j] = (i, coords)
                heapq.heappush(heap, (d + w, j))
    seq, i = [], end
    while i != start:
        j, coords = prev[i]
        seq.append(coords)
        i = j
    pts = []
    for coords in reversed(seq):
        pts += coords if not pts else coords[1:]
    return LineString(pts)


def main():
    fc = json.loads((DATA_OUT / "waterways_major.geojson").read_text(encoding="utf-8"))
    geoms = [shape(f["geometry"]) for f in fc["features"] if f["properties"]["name"] == RIVER]
    merged = linemerge(unary_union(geoms))
    parts = list(getattr(merged, "geoms", [merged]))
    route = stitch(parts)

    # Smooth for the camera: meanders are kept at the scale of a few km, jitter is not
    route = smooth(route.simplify(0.01), rounds=4)

    coords = list(route.coords)
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + km_between(a, b))
    total = cum[-1]

    # resample every STEP_KM along the geodesic length
    out, k = [], 0
    for i in range(int(total // STEP_KM) + 1):
        d = i * STEP_KM
        while k < len(cum) - 2 and cum[k + 1] < d:
            k += 1
        seg = cum[k + 1] - cum[k] or 1
        t = (d - cum[k]) / seg
        (x0, y0), (x1, y1) = coords[k], coords[k + 1]
        out.append([round(x0 + (x1 - x0) * t, 5), round(y0 + (y1 - y0) * t, 5)])

    stops = []
    for name, lon, lat, zoom, pitch, note in STOPS:
        i = min(range(len(out)), key=lambda n: km_between(out[n], (lon, lat)))
        stops.append({"name": name, "km": i * STEP_KM, "zoom": zoom, "pitch": pitch, "note": note,
                      "off_km": round(km_between(out[i], (lon, lat)), 1)})
    assert all(a["km"] < b["km"] for a, b in zip(stops, stops[1:])), "stops out of order along the route"

    data = {"name": RIVER, "step_km": STEP_KM, "length_km": round(total), "coords": out, "stops": stops}
    path = DATA_OUT / "indus_route.json"
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"{path.name}: {total:.0f} km, {len(out)} points, {path.stat().st_size / 1e3:.0f} kB")
    for s in stops:
        print(f"  {s['km']:6.0f} km  {s['name']:32} (landmark {s['off_km']} km from route)")


if __name__ == "__main__":
    main()
