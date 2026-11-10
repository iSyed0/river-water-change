"""Local administrative units for the map popup: union councils if available, otherwise tehsils.

Union councils: put the shapefile from HDX under osm_src/union_councils/ (any subfolder).
  https://data.humdata.org/dataset/pakistan-union-council-boundaries-along-with-other-admin-boundaries-dataset
Tehsils: boundries/Pakistan_Tehsils.geojson

Each unit is simplified for the browser, clipped to the official national outline, and given the
district and province it mostly falls in, rather than the ones in its file: the tehsil file spells
some districts differently and predates others. Rows with the same name in the same district are
one unit. Run analyse_floodplain.py and prepare_land_change.py afterwards: they add each unit's figures.

Output: portal/data/admin_units.geojson

    python scripts/prepare_admin_units.py     (after prepare_boundaries.py)
"""
import json
import re

import floodplain_common  # noqa: F401  (clears a conflicting PROJ path before pyogrio loads)
import pyogrio
import shapely
from shapely.geometry import mapping, shape

from common import DATA_OUT, ROOT, national_outline, read_admin

UC_DIR = ROOT / "osm_src" / "union_councils"
SIMPLIFY = 0.001    # roughly the square root of the area of the corners removed, in degrees: ~110 m


def tidy(name):
    return " ".join(w.capitalize() if w.isupper() or w.islower() else w for w in str(name).strip().split())


def field(columns, *patterns):
    for pat in patterns:
        for c in columns:
            if re.search(pat, c, re.I):
                return c
    return None


def read_units():
    """(geometries, names, tehsil names or None, level)"""
    shps = sorted(UC_DIR.rglob("*.shp")) if UC_DIR.exists() else []
    best = None
    for s in shps:
        info = pyogrio.read_info(s)
        cols = list(info["fields"])
        print(f"  {s.name}: {info['features']:,} features, fields {cols}")
        name = field(cols, r"^uc[_ ]?name", r"union", r"^uc$", r"adm4", r"^uc")
        if name and "Polygon" in str(info["geometry_type"]) and (not best or info["features"] > best[1]):
            best = (s, info["features"], name, field(cols, r"teh", r"tal", r"adm3"))
    if best:
        s, n, name, teh = best
        print(f"Union councils: {s.name}, {n:,} units, name field {name}, tehsil field {teh}")
        df = pyogrio.read_dataframe(s)
        if df.crs is not None and not df.crs.is_geographic:
            df = df.to_crs(4326)
        return df.geometry.values, [tidy(v) for v in df[name]], [tidy(v) for v in df[teh]] if teh else None, "Union council"
    if shps:
        raise SystemExit("Found shapefiles under osm_src/union_councils but none has a recognisable union council name field: "
                         "add its field name to the patterns in read_units().")
    print("No union council file yet: using tehsils")
    names, geoms = read_admin("Pakistan_Tehsils", "name")
    return geoms, [tidy(v) for v in names], None, "Tehsil"


def rounded(g, nd=5):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def main():
    geoms, names, tehsils, level = read_units()
    outline = national_outline()
    shapely.prepare(outline)
    districts = json.loads((DATA_OUT / "districts.geojson").read_text(encoding="utf-8"))["features"]
    dg = [shape(f["geometry"]) for f in districts]
    tree = shapely.STRtree(dg)

    parts = {}
    dropped = 0
    for i, g in enumerate(shapely.coverage_simplify(shapely.make_valid(geoms), SIMPLIFY)):
        if not shapely.intersects(outline, g):
            dropped += 1
            continue
        g = shapely.intersection(g, outline)
        if g.geom_type == "GeometryCollection":
            g = shapely.union_all([p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")])
        if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            dropped += 1
            continue
        # Most of its area, or for a unit off the district polygons (a coastal island) the nearest district
        area, j = max(((dg[j].intersection(g).area, j) for j in tree.query(g)), default=(0, None))
        d = districts[j if area > 0 else tree.nearest(g)]["properties"]
        parts.setdefault((names[i], d["name"], d["province"], tehsils[i] if tehsils else None), []).append(g)

    out = []
    for (name, district, province, tehsil), gs in parts.items():
        g = shapely.union_all(gs)
        pt = g.representative_point()
        props = {"id": len(out) + 1, "name": name, "district": district, "province": province,
                 "lx": round(pt.x, 4), "ly": round(pt.y, 4), "bbox": [round(v, 4) for v in g.bounds]}
        if tehsil:
            props["tehsil"] = tehsil
        out.append({"type": "Feature", "properties": props, "geometry": rounded(mapping(g))})

    path = DATA_OUT / "admin_units.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "level": level, "features": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path.name}: {len(out):,} {level.lower()} units ({dropped} outside the boundary dropped), {path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
