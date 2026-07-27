# Chapter 3 — The waitlist

*Somebody types their email into your landing page. It lands in a database
and they get a confirmation. This is the chapter where the whole idea of
GraphIaC either lands or it doesn't.*

```
POST /signup  →  API Gateway  →  Lambda  →  DynamoDB   (the row)
                                        →  SES        (the confirmation)
```

Nine node lines and **six arrows**. Look at what the arrows stand for:

| the arrow | what it provisions |
|---|---|
| `signup-role -> signup` | the Lambda execution role's basic policy + log group access |
| `api -> join` | a route on the HTTP API |
| `join -> signup` | the AWS_PROXY integration **and** the `lambda:InvokeFunction` permission scoped to that route's source ARN |
| `signup -> signups` | an inline IAM policy granting DynamoDB on that table and its indexes — nothing else |
| `signup -> mail` | an inline IAM policy granting `ses:SendEmail` from that identity ARN — nothing else |
| `mail -> hz` | the three DKIM CNAME records SES needs, with the tokens read off the identity |

Not one ARN appears in `waitlist.giac`. The edges look them up in the graph
at create time.

---

## Run it

```bash
./build.sh
python -m GraphIaC graphiac --infra_file waitlist.giac run
```

`build.sh` zips `lambda/` into `deployment.zip`. The handler is plain
boto3 — no dependencies, nothing to compile.

The run prints the API's invoke URL:

```
https://a1b2c3d4e5.execute-api.us-east-2.amazonaws.com
```

Paste it into the `API` constant at the bottom of `index.html`, then
publish the page over chapter 2's:

```bash
aws s3 cp index.html s3://yourco-com-site/ --profile graphiac
```

Load your site, type an email, submit. Then:

```bash
aws dynamodb scan --table-name signups --profile graphiac
```

---

## About SES: you are in the sandbox

Every new AWS account can only send email to addresses it has verified.
Until you leave the sandbox:

- **Verify yourself as a recipient** so you can test the confirmation:
  console → SES → Identities → Create identity → Email address, then click
  the link AWS sends you.
- **Ask for production access**: console → SES → Account dashboard →
  Request production access. It's a short form and usually answered within
  a day. Say what you send and how people opt out — that's all they want to
  know.

Signups work fine in the sandbox. `_confirm()` catches the send failure and
logs it, so the row is still written and the visitor still gets "You're on
the list." Losing the confirmation email is not worth losing the signup.

**DKIM** takes a few minutes after `mail -> hz` writes the records. Check
with:

```bash
aws sesv2 get-email-identity --email-identity yourco.com --profile graphiac
```

`DkimAttributes.Status: SUCCESS` means your mail is signed and far less
likely to land in spam. This is the step people forget, and it's an arrow.

---

## The guard

```
? cors-locked(api)
```

An HTTP API with `AllowOrigins: ["*"]` can be called from a browser on any
site on the internet — which for a signup endpoint means anyone can point
a script at it and fill your table. `cors_origins: ["https://yourco.com"]`
sets the allow-list; the guard proves it stayed that way:

```bash
python -m GraphIaC graphiac --infra_file waitlist.giac verify
```

The predicate reads the live API with raw boto3. It doesn't know
`ApiSite` exists — that's the point. If someone widens CORS in the console
at 2am, the auditing code isn't the code that got it wrong.

---

## Where the state lives

You have two `.giac` files with two `.db`s now, and chapter 1 built you a
bucket for exactly this:

```bash
python -m GraphIaC graphiac --infra_file waitlist.giac \
    --state s3://yourco-state/prod run
```

Each script gets its own DB under the prefix, keyed by its path in the
workspace. Add a co-founder and you're both running against the same state,
with locking, without setting anything else up.

---

## The whole book, in one window

From `examples/founding/`:

```bash
python -m GraphIaC graphiac --infra_file 03-waitlist/waitlist.giac serve
```

The file picker lists every `.giac` in the directory tree — the book's
table of contents, live. Switch chapters, plan them, run them, watch the
diagram redraw. Same state, same locks as the CLI.

---

## What you'd have written instead

The Terraform equivalent is about 200 lines: `aws_apigatewayv2_api`,
`_stage`, `_route`, `_integration`, `aws_lambda_permission` with a
hand-built `source_arn`, `aws_iam_role`, three `aws_iam_role_policy`
resources each wrapping a `data.aws_iam_policy_document`,
`aws_dynamodb_table`, `aws_sesv2_email_identity`, and an
`aws_route53_record` with a `for_each` over the DKIM tokens.

Every one of those policies is the same policy every other company writes
for the same pair of services. That's the boilerplate this project exists
to delete — and it's why the edge, not your config, is where the knowledge
belongs.

---

**Next:** [Chapter 4 — the app](../04-app/) — turn the list into users who
can log in.

**Or:** [Chapter 5 — a web server and Postgres](../05-webapp/) if what you
have is a container and a schema, not functions.
