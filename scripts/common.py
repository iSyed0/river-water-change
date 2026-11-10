"""Shared configuration for the Pakistan surface-water pipeline.

Source data: JRC Global Surface Water v1.5 (1984-2024), 10x10 degree GeoTIFF
granules at 0.00025 deg (~30 m). The four granules in this project form an
80000 x 80000 pixel mosaic covering 60-80 E, 20-40 N.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTAL = ROOT / "portal"
TILES = PORTAL / "tiles"
DATA_OUT = PORTAL / "data"
BOUNDARIES = ROOT / "boundaries_src"

# Official national boundary. Features named "Pakistan" form the country; any other
# feature (e.g. IIOJK) is drawn as disputed territory and excluded from water clipping.
NATIONAL = BOUNDARIES / "Pakistan_National.geojson"
NATIONAL_NAME_FIELD = "Admin01_Na"

# Provinces, districts and tehsils, in full detail.
ADMIN = ROOT / "boundries"


def read_admin(name, field):
    """(names, valid polygons) of a file in ADMIN. These files also hold IIOJK as a unit: it is left out here
    because the portal draws it as disputed territory, from NATIONAL. Import floodplain_common first."""
    import pyogrio
    import shapely

    df = pyogrio.read_dataframe(ADMIN / f"{name}.geojson", columns=[field])
    df = df[~df[field].str.contains("Occupied")]
    return [str(v).strip() for v in df[field]], shapely.make_valid(df.geometry.values)


def national_boundary():
    """Return (pakistan_geometry, [(name, disputed_geometry), ...]) from NATIONAL."""
    import json
    from shapely.geometry import shape
    from shapely.ops import unary_union

    feats = json.loads(NATIONAL.read_text(encoding="utf-8"))["features"]
    own, other = [], []
    for f in feats:
        name = f["properties"].get(NATIONAL_NAME_FIELD, "")
        (own if name == "Pakistan" else other).append((name, shape(f["geometry"])))
    return unary_union([g for _, g in own]), other


def national_outline():
    """Outer outline of every feature in NATIONAL (Pakistan plus disputed territory),
    i.e. the shape the official map draws."""
    from shapely.ops import unary_union

    pakistan, other = national_boundary()
    return unary_union([pakistan] + [g for _, g in other])


# Mosaic grid
LON0, LAT0 = 60.0, 40.0
RES = 0.00025
GRANULE = 40000
MOSAIC = 2 * GRANULE

# (col_block, row_block) -> granule suffix. Granule names give the top-left corner.
GRANULES = {
    (0, 0): "60E_40N",
    (1, 0): "70E_40N",
    (0, 1): "60E_30N",
    (1, 1): "70E_30N",
}


def granule_path(layer, key):
    return ROOT / layer / f"{layer}_{GRANULES[key]}_v1_5_2024.tif"


NODATA = 255  # fill value outside Pakistan / no data; always transparent


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def ramp(stops, lo, hi):
    """Linear colour ramp across integer values lo..hi. stops = [(t, '#rrggbb'), ...]."""
    out = {}
    for v in range(lo, hi + 1):
        t = (v - lo) / (hi - lo) if hi > lo else 0
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0) if t1 > t0 else 0
                a, b = _hex(c0), _hex(c1)
                out[v] = tuple(round(a[i] + (b[i] - a[i]) * f) for i in range(3))
                break
    return out


TRANSITION_CLASSES = {
    1: ("Permanent", "#2563eb"),
    2: ("New permanent", "#22c55e"),
    3: ("Lost permanent", "#e11d48"),
    4: ("Seasonal", "#7dd3fc"),
    5: ("New seasonal", "#a3e635"),
    6: ("Lost seasonal", "#f9a8d4"),
    7: ("Seasonal to permanent", "#fb923c"),
    8: ("Permanent to seasonal", "#facc15"),
    9: ("Ephemeral permanent", "#a1a1aa"),
    10: ("Ephemeral seasonal", "#e4e4e7"),
}

# reduce: how a 2x2 block of children collapses into one parent pixel
#   any  -> max of valid values      mean -> mean of valid values
#   mode -> most frequent valid value
LAYERS = {
    "extent": {
        "reduce": "any",
        "colors": {1: _hex("#22d3ee")},
    },
    "occurrence": {
        "reduce": "mean",
        "colors": ramp([(0, "#fda4af"), (0.3, "#e879f9"), (0.55, "#a78bfa"),
                        (0.8, "#3b82f6"), (1, "#22d3ee")], 1, 100),
    },
    "recurrence": {
        "reduce": "mean",
        "colors": ramp([(0, "#f97316"), (0.5, "#facc15"), (1, "#22d3ee")], 1, 100),
    },
    "seasonality": {
        "reduce": "mean",
        "colors": ramp([(0, "#fef08a"), (0.3, "#4ade80"), (0.65, "#22d3ee"),
                        (1, "#3b82f6")], 1, 12),
    },
    "change": {
        # 0..200, 100 = no change; 253 not water, 254 not computable
        "reduce": "mean",
        "colors": {**ramp([(0, "#ef4444"), (0.42, "#7f1d1d"), (0.5, "#475569"),
                           (0.58, "#065f46"), (1, "#22c55e")], 0, 200)},
    },
    "transitions": {
        "reduce": "mode",
        "colors": {k: _hex(c) for k, (_, c) in TRANSITION_CLASSES.items()},
    },
}

# Derived from transitions inside the Indus floodplain by analyse_floodplain.py. Colours are the
# validated dark chart palette; portal/js/app.js uses the same ones for the cross-section strips.
LAYERS["erosion"] = {
    "reduce": "mode",
    "colors": {1: _hex("#d95926"),   # land -> permanent water
               2: _hex("#c98500"),   # land -> seasonal water
               3: _hex("#199e70"),   # permanent water -> land
               4: _hex("#1aa878"),   # seasonal water -> land
               5: _hex("#3987e5"),   # water in both years
               6: _hex("#7b8494")},  # wet at times, no lasting change
}

# Written by prepare_water_types.py. Lightness as well as hue keeps all five apart on the map, colour-blind included.
LAYERS["watertype"] = {
    "reduce": "mode",
    "colors": {1: _hex("#2f6fd6"),   # river or canal
               2: _hex("#9fd3ff"),   # lake or reservoir
               3: _hex("#d95926"),   # seasonal water near a river
               4: _hex("#f2c94c"),   # seasonal water away from rivers
               5: _hex("#14a37f")},  # coast and tidal water
}

# Tiles written by prepare_land_change.py, not by build_tiles.py
LAYERS["landloss"] = {
    "separate_tiles": True,
    "reduce": "mode",
    "colors": {1: _hex("#d95926"),   # land lost to the river
               2: _hex("#199e70"),   # land deposited
               3: _hex("#3987e5")},  # water in both
}

TILE = 256
MAX_ZOOM = 12
