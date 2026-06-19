import json
from typing import Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseEdge, BaseNode, VerifyResult

from ..logs import setup_logger

logger = setup_logger()

# Managed CloudFront cache policy: CachingOptimized (good default for static S3 sites)
_CACHING_OPTIMIZED_POLICY_ID = "658327ea-f89d-4fab-a63d-7e88639e58f6"


class CloudFrontDistribution(BaseNode):
    domain_name: str          # custom domain alias, e.g. "begriff.co"
    cert_arn: str             # ACM certificate ARN (must be us-east-1)
    distribution_id: Optional[str] = None
    distribution_domain_name: Optional[str] = None  # e.g. "xxxx.cloudfront.net"
    arn: Optional[str] = None
    oac_id: Optional[str] = None
    status: Optional[str] = None  # InProgress | Deployed
    default_root_object: str = "index.html"

    @property
    def read_id(self) -> Optional[str]:
        return self.distribution_id or self.domain_name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        cf = session.client("cloudfront")
        try:
            # Direct lookup by ID
            if read_id and not read_id.startswith("http") and "." not in read_id[:10]:
                resp = cf.get_distribution(Id=read_id)
                d = resp["Distribution"]
                return cls(
                    g_id=g_id,
                    domain_name=d["DistributionConfig"]["Aliases"]["Items"][0],
                    cert_arn=d["DistributionConfig"]["ViewerCertificate"]["ACMCertificateArn"],
                    distribution_id=d["Id"],
                    distribution_domain_name=d["DomainName"],
                    arn=d["ARN"],
                    oac_id=d["DistributionConfig"]["Origins"]["Items"][0].get(
                        "OriginAccessControlId"
                    ),
                    status=d["Status"],
                )

            # Search by domain alias
            paginator = cf.get_paginator("list_distributions")
            for page in paginator.paginate():
                for d in page.get("DistributionList", {}).get("Items", []):
                    aliases = d.get("Aliases", {}).get("Items", [])
                    if read_id in aliases:
                        resp = cf.get_distribution(Id=d["Id"])
                        dist = resp["Distribution"]
                        return cls(
                            g_id=g_id,
                            domain_name=read_id,
                            cert_arn=dist["DistributionConfig"]["ViewerCertificate"][
                                "ACMCertificateArn"
                            ],
                            distribution_id=dist["Id"],
                            distribution_domain_name=dist["DomainName"],
                            arn=dist["ARN"],
                            oac_id=dist["DistributionConfig"]["Origins"]["Items"][0].get(
                                "OriginAccessControlId"
                            ),
                            status=dist["Status"],
                        )
        except ClientError as e:
            logger.error(f"Error reading CloudFront distribution {read_id}: {e}")
        return None

    def create(self, session, G):
        # Find the connected S3 bucket in the graph
        s3_bucket = None
        for neighbor in G.neighbors(self.g_id):
            node = G.nodes[neighbor]["data"]
            if node.__class__.__name__ == "S3Bucket":
                s3_bucket = node
                break

        if s3_bucket is None:
            raise ValueError(
                f"CloudFrontDistribution {self.g_id} requires an S3Bucket neighbor in the graph"
            )

        cf = session.client("cloudfront")

        # Create the Origin Access Control
        oac_resp = cf.create_origin_access_control(
            OriginAccessControlConfig={
                "Name": f"oac-{s3_bucket.bucket_name}",
                "Description": f"OAC for {s3_bucket.bucket_name}",
                "SigningProtocol": "sigv4",
                "SigningBehavior": "always",
                "OriginAccessControlOriginType": "s3",
            }
        )
        oac_id = oac_resp["OriginAccessControl"]["Id"]

        origin_domain = f"{s3_bucket.bucket_name}.s3.amazonaws.com"
        origin_id = f"S3-{s3_bucket.bucket_name}"

        try:
            resp = cf.create_distribution(
                DistributionConfig={
                    "CallerReference": self.domain_name,
                    "Aliases": {"Quantity": 1, "Items": [self.domain_name]},
                    "DefaultRootObject": self.default_root_object,
                    "Origins": {
                        "Quantity": 1,
                        "Items": [
                            {
                                "Id": origin_id,
                                "DomainName": origin_domain,
                                "S3OriginConfig": {"OriginAccessIdentity": ""},
                                "OriginAccessControlId": oac_id,
                            }
                        ],
                    },
                    "DefaultCacheBehavior": {
                        "TargetOriginId": origin_id,
                        "ViewerProtocolPolicy": "redirect-to-https",
                        "AllowedMethods": {
                            "Quantity": 2,
                            "Items": ["GET", "HEAD"],
                            "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                        },
                        "Compress": True,
                        "CachePolicyId": _CACHING_OPTIMIZED_POLICY_ID,
                    },
                    "ViewerCertificate": {
                        "ACMCertificateArn": self.cert_arn,
                        "SSLSupportMethod": "sni-only",
                        "MinimumProtocolVersion": "TLSv1.2_2021",
                    },
                    "Comment": self.domain_name,
                    "Enabled": True,
                    "PriceClass": "PriceClass_All",
                    "IsIPV6Enabled": True,
                }
            )
            d = resp["Distribution"]
            self.distribution_id = d["Id"]
            self.distribution_domain_name = d["DomainName"]
            self.arn = d["ARN"]
            self.oac_id = oac_id
            self.status = d["Status"]
            logger.info(
                f"Created CloudFront distribution {self.distribution_id} for {self.domain_name}"
            )
        except ClientError as e:
            # Clean up OAC if distribution creation fails
            try:
                oac_etag = cf.get_origin_access_control(Id=oac_id)["ETag"]
                cf.delete_origin_access_control(Id=oac_id, IfMatch=oac_etag)
            except Exception:
                pass
            logger.error(f"Failed to create CloudFront distribution: {e}")
            raise

    def update(self, session, G, diff=None):
        pass

    def verify(self, session, G) -> list:
        if not self.distribution_id:
            return [VerifyResult(name="Distribution exists", passed=False,
                                 message="no distribution_id — not yet created")]
        cf = session.client("cloudfront")
        results = []
        try:
            resp = cf.get_distribution(Id=self.distribution_id)
            dist = resp["Distribution"]
            config = dist["DistributionConfig"]
            origin = config["Origins"]["Items"][0]
            cache_behavior = config["DefaultCacheBehavior"]
            viewer_cert = config["ViewerCertificate"]

            # HTTPS enforcement
            vpp = cache_behavior.get("ViewerProtocolPolicy", "")
            https_ok = vpp in ("redirect-to-https", "https-only")
            results.append(VerifyResult(
                name="HTTPS enforced",
                passed=https_ok,
                message=vpp if https_ok else f"ViewerProtocolPolicy is '{vpp}'",
            ))

            # TLS minimum version
            tls = viewer_cert.get("MinimumProtocolVersion", "")
            tls_ok = tls >= "TLSv1.2_2021"
            results.append(VerifyResult(
                name="TLS minimum version",
                passed=tls_ok,
                message=tls if tls_ok else f"minimum TLS is '{tls}' — upgrade to TLSv1.2_2021",
            ))

            # OAC configured on S3 origin
            oac_id = origin.get("OriginAccessControlId", "")
            results.append(VerifyResult(
                name="OAC configured on S3 origin",
                passed=bool(oac_id),
                message=f"OAC: {oac_id}" if oac_id else "no OAC — S3 origin may be open",
            ))

            # No legacy OAI
            oai = origin.get("S3OriginConfig", {}).get("OriginAccessIdentity", "")
            results.append(VerifyResult(
                name="No legacy OAI in use",
                passed=not oai,
                message="using OAC (current)" if not oai else f"OAI still set: {oai}",
            ))

            # Distribution enabled
            results.append(VerifyResult(
                name="Distribution enabled",
                passed=config.get("Enabled", False),
                message="enabled" if config.get("Enabled") else "distribution is disabled",
            ))

        except ClientError as e:
            results.append(VerifyResult(name="Distribution readable", passed=False,
                                        message=str(e)))
        return results

    def delete(self, session, G):
        if not self.distribution_id:
            return
        cf = session.client("cloudfront")
        try:
            resp = cf.get_distribution_config(Id=self.distribution_id)
            config = resp["DistributionConfig"]
            etag = resp["ETag"]

            if config["Enabled"]:
                config["Enabled"] = False
                cf.update_distribution(
                    Id=self.distribution_id, DistributionConfig=config, IfMatch=etag
                )
                logger.info(
                    f"Disabled distribution {self.distribution_id}; "
                    "re-run after status is Deployed to complete deletion"
                )
                return

            cf.delete_distribution(Id=self.distribution_id, IfMatch=etag)
            logger.info(f"Deleted CloudFront distribution {self.distribution_id}")

            if self.oac_id:
                oac_etag = cf.get_origin_access_control(Id=self.oac_id)["ETag"]
                cf.delete_origin_access_control(Id=self.oac_id, IfMatch=oac_etag)
                logger.info(f"Deleted OAC {self.oac_id}")
        except ClientError as e:
            logger.error(f"Failed to delete CloudFront distribution {self.distribution_id}: {e}")
            raise


class CloudFrontS3OACEdge(BaseEdge):
    """
    Locks an S3 bucket so only the paired CloudFront distribution can read from it.
    Sets a bucket policy that allows the CloudFront service principal conditioned on
    the specific distribution ARN — the standard OAC pattern.
    """

    cf_g_id: str
    s3_g_id: str

    @property
    def source_g_id(self):
        return self.cf_g_id

    @property
    def destination_g_id(self):
        return self.s3_g_id

    def read(self, session, G=None):
        if G is None:
            return None
        cf_node = G.nodes[self.cf_g_id]["data"]
        s3_node = G.nodes[self.s3_g_id]["data"]
        if not cf_node.arn:
            return None

        s3 = session.client("s3")
        try:
            resp = s3.get_bucket_policy(Bucket=s3_node.bucket_name)
            policy = json.loads(resp["Policy"])
            for stmt in policy.get("Statement", []):
                condition = stmt.get("Condition", {}).get("StringEquals", {})
                if condition.get("AWS:SourceArn") == cf_node.arn:
                    return self
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchBucketPolicy":
                return None
            logger.error(f"Error reading bucket policy for {s3_node.bucket_name}: {e}")
        return None

    def create(self, session, G):
        cf_node = G.nodes[self.cf_g_id]["data"]
        s3_node = G.nodes[self.s3_g_id]["data"]

        if not cf_node.arn:
            logger.warning("CloudFront distribution ARN not yet available; skipping bucket policy")
            return

        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowCloudFrontServicePrincipal",
                    "Effect": "Allow",
                    "Principal": {"Service": "cloudfront.amazonaws.com"},
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{s3_node.bucket_name}/*",
                    "Condition": {
                        "StringEquals": {"AWS:SourceArn": cf_node.arn}
                    },
                }
            ],
        }
        s3 = session.client("s3")
        try:
            s3.put_bucket_policy(Bucket=s3_node.bucket_name, Policy=json.dumps(policy))
            logger.info(
                f"Set OAC bucket policy on {s3_node.bucket_name} "
                f"for distribution {cf_node.distribution_id}"
            )
        except ClientError as e:
            logger.error(f"Failed to set bucket policy on {s3_node.bucket_name}: {e}")
            raise

    def update(self, session, G, diff=None):
        self.create(session, G)

    def verify(self, session, G) -> list:
        cf_node = G.nodes[self.cf_g_id]["data"]
        s3_node = G.nodes[self.s3_g_id]["data"]
        results = []
        if not cf_node.arn:
            return [VerifyResult(name="OAC bucket policy", passed=False,
                                 message="CloudFront ARN unknown — cannot verify")]
        s3 = session.client("s3")
        try:
            resp = s3.get_bucket_policy(Bucket=s3_node.bucket_name)
            policy = json.loads(resp["Policy"])
            arn_match = any(
                stmt.get("Condition", {}).get("StringEquals", {}).get("AWS:SourceArn") == cf_node.arn
                for stmt in policy.get("Statement", [])
            )
            results.append(VerifyResult(
                name="Bucket policy scoped to this distribution",
                passed=arn_match,
                message=f"condition matches {cf_node.arn}" if arn_match
                        else "policy exists but does not match this distribution's ARN",
            ))
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchBucketPolicy":
                results.append(VerifyResult(name="Bucket policy scoped to this distribution",
                                            passed=False, message="no bucket policy found"))
            else:
                results.append(VerifyResult(name="Bucket policy scoped to this distribution",
                                            passed=False, message=str(e)))
        return results

    def delete(self, session, G):
        s3_node = G.nodes[self.s3_g_id]["data"]
        s3 = session.client("s3")
        try:
            s3.delete_bucket_policy(Bucket=s3_node.bucket_name)
            logger.info(f"Removed bucket policy from {s3_node.bucket_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchBucketPolicy":
                logger.error(f"Failed to remove bucket policy: {e}")
                raise


class CloudFrontRoute53Edge(BaseEdge):
    """
    Creates a Route53 A alias record pointing a domain at a CloudFront distribution.
    Reads the distribution domain name from the graph at create() time, so it works
    even when CF was just created in the same run.
    """

    cf_g_id: str
    hz_g_id: str
    domain_name: str  # e.g. "begriff.co"

    @property
    def source_g_id(self):
        return self.cf_g_id

    @property
    def destination_g_id(self):
        return self.hz_g_id

    def read(self, session, G=None):
        if G is None:
            return None
        cf_node = G.nodes[self.cf_g_id]["data"]
        hz_node = G.nodes[self.hz_g_id]["data"]
        if not cf_node.distribution_domain_name or not hz_node.zone_id:
            return None

        route53 = session.client("route53")
        try:
            resp = route53.list_resource_record_sets(
                HostedZoneId=hz_node.zone_id,
                StartRecordName=self.domain_name,
                StartRecordType="A",
                MaxItems="1",
            )
            for rrs in resp.get("ResourceRecordSets", []):
                if rrs["Name"].rstrip(".") == self.domain_name.rstrip(".") and rrs["Type"] == "A":
                    return self
        except Exception as e:
            logger.error(f"Error checking Route53 alias record for {self.domain_name}: {e}")
        return None

    def create(self, session, G):
        cf_node = G.nodes[self.cf_g_id]["data"]
        hz_node = G.nodes[self.hz_g_id]["data"]

        if not cf_node.distribution_domain_name:
            logger.warning("CloudFront domain name not yet available; skipping Route53 alias")
            return

        route53 = session.client("route53")
        try:
            route53.change_resource_record_sets(
                HostedZoneId=hz_node.zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "UPSERT",
                            "ResourceRecordSet": {
                                "Name": self.domain_name,
                                "Type": "A",
                                "AliasTarget": {
                                    "HostedZoneId": "Z2FDTNDATAQYW2",
                                    "DNSName": cf_node.distribution_domain_name,
                                    "EvaluateTargetHealth": False,
                                },
                            },
                        }
                    ]
                },
            )
            logger.info(
                f"Created Route53 alias {self.domain_name} -> {cf_node.distribution_domain_name}"
            )
        except Exception as e:
            logger.error(f"Failed to create Route53 alias record: {e}")
            raise

    def update(self, session, G, diff=None):
        self.create(session, G)

    def delete(self, session, G):
        cf_node = G.nodes[self.cf_g_id]["data"]
        hz_node = G.nodes[self.hz_g_id]["data"]
        if not cf_node.distribution_domain_name:
            return

        route53 = session.client("route53")
        try:
            route53.change_resource_record_sets(
                HostedZoneId=hz_node.zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "DELETE",
                            "ResourceRecordSet": {
                                "Name": self.domain_name,
                                "Type": "A",
                                "AliasTarget": {
                                    "HostedZoneId": "Z2FDTNDATAQYW2",
                                    "DNSName": cf_node.distribution_domain_name,
                                    "EvaluateTargetHealth": False,
                                },
                            },
                        }
                    ]
                },
            )
            logger.info(f"Deleted Route53 alias record for {self.domain_name}")
        except Exception as e:
            logger.error(f"Failed to delete Route53 alias record: {e}")
            raise


class CloudFrontFunction(BaseNode):
    """A CloudFront Function (viewer-request handler) managed as a graph node."""

    name: str                          # CloudFront function name (unique per account)
    function_code: str                 # JavaScript source code
    comment: str = ""
    runtime: str = "cloudfront-js-2.0"
    function_arn: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        cf = session.client("cloudfront")
        name = read_id or g_id
        try:
            # describe_function gives metadata; get_function gives the code
            desc = cf.describe_function(Name=name, Stage="LIVE")
            summary = desc["FunctionSummary"]
            code_resp = cf.get_function(Name=name, Stage="LIVE")
            code = code_resp["FunctionCode"].read().decode("utf-8")
            return cls(
                g_id=g_id,
                name=name,
                function_code=code,
                comment=summary["FunctionConfig"].get("Comment", ""),
                runtime=summary["FunctionConfig"]["Runtime"],
                function_arn=summary["FunctionMetadata"]["FunctionARN"],
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchFunctionExists":
                return None
            logger.error(f"Error reading CloudFront function {name}: {e}")
        return None

    def create(self, session, G):
        cf = session.client("cloudfront")
        try:
            resp = cf.create_function(
                Name=self.name,
                FunctionConfig={"Comment": self.comment, "Runtime": self.runtime},
                FunctionCode=self.function_code.encode("utf-8"),
            )
            etag = resp["ETag"]
            self.function_arn = resp["FunctionSummary"]["FunctionMetadata"]["FunctionARN"]
            cf.publish_function(Name=self.name, IfMatch=etag)
            logger.info(f"Created and published CloudFront function {self.name}")
        except ClientError as e:
            logger.error(f"Failed to create CloudFront function {self.name}: {e}")
            raise

    def update(self, session, G, diff=None):
        cf = session.client("cloudfront")
        try:
            desc = cf.describe_function(Name=self.name, Stage="DEVELOPMENT")
            etag = desc["ETag"]
            resp = cf.update_function(
                Name=self.name,
                IfMatch=etag,
                FunctionConfig={"Comment": self.comment, "Runtime": self.runtime},
                FunctionCode=self.function_code.encode("utf-8"),
            )
            cf.publish_function(Name=self.name, IfMatch=resp["ETag"])
            logger.info(f"Updated and published CloudFront function {self.name}")
        except ClientError as e:
            logger.error(f"Failed to update CloudFront function {self.name}: {e}")
            raise

    def delete(self, session, G):
        cf = session.client("cloudfront")
        try:
            desc = cf.describe_function(Name=self.name, Stage="DEVELOPMENT")
            cf.delete_function(Name=self.name, IfMatch=desc["ETag"])
            logger.info(f"Deleted CloudFront function {self.name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchFunctionExists":
                return
            logger.error(f"Failed to delete CloudFront function {self.name}: {e}")
            raise

    def verify(self, session, G) -> list:
        cf = session.client("cloudfront")
        try:
            resp = cf.describe_function(Name=self.name, Stage="LIVE")
            status = resp["FunctionSummary"].get("Status", "")
            return [VerifyResult(
                name="Function published to LIVE",
                passed=True,
                message=f"status: {status}",
            )]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchFunctionExists":
                return [VerifyResult(name="Function published to LIVE", passed=False,
                                     message=f"{self.name} not found in LIVE stage")]
            return [VerifyResult(name="Function readable", passed=False, message=str(e))]


class CloudFrontFunctionEdge(BaseEdge):
    """
    Associates a CloudFrontFunction with a CloudFrontDistribution on the viewer-request
    event of the default cache behavior. The edge holds the wiring intelligence: which
    event type, how to patch the distribution config, and how to verify the association.
    """

    fn_g_id: str
    cf_g_id: str
    event_type: str = "viewer-request"

    @property
    def source_g_id(self):
        return self.fn_g_id

    @property
    def destination_g_id(self):
        return self.cf_g_id

    def _fn_arn(self, G) -> Optional[str]:
        return G.nodes[self.fn_g_id]["data"].function_arn

    def read(self, session, G=None):
        if G is None:
            return None
        cf_node = G.nodes[self.cf_g_id]["data"]
        fn_arn = self._fn_arn(G)
        if not cf_node.distribution_id or not fn_arn:
            return None
        cf = session.client("cloudfront")
        try:
            resp = cf.get_distribution_config(Id=cf_node.distribution_id)
            items = resp["DistributionConfig"]["DefaultCacheBehavior"].get(
                "FunctionAssociations", {}
            ).get("Items", [])
            for a in items:
                if a["FunctionARN"] == fn_arn and a["EventType"] == self.event_type:
                    return self
        except ClientError as e:
            logger.error(f"Error reading CloudFront function association: {e}")
        return None

    def create(self, session, G):
        cf_node = G.nodes[self.cf_g_id]["data"]
        fn_arn = self._fn_arn(G)
        if not fn_arn or not cf_node.distribution_id:
            logger.warning("Function ARN or distribution ID unavailable; skipping association")
            return
        cf = session.client("cloudfront")
        try:
            resp = cf.get_distribution_config(Id=cf_node.distribution_id)
            config = resp["DistributionConfig"]
            etag = resp["ETag"]
            assocs = config["DefaultCacheBehavior"].setdefault(
                "FunctionAssociations", {"Quantity": 0, "Items": []}
            )
            assocs["Items"] = [a for a in assocs.get("Items", []) if a["EventType"] != self.event_type]
            assocs["Items"].append({"FunctionARN": fn_arn, "EventType": self.event_type})
            assocs["Quantity"] = len(assocs["Items"])
            cf.update_distribution(
                Id=cf_node.distribution_id, DistributionConfig=config, IfMatch=etag
            )
            logger.info(
                f"Associated {fn_arn} with distribution {cf_node.distribution_id} "
                f"on {self.event_type}"
            )
        except ClientError as e:
            logger.error(f"Failed to associate CloudFront function: {e}")
            raise

    def update(self, session, G, diff=None):
        self.create(session, G)

    def delete(self, session, G):
        cf_node = G.nodes[self.cf_g_id]["data"]
        fn_arn = self._fn_arn(G)
        if not fn_arn or not cf_node.distribution_id:
            return
        cf = session.client("cloudfront")
        try:
            resp = cf.get_distribution_config(Id=cf_node.distribution_id)
            config = resp["DistributionConfig"]
            etag = resp["ETag"]
            assocs = config["DefaultCacheBehavior"].get(
                "FunctionAssociations", {"Quantity": 0, "Items": []}
            )
            assocs["Items"] = [a for a in assocs["Items"] if a["FunctionARN"] != fn_arn]
            assocs["Quantity"] = len(assocs["Items"])
            config["DefaultCacheBehavior"]["FunctionAssociations"] = assocs
            cf.update_distribution(
                Id=cf_node.distribution_id, DistributionConfig=config, IfMatch=etag
            )
            logger.info(f"Removed function association from distribution {cf_node.distribution_id}")
        except ClientError as e:
            logger.error(f"Failed to remove CloudFront function association: {e}")
            raise

    def verify(self, session, G) -> list:
        cf_node = G.nodes[self.cf_g_id]["data"]
        fn_arn = self._fn_arn(G)
        if not fn_arn or not cf_node.distribution_id:
            return [VerifyResult(name="Function associated", passed=False,
                                 message="function ARN or distribution ID unknown")]
        cf = session.client("cloudfront")
        try:
            resp = cf.get_distribution_config(Id=cf_node.distribution_id)
            items = resp["DistributionConfig"]["DefaultCacheBehavior"].get(
                "FunctionAssociations", {}
            ).get("Items", [])
            ok = any(a["FunctionARN"] == fn_arn and a["EventType"] == self.event_type for a in items)
            return [VerifyResult(
                name=f"Function associated on {self.event_type}",
                passed=ok,
                message=f"ARN: {fn_arn}" if ok else f"function not wired to {self.event_type}",
            )]
        except ClientError as e:
            return [VerifyResult(name="Function association readable", passed=False, message=str(e))]
