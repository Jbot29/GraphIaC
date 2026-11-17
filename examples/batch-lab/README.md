# A lab's batch queue

*The executor every bioinformatics workflow engine targets, as three nodes
and three arrows.*

Nextflow (`awsbatch`), Snakemake (`aws-batch`), Cromwell and Step Functions
all submit to the same thing: an AWS Batch **job queue** backed by a
**compute environment** that turns into Spot instances when there is work
and into nothing when there isn't. Every lab builds it by hand once, badly,
in week one. This file is the version you'd build in year three.

```
spot  : BatchComputeEnv(capacity: "spot", max_vcpus: 256, root_volume_gb: 200,
                        vpc_id: net.vpc_id, subnet_ids: net.private_subnet_ids)
queue : BatchJobQueue
align : BatchJobDef(image: "quay.io/biocontainers/bwa:…", vcpus: 8, memory_mb: 32768)

spot     -> queue
job-role -> align
align    -> data
```

| node / arrow | what it owns so you don't |
|---|---|
| `BatchComputeEnv` | the environment, its security group (no ingress), the EC2 instance role and profile, a launch template with the root volume you chose (the stock 30 GB fills with container images on the first real run), the Batch service-linked role, Spot allocation strategy, and tags on every instance it launches |
| `BatchJobQueue` | the queue, enabled, with a priority |
| `BatchJobDef` | a job's shape: image, vCPUs, memory, command, retries, timeout, a CloudWatch log group with retention. Immutable in AWS, so a change is a new revision |
| `spot -> queue` | which environment the queue dispatches to. **Gates the queue** — Batch refuses a queue whose environment is still CREATING, so the planner holds it BLOCKED until the environment is VALID |
| `job-role -> align` | the role the container runs as, with the pull-from-ECR-and-write-logs policy attached |
| `align -> data` | an inline policy on that role granting read/write on exactly that bucket (`access: "read"` for reference data) |

## Running it

```bash
python -m GraphIaC graphiac --infra_file lab.giac run     # 1: the network
python -m GraphIaC graphiac --infra_file lab.giac run     # 2: the environment, roles, bucket, job
python -m GraphIaC graphiac --infra_file lab.giac run     # 3: the queue
python -m GraphIaC graphiac --infra_file lab.giac verify
```

Three runs, nothing changing in the file: the environment waits on the
network (attribute references), the queue waits on the environment (the
gating arrow). The plan shows the whole graph each time with the waiting
part greyed out.

```
✓ batch-env:yourlab-spot:valid              VALID/ENABLED
✓ batch-env:yourlab-spot:no-world-ingress   no ingress from the internet
✓ batch-env:yourlab-spot:private-subnets    instances launch without public IPs
✓ batch-env:yourlab-spot:vcpu-quota         max_vcpus 256 within the 640 vCPU quota
✓ batch-queue:yourlab-queue:valid           VALID/ENABLED
✓ batch-job:yourlab-align:has-role          runs as yourlab-job-role
✓ batch-job:yourlab-align:no-plaintext-secrets   no secret-looking environment variables
✓ ? batch-private(spot)                     no ingress from the internet, no public IPs
```

The `vcpu-quota` line is the one that saves the first big run: a new
account's Spot vCPU quota is often lower than `max_vcpus`, and the symptom
is jobs sitting in RUNNABLE forever with no error anywhere. `verify` reads
the quota and tells you before Friday.

## Pointing a workflow engine at it

**Nextflow** — `nextflow.config`:

```groovy
plugins { id 'nf-amazon' }
process.executor = 'awsbatch'
process.queue    = 'yourlab-queue'
workDir          = 's3://yourlab-data/work'
aws.region       = 'us-east-2'
aws.batch.cliPath = '/home/ec2-user/miniconda/bin/aws'   // or use Fusion/Wave and skip this
```

Nextflow registers its own job definition per process; the `align` job
definition in this file is for submitting by hand:

```bash
aws batch submit-job --job-queue yourlab-queue --job-definition yourlab-align \
  --job-name test --container-overrides 'command=["bwa","index","/data/ref.fa"]'
```

**Snakemake** — `--executor aws-batch --aws-batch-job-queue yourlab-queue
--default-storage-provider s3 --default-storage-prefix s3://yourlab-data/work`.

The engine's own role needs to submit jobs and read the bucket; that is a
separate principal (the laptop, a notebook, or a small EC2 "head node") and
a later arrow.

## Capacity, in one word

| `capacity:` | what you get | when |
|---|---|---|
| `"spot"` (default) | EC2 Spot, price-capacity-optimized allocation, scale to zero | the textbook Spot workload: interruptible, retried by the queue, 60–90 % off |
| `"on-demand"` | EC2 On-Demand, best-fit-progressive | a job that can't be interrupted or re-run |
| `"fargate"` / `"fargate-spot"` | no instances at all | small jobs (≤ 16 vCPU / 120 GB), nothing to patch |

Two environments on one queue is two arrows: `spot -> queue : (order: 1)`
and `ondemand -> queue : (order: 2)` — Spot first, On-Demand when Spot is
unavailable.

## How the instances reach ECR

Batch instances in **private subnets** need a way to pull images and talk to
the Batch and ECS control planes. This file uses the `Vpc` node's
`nat: "single"` (~$32/month plus data) with the free S3 gateway endpoint so
the genomics data — the volume that matters — never touches the NAT. The
alternative with no internet route at all is interface endpoints for
`ecr.api`, `ecr.dkr`, `ecs`, `logs` and `secretsmanager`, which costs more
at two AZs; the [private-network README](../private-network/README.md#the-nat-decision)
does the arithmetic. In the default VPC (drop the `Vpc` node and the two
`net.` references) instances get public IPs and need neither.

## Not here yet

An FSx for Lustre scratch filesystem mounted into the jobs, a job queue
alarm to email (the `Alerts` node), an ECR pull-through cache for
`quay.io/biocontainers`, and the Nextflow head node itself. Each is a node
or an arrow on this file, not a rewrite of it — see the
[bioinformatics backlog](../../bioinformatics-backlog.md).
