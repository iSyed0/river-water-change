"""Layer downloads: cut a portal layer to an area and save the parts asked for as GeoTIFF, GeoJSON or a zipped shapefile.

Every layer is cut from its source data, not from the map tiles, so a download has the data's own resolution and
values: JRC Global Surface Water at 30 m, the erosion raster, the monthly water record (about 280 m) or the Sukkur
Sentinel-2 change (20 m). Each layer is split into parts (LAYERS below): Occurrence Change into gain (green) and loss
(red), the erosion layer into its classes, and so on. Ask for one part or several.

  GeoTIFF     the layer's own values where a chosen part is, empty elsewhere (see each layer's "values")
  GeoJSON     polygons of the chosen parts in WGS 84, each with its part and its area in hectares
  Shapefile   the same polygons, zipped

Options:
  source      sourced layers only: one water source (common.WATER_GROUPS), as the map's Water source filter
  month       the monthly layer: YYYY_MM
  lasting     Occurrence Change only: change of 25 points or more, the rule behind "land lost"
  min_ha      leave out patches (8-connected pixels of the chosen parts) smaller than this many hectares

Polygons follow the pixels exactly and are cut into 1 degree blocks, so a patch that crosses a block edge comes as two
polygons. Files are kept in data/cache/exports/, so asking for the same cut again is instant.

backend/serve.py runs these as background jobs for the portal (/api/export/...). From the command line:

    python backend/exports.py change --parts gain --area pakistan --format geojson --min-ha 0.5
    python backend/exports.py erosion --parts 1,2 --area district:85 --format shp
    python backend/exports.py --list
"""
import argparse
import hashlib
import json
import math
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pipeline"))

import floodplain_common  # noqa: F401  (clears a conflicting PROJ path before rasterio loads)
from common import CACHE_ROOT, DATA_OUT, LAT0, LON0, MOSAIC, RES, WATER_GROUPS, granule_path, national_outline

import numpy as np
import rasterio
import shapely
from affine import Affine
from rasterio import features
from rasterio.windows import Window
from scipy.ndimage import label

OUT = CACHE_ROOT / "exports"
BLOCK = 4000                # JRC pixels, 1 degree: polygons are cut at these edges
PAD = 80                    # pixels read around a block, so a patch crossing its edge is not taken for a small one
MAX_SHAPES = 1_500_000      # a vector file with more polygons than this is refused: ask for a GeoTIFF or a smaller area
MIN_HA = (0, 0.5, 1, 5)     # the patch sizes the portal offers; 5 ha is under PAD pixels anywhere in Pakistan
KM_LAT = 110.574
FORMATS = {"tif": "GeoTIFF", "geojson": "GeoJSON", "shp": "Shapefile (zip)"}
SUFFIX = {"tif": ".tif", "geojson": ".geojson", "shp": ".zip"}


def _band(lo, hi):
    return lambda a: (a >= lo) & (a <= hi)


def _eq(v):
    return lambda a: a == v


SHARE_BINS = [("p1_24", "1–24%", 1, 24), ("p25_49", "25–49%", 25, 49), ("p50_74", "50–74%", 50, 74), ("p75_100", "75–100%", 75, 100)]

