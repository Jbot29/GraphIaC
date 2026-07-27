"""The back office. Add a function, register it in APIS, rebuild, run —
that's a new authenticated endpoint at POST /api/<name>.

Every API gets (payload, user): the parsed JSON body and the signed-in
user's email. Whatever it returns goes back as JSON. Login, sessions,
static assets and security headers are miniui's problem, not yours.

The only permission this file needs was one arrow in console.giac:

    console -> signups     # DynamoDB on that table, nothing else
"""

import os
from datetime import datetime, timedelta, timezone

import boto3

from miniui import serve

TABLE_NAME = os.environ["TABLE_NAME"]
REGION = os.environ.get("AWS_REGION", "us-east-2")

ddb = boto3.client("dynamodb", region_name=REGION)

# A scan is the right call while the list is small and you want all of it.
# When "small" stops being true, this is where you add a GSI on joined_at
# and paginate — and the change is here, not in your infrastructure.
MAX_ROWS = 2000


def _text(item, key):
    return item.get(key, {}).get("S", "")


def _rows():
    rows, kwargs = [], {"TableName": TABLE_NAME}
    while len(rows) < MAX_ROWS:
        page = ddb.scan(**kwargs)
        rows.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return [
        {"email": _text(i, "email"), "joined_at": _text(i, "joined_at")}
        for i in rows[:MAX_ROWS]
    ]


def signups(payload, user):
    """Everyone on the waitlist, newest first."""
    rows = sorted(_rows(), key=lambda r: r["joined_at"], reverse=True)
    return {"count": len(rows), "rows": rows, "truncated": len(rows) >= MAX_ROWS}


def stats(payload, user):
    """The two numbers you actually check every morning."""
    rows = _rows()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return {
        "total": len(rows),
        "last_7_days": sum(1 for r in rows if r["joined_at"] >= week_ago),
        "latest": max((r["joined_at"] for r in rows), default=None),
        "viewer": user,
    }


APIS = {
    "signups": signups,
    "stats": stats,
}


def handler(event, context):
    return serve(event, APIS)
