# Chapter 2 — The domain

*Your company exists on the internet. HTTPS, your own name, nothing public
that shouldn't be.*

Eleven lines of `site.giac` produce:

```
https://yourco.com
      ↓  Route53 A alias
   CloudFront distribution — HTTPS forced, HTTP/2, compression
      ↓  Origin Access Control
   S3 bucket — private, readable by that one distribution and nothing else
```

plus an ACM certificate that validates itself, and a CloudFront function so
`/about` serves `/about/index.html`.

---

## Before you run: the hosted zone

Route53 has to be authoritative for your domain. Two ways:

**Buy the domain in Route53** (console → Route53 → Registered domains).
The hosted zone is created for you and you're done.

**Already own it elsewhere?** Create a hosted zone for the domain in
Route53, then copy its four nameservers into your registrar's control panel.
Propagation takes anywhere from minutes to a day.

Either way, GraphIaC **imports** the zone rather than creating it — that's
why `hz : HostedZone(...)` doesn't destroy your DNS if you delete the file.

---

## The run

Edit the two constants at the top of `site.giac`, then:

```bash
python -m GraphIaC graphiac --infra_file site.giac run
```

### The first run looks like it failed. It didn't.

```
+ ACMCertificate cert            will be created
+ HostedZone hz                  will be created
+ S3Bucket bucket                will be created
⊘ CloudFrontDistribution cf      BLOCKED — cert is not ISSUED
⊘ CloudFrontS3OACEdge cf→bucket  BLOCKED
⊘ CloudFrontRoute53Edge cf→hz    BLOCKED
```

ACM issues certificates in minutes to hours. Rather than making you split
the file in two and run them in order, the planner marks the certificate's
dependents **BLOCKED** and skips them. The whole graph is still declared —
you can see it, diagram it, reason about it — it just isn't all applied yet.

Go get coffee. Then:

```bash
python -m GraphIaC graphiac --infra_file site.giac run
```

The certificate is `ISSUED`, `cert -> cf` unblocks, and the distribution,
bucket policy, and DNS alias come up. **Nothing about the file changed.**

This is the point of the gating edge `cert -> cf`: the relationship you'd
declare anyway is also the dependency the planner waits on. No ARN plumbing,
no two-phase script, no `depends_on`.

---

## Publish the page

```bash
aws s3 cp index.html s3://yourco-com-site/ --profile graphiac
```

Load `https://yourco.com`. If CloudFront still serves the old thing, it's
caching — wait, or invalidate from the console.

---

## Watch an edge dissolve

The pitch of this project is that **the intelligence lives in the edge**.
`cf -> bucket` is three words; here is what it stands for:

```bash
python -m GraphIaC graphiac --infra_file site.giac serve
```

Open the editor, click the `cf → bucket` edge, and expand it. You get the
Origin Access Control config and the bucket policy JSON it wrote — the
principal, the `AWS:SourceArn` condition scoping it to exactly this
distribution, the deny-everything-else default. That policy is the same in
every project on earth that fronts S3 with CloudFront, which is why you
shouldn't have to write it.

---

## The guards

```
? private(bucket)
? https-only(cf)
? locked-to(bucket, cf)
```

The scariest failure mode of infrastructure you built yourself is leaving
something open to the internet. These three lines say what must stay true,
and `verify` proves it against live AWS with **independent code** — the
predicates in `guards.py` are raw boto3 and are forbidden from calling the
same classes that did the provisioning. Builder and auditor share
vocabulary, not implementation.

```bash
python -m GraphIaC graphiac --infra_file site.giac verify
```

Non-zero exit on failure, so this is your first CI check. Run it on a
schedule and you'll know the day somebody makes the bucket public from the
console.

---

## What you'd have written instead

The Terraform equivalent is roughly 120 lines across
`aws_acm_certificate`, `aws_route53_record` (with a `for_each` over
`domain_validation_options`), `aws_acm_certificate_validation`,
`aws_cloudfront_origin_access_control`, `aws_cloudfront_distribution`,
`aws_s3_bucket_policy` with a `data.aws_iam_policy_document`, and
`aws_s3_bucket_public_access_block`. Most of it is not decisions. It's
transcription.

---

**Next:** [Chapter 3 — the waitlist](../03-waitlist/) — start collecting
signups.

**Or skip ahead:** if you already have a containerized app and a Postgres
schema, [chapter 5](../05-webapp/) puts it on `app.yourco.com` behind a load
balancer, sharing this chapter's hosted zone. (It requests its own
certificate — this one lives in `us-east-1` because CloudFront requires it,
and an ALB needs one in its own region.)
