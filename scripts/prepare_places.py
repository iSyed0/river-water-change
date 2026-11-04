"""Build the settlement layer (cities, towns, villages, bastis) for the portal.

Sources (both under osm_src/, downloaded by hand):
  geonames/PK.txt     GeoNames Pakistan dump, https://download.geonames.org/export/dump/PK.zip
                      ~145k populated places; the bulk of the villages and bastis.
  places/gis_osm_places_free_1.shp, gis_osm_places_a_free_1.shp
                      from Geofabrik's pakistan-latest-free.shp.zip (OpenStreetMap).
                      Used to grade cities/towns, refine positions and add places
                      GeoNames lacks.

Each place is tagged with the district and province it falls in.

Outputs (portal/data):
  places_major.geojson  cities and towns, loaded with the page
  places.json           every place in compact columnar form, loaded by the portal
                        once zoomed in or when search is used

    python scripts/prepare_places.py
"""
import csv
import json
import math
import re
import unicodedata

from shapely import STRtree
from shapely.geometry import Point, shape
from shapely.prepared import prep

from common import DATA_OUT, ROOT, national_outline

SRC = ROOT / "osm_src"
GEONAMES = SRC / "geonames" / "PK.txt"
OSM_POINTS = SRC / "places" / "gis_osm_places_free_1.shp"
OSM_AREAS = SRC / "places" / "gis_osm_places_a_free_1.shp"

KINDS = ["city", "town", "village", "hamlet", "locality"]  # rank order, most important first
CITY_POP, TOWN_POP = 100_000, 10_000

# GeoNames feature codes kept; abandoned/destroyed/historical places are dropped
GN_KIND = {"PPL": "village", "PPLF": "village", "PPLS": "village", "PPLR": "village",
           "PPLX": "locality", "PPLL": "locality",
           "PPLA": "town", "PPLA2": "town", "PPLA3": "town", "PPLA4": "town", "PPLC": "city"}
OSM_KIND = {"national_capital": "city", "city": "city", "town": "town", "village": "village",
            "hamlet": "hamlet", "suburb": "locality", "locality": "locality", "neighbourhood": "locality"}

SAME_PLACE_M = 2500      # same-name points closer than this are one place
SAME_TOWN_M = 8000       # looser for cities/towns, whose points sit anywhere in a large area


