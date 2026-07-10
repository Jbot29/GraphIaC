# Get started

From zero to running any example in this directory. Two manual steps, one
GraphIaC run.

## 0. The two manual steps (AWS won't let anyone skip these)

1. **An AWS account** — [aws.amazon.com](https://aws.amazon.com). Then create
   one IAM user for yourself (console → IAM → Users) with an access key, and
   configure the CLI:

   ```bash
   aws configure --profile bootstrap
   ```

   For this first run, give the user admin-ish permissions (the AWS-managed
   `AdministratorAccess` policy is fine — you're about to replace its job
   with something narrower).

2. **Install GraphIaC:**

   ```bash
   pip install --upgrade GraphIaC
   ```

## 1. Create the deploy role

```bash
python -m GraphIaC bootstrap --infra_file setup.giac run
```

This creates a single IAM role, `graphiac-deploy`, whose policy is
**generated from GraphIaC itself** — every node and edge class declares the
IAM actions it needs, and the role's policy is their union. It covers every
example out of the box, and re-running setup after an upgrade re-syncs it.

The run prints a profile snippet. Paste it into `~/.aws/config`:

```ini
[profile graphiac]
role_arn = arn:aws:iam::<your-account>:role/graphiac-deploy
source_profile = bootstrap
region = us-east-2
```

**Why a role?** Best practice: your user keeps (or shrinks to) almost no
direct permissions; the role carries them all, assumed on demand with
short-lived credentials. And when GraphIaC's Lambda-hosted UI arrives, the
same role serves it — one deploy identity, wherever it runs. If you want
your day-to-day user locked down, all it needs is:

```json
{"Effect": "Allow", "Action": "sts:AssumeRole",
 "Resource": "arn:aws:iam::<your-account>:role/graphiac-deploy"}
```

## 2. Run anything

```bash
cd ../static-site      # or ../lambda-ui, ../cognito
python -m GraphIaC graphiac --infra_file site.giac run
```

## Minimal policies instead

Rather not have one broad role? Generate the exact policy a single infra
file needs — the graph knows its own permissions:

```bash
python -m GraphIaC graphiac --infra_file site.giac policy
```

## Auditing

`verify` on the setup file checks the role still covers everything
registered (e.g. after an upgrade adds a service):

```bash
python -m GraphIaC bootstrap --infra_file setup.giac verify
```
