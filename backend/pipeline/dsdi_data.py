"""NDMA DSDI layers used by the floodplain analysis and the district reports.

Downloads, through the DSDI API with its public guest login (the route DSDI asks integrators to use), into data/dsdi/:
  schools.geojson       gcop:Schools_QC              schools: name, level, status, enrolment
  health.geojson        gcop:Medical_Facilities_QC   health and medical facilities: name, category, government/private
  embankments.geojson   gcop:protection_bands        protection bands (bunds, spurs, embankments)
  police.geojson        gcop:Police_Stations_QC      police stations: name
  rescue.geojson        gcop:Rescue_Stations_QC      rescue stations (Rescue 1122, Edhi, ...): name, category
  railway.geojson       gcop:Railway_Stations_QC     railway stations: name
  districts.geojson     gcop:district_boundary_updated, simplified to ~10 m (the original is ~180 MB)

    python backend/pipeline/dsdi_data.py            # fetch what is missing
    python backend/pipeline/dsdi_data.py --refresh  # fetch everything again

DSDI is on the NDMA network only. The scripts fall back to OpenStreetMap schools and health facilities when
data/dsdi/ is empty. Address and login: environment variables DSDI_API, DSDI_USER, DSDI_PASSWORD.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

import numpy as np
import shapely
from shapely.geometry import mapping, shape

from common import DSDI_SRC

SRC = DSDI_SRC
API = os.environ.get("DSDI_API", "http://172.18.7.13:8080/dsdi/api/v1")
LAYERS = {
    "schools": "gcop:Schools_QC",
    "health": "gcop:Medical_Facilities_QC",
    "embankments": "gcop:protection_bands",
    "police": "gcop:Police_Stations_QC",
    "rescue": "gcop:Rescue_Stations_QC",
    "railway": "gcop:Railway_Stations_QC",
    "districts": "gcop:district_boundary_updated",
}


def _token():
    body = json.dumps({"email": os.environ.get("DSDI_USER", "guest"), "password": os.environ.get("DSDI_PASSWORD", "guest")}).encode()
    req = urllib.request.Request(f"{API}/auth/login", body, {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]["token"]


DOWNLOAD_CAP = 50000   # the DSDI download route returns at most this many features, with no paging


def _api(path, token):
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {token}", "X-Page-Key": "map"})
    return json.loads(urllib.request.urlopen(req, timeout=900).read())


def _wfs_paged(geoserver, layer, page=20000):
    """Every feature of a layer from GeoServer's WFS, a page at a time: used only when the DSDI route is capped."""
    feats, start = [], 0
    while True:
        q = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": layer, "outputFormat": "application/json",
             "srsName": "EPSG:4326", "count": page, "startIndex": start, "sortBy": "objectid"}
        got = json.loads(urllib.request.urlopen(f"{geoserver}/wfs?{urllib.parse.urlencode(q)}", timeout=900).read())["features"]
        feats += got
        if len(got) < page:
            return feats
        start += page


def fetch(refresh=False):
    SRC.mkdir(exist_ok=True)
    token = None
    for key, layer in LAYERS.items():
        out = SRC / f"{key}.geojson"
        if out.exists() and not refresh:
            continue
        token = token or _token()
        print(f"  {layer} ...", end=" ", flush=True)
        fc = _api(f"/geoserver/download?{urllib.parse.urlencode({'layer': layer, 'format': 'geojson'})}", token)
        feats = [f for f in fc["features"] if f.get("geometry")]
        if len(fc["features"]) >= DOWNLOAD_CAP:
            geoserver = _api("/geoserver/layers", token)["data"]["geoserverUrl"].rstrip("/")
            print(f"capped at {DOWNLOAD_CAP:,}; paging from GeoServer ...", end=" ", flush=True)
            feats = [f for f in _wfs_paged(geoserver, layer) if f.get("geometry")]
        if key == "districts":
            for f in feats:
                f["geometry"] = mapping(shapely.simplify(shape(f["geometry"]), 0.0001))
        out.write_text(json.dumps({"type": "FeatureCollection", "layer": layer, "features": feats}, ensure_ascii=False), encoding="utf-8")
        print(f"{len(feats):,} features, {out.stat().st_size / 1e6:.1f} MB", flush=True)


def _read(key):
    path = SRC / f"{key}.geojson"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def _clean(v):
    return " ".join(str(v).split()) if v not in (None, "") else ""


# The kind each point layer is listed as in the reports, and its name when the register has none
POINT_KINDS = {"schools": "School", "health": "Health facility", "police": "Police station", "rescue": "Rescue station",
               "railway": "Railway station"}


def points(key, area=None):
    """(xy array, names, details) of a DSDI point layer (POINT_KINDS), inside `area` if given; None if not fetched."""
    feats = _read(key)
    if feats is None:
        return None
    xy, names, details = [], [], []
    for f in feats:
        g = shape(f["geometry"])
        g = g if g.geom_type == "Point" else g.centroid
        P = f["properties"]
        if key == "schools":
            name = _clean(P.get("schoolname")) or "Unnamed school"
            detail = ", ".join(x for x in (_clean(P.get("level") or P.get("sch_level")).lower(), _clean(P.get("status")).lower()) if x)
        elif key == "health":
            name = _clean(P.get("name")) or "Unnamed health facility"
            detail = ", ".join(x for x in (_clean(P.get("sub_cat") or P.get("category")).lower(), _clean(P.get("gov_pvt")).lower()) if x)
        else:
            name = _clean(P.get("name")) or f"Unnamed {POINT_KINDS[key].lower()}"
            detail = _clean(P.get("category")) if key == "rescue" else ""
        xy.append((g.x, g.y)); names.append(name); details.append(detail)
    xy = np.array(xy, float).reshape(-1, 2)
    if area is not None and len(xy):
        keep = shapely.contains_xy(area, xy[:, 0], xy[:, 1])
        xy, names, details = xy[keep], [n for n, k in zip(names, keep) if k], [d for d, k in zip(details, keep) if k]
    return xy, names, details


def embankments():
    """[(shapely geometry, properties)] of protection bands; None if not fetched."""
    feats = _read("embankments")
    return None if feats is None else [(shape(f["geometry"]), f["properties"]) for f in feats]


def districts():
    """[(shapely geometry, name)] of DSDI's updated district boundaries; None if not fetched."""
    feats = _read("districts")
    return None if feats is None else [(shape(f["geometry"]).buffer(0), _clean(f["properties"].get("name"))) for f in feats]


if __name__ == "__main__":
    fetch(refresh="--refresh" in sys.argv[1:])
