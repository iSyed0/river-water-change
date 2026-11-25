"""Local server for the portal.

Python's stock `http.server` has a tiny connection queue, so bursts of map-tile
requests get refused. This one uses a large queue and gzips JSON/GeoJSON.

    python backend/serve.py            # http://127.0.0.1:8765, this PC only
    python backend/serve.py 8080
    python backend/serve.py --lan      # also shared on the office network: http://<this PC's IP>:8765

It also relays NDMA's DSDI GeoServer layers under /dsdi/ (see DSDI below): browsers may not call DSDI from
another site (CORS), so the portal asks this server, which asks DSDI.

And it cuts the layer downloads under /api/export/ (backend/exports.py): GeoTIFF, GeoJSON or shapefile of any layer's
parts for an area, from the source data in data/. Those need the GIS libraries of the pipeline (rasterio, shapely,
geopandas); without them, or without data/, the portal works and only the downloads are unavailable.
"""
import gzip
import html
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1] / "frontend"   # the website
COMPRESS = (".json", ".geojson", ".js", ".css", ".html")


# ---------------------------------------------------------------- DSDI relay
# NDMA's Disaster Spatial Data Infrastructure. The layer list and map images come through the DSDI API with the
# public guest login; DSDI relays only map images (GetMap), so legends and click info come from its GeoServer.
DSDI = {
    "api": os.environ.get("DSDI_API", "http://172.18.7.13:8080/dsdi/api/v1"),
    "user": os.environ.get("DSDI_USER", "guest"),
    "password": os.environ.get("DSDI_PASSWORD", "guest"),
    "workspaces": ("gcop",),  # the others on the server are GeoServer's demo layers
}
CATALOG_TTL = 600  # seconds the layer list is kept before it is asked for again


class DsdiError(Exception):
    pass


class Dsdi:
    def __init__(self):
        self.lock = threading.Lock()
        self.token = None
        self.catalog = None  # {"layers": [...], "names": set(), "geoserver": url, "at": time}

    def _open(self, url, data=None, headers=None, timeout=20):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()
        except (urllib.error.URLError, OSError) as e:
            raise DsdiError(f"DSDI not reachable: {e}") from e

    def _login(self):
        body = json.dumps({"email": DSDI["user"], "password": DSDI["password"]}).encode()
        status, _, data = self._open(f"{DSDI['api']}/auth/login", body, {"Content-Type": "application/json"})
        if status != 200:
            raise DsdiError(f"DSDI guest login failed ({status})")
        self.token = json.loads(data)["data"]["token"]

    def api(self, path, timeout=20):
        """GET from the DSDI API as the guest user, signing in again once if the token has expired (24 h)."""
        for attempt in (0, 1):
            with self.lock:
                if not self.token or attempt:
                    self._login()
                token = self.token
            status, ctype, data = self._open(f"{DSDI['api']}{path}", headers={
                "Authorization": f"Bearer {token}", "X-Page-Key": "map"}, timeout=timeout)
            if status != 401:
                return status, ctype, data
        return status, ctype, data

    def layers(self):
        c = self.catalog
        if c and time.time() - c["at"] < CATALOG_TTL:
            return c
        status, _, data = self.api("/geoserver/layers")
        if status != 200:
            if c:
                return c  # keep using the last list
            raise DsdiError(f"DSDI layer list failed ({status})")
        d = json.loads(data)["data"]
        layers = [{"name": l["fullName"], "title": l["title"].strip()} for l in d["layers"]
                  if l["workspace"] in DSDI["workspaces"] and l.get("enabled", True)]
        self.catalog = {"layers": layers, "names": {l["name"] for l in layers},
                        "geoserver": d.get("geoserverUrl", "").rstrip("/"), "at": time.time()}
        return self.catalog

    def known(self, name):
        return name in self.layers()["names"]

    def geoserver(self, params, timeout=20):
        return self._open(f"{self.layers()['geoserver']}/wms?{urllib.parse.urlencode(params)}", timeout=timeout)


    def geometry(self, name):
        """'MultiPolygon', 'Point', ... for a vector layer; None for rasters or if GeoServer won't say."""
        status, ctype, data = self.geoserver({"service": "WFS", "version": "2.0.0", "request": "DescribeFeatureType",
                                              "typeNames": name, "outputFormat": "application/json"})
        if status != 200 or "json" not in ctype:
            return None
        try:
            props = json.loads(data)["featureTypes"][0]["properties"]
        except (ValueError, KeyError, IndexError):
            return None
        return next((p.get("localType") for p in props if str(p.get("type", "")).startswith("gml:")), None)


