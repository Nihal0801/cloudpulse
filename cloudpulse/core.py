"""Probe configured public endpoints and derive incident state."""

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    url: str


def load_targets(raw):
    values = json.loads(raw)
    if not isinstance(values, list) or not 1 <= len(values) <= 10:
        raise ValueError("Configure between 1 and 10 targets")
    try:
        targets = [Target(**item) for item in values]
    except (TypeError, KeyError) as exc:
        raise ValueError("Each target needs id, name and url") from exc
    if any(not isinstance(t.id, str) or not isinstance(t.url, str) for t in targets):
        raise ValueError("Target IDs and URLs must be strings")
    if len({t.id for t in targets}) != len(targets):
        raise ValueError("Target IDs must be unique")
    for target in targets:
        if not re.fullmatch(r"[a-z0-9-]{1,40}", target.id):
            raise ValueError("Target IDs must use lowercase letters, numbers or hyphens")
        if not isinstance(target.name, str) or not 1 <= len(target.name) <= 80:
            raise ValueError("Target name must be 1 to 80 characters")
        if not target.url.isascii() or len(target.url) > 1000:
            raise ValueError("Use an ASCII URL up to 1000 characters (encode non-ASCII paths)")
        parsed = urlsplit(target.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port not in (None, 443)
            or any(c.isspace() or ord(c) < 32 for c in target.url)
        ):
            raise ValueError("Targets must be HTTPS URLs on port 443 without credentials or fragments")
    return targets


class PinnedHTTPS(http.client.HTTPSConnection):
    """Connect to a validated address while verifying TLS against the hostname."""

    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def probe(target, timeout=4):
    start = time.monotonic()
    code = None
    error = None
    connection = None
    try:
        parsed = urlsplit(target.url)
        addresses = sorted(
            {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
        )
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ValueError("Target resolved to a non-public address")
        connection = PinnedHTTPS(parsed.hostname, addresses[0], timeout)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request("GET", path, headers={"User-Agent": "CloudPulse/1.0", "Connection": "close"})
        response = connection.getresponse()
        code = response.status
        # Redirects are deliberately not followed. Configure the final health URL.
        if not 200 <= code < 300:
            error = "HTTP %s" % code
    except (OSError, ValueError, http.client.HTTPException) as exc:
        error = str(exc)[:200]
    finally:
        if connection:
            connection.close()
    return {
        "ok": error is None,
        "code": code,
        "latency_ms": round((time.monotonic() - start) * 1000),
        "error": error,
    }


def transition(previous, result, checked_at, threshold=2):
    """Two consecutive failures open an incident; one success closes it."""
    old = previous or {}
    failures = 0 if result["ok"] else old.get("failures", 0) + 1
    status = "up" if result["ok"] else ("down" if failures >= threshold else "degraded")
    incident = old.get("incident_since")
    event = None
    if status == "down" and incident is None:
        incident = checked_at
        event = {"type": "opened", "at": checked_at, "duration_seconds": None}
    elif result["ok"] and incident is not None:
        event = {"type": "resolved", "at": checked_at, "duration_seconds": checked_at - incident}
        incident = None
    state = {
        "status": status,
        "failures": failures,
        "incident_since": incident,
        "checked_at": checked_at,
        **result,
    }
    return state, event


def run_cycle(targets, store, notifier, checked_at=None, probe_fn=probe):
    timestamp = int(time.time()) if checked_at is None else checked_at
    changes = []
    for target in targets:
        previous = store.state(target.id)
        if previous and previous["checked_at"] >= timestamp:
            continue
        result = probe_fn(target)
        state, event = transition(previous, result, timestamp)
        if store.save(target.id, state, event):
            print(json.dumps({"target": target.id, **state, "event": event}), flush=True)
            if event:
                changes.append({"target": target.id, **event})
    failed_alerts = []
    for target in targets:
        for alert in store.pending(target.id):
            try:
                notifier(target, alert["event"], alert["state"])
                store.acknowledge(target.id, alert["event"]["at"])
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "level": "error",
                            "target": target.id,
                            "message": "Alert delivery failed",
                            "error": str(exc)[:200],
                        }
                    ),
                    flush=True,
                )
                failed_alerts.append(target.id)
    if failed_alerts:
        raise RuntimeError("Alert delivery failed for: " + ", ".join(failed_alerts))
    return changes


def snapshot(targets, store, now=None, stale_after=180):
    now = int(time.time()) if now is None else now
    services = []
    for target in targets:
        state = store.state(target.id)
        history = store.history(target.id, 100)
        events = store.events(target.id, 20)
        checked_at = state.get("checked_at") if state else None
        status = state["status"] if state else "pending"
        if checked_at is not None and now - checked_at > stale_after:
            status = "stale"
        services.append(
            {
                "id": target.id,
                "name": target.name,
                "url": target.url,
                "status": status,
                "checked_at": checked_at,
                "latency_ms": state.get("latency_ms") if state else None,
                "error": state.get("error") if state else None,
                "incident_since": state.get("incident_since") if state else None,
                "uptime_percent": round(sum(h["ok"] for h in history) / len(history) * 100, 2)
                if history
                else None,
                "sample_count": len(history),
                "history": history,
                "events": events,
            }
        )
    return {"generated_at": now, "services": services}
