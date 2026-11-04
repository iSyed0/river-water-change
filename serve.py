"""Local server for the portal.

Python's stock `http.server` has a tiny connection queue, so bursts of map-tile
requests get refused. This one uses a large queue and gzips JSON/GeoJSON.

    python serve.py            # http://127.0.0.1:8765
    python serve.py 8080
"""
import gzip
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTAL = Path(__file__).resolve().parent / "portal"
COMPRESS = (".json", ".geojson", ".js", ".css", ".html")


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".geojson": "application/geo+json"}

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


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    with Server(("127.0.0.1", port), partial(Handler, directory=str(PORTAL))) as httpd:
        print(f"Portal running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
        httpd.serve_forever()