DSDI_CLIENT = Dsdi()

# Outlines only, for area layers: a faint dark edge under a thin pale line reads on the night, satellite and
# terrain basemaps, and stays apart from the portal's own dashed blue boundaries
OUTLINE_SLD = """<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld"><NamedLayer><Name>{name}</Name>\
<UserStyle><FeatureTypeStyle><Rule>\
<LineSymbolizer><Stroke><CssParameter name="stroke">#020611</CssParameter><CssParameter name="stroke-opacity">0.55</CssParameter>\
<CssParameter name="stroke-width">2.4</CssParameter><CssParameter name="stroke-linejoin">round</CssParameter></Stroke></LineSymbolizer>\
<LineSymbolizer><Stroke><CssParameter name="stroke">#f1f5f9</CssParameter><CssParameter name="stroke-opacity">0.9</CssParameter>\
<CssParameter name="stroke-width">0.9</CssParameter><CssParameter name="stroke-linejoin">round</CssParameter></Stroke></LineSymbolizer>\
</Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>"""


def outline_sld(name):
    return OUTLINE_SLD.format(name=html.escape(name))
BBOX = re.compile(r"^-?[\d.]+(,-?[\d.]+){3}$")
# Columns the click info leaves out: database ids, geometry bookkeeping and the fields every KML import carries
HIDDEN_PROPS = re.compile(r"^(fid|gid|id|objectid|objectid_\d+|shape_?(leng|length|area)|bbox|symbolid|altmode|base|clamped|"
                          r"extruded|snippet|popupinfo|folderpath|begin|end|timestamp|tessellate|visibility|draworder|icon)$", re.I)
TAG = re.compile(r"<[^>]*>?")


def clean_props(props):
    out = {}
    for k, v in (props or {}).items():
        if HIDDEN_PROPS.match(k) or v is None:
            continue
        if isinstance(v, str):
            v = " ".join(TAG.sub(" ", v).split())  # HTML left over from KML balloons
            if not v or (k.lower() == "name" and v == "Placemark"):
                continue
        out[k] = v
    return out


# ---------------------------------------------------------------- layer downloads
# Each download is cut in a background thread; the portal polls its progress, then fetches the file. A job's id is the
# file's cache name, so the same request from two people is one job, and a cut already made is ready at once.
EXPORTS = {}
EXPORT_LOCK = threading.Lock()
EXPORT_SLOTS = threading.Semaphore(2)  # cuts running at once; the rest wait their turn
_exports = None


def exports_module():
    global _exports
    if _exports is None:
        import exports  # backend/exports.py: loads rasterio and the pipeline, so only when first asked
        _exports = exports
    return _exports


def start_export(q):
    ex = exports_module()
    args = (q.get("layer", ""), q.get("parts", "").split(","), q.get("area", "pakistan"), q.get("format", "tif"),
            q.get("source"), q.get("month"), q.get("lasting") == "1", q.get("min_ha", "0"))
    *_, name, path = ex.plan(*args)
    jid = path.stem
    with EXPORT_LOCK:
        job = EXPORTS.get(jid)
        if job and job["state"] != "error" and (job["state"] != "done" or path.exists()):
            return jid, job
        job = EXPORTS[jid] = {"state": "done" if path.exists() else "queued", "progress": 1 if path.exists() else 0,
                              "name": name, "path": path, "error": None, "started": time.time()}
    if job["state"] == "done":
        return jid, job

    def work():
        with EXPORT_SLOTS:
            job["state"] = "running"
            try:
                ex.run(*args, progress=lambda f: job.update(progress=round(f, 3)))
                job.update(state="done", progress=1)
            except ex.ExportError as e:
                job.update(state="error", error=str(e))
            except Exception as e:  # noqa: BLE001  (shown to the user; the details go to this window)
                traceback.print_exc()
                job.update(state="error", error=f"The download failed: {e}")

    threading.Thread(target=work, daemon=True).start()
    return jid, job


