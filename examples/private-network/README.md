# A network of your own

*Chapter 5's stack, moved behind a boundary you can draw.*

Every chapter of the [founding kit](../founding/) runs in the account's
**default VPC** on purpose: public subnets, no NAT gateway, security groups
doing all the isolating. For a first web app that is the right call and the
[chapter 5 README](../founding/05-webapp/README.md#the-network-you-didnt-build)
explains why.

This example is for the day it stops being enough:

- an auditor asks whether the database is in a private subnet
- a compliance framework asks for a network boundary (NIST 800-171, which
  anyone holding NIH controlled-access data has needed since January 2025)
- a batch workload starts pulling terabytes from S3 and the NAT data
  charges show up on the bill
- you want a subnet with *no* route to the internet, full stop

One node answers all of them:

```
net : Vpc(name: "yourco-net", cidr: "10.0.0.0/16", az_count: 2,
          nat: "single", endpoints: ["s3"], tags: {project: "yourco"})
```

| it builds | why |
|---|---|
| a public subnet per AZ, an internet gateway, map-public-IP on | where the load balancer (and a NAT) live |
| a private subnet per AZ, **no** route to the internet unless `nat` says so | where tasks and databases live |
| an S3 gateway endpoint on every route table | free; S3 traffic never touches the NAT |
| interface endpoints for anything else you list | `"ecr.api"`, `"logs"`, … — ~$7.30/month per AZ each |
| the default security group stripped of every rule | CIS 5.3: something placed in it by mistake is unreachable, not open |
| `ManagedBy`, `graphiac:vpc`, `graphiac:tier` and your `tags` on everything | compliance tooling keys off tags |

## Joining it

Nothing about the other nodes changed. They take `vpc_id` and `subnet_ids`
as they always did; this file fills them with **attribute references**:

```
lb  : ALB(vpc_id: net.vpc_id, subnet_ids: net.public_subnet_ids, …)
web : EcsService(vpc_id: net.vpc_id, subnet_ids: net.private_subnet_ids, …)
db  : RDSPostgres(vpc_id: net.vpc_id, subnet_ids: net.private_subnet_ids, …)
```

A reference is a data dependency, so on the first run `lb`, `web` and `db`
show `⊘ BLOCKED — waiting on "net"` while the network is built, and land in
the right tier on the second. The ECS service follows its subnets on public
IPs: private tier, no public address, and the NAT is how it still reaches
ECR. (In the default VPC, with no `Vpc` node, it still gets one — the
founding kit is unchanged.)

```bash
python -m GraphIaC graphiac --infra_file network.giac run     # builds the network
python -m GraphIaC graphiac --infra_file network.giac run     # places everything in it
python -m GraphIaC graphiac --infra_file network.giac verify
```

```
✓ vpc:yourco-net:default-sg-empty          default security group has no rules
✓ vpc:yourco-net:private-subnets-offline   2 private subnet(s) have no route to an internet gateway
✓ vpc:yourco-net:private-no-auto-public-ip private subnets do not assign public IPs
✓ vpc:yourco-net:s3-endpoint               S3 gateway endpoint on every route table — no NAT charges for S3 traffic
✓ ? default-sg-empty(net)                  default group sg-… has no rules
✓ ? db-private(db)                         private address, tcp/5432 open to 1 security group(s)
```

## The NAT decision

| `nat:` | cost | when |
|---|---|---|
| `"none"` (default) | $0 | private subnets never reach the internet. Right for RDS, FSx, Batch jobs that only talk to S3 — the gateway endpoint carries that for free. |
| `"single"` | ~$32/month + $0.045/GB | one gateway in one AZ. Cheap egress; if that AZ fails the other AZ's private subnet loses the internet. Fine for staging and most small production. |
| `"per-az"` | that × `az_count` | a gateway per AZ, each private route table pointing at its own. The production answer. |

Changing `nat` in the file and running again reconciles in place: gateways
and elastic IPs are created or released, route tables repointed, nothing
else touched.

**Interface endpoints vs a NAT.** Fargate in a private subnet needs four
services to start a task without any internet route: `ecr.api`, `ecr.dkr`,
`logs`, and (for the database password) `secretsmanager`. At ~$7.30/month
per endpoint per AZ that is ≈ $58/month for two AZs — more than a single
NAT. Use the endpoints when the requirement is *no internet route at all*;
use a NAT when the requirement is *cheap*. This file uses a NAT.

## The network you already have

Most companies have one: the campus VPC, the one the previous contractor
built, the default one. The worst habit of infrastructure tools is
insisting everything be born inside them. So:

```
net : Vpc(existing: true, vpc_id: "vpc-0abc…")   # yours
net : Vpc(existing: true)                         # the account's default VPC
```

`existing: true` makes the node a **reference**: GraphIaC reads it,
classifies its subnets as public or private by their routes (a subnet whose
route table has a default route to an internet gateway is public), and
exposes the same `public_subnet_ids` / `private_subnet_ids` — so the rest of
the file is identical. It is IMPORTed the first time, never created,
updated or deleted, reported BLOCKED if it can't be found, and when you
remove it from the file GraphIaC forgets it and the VPC stays exactly where
it was.

## Not here yet

Flow logs (an audit will ask; it needs a log group and a role — next),
IPv6, VPN and peering, Transit Gateway. A `Vpc` is also the ground the
rest of the [bioinformatics backlog](../../bioinformatics-backlog.md) stands
on: Batch compute environments, FSx for Lustre, and EC2 instances all take
`net.private_subnet_ids`.
