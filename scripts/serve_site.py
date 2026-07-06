"""Serve the built _site directory for local verification of the Pages build."""

from __future__ import annotations

import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "_site"

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    os.chdir(SITE)
    handler = partial(SimpleHTTPRequestHandler, directory=str(SITE))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"serving {SITE} on http://127.0.0.1:{port}")
        httpd.serve_forever()