# id: title, period, grid, source raster, parts (id, label, test on the source values) and what the GeoTIFF values mean.
# "sourced" layers can be limited to one water source, as on the map.
LAYERS = {
    "extent": {
        "title": "Maximum Water Extent", "period": "1984-2024", "grid": "jrc", "src": "extent", "sourced": True,
        "parts": [("water", "Water detected at least once", _eq(1))],
        "values": "1 = water detected at least once in 1984–2024",
    },
    "occurrence": {
        "title": "Water Occurrence", "period": "1984-2024", "grid": "jrc", "src": "occurrence", "sourced": True,
        "parts": [(p, f"Wet {label} of the time", _band(lo, hi)) for p, label, lo, hi in SHARE_BINS],
        "values": "1–100 = % of valid observations with water, 1984–2024",
    },
    "recurrence": {
        "title": "Recurrence", "period": "1984-2024", "grid": "jrc", "src": "recurrence", "sourced": True,
        "parts": [(p, f"Water returns in {label} of years", _band(lo, hi)) for p, label, lo, hi in SHARE_BINS],
        "values": "1–100 = % of years in which water returned, 1984–2024",
    },
    "change": {
        "title": "Occurrence Change", "period": "1984-99 vs 2000-24", "grid": "jrc", "src": "change", "sourced": True,
        # The JRC file stores change + 100; the GeoTIFF gives the change itself, -100 to +100 points
        "parts": [("gain", "Gain (green): water more often in 2000–24", _band(101, 200)),
                  ("loss", "Loss (red): water less often in 2000–24", _band(0, 99))],
        "lasting": {"gain": ("Lasting gain (green): +25 points or more", _band(125, 200)),
                    "loss": ("Lasting loss (red): −25 points or more", _band(0, 75))},
        "values": "−100 to +100 = change in how often water was seen, in percentage points, from 1984–99 to 2000–24",
        "signed": True,
    },
    "erosion": {
        "title": "River Erosion & Accretion", "period": "1980s to 2024", "grid": "jrc", "src": "erosion",
        "parts": [
            ("1", "Land lost to permanent water", _eq(1)),
            ("2", "Land turned seasonally wet", _eq(2)),
            ("3", "Regained from permanent water", _eq(3)),
            ("4", "Regained from seasonal water", _eq(4)),
            ("5", "Water in both years", _eq(5)),
            ("6", "Wet at times, no lasting change", _eq(6)),
        ],
        "values": "1–6 = the classes above; Indus floodplain only",
    },
    "monthly": {
        "title": "Seasonality (monthly water)", "period": "one month, 1984-2021", "grid": "month",
        "parts": [("usual", "Usual water for the month", None),
                  ("above", "Water well above usual: likely flood", None),
                  ("unseen", "Not seen: cloud or no image", None)],
        "values": "band 1: 1 usual water, 2 water well above usual, 3 not seen; band 2: % of the 280 m cell under water",
    },
    "landloss": {
        "title": "Land Loss & Deposition", "period": "winter 2018-19 to winter 2025-26", "grid": "utm",
        "parts": [("1", "Land lost to the river", _eq(1)), ("2", "Land deposited", _eq(2)), ("3", "Water in both", _eq(3))],
        "values": "1 land lost to the river, 2 land deposited, 3 water in both; Sukkur reach, Sentinel-2, 20 m, UTM 42N",
    },
}


class ExportError(Exception):
    """A request that cannot be met; the message is shown to the user."""


# ---------------------------------------------------------------- areas
_outline = None


def outline():
    global _outline
    if _outline is None:
        _outline = national_outline()
    return _outline


def _slug(s):
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")


def _view_box(arg):
    try:
        w, s, e, n = (float(v) for v in arg.split(","))
    except ValueError:
        raise ExportError("The map view could not be read") from None
    return w, s, e, n


def area_label(area):
    kind, _, arg = area.partition(":")
    if kind == "view":
        return "view_" + "_".join(f"{v:.2f}" for v in _view_box(arg))
    return area_geometry(area)[1]


def area_geometry(area):
    """WGS 84 geometry and a file-name label for 'pakistan', 'district:<id>', 'province:<name>' or 'view:w,s,e,n'.
    Always inside the outline the map's water layers are clipped to."""
    kind, _, arg = area.partition(":")
    if kind == "pakistan":
        return outline(), "pakistan"
    if kind in ("district", "province"):
        fc = json.loads((DATA_OUT / f"{kind}s.geojson").read_text(encoding="utf-8"))
        key = (lambda p: str(p["id"]) == arg) if kind == "district" else (lambda p: p["name"] == arg)
        f = next((f for f in fc["features"] if key(f["properties"])), None)
        if not f:
            raise ExportError(f"Unknown {kind}: {arg}")
        g = shapely.make_valid(shapely.geometry.shape(f["geometry"])).intersection(outline())
        return g, f"{kind}_{_slug(f['properties']['name'])}"
    if kind == "view":
        w, s, e, n = _view_box(arg)
        g = shapely.box(max(w, 60), max(s, 20), min(e, 80), min(n, 40)).intersection(outline())
        if g.is_empty:
            raise ExportError("The map view does not include Pakistan")
        return g, area_label(area)
    raise ExportError(f"Unknown area: {area}")


