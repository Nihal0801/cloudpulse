import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LocalIntegrationTests(unittest.TestCase):
    def test_demo_failure_recovery_and_origin_protection(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cloudpulse.local",
                    "--port",
                    str(port),
                    "--db",
                    os.path.join(directory, "demo.db"),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self.addCleanup(process.stderr.close)
            origin = "http://127.0.0.1:%s" % port

            def get():
                with urlopen(origin + "/api/status", timeout=5) as response:
                    return json.load(response)

            def post(path, allowed=True):
                request = Request(
                    origin + "/api/demo/" + path,
                    method="POST",
                    headers={"Origin": origin if allowed else "https://attacker.example"},
                )
                with urlopen(request, timeout=5) as response:
                    return json.load(response)

            try:
                for _ in range(60):
                    try:
                        data = get()
                        break
                    except (URLError, ConnectionError):
                        time.sleep(0.1)
                else:
                    self.fail("Local server did not start")
                self.assertEqual(data["mode"], "demo")
                self.assertEqual(len(data["services"]), 3)
                self.assertGreaterEqual(data["services"][0]["sample_count"], 99)
                with self.assertRaises(HTTPError) as rejected:
                    post("toggle", allowed=False)
                self.assertEqual(rejected.exception.code, 403)
                post("toggle")
                # Ensure every explicit check gets a unique second, including the startup cycle.
                for _ in range(2):
                    time.sleep(1.1)
                    post("check")
                billing = next(s for s in get()["services"] if s["id"] == "billing")
                self.assertEqual(billing["status"], "down")
                self.assertEqual(billing["events"][-1]["type"], "opened")
                post("toggle")
                time.sleep(1.1)
                post("check")
                billing = next(s for s in get()["services"] if s["id"] == "billing")
                self.assertEqual(billing["status"], "up")
                self.assertEqual(billing["events"][-1]["type"], "resolved")
            finally:
                process.terminate()
                process.wait(timeout=10)
