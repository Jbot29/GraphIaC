# Static site example: Route53 -> CloudFront (HTTPS) -> S3
#
# This example provisions the full stack for a static website:
#   - ACM certificate (us-east-1, required for CloudFront)
#   - Private S3 bucket (no public access)
#   - CloudFront distribution with OAC — only CloudFront can read from S3
#   - Route53 A alias record pointing your domain at CloudFront
#
# Usage:
#   python -m GraphIaC <aws-profile> --infra_file infra.py plan
#   python -m GraphIaC <aws-profile> --infra_file infra.py run
#
# The hosted zone for your domain must already exist in Route53.
# GraphIaC will import it automatically on first run.
#
# Because ACM certificate validation can take several hours, this infra
# is split into two phases. Run once to create the cert and add the DNS
# validation records, then run again after the cert is ISSUED to bring
# up the rest of the stack.

import GraphIaC
from GraphIaC.aws.certificate import ACMCertificate, ACMCertificateHostedZoneEdge
from GraphIaC.aws.cloudfront import CloudFrontDistribution, CloudFrontRoute53Edge, CloudFrontS3OACEdge
from GraphIaC.aws.route53 import HostedZone
from GraphIaC.aws.s3 import S3Bucket

DOMAIN = "example.com"
BUCKET_NAME = "example-com-site"


def infra(state):
    # --- Phase 1 (always runs) ---
    #
    # Import the existing hosted zone and request the ACM certificate.
    # The ACMCertificateHostedZoneEdge automatically adds the CNAME validation
    # records to Route53 — no manual DNS steps required.

    hz = HostedZone(g_id="hz", domain_name=DOMAIN)
    cert = ACMCertificate(g_id="cert", domain_name=DOMAIN)

    GraphIaC.add_node(state, hz)
    GraphIaC.add_node(state, cert)
    GraphIaC.add_edge(state, ACMCertificateHostedZoneEdge(cert_g_id="cert", hz_g_id="hz"))

    # --- Phase 2 (runs once cert is ISSUED) ---
    #
    # ACM validates the cert against the CNAME records above.
    # This can take minutes to hours. Re-run after validation completes.

    live_cert = ACMCertificate.read(state.session, state.G, "cert", None)
    if not (live_cert and live_cert.status == "ISSUED"):
        return

    # S3 bucket — private, no public access.
    # CloudFrontS3OACEdge will add a bucket policy that allows only this
    # CloudFront distribution to read from it (Origin Access Control).
    bucket = S3Bucket(g_id="bucket", bucket_name=BUCKET_NAME)

    # CloudFront distribution — serves the site over HTTPS with your custom domain.
    # On create(), it also provisions the OAC used to authenticate with S3.
    cf = CloudFrontDistribution(
        g_id="cf",
        domain_name=DOMAIN,
        cert_arn=live_cert.arn,
    )

    GraphIaC.add_node(state, bucket)
    GraphIaC.add_node(state, cf)

    # Lock S3 so only this CloudFront distribution can read objects.
    GraphIaC.add_edge(state, CloudFrontS3OACEdge(cf_g_id="cf", s3_g_id="bucket"))

    # Point the domain at CloudFront via a Route53 A alias record.
    # This edge reads the CloudFront domain name from the graph at runtime,
    # so it works even when CF was just created in the same run.
    GraphIaC.add_edge(state, CloudFrontRoute53Edge(cf_g_id="cf", hz_g_id="hz", domain_name=DOMAIN))