def pixel_ha(lat, size_x, size_y=None):
    """Hectares of one pixel of size_x by size_y degrees at this latitude."""
    return size_x * 111.320 * math.cos(math.radians(lat)) * (size_y or size_x) * KM_LAT * 100


def drop_small(codes, min_px):
    """Clear patches of chosen pixels (8-connected, whatever their part) of fewer than min_px pixels."""
    if min_px <= 1:
        return codes
    lab, _ = label(codes > 0, structure=np.ones((3, 3), bool))
    small = np.bincount(lab.ravel()) < min_px
    small[0] = False
    codes[small[lab]] = 0
    return codes


# ---------------------------------------------------------------- writers
class VectorWriter:
    """Polygons streamed into a GeoJSON file or a shapefile, block by block."""

    def __init__(self, path, fmt, layer, parts, meta):
        self.path, self.fmt, self.n, self.layer = path, fmt, 0, layer
        self.labels = {i + 1: (pid, label) for i, (pid, label, _) in enumerate(parts)}
        if fmt == "geojson":
            self.f = open(path, "w", encoding="utf-8", newline="\n")
            head = {"type": "FeatureCollection", "name": layer, "parts": {p: lab for p, lab in self.labels.values()}, **meta}
            self.f.write(json.dumps(head, ensure_ascii=False)[:-1] + ',"features":[\n')
        else:
            self.shp = path.with_name(path.stem + "_shp").with_suffix(".shp")
            self.first = True

    def add(self, geoms, codes):
        """geoms: shapely polygons in WGS 84; codes: the part number of each (1-based)."""
        if not len(geoms):
            return
        self.n += len(geoms)
        if self.n > MAX_SHAPES:
            raise ExportError(f"Over {MAX_SHAPES:,} polygons: leave out small patches, choose a smaller area or fewer parts, or download a GeoTIFF")
        lat = shapely.get_coordinates(shapely.centroid(geoms))[:, 1]
        ha = shapely.area(geoms) * (111.320 * np.cos(np.radians(lat))) * KM_LAT * 100
        if self.fmt == "geojson":
            gj = shapely.to_geojson(shapely.set_precision(geoms, 1e-6))
            rows = ['{"type":"Feature","properties":{"part":%s,"area_ha":%.2f},"geometry":%s}'
                    % (json.dumps(self.labels[int(c)][0]), a, g) for g, c, a in zip(gj, codes, ha)]
            self.f.write((",\n" if self.n > len(geoms) else "") + ",\n".join(rows))
        else:
            import geopandas as gpd
            import pyogrio
            df = gpd.GeoDataFrame({"part": [self.labels[int(c)][0] for c in codes],
                                   "label": [self.labels[int(c)][1] for c in codes],
                                   "area_ha": np.round(ha, 2)}, geometry=list(geoms), crs="EPSG:4326")
            pyogrio.write_dataframe(df, self.shp, append=not self.first, encoding="UTF-8")
            self.first = False

    def abort(self):
        if self.fmt == "geojson" and not self.f.closed:
            self.f.close()
        elif self.fmt == "shp":
            for p in self.shp.parent.glob(self.shp.stem + ".*"):
                p.unlink()

    def close(self):
        if self.fmt == "geojson":
            self.f.write("\n]}\n")
            self.f.close()
            return self.path
        if self.first:
            raise ExportError("Nothing of the chosen parts in this area")
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as z:
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                p = self.shp.with_suffix(ext)
                if p.exists():
                    z.write(p, f"{self.layer}{ext}")
                    p.unlink()
        return self.path


