# Examples

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
| [`static-site/`](static-site/) | A real website: `https://your-domain` → CloudFront → private S3, DNS-validated cert, pretty URLs via a CloudFront function. Includes a starter `index.html` to publish. |
| [`cognito/`](cognito/) | Auth for a self-hosted UI: a locked-down Cognito user pool (admin-only signup, deletion protection) + an OAuth app client. `verify` audits the security posture. |
