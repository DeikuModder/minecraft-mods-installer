import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote


def build_manifest(root):
    entries = []
    with os.scandir(root) as it:
        for e in it:
            if e.is_file():
                entries.append({"name": e.name, "size": e.stat().st_size})
    entries.sort(key=lambda e: e["name"].lower())
    return entries


class _ModsHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "InstaladorMods/1.0"

    def setup(self):
        super().setup()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def log_message(self, fmt, *args):
        pass

    def _text(self, code, body):
        body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            pass

    def _json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            pass

    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0]).rstrip("/") or "/"
        if path == "/manifest":
            self._json({"mods": build_manifest(self.server.root)})
            return
        if path.startswith("/mods/"):
            nombre = path[len("/mods/"):]
            if nombre in ("", ".", "..") or "/" in nombre or "\\" in nombre:
                self._text(400, "nombre no valido")
                return
            full = os.path.join(self.server.root, nombre)
            if not os.path.isfile(full):
                self._text(404, "mod no encontrado")
                return
            size = os.path.getsize(full)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                with open(full, "rb") as f:
                    while True:
                        chunk = f.read(131072)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except OSError:
                pass
            self.server.register_download()
            return
        self._text(404, "endpoint no encontrado")


class ModsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, root):
        self.root = root
        self.downloads = 0
        self._lock = threading.Lock()
        super().__init__(addr, _ModsHandler)

    def register_download(self):
        with self._lock:
            self.downloads += 1