def polygons(codes, transform, to_wgs84=None):
    """Polygons of each run of equal non-zero codes."""
    geoms, vals = [], []
    for g, v in features.shapes(codes, mask=codes > 0, transform=transform, connectivity=4):
        geoms.append(shapely.geometry.shape(g))
        vals.append(v)
    geoms = np.array(geoms, dtype=object)
    if to_wgs84 is not None and len(geoms):
        geoms = shapely.transform(geoms, to_wgs84)
    return geoms, np.array(vals)


# ---------------------------------------------------------------- grids
def _source_mask(source, r0, r1, c0, c1):
    if not source:
        return None
    if not granule_path("watertype", (0, 0)).exists():
        raise ExportError("The water type raster (data/derived/watertype/) is not on this server, so downloads can't be limited to one water source")
    return np.isin(floodplain_common.mosaic("watertype", r0, r1, c0, c1, fill=0), WATER_GROUPS[source])


def export_jrc(spec, chosen, geom, fmt, path, progress, opts, meta):
    if not granule_path(spec["src"], (0, 0)).exists():
        raise ExportError(f"The source data for {spec['title']} is not on this server")
    w, s, e, n = geom.bounds
    r0, c0 = max(0, math.floor((LAT0 - n) / RES)), max(0, math.floor((w - LON0) / RES))
    r1, c1 = min(MOSAIC, math.ceil((LAT0 - s) / RES)), min(MOSAIC, math.ceil((e - LON0) / RES))
    if r1 <= r0 or c1 <= c0:
        raise ExportError("The area is empty")
    signed = spec.get("signed")
    nodata = -128 if signed else 255
    pad = PAD if opts["min_ha"] else 0
    blocks = [(rb, cb) for rb in range(r0, r1, BLOCK) for cb in range(c0, c1, BLOCK)]
    out = writer = None
    if fmt == "tif":
        tf = Affine(RES, 0, LON0 + c0 * RES, 0, -RES, LAT0 - r0 * RES)
        out = rasterio.open(path, "w", driver="GTiff", width=c1 - c0, height=r1 - r0, count=1, dtype="int8" if signed else "uint8",
                            nodata=nodata, crs="EPSG:4326", transform=tf, tiled=True, blockxsize=512, blockysize=512,
                            compress="deflate", BIGTIFF="IF_SAFER", SPARSE_OK=True)
        out.update_tags(**meta)
    else:
        writer = VectorWriter(path, fmt, spec["src"], chosen, meta)
    found = 0
    try:
        for i, (rb, cb) in enumerate(blocks):
            progress(i / len(blocks))
            re_, ce = min(rb + BLOCK, r1), min(cb + BLOCK, c1)
            if shapely.clip_by_rect(geom, LON0 + cb * RES, LAT0 - re_ * RES, LON0 + ce * RES, LAT0 - rb * RES).is_empty:
                continue
            # Read with a margin so patch sizes are judged across block edges, then cut the margin off
            pr0, pr1, pc0, pc1 = rb - pad, re_ + pad, cb - pad, ce + pad
            bbox = (LON0 + pc0 * RES, LAT0 - pr1 * RES, LON0 + pc1 * RES, LAT0 - pr0 * RES)
            part = shapely.clip_by_rect(geom, *bbox)
            a = floodplain_common.mosaic(spec["src"], pr0, pr1, pc0, pc1)
            inside = features.rasterize([(part, 1)], out_shape=a.shape, transform=Affine(RES, 0, bbox[0], 0, -RES, bbox[3]),
                                        fill=0, dtype="uint8").astype(bool)
            src_mask = _source_mask(opts["source"], pr0, pr1, pc0, pc1)
            if src_mask is not None:
                inside &= src_mask
            codes = np.zeros(a.shape, np.uint8)
            for k, (_, _, test) in enumerate(chosen, 1):
                codes[test(a) & inside & (codes == 0)] = k
            if opts["min_ha"]:
                codes = drop_small(codes, math.ceil(opts["min_ha"] / pixel_ha((bbox[1] + bbox[3]) / 2, RES)))
            codes, a = codes[pad:pad + re_ - rb, pad:pad + ce - cb], a[pad:pad + re_ - rb, pad:pad + ce - cb]
            if not codes.any():
                continue
            found += 1
            if out is not None:
                vals = (a.astype(np.int16) - 100).astype(np.int8) if signed else a
                out.write(np.where(codes > 0, vals, nodata).astype(out.dtypes[0]), 1, window=Window(cb - c0, rb - r0, ce - cb, re_ - rb))
            else:
                writer.add(*polygons(codes, Affine(RES, 0, LON0 + cb * RES, 0, -RES, LAT0 - rb * RES)))
    except BaseException:
        if writer is not None:
            writer.abort()
        raise
    finally:
        if out is not None:
            out.close()
    if not found:
        if writer is not None:
            writer.abort()
        raise ExportError("Nothing of the chosen parts in this area")
    return path if out is not None else writer.close()


