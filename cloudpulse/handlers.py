"""AWS Lambda entry points. Targets come from config, never from HTTP input."""

import json
import os
import time

from cloudpulse.api import route
from cloudpulse.core import load_targets, run_cycle
from cloudpulse.storage import DynamoStore


def config():
    return load_targets(os.environ["TARGETS_JSON"]), DynamoStore(os.environ["TABLE_NAME"])


def notify(target, event, state):
    import boto3

    boto3.client("sns").publish(
        TopicArn=os.environ["TOPIC_ARN"],
        Subject="CloudPulse: %s %s" % (target.id, event["type"]),
        Message=json.dumps({"target": target.name, "url": target.url, **event, "state": state}, indent=2),
    )


def checker(event, context):
    targets, store = config()
    # Scheduler passes its original scheduled time, keeping retries idempotent.
    from datetime import datetime

    scheduled = event.get("scheduled_at")
    at = (
        int(datetime.fromisoformat(scheduled.replace("Z", "+00:00")).timestamp())
        if scheduled
        else int(time.time())
    )
    return {"changes": run_cycle(targets, store, notify, checked_at=at)}


def api(event, context):
    targets, store = config()
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    code, headers, body = route(event.get("rawPath", "/"), method, targets, store)
    return {"statusCode": code, "headers": headers, "body": body.decode(), "isBase64Encoded": False}
