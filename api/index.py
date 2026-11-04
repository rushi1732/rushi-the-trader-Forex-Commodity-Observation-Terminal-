"""Vercel Serverless Function entry point for RUSHI THE TRADER Terminal."""

import os
import sys

# Ensure repository root is on sys.path so 'forex' package can be imported
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from forex.dashboard import DashboardRequestHandler


class handler(DashboardRequestHandler):
    """Vercel Serverless Function HTTP request handler."""

    def do_GET(self) -> None:
        # Route root or index paths to the dashboard UI
        if self.path in ("/", "/index.html", "/api", "/api/", "/api/index.py", "/api/index"):
            self.path = "/"
        super().do_GET()

    def do_POST(self) -> None:
        super().do_POST()