def export_month(spec, chosen, geom, fmt, path, progress, opts, meta):
    from prepare_timeline import FIRST, LAST, OUTSIDE, UNSEEN, UNUSUAL_POINTS, WORK
    try:
        y, m = (int(v) for v in opts["month"].split("_"))
    except (AttributeError, ValueError):
        raise ExportError("Pick a month on the Seasonality layer first") from None
    if not FIRST <= (y, m) <= LAST:
        raise ExportError(f"The monthly record runs from {FIRST[1]}/{FIRST[0]} to {LAST[1]}/{LAST[0]}")
    share_path = WORK / f"share_{y}.tif"
    if not share_path.exists() or not (WORK / "normal.tif").exists():
        raise ExportError("The monthly water record (data/cache/timeline/) is not on this server")
    with rasterio.open(share_path) as d, rasterio.open(WORK / "normal.tif") as nd:
        w, s, e, n = geom.bounds
        (r0, r1), (c0, c1) = d.index(w, n), d.index(e, s)
        r0, c0 = max(0, r0), max(0, c0)
        r1, c1 = min(d.height, r1 + 1), min(d.width, c1 + 1)
        if r1 <= r0 or c1 <= c0:
            raise ExportError("The monthly record does not cover this area")
        win = Window(c0, r0, c1 - c0, r1 - r0)
        share = d.read(m, window=win)
        normal = nd.read(m, window=win)
        tf = d.window_transform(win)
    progress(0.3)
    inside = features.rasterize([(geom, 1)], out_shape=share.shape, transform=tf, fill=0, dtype="uint8").astype(bool) & (share != OUTSIDE)
    seen = share <= 100
    wet = seen & (share > 0)
    above = wet & (normal <= 100) & (share.astype(np.int16) - normal >= UNUSUAL_POINTS)
    tests = {"usual": wet & ~above, "above": above, "unseen": share == UNSEEN}
    codes = np.zeros(share.shape, np.uint8)
    for k, (pid, _, _) in enumerate(chosen, 1):
        codes[tests[pid] & inside & (codes == 0)] = k
    codes = drop_small(codes, math.ceil(opts["min_ha"] / pixel_ha((s + n) / 2, tf.a)))
    if not codes.any():
        raise ExportError("Nothing of the chosen parts in this area that month")
    if fmt == "tif":
        cls = np.zeros(share.shape, np.uint8)
        for pid, v in (("usual", 1), ("above", 2), ("unseen", 3)):
            cls[tests[pid]] = v
        keep = codes > 0
        with rasterio.open(path, "w", driver="GTiff", width=share.shape[1], height=share.shape[0], count=2, dtype="uint8", nodata=255,
                           crs="EPSG:4326", transform=tf, compress="deflate") as out:
            out.write(np.where(keep, cls, 255), 1)
            out.write(np.where(keep & seen, share, 255), 2)
            out.set_band_description(1, "class: 1 usual water, 2 well above usual, 3 not seen")
            out.set_band_description(2, "% of the cell under water")
            out.update_tags(**meta)
        return path
    writer = VectorWriter(path, fmt, f"water_{y}_{m:02d}", chosen, meta)
    writer.add(*polygons(codes, tf))
    return writer.close()


