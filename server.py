"""Serve the landing page in docs/ over HTTP (used by Render web services).

The desktop app itself is PyQt6 and cannot run on a server, so this only
serves the static site. Standard library only — no extra dependencies.

    python server.py            # listens on $PORT (Render sets it) or 8000
"""

from __future__ import annotations

import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent / "docs"


class SiteHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def send_error(self, code, message=None, explain=None):
        # Unknown paths fall back to the landing page instead of a bare 404.
        if code == 404 and self.command in ("GET", "HEAD"):
            self.path = "/index.html"
            return SimpleHTTPRequestHandler.do_GET(self) if self.command == "GET" else self.do_HEAD()
        return super().send_error(code, message, explain)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    handler = partial(SiteHandler, directory=str(SITE_DIR))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"Serving {SITE_DIR} on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
