# Chapter 4 — The back office

*You have signups and no way to look at them. This is the chapter where you
stop running `aws dynamodb scan`.*

A private, password-protected web app that reads chapter 3's table:

```
https://<generated>.lambda-url.us-east-2.on.aws
  → login page (Cognito — an admin creates users; nobody signs themselves up)
  → your signups, with a count, a 7-day number, and a CSV button
```

**One Lambda.** No S3 bucket, no API Gateway, no frontend build, no
CloudFront. The HTML and JS ride inside the deployment zip and the same
function serves them. For an internal tool with five users, everything else
is ceremony.

---

## Run it

```bash
./build.sh
python -m GraphIaC graphiac --infra_file console.giac run
```

The function URL and the Cognito pool ID are printed on create. Now make
yourself a user — there is no signup page on purpose:

```bash
aws cognito-idp admin-create-user --user-pool-id <pool_id> \
    --username you@yourco.com --message-action SUPPRESS --profile graphiac

aws cognito-idp admin-set-user-password --user-pool-id <pool_id> \
    --username you@yourco.com --password '<12+ chars>' --permanent \
    --profile graphiac
```

`--permanent` matters: a temporary password puts the user in a challenge
state that miniui's deliberately simple login doesn't handle. The pool wants
12+ characters with upper, lower, and a number.

Open the function URL and sign in.

---

## The table gets imported, not duplicated

`console.giac` declares `signups` again, even though chapter 3 created it:

```
signups : DynamoTable(region: region,
                      partition_key: {name: "email", attr_type: "S"})
```

Run `plan` and you'll see:

```
↓ DynamoTable signups   will be imported
```

**IMPORT**, not CREATE. The planner reads live AWS before it decides
anything: the table exists and matches, so it's adopted into this file's
state rather than recreated. Two files, one table, no `terraform import`
ritual and no data source.

That's the workaround for what the DSL can't do yet — reference a node in
another file. It's honest and it works; a real cross-file reference is on
the list.

---

## What the arrows did this time

| the arrow | what it provisions |
|---|---|
| `console-role -> console` | execution role + basic policy + log group |
| `team -> team-login` | the app client on the pool — SRP, refresh, and password auth |
| `team-login -> console` | `COGNITO_REGION`, `COGNITO_CLIENT_ID`, `COGNITO_POOL_ID` set on the function |
| `console -> signups` | an inline policy granting DynamoDB on that table and its indexes |

That third one is the interesting one. Wiring an app to its identity
provider normally means copying two IDs out of the console into a config
file, and then again into your staging config, and then discovering the
staging function is pointed at the production pool. The edge reads them off
the graph at create time.

---

## The two guards

```
? admin-only-signup(team)
? authed(console)
```

The second one is the guard that would have saved somebody's weekend. A
Lambda function URL with `AuthType: NONE` is a public endpoint on the open
internet, and it is one checkbox away from your internal tools. The
predicate reads the live URL config: if a public URL exists and the
function has no Cognito wiring, it fails loudly.

```bash
python -m GraphIaC graphiac --infra_file console.giac verify
```

Try it: delete the `team-login -> console` line, run, then verify.

```
✗ ? authed(console): PUBLIC URL WITH NO AUTH — anyone on the internet can call this
```

---

## Making it yours

`lambda/app.py` is the whole application surface:

```python
def signups(payload, user):
    ...

APIS = {"signups": signups, "stats": stats}
```

Add a function, put it in `APIS`, rebuild, run — that's a new authenticated
endpoint at `POST /api/<name>`. Every one receives the parsed JSON body and
the signed-in user's email. Sessions, security headers, CSRF posture and
static serving are `miniui.py`'s problem; read its docstring before you
ship it anywhere serious, especially the part about authorization being
all-or-nothing.

**The natural next feature** is inviting people off the waitlist —
`cognito-idp:AdminCreateUser` on the pool. Today that's an inline policy you
write yourself. It should be an arrow (`console -> team`), and that's a good
first edge class to write if you want to see how the machinery works:
`src/GraphIaC/aws/cognito.py` next to `CognitoLambdaAuthEdge`.

---

**Next:** [Chapter 5 — a web server and Postgres](../05-webapp/) — the other
track, for when what you have is a container and a schema.
