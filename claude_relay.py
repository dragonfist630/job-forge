#!/usr/bin/env python3
"""
claude_relay.py -- lightweight HTTP proxy for the claude CLI.

Runs on the HOST (macOS). The Docker container calls
http://host.docker.internal:7071 to run `claude -p` because the claude
binary is a macOS ARM executable and cannot run inside the Linux container.

POST /claude
  Body: {"system": "...", "user": "..."}
  Response: {"out": "...", "err": "...", "code": 0}
"""

import json
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length))

        claude = shutil.which("claude")
        if not claude:
            self._respond({"out": "", "err": "claude not found in PATH", "code": 1})
            return

        try:
            p = subprocess.run(
                [claude, "--system-prompt", data.get("system", ""), "-p"],
                input=data.get("user", ""),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600,
            )
            self._respond({"out": p.stdout, "err": p.stderr, "code": p.returncode})
        except subprocess.TimeoutExpired:
            self._respond({"out": "", "err": "claude timed out", "code": 1})
        except Exception as e:
            self._respond({"out": "", "err": str(e), "code": 1})

    def _respond(self, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # silence access logs


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7071
    server = HTTPServer(("0.0.0.0", port), _Handler)
    print(f"claude relay listening on :{port}", flush=True)
    server.serve_forever()
