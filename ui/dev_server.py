"""Local stand-in for Vercel: serves public/ and routes /api/<name> to api/<name>.py handlers.

    python ui/dev_server.py [port]

Loads the same handler classes Vercel runs, so what you see here is what deploys.
Reads only. Never used in production.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(ROOT, "api")
PUBLIC_DIR = os.path.join(ROOT, "public")


def _load_api_handlers() -> dict[str, type]:
    handlers: dict[str, type] = {}
    for fname in sorted(os.listdir(API_DIR)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        name = fname[:-3]
        spec = importlib.util.spec_from_file_location(f"api_{name}", os.path.join(API_DIR, fname))
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        handlers[name] = module.handler
    return handlers


class DevHandler(SimpleHTTPRequestHandler):
    api_handlers: dict[str, type] = {}

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            name = path[len("/api/"):].strip("/")
            api_handler = self.api_handlers.get(name)
            if api_handler is None:
                self.send_error(404, f"no function api/{name}.py")
                return
            # Vercel handlers only touch path/send_response/send_header/end_headers/wfile,
            # all of which this instance provides, so the unbound do_GET runs as-is.
            api_handler.do_GET(self)
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, fmt, *args):  # keep the terminal readable
        sys.stderr.write("%s %s\n" % (self.command, fmt % args))


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    DevHandler.api_handlers = _load_api_handlers()
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(DevHandler, directory=PUBLIC_DIR))
    print(f"Rehearsal Room dev server on http://127.0.0.1:{port}  (api: {', '.join(DevHandler.api_handlers)})")
    server.serve_forever()


if __name__ == "__main__":
    main()
