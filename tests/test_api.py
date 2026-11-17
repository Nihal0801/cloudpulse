import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import OK, TARGET

from cloudpulse.api import route
from cloudpulse.handlers import api, checker, notify
from cloudpulse.storage import SQLiteStore


class APITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SQLiteStore(Path(self.directory.name) / "test.db")

    def test_status_and_security_headers(self):
        code, headers, body = route("/api/status", "GET", [TARGET], self.store)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["services"][0]["status"], "pending")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_assets_and_health(self):
        for path in ("/", "/app.js", "/style.css", "/favicon.svg", "/healthz"):
            code, _, body = route(path, "GET", [TARGET], self.store)
            self.assertEqual(code, 200, path)
            self.assertTrue(body)

    def test_reject_traversal_and_unknown_routes(self):
        for path in ("/../cloudpulse/core.py", "/.env", "/api/targets", "/missing"):
            self.assertEqual(route(path, "GET", [TARGET], self.store)[0], 404)

    def test_cloud_routes_are_read_only(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertEqual(route("/api/status", method, [TARGET], self.store)[0], 405)
        self.assertEqual(route("/", "HEAD", [TARGET], self.store)[2], b"")

    def test_lambda_api_envelope(self):
        with patch("cloudpulse.handlers.config", return_value=([TARGET], self.store)):
            result = api({"rawPath": "/api/status", "requestContext": {"http": {"method": "GET"}}}, None)
        self.assertEqual(result["statusCode"], 200)
        self.assertFalse(result["isBase64Encoded"])
        self.assertEqual(json.loads(result["body"])["mode"], "aws")

    def test_scheduler_preserves_original_timestamp(self):
        with (
            patch("cloudpulse.handlers.config", return_value=([TARGET], self.store)),
            patch("cloudpulse.handlers.run_cycle") as cycle,
        ):
            checker({"scheduled_at": "2026-11-17T17:00:00Z"}, None)
        self.assertEqual(cycle.call_args.kwargs["checked_at"], 1794934800)

    def test_sns_notification_content(self):
        client = Mock()
        with (
            patch.dict("os.environ", {"TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:test"}),
            patch("boto3.client", return_value=client),
        ):
            notify(TARGET, {"type": "opened", "at": 100}, OK)
        message = client.publish.call_args.kwargs
        self.assertEqual(message["Subject"], "CloudPulse: api opened")
        self.assertEqual(json.loads(message["Message"])["target"], TARGET.name)
