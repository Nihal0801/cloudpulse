import os
import tempfile
import unittest
from unittest.mock import Mock

import boto3
from moto import mock_aws
from test_core import FAIL, OK, TARGET

from cloudpulse.core import run_cycle, snapshot, transition
from cloudpulse.seed import seed
from cloudpulse.storage import DynamoStore, SQLiteStore


class StoreContract:
    def test_empty_store(self):
        self.assertIsNone(self.store.state("missing"))
        self.assertEqual(self.store.history("missing", 100), [])
        self.assertEqual(self.store.pending("missing"), [])

    def test_duplicate_and_out_of_order_writes_are_rejected(self):
        state, _ = transition(None, OK, 100)
        self.assertTrue(self.store.save(TARGET.id, state, None))
        self.assertFalse(self.store.save(TARGET.id, state, None))
        old, _ = transition(None, FAIL, 90)
        self.assertFalse(self.store.save(TARGET.id, old, None))
        self.assertEqual(self.store.state(TARGET.id), state)
        self.assertEqual(len(self.store.history(TARGET.id, 100)), 1)

    def test_history_order_and_limit(self):
        for at in range(100, 110):
            state, _ = transition(None, OK, at)
            self.store.save(TARGET.id, state, None)
        self.assertEqual([h["checked_at"] for h in self.store.history(TARGET.id, 3)], [107, 108, 109])

    def test_incident_alerts_are_not_repeated(self):
        notifier = Mock()
        for at, result in [(100, FAIL), (160, FAIL), (220, FAIL), (280, OK), (340, OK)]:
            run_cycle([TARGET], self.store, notifier, at, lambda t, r=result: r)
        self.assertEqual(notifier.call_count, 2)
        self.assertEqual([e["type"] for e in self.store.events(TARGET.id, 20)], ["opened", "resolved"])
        self.assertEqual(self.store.pending(TARGET.id), [])

    def test_failed_alert_is_retried_even_on_duplicate_cycle(self):
        run_cycle([TARGET], self.store, Mock(), 100, lambda t: FAIL)
        failed = Mock(side_effect=RuntimeError("SNS unavailable"))
        with self.assertRaises(RuntimeError):
            run_cycle([TARGET], self.store, failed, 160, lambda t: FAIL)
        self.assertEqual(len(self.store.pending(TARGET.id)), 1)
        notifier = Mock()
        probe = Mock()
        run_cycle([TARGET], self.store, notifier, 160, probe)
        probe.assert_not_called()
        notifier.assert_called_once()
        self.assertEqual(self.store.pending(TARGET.id), [])
        self.assertEqual(len(self.store.history(TARGET.id, 100)), 2)

    def test_duplicate_run_does_not_probe_or_notify(self):
        probe, notifier = Mock(return_value=OK), Mock()
        run_cycle([TARGET], self.store, notifier, 100, probe)
        run_cycle([TARGET], self.store, notifier, 100, probe)
        probe.assert_called_once()
        notifier.assert_not_called()

    def test_snapshot_pending_stale_and_sample_uptime(self):
        self.assertEqual(snapshot([TARGET], self.store, now=100)["services"][0]["status"], "pending")
        run_cycle([TARGET], self.store, Mock(), 100, lambda t: OK)
        run_cycle([TARGET], self.store, Mock(), 160, lambda t: FAIL)
        service = snapshot([TARGET], self.store, now=170)["services"][0]
        self.assertEqual(service["uptime_percent"], 50)
        self.assertEqual(service["sample_count"], 2)
        self.assertEqual(service["status"], "degraded")
        self.assertEqual(snapshot([TARGET], self.store, now=400)["services"][0]["status"], "stale")

    def test_seed_is_repeatable_and_does_not_queue_alerts(self):
        seed([TARGET], self.store, now=10000)
        seed([TARGET], self.store, now=11000)
        self.assertEqual(len(self.store.history(TARGET.id, 100)), 100)
        self.assertEqual(self.store.state(TARGET.id)["checked_at"], 9940)
        self.assertEqual(self.store.pending(TARGET.id), [])


class SQLiteTests(StoreContract, unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SQLiteStore(os.path.join(self.directory.name, "test.db"))


class DynamoTests(StoreContract, unittest.TestCase):
    def setUp(self):
        self.aws = mock_aws()
        self.aws.start()
        self.addCleanup(self.aws.stop)
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        boto3.client("dynamodb").create_table(
            TableName="cloudpulse-test",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
        )
        self.store = DynamoStore("cloudpulse-test")