def skeleton(name):
    """Spelling-insensitive key for romanised Urdu/Punjabi/Sindhi names:
    Kanjal / Kunjal / Kanjaal all give 'knjl'. Mirrors placeKey() in portal/js/app.js."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z]", "", s).replace("ph", "f").replace("q", "k").replace("v", "w")
    s = re.sub(r"[aeiouyh]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)


def metres(a, b):
    dx = (a[0] - b[0]) * 111_320 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (a[1] - b[1]) * 110_540
    return math.hypot(dx, dy)


def read_geonames():
    out = []
    with open(GEONAMES, encoding="utf-8", newline="") as fh:
        for r in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            if r[6] != "P" or r[7] not in GN_KIND:
                continue
            kind, pop = GN_KIND[r[7]], int(r[14] or 0)
            if pop >= CITY_POP:
                kind = "city"
            elif pop >= TOWN_POP and kind in ("village", "locality"):
                kind = "town"
            out.append({"name": r[1].strip(), "x": float(r[5]), "y": float(r[4]), "kind": kind})
    return out


def read_osm():
    import pyogrio

    out = []
    for path in (OSM_POINTS, OSM_AREAS):
        df = pyogrio.read_dataframe(path, columns=["fclass", "name"])
        for fclass, name, geom in zip(df.fclass, df.name, df.geometry):
            # Names in Urdu script only would need the RTL text plugin to label; GeoNames covers them
            if fclass not in OSM_KIND or not isinstance(name, str) or not re.search(r"[A-Za-z]", name):
                continue
            pt = geom if geom.geom_type == "Point" else geom.representative_point()
            out.append({"name": name.strip(), "x": pt.x, "y": pt.y, "kind": OSM_KIND[fclass]})
    return out


class Grid:
    """Points bucketed by ~0.05 deg cells for same-name lookups nearby."""

    def __init__(self):
        self.cells = {}

    def _key(self, x, y):
        return int(x // 0.05), int(y // 0.05)

    def add(self, p):
        self.cells.setdefault(self._key(p["x"], p["y"]), []).append(p)

    def near(self, x, y, reach=2):
        cx, cy = self._key(x, y)
        for i in range(cx - reach, cx + reach + 1):
            for j in range(cy - reach, cy + reach + 1):
                yield from self.cells.get((i, j), ())


def rank(kind):
    return KINDS.index(kind)


def merge(geonames, osm):
    grid, places = Grid(), []
    for p in geonames:
        p["key"] = skeleton(p["name"])
        dup = next((q for q in grid.near(p["x"], p["y"], 1)
                    if q["key"] == p["key"] and metres((p["x"], p["y"]), (q["x"], q["y"])) < SAME_PLACE_M), None)
        if dup:
            if rank(p["kind"]) < rank(dup["kind"]):
                dup["kind"] = p["kind"]
            continue
        grid.add(p)
        places.append(p)

    added = matched = 0
    for o in osm:
        o["key"] = skeleton(o["name"])
        reach_m = SAME_TOWN_M if o["kind"] in ("city", "town") else SAME_PLACE_M
        cands = [q for q in grid.near(o["x"], o["y"]) if q["key"] == o["key"]]
        cands = [(metres((o["x"], o["y"]), (q["x"], q["y"])), q) for q in cands]
        cands = [c for c in cands if c[0] < reach_m]
        if cands:
            q = min(cands, key=lambda c: c[0])[1]
            # OSM positions are usually the more precise; its spelling is kept as the search alias
            q["x"], q["y"] = o["x"], o["y"]
            if rank(o["kind"]) < rank(q["kind"]):
                q["kind"] = o["kind"]
            if o["name"].lower() != q["name"].lower():
                q["alt"] = o["name"]
            matched += 1
        elif o["key"] or o["kind"] in ("city", "town"):
            grid.add(o)
            places.append(o)
            added += 1
    print(f"  GeoNames {len(geonames):,} -> {len(places) - added:,} after de-duplication")
    print(f"  OSM {len(osm):,}: {matched:,} matched GeoNames, {added:,} added")
    return places


def tag_admin(places):
    """District id and province of each place; drops points outside the official outline."""
    outline = prep(national_outline())
    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    provinces = json.loads((DATA_OUT / "provinces.geojson").read_text(encoding="utf-8"))["features"]
    dgeoms = [shape(f["geometry"]) for f in districts]
    pgeoms = [shape(f["geometry"]) for f in provinces]
    dtree, ptree = STRtree(dgeoms), STRtree(pgeoms)
    prov_names = [f["properties"]["name"] for f in provinces]

    kept = []
    for p in places:
        pt = Point(p["x"], p["y"])
        if not outline.contains(pt):
            continue
        hit = [i for i in dtree.query(pt) if dgeoms[i].contains(pt)]
        p["district"] = districts[hit[0]]["properties"]["id"] if hit else -1
        if hit:
            p["province"] = districts[hit[0]]["properties"]["province"]
        else:
            ph = [i for i in ptree.query(pt) if pgeoms[i].contains(pt)]
            p["province"] = prov_names[ph[0]] if ph else ""
        kept.append(p)
    return kept, prov_names


def main():
    print("Reading sources")
    geonames, osm = read_geonames(), read_osm()
    print("Merging")
    places = merge(geonames, osm)
    print("Tagging districts")
    places, prov_names = tag_admin(places)
    places.sort(key=lambda p: (rank(p["kind"]), p["name"]))

    provs = sorted({p["province"] for p in places if p["province"]} | set(prov_names))
    columns = {
        "kinds": KINDS,
        "provinces": provs,
        "n": [p["name"] for p in places],
        "x": [round(p["x"] * 1e5) for p in places],
        "y": [round(p["y"] * 1e5) for p in places],
        "k": [rank(p["kind"]) for p in places],
        "d": [p["district"] for p in places],
        "p": [provs.index(p["province"]) if p["province"] else -1 for p in places],
        "alt": {str(i): p["alt"] for i, p in enumerate(places) if p.get("alt")},
        "credit": "Places: GeoNames (CC BY 4.0), OpenStreetMap contributors (ODbL)",
    }
    (DATA_OUT / "places.json").write_text(json.dumps(columns, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    major = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(p["x"], 5), round(p["y"], 5)]},
         "properties": {"name": p["name"], "kind": p["kind"], "rank": rank(p["kind"])}}
        for p in places if p["kind"] in ("city", "town")]}
    (DATA_OUT / "places_major.geojson").write_text(json.dumps(major, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    counts = {k: sum(p["kind"] == k for p in places) for k in KINDS}
    print(f"Wrote {len(places):,} places {counts}; {sum(p['district'] < 0 for p in places):,} outside any district")


if __name__ == "__main__":
    main()
