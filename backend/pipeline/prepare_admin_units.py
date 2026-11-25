"""Local administrative units for the map: tehsils, and union councils when a union council file is present.

Tehsils:        data/boundaries/admin/Pakistan_Tehsils.geojson -> frontend/data/admin_units.geojson
                The floodplain figures, the district reports and the tehsil overlay are built per tehsil.
Union councils: the first shapefile with polygons and a union council name field found under UC_DIRS
                (data/boundaries/union_councils/ as supplied, or data/osm/union_councils/) -> frontend/data/union_councils.geojson
                Drawn as their own overlay, named in the map popup and in the district reports' village tables.
                The supplied file mixes census (PCO) and Alhasan Systems boundaries of about 2017 and covers
                Punjab, Sindh, Khyber Pakhtunkhwa and Balochistan; it is not an official NDMA set.

Each unit is simplified for the browser, clipped to the official national outline, and given the district and
province it mostly falls in, rather than the ones in its file: the files spell some districts differently and
predate others. A union council also gets the tehsil it mostly falls in. Rows with the same name in the same
district (and tehsil) are one unit. After a tehsil change, run analyse_floodplain.py, prepare_land_change.py and
prepare_district_reports.py; after a union council change, prepare_district_reports.py.

    python backend/pipeline/prepare_admin_units.py     (after prepare_boundaries.py)
"""
import json
import re

import floodplain_common  # noqa: F401  (clears a conflicting PROJ path before pyogrio loads)
import pyogrio
import shapely
from shapely.geometry import mapping, shape

from common import DATA_OUT, OSM, ROOT, UNION_COUNCILS, national_outline, read_admin

UC_DIRS = [UNION_COUNCILS, OSM / "union_councils"]
UC_NAME_FIELDS = [r"^uc$", r"^uc[_ ]?name$", r"^union", r"^adm4_en$", r"^new_name$"]  # first non-empty wins, per row
TEHSIL_SIMPLIFY = 0.001   # roughly the square root of the area of the corners removed, in degrees: ~110 m
UC_SIMPLIFY = 0.0004      # union councils are a few km across: ~45 m


def tidy(name):
    return " ".join(w.capitalize() if w.isupper() or w.islower() else w for w in str(name).strip().split())


ACRONYMS = {"uc", "mc", "tc", "tmc", "dc"}


def tidy_uc(name):
    """Union council names hold ward codes such as (H-65) and UC-26: fix the case of each run of letters, not each
    word, so codes keep their capitals and a word after a bracket is capitalised too."""
    def fix(m):
        w = m.group(0)
        return w.upper() if w.lower() in ACRONYMS else w.capitalize() if w.isupper() or w.islower() else w
    return re.sub(r"[A-Za-z]+", fix, " ".join(str(name).split()))


def find_uc_file():
    """(path, name fields) of the largest union council shapefile, or None."""
    best = None
    for folder in UC_DIRS:
        for s in sorted(folder.rglob("*.shp")) if folder.exists() else []:
            info = pyogrio.read_info(s)
            cols = list(info["fields"])
            names = [c for pat in UC_NAME_FIELDS for c in cols if re.search(pat, c, re.I)]
            print(f"  {s.relative_to(ROOT)}: {info['features']:,} features, name fields {names}")
            if names and "Polygon" in str(info["geometry_type"]) and (not best or info["features"] > best[2]):
                best = (s, list(dict.fromkeys(names)), info["features"])
    return best


def read_union_councils():
    found = find_uc_file()
    if not found:
        return None
    path, fields, n = found
    df = pyogrio.read_dataframe(path)
    if df.crs is not None and not df.crs.is_geographic:
        df = df.to_crs(4326)
    names = []
    for _, row in df[fields].iterrows():
        v = next((str(row[f]) for f in fields if isinstance(row[f], str) and row[f].strip()), None)
        names.append(tidy_uc(v) if v else "Unnamed union council")
    print(f"Union councils: {path.relative_to(ROOT)}, {n:,} polygons, names from {fields}")
    return df.geometry.values, names


def rounded(g, nd=5):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def most_of(g, geoms, tree):
    """Index of the polygon holding most of g; the nearest one when g overlaps none (a coastal island)."""
    area, j = max(((geoms[j].intersection(g).area, j) for j in tree.query(g)), default=(0, None))
    return j if area > 0 else int(tree.nearest(g))


def polygonal(g):
    """g made valid and kept to its polygons; an empty polygon for a missing shape."""
    if g is None or g.is_empty:
        return shapely.Polygon()
    g = shapely.make_valid(g)
    if g.geom_type == "GeometryCollection":
        g = shapely.union_all([p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")])
    return g if g.geom_type in ("Polygon", "MultiPolygon") else shapely.Polygon()


def build(geoms, names, simplify, outline, districts, tehsils=None):
    """GeoJSON features, one per (name, district[, tehsil]), with district, province and tehsil from our polygons."""
    dg = [shapely.make_valid(shape(f["geometry"])) for f in districts]   # rounded coordinates can cross
    dtree = shapely.STRtree(dg)
    if tehsils:
        tg = [shapely.make_valid(shape(f["geometry"])) for f in tehsils]
        ttree = shapely.STRtree(tg)
    parts = {}
    dropped = 0
    for i, g in enumerate(shapely.coverage_simplify([polygonal(g) for g in geoms], simplify)):
        g = polygonal(g)  # simplifying overlapping shapes can leave them invalid
        if g.is_empty or not shapely.intersects(outline, g):
            dropped += 1
            continue
        g = shapely.intersection(g, outline)
        if g.geom_type == "GeometryCollection":
            g = shapely.union_all([p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")])
        if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            dropped += 1
            continue
        d = districts[most_of(g, dg, dtree)]["properties"]
        t = tehsils[most_of(g, tg, ttree)]["properties"]["name"] if tehsils else None
        parts.setdefault((names[i], d["name"], d["province"], t), []).append(g)

    out = []
    for (name, district, province, tehsil), gs in parts.items():
        g = shapely.union_all(gs)
        pt = g.representative_point()
        props = {"id": len(out) + 1, "name": name, "district": district, "province": province,
                 "lx": round(pt.x, 4), "ly": round(pt.y, 4), "bbox": [round(v, 4) for v in g.bounds]}
        if tehsil:
            props["tehsil"] = tehsil
        out.append({"type": "Feature", "properties": props, "geometry": rounded(mapping(g))})
    return out, dropped


def write(name, level, feats, dropped):
    path = DATA_OUT / name
    path.write_text(json.dumps({"type": "FeatureCollection", "level": level, "features": feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path.name}: {len(feats):,} {level.lower()} units ({dropped} outside the boundary dropped), {path.stat().st_size / 1e6:.1f} MB")


def main():
    outline = national_outline()
    shapely.prepare(outline)
    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]

    names, geoms = read_admin("Pakistan_Tehsils", "name")
    tehsils, dropped = build(geoms, [tidy(v) for v in names], TEHSIL_SIMPLIFY, outline, districts)
    write("admin_units.geojson", "Tehsil", tehsils, dropped)

    ucs = read_union_councils()
    if ucs is None:
        print("No union council file: put one under data/boundaries/union_councils/ or data/osm/union_councils/")
        return
    feats, dropped = build(*ucs, UC_SIMPLIFY, outline, districts, tehsils)
    write("union_councils.geojson", "Union council", feats, dropped)


if __name__ == "__main__":
    main()