def export_utm(spec, chosen, geom, fmt, path, progress, opts, meta):
    from pyproj import Transformer
    from prepare_land_change import land_change_classes
    lc = land_change_classes()
    cls, tf, crs = lc["classes"], lc["tf"], lc["crs"]
    progress(0.4)
    to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    g_utm = shapely.transform(geom, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))
    b = lc["bounds"]
    g_utm = shapely.clip_by_rect(g_utm, b.left, b.bottom, b.right, b.top)
    if g_utm.is_empty:
        raise ExportError("This layer covers the Sukkur reach only, which is outside the chosen area")
    inside = features.rasterize([(g_utm, 1)], out_shape=cls.shape, transform=tf, fill=0, dtype="uint8").astype(bool)
    codes = np.zeros(cls.shape, np.uint8)
    for k, (_, _, test) in enumerate(chosen, 1):
        codes[test(cls) & inside & (codes == 0)] = k
    codes = drop_small(codes, math.ceil(opts["min_ha"] * 1e4 / (tf.a * -tf.e)))
    if not codes.any():
        raise ExportError("Nothing of the chosen parts in this area")
    if fmt == "tif":
        with rasterio.open(path, "w", driver="GTiff", width=cls.shape[1], height=cls.shape[0], count=1, dtype="uint8", nodata=0,
                           crs=crs, transform=tf, compress="deflate") as out:
            out.write(np.where(codes > 0, cls, 0), 1)
            out.update_tags(**meta)
        return path
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    writer = VectorWriter(path, fmt, "landloss", chosen, meta)
    writer.add(*polygons(codes, tf, lambda xy: np.column_stack(to_ll.transform(xy[:, 0], xy[:, 1]))))
    return writer.close()


# ---------------------------------------------------------------- entry point
SOURCES = {"extent": "JRC Global Surface Water v1.5 (EC JRC / Google), Landsat 1984-2024, 30 m",
           "erosion": "Derived from JRC Global Surface Water v1.5 by backend/pipeline/analyse_floodplain.py",
           "month": "JRC Monthly Water History v1.4, summed into 0.0025 degree cells in Earth Engine",
           "utm": "Sentinel-2, NDMA Earth Engine exports (backend/earth_engine/indus_land_change.js)"}


def plan(layer, parts, area, fmt, source=None, month=None, lasting=False, min_ha=0):
    """Check a request; returns (spec, chosen parts, options, download name, cache path)."""
    spec = LAYERS.get(layer)
    if not spec:
        raise ExportError(f"Unknown layer: {layer}")
    if fmt not in FORMATS:
        raise ExportError(f"Unknown format: {fmt}")
    want = {p for p in parts if p}
    lasting = bool(lasting) and "lasting" in spec
    chosen = [(p, *spec["lasting"][p]) if lasting else (p, lab, test) for p, lab, test in spec["parts"] if p in want]
    if not chosen:
        raise ExportError("Choose at least one part of the layer")
    try:
        min_ha = float(min_ha or 0)
    except ValueError:
        raise ExportError("Unknown patch size") from None
    if min_ha not in MIN_HA:
        raise ExportError(f"Smallest patch must be one of {', '.join(f'{v:g}' for v in MIN_HA)} ha")
    source = source if spec.get("sourced") and source not in (None, "", "all") else None
    if source and source not in WATER_GROUPS:
        raise ExportError(f"Unknown water source: {source}")
    month = month if spec["grid"] == "month" else None
    opts = {"source": source, "month": month, "lasting": lasting, "min_ha": min_ha}
    key = json.dumps([layer, sorted(p[0] for p in chosen), area, fmt, opts], sort_keys=True)
    digest = hashlib.sha1(key.encode()).hexdigest()[:10]
    what = "all" if len(chosen) == len(spec["parts"]) else "_".join(p[0] for p in chosen)
    name = "_".join(x for x in (layer, month, "lasting" if lasting else "", what, source, f"min{min_ha:g}ha" if min_ha else "", area_label(area)) if x)
    return spec, chosen, opts, name + SUFFIX[fmt], OUT / (digest + SUFFIX[fmt])


