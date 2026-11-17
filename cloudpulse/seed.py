"""Clearly labelled synthetic history, used only for the local fixture demo."""

import random
import time

from cloudpulse.core import transition


def seed(targets, store, now=None):
    rng = random.Random(42)
    now = int(time.time()) if now is None else now
    for index, target in enumerate(targets):
        if store.state(target.id):
            continue
        state = None
        for tick in range(100):
            at = now - (100 - tick) * 60
            ok = not (target.id == "billing" and 62 <= tick < 66)
            result = {
                "ok": ok,
                "code": 200 if ok else 503,
                "error": None if ok else "HTTP 503",
                "latency_ms": 40 + index * 24 + rng.randrange(10, 85),
            }
            state, event = transition(state, result, at)
            store.save(target.id, state, event)
            if event:
                store.acknowledge(target.id, at)
