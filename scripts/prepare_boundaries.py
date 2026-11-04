"""Build web-ready boundary GeoJSON for the portal.

Country outline and disputed territory come from the official national boundary
(common.NATIONAL); provinces and districts come from geoBoundaries.
"""
import json

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from common import BOUNDARIES, DATA_OUT, national_boundary, national_outline


def load(level, kind):
    return json.loads((BOUNDARIES / f"pak_{level}_{kind}.geojson").read_text(encoding="utf-8"))


def rounded(geom, nd=5):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    g = mapping(geom)
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def clip_to(geom, boundary):
    """geoBoundaries units run offshore; trim them to the official national boundary."""
    g = geom.intersection(boundary)
    if g.geom_type == "GeometryCollection":
        g = unary_union([p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")])
    return g


def place(geom):
    """Label point and bounding box used by the portal for labels and fly-to."""
    pt = geom.representative_point()
    return {"lx": round(pt.x, 4), "ly": round(pt.y, 4), "bbox": [round(v, 4) for v in geom.bounds]}


def main():
    DATA_OUT.mkdir(parents=True, exist_ok=True)

    pakistan, disputed = national_boundary()
    outline = national_outline()
    country = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"name": "Pakistan", **place(outline)},
        "geometry": rounded(outline.simplify(0.0003)),
    }]}
    disputed_fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"name": name, **place(g)},
        "geometry": rounded(g.simplify(0.0003)),
    } for name, g in disputed]}
    # Internal border between Pakistan and disputed territory (drawn dashed on official maps)
    border_fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"name": name},
        "geometry": rounded(g.boundary.intersection(pakistan.buffer(0.0005)).simplify(0.0003)),
    } for name, g in disputed]}
    # Coarse copy of the outline, used to hide basemap labels that fall inside it
    lowres_fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {}, "geometry": rounded(outline.simplify(0.01), 3),
    }]}

    provinces_full = sorted(load("ADM1", "full")["features"], key=lambda f: f["properties"]["shapeName"])
    prov_shapes = [(f["properties"]["shapeName"], shape(f["geometry"])) for f in provinces_full]
    provinces = {"type": "FeatureCollection", "features": []}
    for i, (f, (name, geom)) in enumerate(zip(provinces_full, prov_shapes), start=1):
        g = clip_to(geom, pakistan)
        provinces["features"].append({
            "type": "Feature", "id": i,
            "properties": {"id": i, "name": name, "iso": f["properties"].get("shapeISO", ""), **place(g)},
            "geometry": rounded(g.simplify(0.002)),
        })

    districts_full = sorted(load("ADM2", "full")["features"], key=lambda f: f["properties"]["shapeName"])
    districts = {"type": "FeatureCollection", "features": []}
    for i, f in enumerate(districts_full, start=1):
        name = f["properties"]["shapeName"]
        geom = shape(f["geometry"])
        pt = geom.representative_point()
        prov = next((pn for pn, pg in prov_shapes if pg.contains(pt)), None)
        if prov is None:  # fall back to the nearest province
            prov = min(prov_shapes, key=lambda p: p[1].distance(pt))[0]
        g = clip_to(geom, pakistan)
        districts["features"].append({
            "type": "Feature", "id": i,
            "properties": {"id": i, "name": name, "province": prov, **place(g)},
            "geometry": rounded(g.simplify(0.001)),
        })

    for fname, fc in [("pakistan.geojson", country), ("disputed.geojson", disputed_fc),
                      ("disputed_border.geojson", border_fc), ("outline_lowres.geojson", lowres_fc),
                      ("provinces.geojson", provinces),
                      ("districts.geojson", districts)]:
        (DATA_OUT / fname).write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
        print(fname, len(fc["features"]), "features")


if __name__ == "__main__":
    main()
