# The founding kit

*You have an idea, a domain name you just bought, and an empty AWS account.
This is the rest.*

Six chapters. Each one is a single `.giac` file, none longer than forty
lines, and each one leaves you with something you'd actually want:

```
chapter 1   an account that can deploy itself, and somewhere to keep its state
chapter 2   your company on the internet — your domain, HTTPS, a landing page
chapter 3   a waitlist that stores signups and emails people back
chapter 4   a private back office to read them
chapter 5   or: a web server and a Postgres database, if that's what you have
chapter 6   and then: that whole stack as one line you can reuse
```

Read them in order the first time. Every chapter's README ends by telling
you where to go next.

After that, treat it as a runbook rather than a tutorial. These are the
patterns almost every company builds in its first year, and they are meant
to be copied, renamed, and stacked — not read once and admired. The goal is
that "put a database behind my app" stops being a day of reading IAM
documentation and becomes a line you write.

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

They aren't exclusive. Chapter 5 shares chapter 2's hosted zone, and plenty
of companies run both: `yourco.com` serving the marketing site and waitlist
from CloudFront, `app.yourco.com` serving the product from ECS.

| chapter | what you end up with | new AWS bill |
|---|---|---|
| [1 — the account](01-account/) | `graphiac-deploy` IAM role whose policy GraphIaC generates from itself, plus a private versioned S3 bucket for state | ~$0 |
| [2 — the domain](02-domain/) | `https://yourco.com` → CloudFront → private S3, DNS-validated cert, pretty URLs | cents |
| [3 — the waitlist](03-waitlist/) | `POST /signup` → Lambda → DynamoDB, plus a DKIM-signed confirmation email | ~$0 idle |
| [4 — the back office](04-app/) | One Lambda serving a Cognito-protected console over your signups | ~$0 idle |
| [5 — the web server](05-webapp/) | ALB → ECS Fargate → RDS Postgres on your domain, with the security groups written for you | ~$45/mo |
| [6 — the module](06-module/) | Chapter 5's stack named once with `define` and reusable — the same graph, asserted by a test | same as 5 |

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
something open to the internet and not knowing.

**A guard checks reality, not your intent.** That's the whole difference,
and it's worth being precise about it, because there are already tools in
this space and they answer a different question. Checkov reads your HCL.
OPA and Sentinel read the plan JSON. All three are asking *"does the code
you wrote describe something safe?"* — a real question, but not the one
that keeps you up. None of them ever talks to AWS, so none of them can
notice that somebody clicked a checkbox in the console at 2am, or that a
teammate widened a bucket policy by hand to unblock a demo, or that the
thing you shipped in March quietly stopped matching the thing you wrote.

`verify` talks to AWS. It reads the live bucket, the live distribution, the
live security group, and tells you what is true right now.

And it does it with **independent code**. The predicates in `guards.py` are
raw boto3 and are forbidden from calling the node and edge classes that did
the provisioning. Builder and auditor share vocabulary, not
implementation — so a bug in `CloudFrontS3OACEdge` cannot also be the bug
that makes `? locked-to(bucket, cf)` pass. Most infrastructure tools grade
their own homework.

The last part is where it lives. The invariant sits in the same forty-line
file as the thing it protects — not in a policy repo owned by a platform
team you file tickets with. For a company of three people that's the
difference between having a security review and not having one.

`verify` exits non-zero on failure, so the same line is a CI gate and a
cron job:

```bash
python -m GraphIaC graphiac --infra_file site.giac verify
```

Guards warn; they never block a `run`. Being locked out of your own account
at 2am is a failure mode too.

---

## Before chapter 1

- An AWS account
- Python 3.9+ and `pip install GraphIaC`
- The AWS CLI, for the handful of things that are genuinely one-off

[**Start → Chapter 1: the account**](01-account/)
