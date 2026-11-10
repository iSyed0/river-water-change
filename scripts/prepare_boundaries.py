"""Build web-ready boundary GeoJSON for the portal.

Country outline and disputed territory come from the official national boundary
(common.NATIONAL); provinces and districts come from the files in common.ADMIN. Those are
far too detailed for a browser, so they are simplified together, which keeps the borders
of neighbouring units on one line. A district's province is where it lies, not its label:
the file spells some provinces differently.
"""
import json

import floodplain_common  # noqa: F401  (clears a conflicting PROJ path before pyogrio loads)
import shapely
from shapely.geometry import mapping

from common import DATA_OUT, national_boundary, national_outline, read_admin

# Roughly the square root of the area of the corners removed, in degrees (0.001 is about 110 m)
PROVINCE_TOLERANCE = 0.002
DISTRICT_TOLERANCE = 0.002


def rounded(geom, nd=5):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    g = mapping(geom)
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


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

    prov_names, prov_full = read_admin("Pakistan_Provinces", "Province")
    prov_web = shapely.coverage_simplify(prov_full, PROVINCE_TOLERANCE)
    provinces = {"type": "FeatureCollection", "features": []}
    for i, k in enumerate(sorted(range(len(prov_names)), key=lambda j: prov_names[j]), start=1):
        provinces["features"].append({
            "type": "Feature", "id": i,
            "properties": {"id": i, "name": prov_names[k], **place(prov_web[k])},
            "geometry": rounded(prov_web[k]),
        })

    dist_names, dist_full = read_admin("Pakistan_Districts", "Districts")
    dist_web = shapely.coverage_simplify(dist_full, DISTRICT_TOLERANCE)
    districts = {"type": "FeatureCollection", "features": []}
    for i, k in enumerate(sorted(range(len(dist_names)), key=lambda j: dist_names[j]), start=1):
        pt = shapely.point_on_surface(dist_full[k])
        # Fall back to the nearest province
        p = next((j for j, pg in enumerate(prov_full) if shapely.contains(pg, pt)), None)
        if p is None:
            p = min(range(len(prov_full)), key=lambda j: shapely.distance(prov_full[j], pt))
        districts["features"].append({
            "type": "Feature", "id": i,
            "properties": {"id": i, "name": dist_names[k], "province": prov_names[p], **place(dist_web[k])},
            "geometry": rounded(dist_web[k]),
        })

    for fname, fc in [("pakistan.geojson", country), ("disputed.geojson", disputed_fc),
                      ("disputed_border.geojson", border_fc), ("outline_lowres.geojson", lowres_fc),
                      ("provinces.geojson", provinces),
                      ("districts.geojson", districts)]:
        (DATA_OUT / fname).write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
        print(fname, len(fc["features"]), "features", f"{(DATA_OUT / fname).stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