def job_view(jid, job):
    out = {"id": jid, "state": job["state"], "progress": job["progress"], "name": job["name"], "error": job["error"]}
    if job["state"] == "done" and job["path"].exists():
        out["size"] = job["path"].stat().st_size
    return out


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".geojson": "application/geo+json"}

    def do_GET(self):
        if self.path.startswith("/dsdi/"):
            return self.dsdi()
        if self.path.startswith("/api/export/"):
            return self.export()
        return super().do_GET()

    def export(self):
        url = urllib.parse.urlsplit(self.path)
        route = url.path[len("/api/export/"):]
        q = dict(urllib.parse.parse_qsl(url.query))
        try:
            ex = exports_module()
        except Exception as e:  # noqa: BLE001
            return self.reply_json(503, {"error": f"Downloads are not available on this server ({e})"})
        if route == "catalogue":
            return self.reply_json(200, {"layers": ex.catalogue(), "formats": ex.FORMATS})
        if route == "start":
            try:
                jid, job = start_export(q)
            except ex.ExportError as e:
                return self.reply_json(400, {"error": str(e)})
            return self.reply_json(200, job_view(jid, job))
        job = EXPORTS.get(q.get("id", ""))
        if not job:
            return self.reply_json(404, {"error": "Unknown download; start it again"})
        if route == "status":
            return self.reply_json(200, job_view(q["id"], job))
        if route == "file":
            if job["state"] != "done" or not job["path"].exists():
                return self.reply_json(409, {"error": "Not ready"})
            mime = {".tif": "image/tiff", ".geojson": "application/geo+json", ".zip": "application/zip"}[job["path"].suffix]
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(job["path"].stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{job["name"]}"')
            self.end_headers()
            with open(job["path"], "rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            return None
        return self.reply_json(404, {"error": "Unknown route"})

    def reply(self, status, ctype, body, cache=0):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", f"max-age={cache}" if cache else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, status, obj, cache=0):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.reply(status, "application/json; charset=utf-8", body, cache)

    def dsdi(self):
        url = urllib.parse.urlsplit(self.path)
        route = url.path[len("/dsdi/"):]
        q = dict(urllib.parse.parse_qsl(url.query))
        try:
            if route == "layers":
                c = DSDI_CLIENT.layers()
                return self.reply_json(200, {"layers": c["layers"], "server": DSDI["api"].split("/api/")[0]})

            layer = q.get("layer", "")
            if not DSDI_CLIENT.known(layer):
                return self.reply_json(404, {"error": "Unknown DSDI layer"})

            if route == "tile":
                if not BBOX.match(q.get("bbox", "")):
                    return self.reply_json(400, {"error": "bbox required"})
                params = {"service": "WMS", "version": "1.1.0", "request": "GetMap", "layers": layer, "styles": "",
                          "srs": "EPSG:3857", "width": "256", "height": "256", "format": "image/png",
                          "transparent": "true", "bbox": q["bbox"]}
                if q.get("style") == "outline":
                    params["sld_body"] = outline_sld(layer)
                status, ctype, data = DSDI_CLIENT.api(f"/geoserver/wms-tile?{urllib.parse.urlencode(params)}", timeout=30)
                if status != 200 or not ctype.startswith("image/"):
                    return self.reply_json(502, {"error": f"DSDI tile failed ({status})"})
                return self.reply(200, ctype, data, cache=3600)

            if route == "detail":
                status, _, data = DSDI_CLIENT.api(f"/geoserver/layers/detail?{urllib.parse.urlencode({'layer': layer})}")
                if status != 200:
                    return self.reply_json(502, {"error": f"DSDI layer detail failed ({status})"})
                d = json.loads(data)["data"]
                geom = DSDI_CLIENT.geometry(layer) if d.get("type") != "RASTER" else None
                return self.reply_json(200, {"name": layer, "title": d.get("title"), "type": d.get("type"), "geometry": geom,
                                             "bounds": d.get("bounds"), "srs": d.get("srs")}, cache=3600)

            if route == "legend":
                params = {"service": "WMS", "version": "1.0.0", "request": "GetLegendGraphic", "format": "image/png",
                          "layer": layer, "legend_options": "fontColor:0xE2E8F0;fontAntiAliasing:true;fontSize:11;bgColor:0x0B1222;dpi:96"}
                if q.get("style") == "outline":
                    params["sld_body"] = outline_sld(layer)
                status, ctype, data = DSDI_CLIENT.geoserver(params)
                if status != 200 or not ctype.startswith("image/"):
                    return self.reply_json(502, {"error": f"Legend failed ({status})"})
                return self.reply(200, ctype, data, cache=3600)

            if route == "info":
                if not BBOX.match(q.get("bbox", "")):
                    return self.reply_json(400, {"error": "bbox required"})
                status, ctype, data = DSDI_CLIENT.geoserver({
                    "service": "WMS", "version": "1.1.1", "request": "GetFeatureInfo", "layers": layer,
                    "query_layers": layer, "srs": "EPSG:4326", "bbox": q["bbox"], "width": "9", "height": "9",
                    "x": "4", "y": "4", "info_format": "application/json", "feature_count": "3", "buffer": "6"})
                if status != 200 or "json" not in ctype:
                    return self.reply_json(502, {"error": f"Feature info failed ({status})"})
                feats = [clean_props(f.get("properties")) for f in json.loads(data).get("features", [])]
                return self.reply_json(200, {"layer": layer, "features": feats})

            return self.reply_json(404, {"error": "Unknown DSDI route"})
        except DsdiError as e:
            return self.reply_json(502, {"error": str(e)})
        except (ValueError, KeyError) as e:
            return self.reply_json(502, {"error": f"Unexpected DSDI reply: {e}"})

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            path = path / "index.html"
        if path.suffix in COMPRESS and path.is_file() and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(path.read_bytes(), 6)
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(str(path)))
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return _Body(body)
        return super().send_head()

    def log_message(self, fmt, *args):  # keep the console quiet; tiles 404 by design
        pass


class _Body:
    def __init__(self, data):
        self.data = data

    def read(self, *_):
        data, self.data = self.data, b""
        return data

    def close(self):
        pass


class Server(ThreadingHTTPServer):
    request_queue_size = 512
    daemon_threads = True
    # On Windows "reuse address" lets a second server take the same port while an old one keeps answering, so a
    # restarted portal could silently keep serving old code. There, refuse to start if the port is taken.
    allow_reuse_address = os.name != "nt"

    def handle_error(self, request, client_address):
        # The map cancels tile requests it no longer needs while panning; that is normal, not an error
        if isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def lan_ip():
    """This PC's address on the office network: the one it would use to reach another machine."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            return s.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--lan"]
    lan = "--lan" in sys.argv[1:]
    port = int(args[0]) if args else 8765
    try:
        Server(("0.0.0.0" if lan else "127.0.0.1", port), Handler).server_close()
    except OSError:
        print(f"Port {port} is already in use: the portal is probably already running in another window.")
        print("Close the other portal windows (or end python.exe in Task Manager), then start it again.")
        sys.exit(1)
    with Server(("0.0.0.0" if lan else "127.0.0.1", port), partial(Handler, directory=str(PORTAL))) as httpd:
        print(f"Portal running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
        if lan:
            print(f"Other PCs on the network: http://{lan_ip()}:{port}")
        httpd.serve_forever()
