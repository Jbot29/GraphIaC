"""Your app. Add a function, register it in APIS, rebuild, run — that's a
new authenticated endpoint at POST /api/<name>.

Every API gets (payload, user): the parsed JSON body and the signed-in
user's email. Whatever it returns goes back as JSON.
"""

import time

from miniui import serve


def now(payload, user):
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "user": user}


def echo(payload, user):
    return {"you_sent": payload, "from": user}


APIS = {
    "now": now,
    "echo": echo,
}


def handler(event, context):
    return serve(event, APIS)
