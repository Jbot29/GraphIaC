# Chapter 1 — The account

*Two manual steps, one run. After this, everything is a `.giac` file.*

You have an idea and an empty AWS account. Before the company can exist,
two things have to: **an identity that deploys** and **a place its state
lives**. That's this whole chapter — `account.giac` is five lines.

---

## The two manual steps

AWS won't let anyone skip these. Everything after them is GraphIaC.

**1. An account and one bootstrap user.**
Sign up at [aws.amazon.com](https://aws.amazon.com), then console → IAM →
Users → create a user for yourself with an access key. Give it
`AdministratorAccess` for now — you're about to replace its job with
something narrower. Then:

```bash
aws configure --profile bootstrap
```

**2. Install GraphIaC.**

```bash
pip install --upgrade GraphIaC
```

---

## The run

```bash
python -m GraphIaC bootstrap --infra_file account.giac run
```

Change `yourco-state` first — S3 bucket names are globally unique, so
pick something nobody else has.

Two things now exist:

**`graphiac-deploy`** — an IAM role whose policy is **generated from
GraphIaC itself**. Every node and edge class declares the AWS actions its
code calls; the role's policy is their union. It covers every chapter in
this book out of the box, and re-running this file after a `pip upgrade`
re-syncs it.

**`yourco-state`** — a private, versioned S3 bucket. Your infrastructure's
state DB goes here instead of on your laptop, which is what makes chapter 4
possible when there are two of you.

The run finishes by printing a profile snippet. Append it to
`~/.aws/config` as a **new section** — keep `[profile bootstrap]` as-is
(the new profile holds no credentials of its own; `source_profile` tells it
to borrow bootstrap's keys to assume the role):

```ini
[profile graphiac]
role_arn = arn:aws:iam::<your-account>:role/graphiac-deploy
source_profile = bootstrap
region = us-east-2
```

From here on, every command in this book uses `graphiac`, not `bootstrap`.

---

## Why a role instead of just using your user

Your user keeps almost no permissions; the role carries them all and is
assumed on demand with short-lived credentials that expire. If your laptop
is ever compromised, the attacker gets a key that can do exactly one thing:

```json
{"Effect": "Allow", "Action": "sts:AssumeRole",
 "Resource": "arn:aws:iam::<your-account>:role/graphiac-deploy"}
```

That's all your day-to-day user needs once this chapter is done.

The same role serves GraphIaC's hosted UI when you get there — one deploy
identity, wherever it runs.

---

## Using the state bucket

Local state (a `.db` next to your `.giac` file) is fine while it's just
you. The moment there are two of you, add `--state`:

```bash
python -m GraphIaC graphiac --infra_file site.giac \
    --state s3://yourco-state/prod run
```

Same state from every machine, with locking. `run` takes a lock; a second
runner is told who holds it and since when. `plan` and `verify` read
lock-free. No DynamoDB table — S3's conditional writes make a plain
lockfile atomic (the protocol Terraform adopted in 1.10).

A crashed run leaves its lock in place on purpose. Releasing it is a
decision, not a timeout:

```bash
python -m GraphIaC graphiac --infra_file site.giac \
    --state s3://yourco-state/prod unlock
```

This file's own state stays local — the turtle at the bottom. Keep
`account.db` in your repo.

---

## The guard

```
? private(yourco-state)
```

`verify` proves it against live AWS, using code that has nothing to do
with the code that created the bucket:

```bash
python -m GraphIaC graphiac --infra_file account.giac verify
```

Guards exit non-zero on failure, so this line is also a CI check. You'll
collect more of them as the book goes on.

---

## Prefer a minimal policy?

`DeployRole` is broad by design — it's the "I want to try every chapter"
option. To generate the exact policy one file needs, ask the graph:

```bash
python -m GraphIaC graphiac --infra_file ../02-domain/site.giac policy
```

---

**Next:** [Chapter 2 — the domain](../02-domain/) — you exist on the internet.
