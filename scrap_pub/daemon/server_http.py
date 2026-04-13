"""
server_http.py — Minimal HTTP server (stdlib) that serves the web UI.

The HTTP server runs in a daemon thread. It handles:
  GET /          → Web UI HTML (from ui.py)
  GET /health    → {"ok": true}  (useful for monitoring)
  All other GETs → 404
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


def start_http_server(config: "Config") -> None:
    """Start the HTTP server (blocking — run in a thread)."""
    from .ui import HTML_UI

    port = config.http_port

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # Suppress noisy access log

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/" or path == "/index.html":
                body = HTML_UI.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/health":
                body = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(("localhost", port), Handler)
    print(f"[http] Serving web UI at http://localhost:{port}")
    server.serve_forever()
