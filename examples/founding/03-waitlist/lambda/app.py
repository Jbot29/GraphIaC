"""The waitlist handler: one row in DynamoDB, one confirmation email.

Nothing here knows about IAM. The permissions this code needs were
declared as two arrows in waitlist.giac:

    signup -> signups   # dynamodb on that table
    signup -> mail      # ses:SendEmail from that domain

Only boto3 — no dependencies, so build.sh is just a zip.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE_NAME = os.environ["TABLE_NAME"]
MAIL_FROM = os.environ["MAIL_FROM"]
SITE_URL = os.environ.get("SITE_URL", "")
REGION = os.environ.get("AWS_REGION", "us-east-2")

ddb = boto3.client("dynamodb", region_name=REGION)
ses = boto3.client("sesv2", region_name=REGION)

# Deliberately loose. Real validation is "can we deliver to it", which you
# find out by sending, not by regex.
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _reply(status, body):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _reply(400, {"error": "expected a JSON body"})

    email = str(payload.get("email", "")).strip().lower()
    if not EMAIL.match(email) or len(email) > 254:
        return _reply(400, {"error": "that doesn't look like an email address"})

    source = (event.get("requestContext", {}).get("http", {}) or {}).get("sourceIp", "")

    try:
        ddb.put_item(
            TableName=TABLE_NAME,
            Item={
                "email": {"S": email},
                "joined_at": {"S": datetime.now(timezone.utc).isoformat()},
                "source_ip": {"S": source},
            },
            # idempotent: a double-submit doesn't reset anyone's join date
            ConditionExpression="attribute_not_exists(email)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _reply(200, {"ok": True, "message": "You're already on the list."})
        log.exception("could not record signup")
        return _reply(500, {"error": "could not record that — try again in a minute"})

    _confirm(email)
    return _reply(200, {"ok": True, "message": "You're on the list."})


def _confirm(email):
    """Best effort. A brand-new AWS account is in the SES sandbox and can
    only send to verified addresses — the signup is already saved, so a
    bounced confirmation is a log line, not a 500."""
    body = (
        "Thanks for signing up.\n\n"
        "You're on the list — we'll email you when there's something to see.\n"
    )
    if SITE_URL:
        body += f"\n{SITE_URL}\n"

    try:
        ses.send_email(
            FromEmailAddress=MAIL_FROM,
            Destination={"ToAddresses": [email]},
            Content={
                "Simple": {
                    "Subject": {"Data": "You're on the list"},
                    "Body": {"Text": {"Data": body}},
                }
            },
        )
    except ClientError as e:
        log.warning("confirmation email to %s not sent: %s", email, e.response["Error"]["Code"])
