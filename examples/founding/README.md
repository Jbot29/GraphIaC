# The founding kit

*You have an idea, a domain name you just bought, and an empty AWS account.
This is the rest.*

Five chapters. Each one is a single `.giac` file, none longer than forty
lines, and each one leaves you with something you'd actually want:

```
chapter 1   an account that can deploy itself, and somewhere to keep its state
chapter 2   your company on the internet — your domain, HTTPS, a landing page
chapter 3   a waitlist that stores signups and emails people back
chapter 4   a private back office to read them
chapter 5   or: a web server and a Postgres database, if that's what you have
```

Read them in order the first time. Every chapter's README ends by telling
you where to go next.

---

## Choose your adventure

Chapters 1 and 2 are for everyone — an identity to deploy with, and a domain
that resolves to something. After that the road forks, and which fork you
take depends on what you're actually building:

**→ Serverless (chapters 3 and 4).** You're starting from nothing and want
to pay nothing until people show up. Functions, a managed table, an HTTP
API. Idle cost is roughly zero.

**→ Containers (chapter 5).** You already have a Django, Rails, or Express
app and a schema, and you are not rewriting it as functions to get it
deployed. A load balancer, a container running on Fargate, and a real
Postgres instance. Costs money the moment it exists, and is the right answer
anyway if this is the app.

They aren't exclusive. Chapter 5 reuses the domain and certificate from
chapter 2, and plenty of companies run both — the marketing site and
waitlist on CloudFront, the product on ECS.

| chapter | what you end up with | new AWS bill |
|---|---|---|
| [1 — the account](01-account/) | `graphiac-deploy` IAM role whose policy GraphIaC generates from itself, plus a private versioned S3 bucket for state | ~$0 |
| [2 — the domain](02-domain/) | `https://yourco.com` → CloudFront → private S3, DNS-validated cert, pretty URLs | cents |
| [3 — the waitlist](03-waitlist/) | `POST /signup` → Lambda → DynamoDB, plus a DKIM-signed confirmation email | ~$0 idle |
| [4 — the back office](04-app/) | One Lambda serving a Cognito-protected console over your signups | ~$0 idle |
| [5 — the web server](05-webapp/) | ALB → ECS Fargate → RDS Postgres on your domain, with the security groups written for you | ~$45/mo |

---

## Read the book in one window

`serve` roots a **workspace**, not a file. From this directory:

```bash
python -m GraphIaC graphiac --infra_file 01-account/account.giac serve
```

The file picker lists every `.giac` in the tree — the table of contents,
live. Switch chapters, plan them, run them, watch the diagram redraw with
plan verdicts badged onto the nodes. Each script keeps its own state, so
switching chapters can't cross wires.

---

## Why the arrow

Every chapter is mostly arrows, and the arrows are the argument.

Defining an S3 bucket is easy in any tool. What's miserable is everything
*between* resources: the bucket policy scoping reads to one CloudFront
distribution, the `lambda:InvokeFunction` grant with the right source ARN,
the inline policy giving a function DynamoDB on one table, the three DKIM
CNAMEs, the security group rule letting your app reach its database and
nothing else letting anything reach it.

Those patterns are the same at every company, because AWS's permission model
doesn't change. So they belong in the tool, not in your config:

```
signup -> signups     # the DynamoDB policy, written for you
signup -> mail        # the SES policy, written for you
join   -> signup      # the integration and the invoke permission
app    -> db          # the security group pair, both sides
```

You declare the relationship. The edge already knows what it costs.

You can watch one dissolve: open the editor, click any edge, and expand it
to see the exact JSON it wrote.

---

## And what must stay true

Every chapter ends with a few lines starting with `?`:

```
? private(bucket)
? locked-to(bucket, cf)
? cors-locked(api)
? authed(console)
```

The scariest failure mode of infrastructure you built yourself is leaving
something open to the internet and not knowing. A guard is an invariant
declared next to the thing it protects, checked against live AWS by
`verify`, exiting non-zero when it fails — so it's also a CI check, and a
cron job, and the thing that tells you somebody changed a setting in the
console at 2am.

Crucially the predicates are **independent code**: raw boto3 in
`guards.py`, forbidden from calling the classes that did the provisioning.
The auditor doesn't share a bug with the builder.

Guards warn; they never block a run. Being locked out of your own account
at 2am is a failure mode too.

---

## Before chapter 1

- An AWS account
- Python 3.9+ and `pip install GraphIaC`
- The AWS CLI, for the handful of things that are genuinely one-off

[**Start → Chapter 1: the account**](01-account/)