def run(layer, parts, area, fmt, source=None, month=None, lasting=False, min_ha=0, progress=lambda f: None):
    """Write the file (or reuse a cached one); returns (path, download name)."""
    spec, chosen, opts, name, path = plan(layer, parts, area, fmt, source, month, lasting, min_ha)
    if path.exists():
        return path, name
    OUT.mkdir(parents=True, exist_ok=True)
    geom, _ = area_geometry(area)
    meta = {"layer": spec["title"], "period": f"{month[:4]}-{month[5:]}" if month else spec["period"], "values": spec["values"],
            "parts_chosen": "; ".join(label for _, label, _ in chosen), "area": name.rsplit(".", 1)[0],
            "water_source": source or "all", "smallest_patch_ha": opts["min_ha"],
            "source": SOURCES.get(spec["grid"] if spec["grid"] != "jrc" else ("erosion" if layer == "erosion" else "extent")),
            "made_by": "Pakistan Surface Water Observatory (NDMA)", "made": time.strftime("%Y-%m-%d")}
    tmp = path.with_name(path.stem + ".part" + path.suffix)
    for stale in OUT.glob(path.stem + ".part*"):
        stale.unlink()
    try:
        export = {"jrc": export_jrc, "month": export_month, "utm": export_utm}[spec["grid"]]
        export(spec, chosen, geom, fmt, tmp, progress, opts, meta)
    except BaseException:
        for stale in OUT.glob(path.stem + ".part*"):
            stale.unlink()
        raise
    tmp.replace(path)
    progress(1)
    return path, name


def catalogue():
    """What the portal shows in its download panels."""
    return {k: {"title": v["title"], "period": v["period"], "values": v["values"], "sourced": bool(v.get("sourced")),
                "lasting": bool(v.get("lasting")), "parts": [[p, label] for p, label, _ in v["parts"]]} for k, v in LAYERS.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("layer", nargs="?")
    ap.add_argument("--parts", default="", help="comma-separated part ids; default all")
    ap.add_argument("--area", default="pakistan", help="pakistan, district:<id>, province:<name> or view:w,s,e,n")
    ap.add_argument("--format", default="tif", choices=list(FORMATS))
    ap.add_argument("--source", default="all", help="all, " + ", ".join(WATER_GROUPS))
    ap.add_argument("--month", help="YYYY_MM, for the monthly layer")
    ap.add_argument("--lasting", action="store_true", help="Occurrence Change: 25 points or more only")
    ap.add_argument("--min-ha", type=float, default=0, help="leave out smaller patches: " + ", ".join(f"{v:g}" for v in MIN_HA))
    ap.add_argument("--out", help="also copy the file here (a folder or a file name)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not a.layer:
        for k, v in catalogue().items():
            print(f"{k:11} {v['title']} ({v['period']})")
            for p, lab in v["parts"]:
                print(f"    {p:12} {lab}")
        return
    parts = a.parts.split(",") if a.parts else [p[0] for p in LAYERS[a.layer]["parts"]]
    t0 = time.time()
    last = [0]

    def progress(f):
        if f - last[0] >= 0.1 or f == 1:
            last[0] = f
            print(f"  {f * 100:3.0f}%  {time.time() - t0:5.0f} s", flush=True)

    path, name = run(a.layer, parts, a.area, a.format, a.source, a.month, a.lasting, a.min_ha, progress)
    print(f"{name}: {path} ({path.stat().st_size / 1e6:,.1f} MB, {time.time() - t0:.0f} s)")
    if a.out:
        import shutil
        dest = Path(a.out)
        if dest.is_dir():
            dest = dest / name
        shutil.copyfile(path, dest)
        print(f"copied to {dest}")


if __name__ == "__main__":
    try:
        main()
    except ExportError as e:
        sys.exit(str(e))
