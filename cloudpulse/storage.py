"""SQLite for local use; DynamoDB for AWS. Both reject stale/duplicate samples."""

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal


class SQLiteStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS states (target TEXT PRIMARY KEY, at INTEGER, data TEXT);
                CREATE TABLE IF NOT EXISTS checks (target TEXT, at INTEGER, data TEXT, PRIMARY KEY(target, at));
                CREATE TABLE IF NOT EXISTS events (target TEXT, at INTEGER, data TEXT, PRIMARY KEY(target, at));
                CREATE TABLE IF NOT EXISTS alerts (target TEXT, at INTEGER, data TEXT, PRIMARY KEY(target, at));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self, target):
        with self.connect() as db:
            row = db.execute("SELECT data FROM states WHERE target = ?", (target,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, target, state, event):
        at = state["checked_at"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT at FROM states WHERE target = ?", (target,)).fetchone()
            if old and old[0] >= at:
                return False
            data = json.dumps(state)
            db.execute("INSERT OR REPLACE INTO states VALUES (?, ?, ?)", (target, at, data))
            db.execute("INSERT INTO checks VALUES (?, ?, ?)", (target, at, data))
            if event:
                db.execute("INSERT INTO events VALUES (?, ?, ?)", (target, at, json.dumps(event)))
                db.execute(
                    "INSERT INTO alerts VALUES (?, ?, ?)",
                    (target, at, json.dumps({"event": event, "state": state})),
                )
            cutoff = at - 30 * 86400
            for table in ("checks", "events"):
                db.execute("DELETE FROM " + table + " WHERE target = ? AND at < ?", (target, cutoff))
        return True

    def _recent(self, table, target, limit):
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM " + table + " WHERE target = ? ORDER BY at DESC LIMIT ?", (target, limit)
            ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def history(self, target, limit):
        return self._recent("checks", target, limit)

    def events(self, target, limit):
        return self._recent("events", target, limit)

    def pending(self, target):
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM alerts WHERE target = ? ORDER BY at ASC LIMIT 20", (target,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def acknowledge(self, target, at):
        with self.connect() as db:
            db.execute("DELETE FROM alerts WHERE target = ? AND at = ?", (target, at))


def plain(value):
    return json.loads(json.dumps(value, default=lambda v: int(v) if isinstance(v, Decimal) else str(v)))


class DynamoStore:
    def __init__(self, table_name):
        import boto3

        self.table_name = table_name
        self.table = boto3.resource("dynamodb").Table(table_name)
        self.client = boto3.client("dynamodb")

    def state(self, target):
        row = self.table.get_item(Key={"pk": target, "sk": "STATE"}, ConsistentRead=True).get("Item")
        return plain(row["data"]) if row else None

    def save(self, target, state, event):
        from boto3.dynamodb.types import TypeSerializer

        serializer = TypeSerializer()
        at = state["checked_at"]

        def item(sk, data, expires=True):
            value = {"pk": target, "sk": sk, "data": data, "checked_at": at}
            if expires:
                value["expires_at"] = at + 30 * 86400
            return {k: serializer.serialize(v) for k, v in value.items()}

        writes = [
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": item("STATE", state, False),
                    "ConditionExpression": "attribute_not_exists(checked_at) OR checked_at < :at",
                    "ExpressionAttributeValues": {":at": {"N": str(at)}},
                }
            },
            {"Put": {"TableName": self.table_name, "Item": item("CHECK#%012d" % at, state)}},
        ]
        if event:
            writes.append({"Put": {"TableName": self.table_name, "Item": item("EVENT#%012d" % at, event)}})
            writes.append(
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": item("ALERT#%012d" % at, {"event": event, "state": state}, False),
                    }
                }
            )
        try:
            self.client.transact_write_items(TransactItems=writes)
        except self.client.exceptions.TransactionCanceledException as exc:
            reasons = exc.response.get("CancellationReasons", [])
            if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
                return False
            raise
        return True

    def _recent(self, target, prefix, limit):
        from boto3.dynamodb.conditions import Key

        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(target) & Key("sk").begins_with(prefix),
            ScanIndexForward=False,
            Limit=limit,
            ConsistentRead=True,
        )
        return [plain(row["data"]) for row in reversed(response["Items"])]

    def history(self, target, limit):
        return self._recent(target, "CHECK#", limit)

    def events(self, target, limit):
        return self._recent(target, "EVENT#", limit)

    def pending(self, target):
        from boto3.dynamodb.conditions import Key

        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(target) & Key("sk").begins_with("ALERT#"),
            ScanIndexForward=True,
            Limit=20,
            ConsistentRead=True,
        )
        return [plain(row["data"]) for row in response["Items"]]

    def acknowledge(self, target, at):
        self.table.delete_item(Key={"pk": target, "sk": "ALERT#%012d" % at})
