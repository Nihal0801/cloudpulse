import http.client
import json
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

from cloudpulse.core import PinnedHTTPS, Target, load_targets, probe, transition

OK = {"ok": True, "code": 200, "latency_ms": 42, "error": None}
FAIL = {"ok": False, "code": 503, "latency_ms": 20, "error": "HTTP 503"}
TARGET = Target("api", "My API", "https://example.com/health?ready=1")


class ConfigTests(unittest.TestCase):
    def test_valid_config(self):
        self.assertEqual(load_targets(json.dumps([TARGET.__dict__])), [TARGET])

    def test_reject_invalid_config(self):
        cases = [
            [],
            [{}],
            [None],
            {},
            [dict(TARGET.__dict__, id="Bad ID")],
            [dict(TARGET.__dict__, name="")],
            [dict(TARGET.__dict__, id=3)],
            [TARGET.__dict__, TARGET.__dict__],
            [TARGET.__dict__] * 11,
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                load_targets(json.dumps(value))

    def test_reject_unsafe_or_invalid_urls(self):
        for url in [
            "http://example.com",
            "https://user:pass@example.com",
            "https://example.com:81",
            "https://example.com/#private",
            "https://example.com/a\r\nb",
            "https://",
            "https://example.com:bad",
            "https://example.com/ café",
            "//example.com",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                load_targets(json.dumps([dict(TARGET.__dict__, url=url)]))


class TransitionTests(unittest.TestCase):
    def test_incident_and_recovery_lifecycle(self):
        state, event = transition(None, OK, 100)
        self.assertEqual(state["status"], "up")
        self.assertIsNone(event)
        state, event = transition(state, FAIL, 160)
        self.assertEqual(state["status"], "degraded")
        self.assertIsNone(event)
        state, event = transition(state, FAIL, 220)
        self.assertEqual(state["status"], "down")
        self.assertEqual(event["type"], "opened")
        state, event = transition(state, FAIL, 280)
        self.assertIsNone(event)
        state, event = transition(state, OK, 340)
        self.assertEqual(state["status"], "up")
        self.assertIsNone(state["incident_since"])
        self.assertEqual(event, {"type": "resolved", "at": 340, "duration_seconds": 120})

    def test_isolated_failures_do_not_open_incidents(self):
        state = None
        for at, result in enumerate([FAIL, OK, FAIL, OK], 1):
            state, event = transition(state, result, at)
            self.assertIsNone(event)
        self.assertEqual(state["failures"], 0)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.dns = patch(
            "cloudpulse.core.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.215.14", 443))]
        )
        self.dns.start()
        self.addCleanup(self.dns.stop)
        self.connection_patch = patch("cloudpulse.core.PinnedHTTPS")
        self.connection = self.connection_patch.start().return_value
        self.addCleanup(self.connection_patch.stop)
        self.connection.getresponse.return_value.status = 200

    def test_success_and_path(self):
        result = probe(TARGET)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["latency_ms"], 0)
        self.connection.request.assert_called_once_with(
            "GET", "/health?ready=1", headers={"User-Agent": "CloudPulse/1.0", "Connection": "close"}
        )
        self.connection.close.assert_called_once()

    def test_non_2xx_and_redirects_fail_without_following(self):
        for code in (301, 404, 429, 500, 503):
            self.connection.getresponse.return_value.status = code
            result = probe(TARGET)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "HTTP %s" % code)

    def test_transport_errors_are_recorded(self):
        for error in (
            socket.timeout("timed out"),
            ssl.SSLError("certificate failed"),
            ConnectionRefusedError("refused"),
            http.client.BadStatusLine("broken"),
        ):
            self.connection.request.side_effect = error
            result = probe(TARGET)
            self.assertFalse(result["ok"])
            self.assertIsNone(result["code"])
            self.assertTrue(result["error"])

    def test_dns_errors_are_recorded(self):
        with patch("cloudpulse.core.socket.getaddrinfo", side_effect=socket.gaierror("DNS failed")):
            self.assertFalse(probe(TARGET)["ok"])

    def test_private_addresses_and_mixed_answers_are_blocked(self):
        for address in ("127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "192.168.1.1"):
            with patch("cloudpulse.core.socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 443))]):
                self.assertIn("non-public", probe(TARGET)["error"])
        self.connection.request.assert_not_called()

    def test_pinned_connection_verifies_original_hostname(self):
        connection = PinnedHTTPS("example.com", "93.184.215.14", 4)
        sock = Mock()
        context = Mock()
        connection._context = context
        with patch("cloudpulse.core.socket.create_connection", return_value=sock) as create:
            connection.connect()
            create.assert_called_once_with(("93.184.215.14", 443), 4)
        context.wrap_socket.assert_called_once_with(sock, server_hostname="example.com")

    def test_tls_failure_closes_socket(self):
        connection = PinnedHTTPS("example.com", "93.184.215.14", 4)
        sock = Mock()
        connection._context = Mock()
        connection._context.wrap_socket.side_effect = ssl.SSLError("bad cert")
        with (
            patch("cloudpulse.core.socket.create_connection", return_value=sock),
            self.assertRaises(ssl.SSLError),
        ):
            connection.connect()
        sock.close.assert_called_once()
