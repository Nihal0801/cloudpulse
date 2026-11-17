"""Run a local demo with real HTTP probes against controlled fixture endpoints."""

import argparse
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from cloudpulse.api import route
from cloudpulse.core import Target, load_targets, probe, run_cycle
from cloudpulse.seed import seed
from cloudpulse.storage import SQLiteStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", default=".data/cloudpulse.db")
    parser.add_argument(
        "--targets", help="JSON file of real public HTTPS targets; otherwise use demo fixtures"
    )
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(args.db)
    demo = not args.targets
    targets = (
        load_targets(Path(args.targets).read_text())
        if args.targets
        else [
            Target(key, name, "http://127.0.0.1:%d/fixtures/%s" % (args.port, key))
            for key, name in (
                ("gateway", "API Gateway"),
                ("billing", "Billing Service"),
                ("storage", "Object Storage"),
            )
        ]
    )
    control = {"failing": False}
    if demo:
        seed(targets, store)
    cycle_lock = threading.Lock()

    def local_probe(target):
        started = time.monotonic()
        connection = http.client.HTTPConnection("127.0.0.1", args.port, timeout=4)
        try:
            connection.request("GET", urlsplit(target.url).path)
            code = connection.getresponse().status
            return {
                "ok": code == 200,
                "code": code,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "error": None if code == 200 else "HTTP %s" % code,
            }
        finally:
            connection.close()

    def cycle():
        with cycle_lock:
            return run_cycle(
                targets,
                store,
                lambda t, e, s: print("ALERT: %s %s" % (t.name, e["type"])),
                probe_fn=local_probe if demo else probe,
            )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, headers, body):
            self.send_response(code)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if demo and path in {"/fixtures/" + t.id for t in targets}:
                time.sleep({"gateway": 0.03, "billing": 0.08, "storage": 0.05}[path.rsplit("/", 1)[1]])
                failed = path == "/fixtures/billing" and control["failing"]
                self.respond(
                    503 if failed else 200, {"Content-Type": "application/json"}, b'{"fixture":true}'
                )
                return
            self.respond(*route(path, "GET", targets, store, "demo" if demo else "local"))

        def do_HEAD(self):
            self.respond(
                *route(urlsplit(self.path).path, "HEAD", targets, store, "demo" if demo else "local")
            )

        def do_POST(self):
            # Demo actions require a same-origin browser request. No remote control API.
            if not demo or self.headers.get("Origin") != "http://127.0.0.1:%d" % args.port:
                self.respond(403, {}, b"Forbidden")
                return
            if self.path == "/api/demo/toggle":
                control["failing"] = not control["failing"]
                self.respond(200, {"Content-Type": "application/json"}, json.dumps(control).encode())
            elif self.path == "/api/demo/check":
                cycle()
                self.respond(200, {"Content-Type": "application/json"}, b'{"ok":true}')
            else:
                self.respond(404, {}, b"Not found")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                cycle()
            except Exception as exc:
                print("Check cycle failed: %s" % exc, flush=True)
            stop.wait(10 if demo else 60)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    print(
        "CloudPulse: http://127.0.0.1:%d (%s)" % (args.port, "demo fixtures" if demo else "public endpoints"),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
