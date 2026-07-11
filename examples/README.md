# Examples

**New to GraphIaC? Start at [`get-started/`](get-started/)** — two manual
steps and one run to create your deploy role; every other example works
from there.

Every example here is a complete, runnable demo — something you'd actually
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
| [`get-started/`](get-started/) | Your deploy setup: a `graphiac-deploy` IAM role whose policy is generated from GraphIaC itself, plus an assume-role profile. GraphIaC deploying its own deployer. |
| [`static-site/`](static-site/) | A real website: `https://your-domain` → CloudFront → private S3, DNS-validated cert, pretty URLs via a CloudFront function. Includes a starter `index.html` to publish. |
| [`lambda-ui/`](lambda-ui/) | A serverless web app with login: one Lambda with a public URL, Cognito auth, static assets baked into the zip, and authenticated JSON APIs — add a Python function, get an endpoint. The base for internal tools (feature-flag consoles, admin panels, CRM helpers). |
| [`cognito/`](cognito/) | Auth for a self-hosted UI: a locked-down Cognito user pool (admin-only signup, deletion protection) + an OAuth app client. `verify` audits the security posture. |
| [`team-state/`](team-state/) | Shared state: a versioned, private S3 bucket holding your `.db`s, used via `--state s3://…`. Locked runs (S3 conditional writes — no DynamoDB), lock-free plans, explicit `unlock`. |
| [`graphiac-ui/`](graphiac-ui/) | **GraphIaC hosting GraphIaC**: the editor UI as a Lambda — public URL, Cognito login, source + state in S3. Edit/plan/run your infra from any browser; runs share the S3 lock with CLI runs. |
