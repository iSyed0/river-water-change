"""Build web-ready waterway layers from boundaries_src/WaterWays_Pakistan.geojson.

The source only carries a `name`, so each line is classed from its name:
river, canal, stream (nala / khwar / nadi / kas), qanat, drain, or unnamed.

Outputs (portal/data):
  waterways_major.geojson  named waterways, loaded with the page
  waterways_minor.geojson  unnamed network, loaded by the portal once zoomed in
  waterways_labels.geojson smoothed named lines used only to place labels along
                           meandering rivers; band "low" (strongly smoothed, for
                           zoomed-out views) and band "high" (closer fit)

    python scripts/prepare_rivers.py
"""
import json
import re

from shapely.geometry import mapping, shape
from shapely.ops import linemerge, unary_union

from common import BOUNDARIES, DATA_OUT

SOURCE = BOUNDARIES / "WaterWays_Pakistan.geojson"

# Name fixes for inconsistent spellings in the source
RENAME = {
    "Chenab RIver": "Chenab River",
    "Basho RIver": "Basho River",
    "River Swat": "Swat River",
    "swat river canal": "Swat River Canal",
    "Indus": "Indus River",
}

# Named rivers in the source whose names don't contain "River"
RIVERS = {"Indus River", "Dharab", "Gish", "Tangir", "Kondus", "Gyong", "Dansam", "Paron",
          "Brakhor", "Ranthuk", "Ghyari", "Shingo River", "Sunni Gunni"}


def classify(name):
    if not name:
        return "unnamed"
    n = name.lower()
    if "qanat" in n or "karez" in n:
        return "qanat"
    if "waste water" in n or "storm water" in n:
        return "drain"
    if re.search(r"canal|kanal|branch|feeder|\bwah\b", n):
        return "canal"
    if name in RIVERS or re.search(r"\briver\b", n):
        return "river"
    return "stream"


def smooth(geom, rounds=3):
    """Chaikin corner-cutting so labels can follow the line without sharp bends."""
    from shapely.geometry import LineString, MultiLineString

    def one(line):
        pts = list(line.coords)
        for _ in range(rounds):
            if len(pts) < 3:
                break
            out = [pts[0]]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                out += [(0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1), (0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1)]
            pts = out + [pts[-1]]
        return LineString(pts)

    if geom.geom_type == "LineString":
        return one(geom)
    return MultiLineString([one(g) for g in geom.geoms])


def rounded(geom, nd):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    g = mapping(geom)
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def main():
    src = json.loads(SOURCE.read_text(encoding="utf-8"))["features"]
    major, minor = [], []
    for f in src:
        if not f.get("geometry"):
            continue
        name = (f["properties"].get("name") or "").strip()
        name = RENAME.get(name, name)
        kind = classify(name)
        geom = shape(f["geometry"])
        if kind == "unnamed":
            g = geom.simplify(0.0003)
            if not g.is_empty:
                minor.append({"type": "Feature", "properties": {"kind": kind}, "geometry": rounded(g, 4)})
        else:
            g = geom.simplify(0.0001)
            major.append({"type": "Feature", "properties": {"name": name, "kind": kind}, "geometry": rounded(g, 5)})

    # One smoothed geometry per named river / canal / stream: labels need gentle curves to fit
    groups = {}
    for f in major:
        p = f["properties"]
        if p["kind"] in ("river", "canal", "stream"):
            groups.setdefault((p["name"], p["kind"]), []).append(shape(f["geometry"]))
    labels = []
    for (name, kind), geoms in groups.items():
        merged = unary_union(geoms)
        if merged.geom_type == "MultiLineString":
            merged = linemerge(merged)
        for band, tol in (("low", 0.04), ("high", 0.008)):
            g = merged.simplify(tol)
            if not g.is_empty:
                labels.append({"type": "Feature", "properties": {"name": name, "kind": kind, "band": band},
                               "geometry": rounded(smooth(g), 4)})

    for fname, feats in [("waterways_major.geojson", major), ("waterways_minor.geojson", minor),
                         ("waterways_labels.geojson", labels)]:
        out = DATA_OUT / fname
        out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")),
                       encoding="utf-8")
        print(f"{fname}: {len(feats)} features, {out.stat().st_size / 1e6:.1f} MB")

    kinds = {}
    for f in major:
        kinds.setdefault(f["properties"]["kind"], set()).add(f["properties"]["name"])
    for k, names in sorted(kinds.items()):
        print(f"  {k:7} {len(names):3} names  e.g. {', '.join(sorted(names)[:6])}")


if __name__ == "__main__":
    main()
