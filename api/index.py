"""Vercel Serverless Function entry point for RUSHI THE TRADER Terminal."""

import os
import sys

# Ensure repository root is on sys.path so 'forex' package can be imported
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from forex.dashboard import DashboardRequestHandler, MOBILE_BLOCKED_HTML


class handler(DashboardRequestHandler):
    """Vercel Serverless Function HTTP request handler."""

    def _resolve_vercel_path(self) -> None:
        forwarded = self.headers.get("x-forwarded-uri") or self.headers.get("x-now-route-matches")
        if forwarded:
            self.path = forwarded
        elif self.path in ("/", "/index.html", "/api", "/api/", "/api/index.py", "/api/index"):
            self.path = "/"

    def do_GET(self) -> None:
        self._resolve_vercel_path()
        if self.path in ("/", "/index.html"):
            user_agent = self.headers.get("User-Agent", "").lower()
            if any(m in user_agent for m in ("mobile", "android", "iphone", "ipad", "ipod", "blackberry", "iemobile", "opera mini")):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("X-Paper-Trading-Only", "true")
                self.end_headers()
                self.wfile.write(MOBILE_BLOCKED_HTML.encode("utf-8"))
                return
        super().do_GET()

    def do_POST(self) -> None:
        self._resolve_vercel_path()
        super().do_POST()

    def do_OPTIONS(self) -> None:
        self._resolve_vercel_path()
        super().do_OPTIONS()

