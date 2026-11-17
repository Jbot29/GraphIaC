# Examples

**New to GraphIaC? Read [`founding/`](founding/)** — the founding kit: six
chapters that take an empty AWS account to a company on the internet, in
order, one `.giac` file each. It's the tour, and it starts with the deploy
role every other example needs.

The rest of this directory is the reference shelf — one example per
capability, standing alone. Every one is a complete, runnable demo — something you'd actually
want at the end, not a syntax exercise. Copy the directory, change the names
at the top of the `.giac` file, and:

```bash
python -m GraphIaC <aws-profile> --infra_file <file>.giac run
```

or open it live in the editor UI:

```bash
python -m GraphIaC <aws-profile> --infra_file <file>.giac serve
```

| example | what you end up with |
|---|---|
| [`founding/`](founding/) | **The founding kit, in six chapters**: the deploy role and state bucket, your domain on HTTPS, a waitlist that emails people back, a private back office over the signups, a container track (ALB → ECS → Postgres) for when what you have is an app, not functions — and that whole stack as a reusable module. |
| [`get-started/`](get-started/) | Your deploy setup: a `graphiac-deploy` IAM role whose policy is generated from GraphIaC itself, plus an assume-role profile. GraphIaC deploying its own deployer. |
| [`static-site/`](static-site/) | A real website: `https://your-domain` → CloudFront → private S3, DNS-validated cert, pretty URLs via a CloudFront function. Includes a starter `index.html` to publish. |
| [`lambda-ui/`](lambda-ui/) | A serverless web app with login: one Lambda with a public URL, Cognito auth, static assets baked into the zip, and authenticated JSON APIs — add a Python function, get an endpoint. The base for internal tools (feature-flag consoles, admin panels, CRM helpers). |
| [`cognito/`](cognito/) | Auth for a self-hosted UI: a locked-down Cognito user pool (admin-only signup, deletion protection) + an OAuth app client. `verify` audits the security posture. |
| [`private-network/`](private-network/) | A network of your own: one `Vpc` node builds public + private subnet tiers, an internet gateway, NAT (`none`/`single`/`per-az`), free S3 endpoint, and strips the default security group. Chapter 5's stack placed in it by attribute reference — or `Vpc(existing: true)` to wire a network you already have. |
| [`batch-lab/`](batch-lab/) | A lab's batch queue: AWS Batch on Spot in the private network, scaled from zero, with the roles, launch template, log group and bucket policy written by the arrows. The executor Nextflow, Snakemake and Cromwell submit to; `verify` reads your vCPU quota before the first big run. |
| [`team-state/`](team-state/) | Shared state: a versioned, private S3 bucket holding your `.db`s, used via `--state s3://…`. Locked runs (S3 conditional writes — no DynamoDB), lock-free plans, explicit `unlock`. |
| [`graphiac-ui/`](graphiac-ui/) | **GraphIaC hosting GraphIaC**: the editor UI as a Lambda — public URL, Cognito login, source + state in S3. Edit/plan/run your infra from any browser; runs share the S3 lock with CLI runs. |
