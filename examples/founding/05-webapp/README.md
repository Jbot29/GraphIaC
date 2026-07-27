# Chapter 5 — The web server

*The other track. You have a Django, Rails, or Express app and a schema, and
you are not rewriting either one as Lambda functions to get it deployed.*

```
https://app.yourco.com
      ↓  Route53 A alias
   Application Load Balancer — HTTPS, :80 redirected
      ↓  target group (ip targets)
   ECS Fargate service — 2 tasks, your container
      ↓  tcp/5432, from that security group and no other
   RDS Postgres — private address, encrypted, backed up
```

Eight nodes and seven arrows. The arrows are the point of this chapter more
than any other, because **the pain in this pattern was never the resources**.
Nobody struggles to define an RDS instance. What eats an afternoon is the
wiring between things:

| the arrow | what it provisions |
|---|---|
| `cert -> hz` | the DNS validation records |
| `cert -> lb` | the HTTPS listener, TLS 1.3 policy, and a 301 from port 80 — **and holds the load balancer BLOCKED until the certificate is ISSUED** |
| `lb -> hz` | the A alias, using the ALB's canonical hosted zone id (not the one you'd guess) |
| `cluster -> web` | which cluster the service runs in |
| `task-role -> web` | `AmazonECSTaskExecutionRolePolicy` — pull the image, write the logs |
| `lb -> web` | the target group (ip targets, health check path, 30s deregistration delay), the listener forward, **and the rule letting the load balancer's security group reach the tasks' security group** |
| `web -> db` | the rule letting the tasks' group reach Postgres on 5432, `GetSecretValue` on exactly the RDS-managed secret, and that secret injected into the container |

That last one is the most-Googled two lines in AWS. In Terraform it's an
`aws_security_group_rule` with `source_security_group_id` cross-referencing
another resource, plus an IAM policy document, plus a `secrets` block in the
task definition. Here it's `web -> db`.

---

## What you're paying for

Unlike chapters 3 and 4, this one costs money the moment it exists —
roughly **$45/month** in `us-east-2` at rest:

| | |
|---|---|
| ALB | ~$16/mo + traffic |
| Fargate, 2 × 0.5 vCPU / 1 GB | ~$25/mo |
| RDS `db.t4g.micro`, 20 GB | ~$13/mo (free tier covers the first year) |
| ECR, CloudWatch Logs | cents |

Drop `desired_count` to 1 while you're building. There is no scale-to-zero
here; that's the trade you make for not rewriting your app.

---

## Run it

Expect **three passes**. Nothing in the file changes between them.

**1. First run — the slow things start.**

```bash
python -m GraphIaC graphiac --infra_file webapp.giac run
```

```
+ ACMCertificate cert       will be created
+ EcrRepository images      will be created
+ RDSPostgres db            will be created
⊘ ALB lb                    BLOCKED — cert is not ISSUED
⊘ EcsService web            BLOCKED — waiting on "db.endpoint" — exists but not ready
```

The certificate takes minutes; Postgres takes five to ten. Both are AWS
being slow, not you being wrong.

**2. Push your image.**

```bash
./push.sh
```

The repository exists now, so this works while the database is still coming
up. `push.sh` passes `--platform linux/amd64` — Fargate is amd64, and an
arm64 image built on an Apple Silicon laptop dies at startup with an error
ECS reports only as "task stopped."

**3. Run again.**

The certificate is `ISSUED` so the load balancer builds; Postgres is
`available` so `db.endpoint` resolves and the service unblocks. Everything
comes up in dependency order and `https://app.yourco.com` answers.

If a run lands between the two, run a third time. That's the design: the
planner owns waiting, so re-running is always safe and never destructive.

---

## The network you didn't build

**There is no VPC in this file**, and that's deliberate. A fresh AWS account
already has a default VPC with a public subnet in every availability zone.
The tasks go there with public IPs, which is how they reach ECR and Secrets
Manager **without a NAT gateway** — the single line item that would add $32
a month before serving a request.

"Public IP" sounds alarming and isn't, because reachability is decided by
security groups, not addresses:

- the **ALB's** group: 80 and 443 from `0.0.0.0/0` — the only thing here
  facing the internet
- the **tasks'** group: port 8000, from the ALB's group only
- the **database's** group: 5432, from the tasks' group only

Each group is created and destroyed with the node that owns it, and every
rule in it came from an arrow. **Security groups are not a node type in
GraphIaC** — a group with no rules is meaningless, and the rules are always
about a relationship.

`verify` checks all three from the outside:

```bash
python -m GraphIaC graphiac --infra_file webapp.giac verify
```

```
✓ svc:yourco-web:no-world-ingress   reachable only from named security groups
✓ db:yourco-db:not-public           not publicly accessible
✓ db:yourco-db:encrypted            storage encrypted at rest
✓ ? db-private(db)                  private address, tcp/5432 open to 1 security group(s)
```

When you do need real isolation — private subnets, a NAT gateway, an audit
that asks about network boundaries — set `vpc_id` and `subnet_ids` on the
nodes and none of the rest of this file changes.

---

## Nobody types a database password

`RDSPostgres` is created with `ManageMasterUserPassword`, so **RDS generates
the credentials, stores them in Secrets Manager, and owns the rotation.** No
password appears in `webapp.giac`, in your state DB, in your shell history,
or in a CI log — because one never exists outside AWS.

The container receives it as `DATABASE_SECRET`, fetched by the ECS agent at
container start from the ARN in the task definition. `app/main.py` shows the
whole of the client side:

```python
password = json.loads(os.environ["DATABASE_SECRET"])["password"]
```

The permission to read that one secret — and no other secret in the account
— came from `web -> db`.

---

## Deploying a new version

```bash
TAG=v2 ./push.sh
python -m GraphIaC graphiac --infra_file webapp.giac run
```

A changed image, cpu, memory, or environment variable registers a new task
definition revision and rolls the service. ECS replaces tasks one at a time
behind the load balancer, waits for the new ones to pass health checks, and
drains the old ones over 30 seconds. That's a normal deploy, not an outage —
and it's `run`, the same verb as everything else in this book.

---

## Where this fits

Chapter 2's certificate lives in `us-east-1` because CloudFront requires it;
an ALB needs one in its own region, so this file requests a second
certificate for the subdomain. The **hosted zone is shared** — `yourco.com`
serves the marketing site from CloudFront while `app.yourco.com` serves the
product from ECS. That's the common arrangement, and both halves are in the
same workspace:

```bash
cd .. && python -m GraphIaC graphiac --infra_file 05-webapp/webapp.giac serve
```

---

## Tearing it down

```bash
python -m GraphIaC graphiac --infra_file webapp.giac run   # after emptying the file
```

The database takes a **final snapshot** named `yourco-db-final` on delete.
That's the default (`skip_final_snapshot: false`) and it's the right one —
but it means a second create-and-destroy cycle fails on the duplicate
snapshot name until you delete the old one.

---

**Back to:** [the founding kit](../) · **Or:** [chapter 3, the serverless
track](../03-waitlist/)
