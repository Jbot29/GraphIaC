/* GENERATED FILE — do not edit.
 * The DSL type registry: node fields/defaults/name fields and the edge
 * inference table, introspected from the GraphIaC Pydantic models.
 * Regenerate with:  python -m GraphIaC.dsl_registry
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.GraphIaCRegistry = api;
})(typeof self !== "undefined" ? self : this, function () {
"use strict";
return {
  "edges": {
    "ACMCertificateALBEdge": {
      "dest": {
        "field": "alb_g_id",
        "type": "ALB"
      },
      "fields": {
        "alb_g_id": {
          "required": true
        },
        "cert_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "cert_g_id",
        "type": "ACMCertificate"
      }
    },
    "ACMCertificateCloudFrontEdge": {
      "dest": {
        "field": "cf_g_id",
        "type": "CloudFrontDistribution"
      },
      "fields": {
        "cert_g_id": {
          "required": true
        },
        "cf_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "cert_g_id",
        "type": "ACMCertificate"
      }
    },
    "ACMCertificateHostedZoneEdge": {
      "dest": {
        "field": "hz_g_id",
        "type": "HostedZone"
      },
      "fields": {
        "cert_g_id": {
          "required": true
        },
        "hz_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "cert_g_id",
        "type": "ACMCertificate"
      }
    },
    "ALBRoute53Edge": {
      "dest": {
        "field": "hz_g_id",
        "type": "HostedZone"
      },
      "fields": {
        "alb_g_id": {
          "required": true
        },
        "domain_name": {
          "required": true
        },
        "hz_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "alb_g_id",
        "type": "ALB"
      }
    },
    "AlbEcsEdge": {
      "dest": {
        "field": "ecs_g_id",
        "type": "EcsService"
      },
      "fields": {
        "alb_g_id": {
          "required": true
        },
        "ecs_g_id": {
          "required": true
        },
        "health_check_path": {
          "default": "/",
          "required": false
        }
      },
      "source": {
        "field": "alb_g_id",
        "type": "ALB"
      }
    },
    "CloudFrontFunctionEdge": {
      "dest": {
        "field": "cf_g_id",
        "type": "CloudFrontDistribution"
      },
      "fields": {
        "cf_g_id": {
          "required": true
        },
        "event_type": {
          "default": "viewer-request",
          "required": false
        },
        "fn_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "fn_g_id",
        "type": "CloudFrontFunction"
      }
    },
    "CloudFrontRoute53Edge": {
      "dest": {
        "field": "hz_g_id",
        "type": "HostedZone"
      },
      "fields": {
        "cf_g_id": {
          "required": true
        },
        "domain_name": {
          "required": true
        },
        "hz_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "cf_g_id",
        "type": "CloudFrontDistribution"
      }
    },
    "CloudFrontS3OACEdge": {
      "dest": {
        "field": "s3_g_id",
        "type": "S3Bucket"
      },
      "fields": {
        "cf_g_id": {
          "required": true
        },
        "s3_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "cf_g_id",
        "type": "CloudFrontDistribution"
      }
    },
    "ClusterServiceEdge": {
      "dest": {
        "field": "service_g_id",
        "type": "EcsService"
      },
      "fields": {
        "cluster_g_id": {
          "required": true
        },
        "service_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "cluster_g_id",
        "type": "EcsCluster"
      }
    },
    "CognitoLambdaAuthEdge": {
      "dest": {
        "field": "fn_g_id",
        "type": "LambdaZipFile"
      },
      "fields": {
        "client_g_id": {
          "required": true
        },
        "fn_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "client_g_id",
        "type": "CognitoUserPoolClient"
      }
    },
    "CognitoPoolClientEdge": {
      "dest": {
        "field": "client_g_id",
        "type": "CognitoUserPoolClient"
      },
      "fields": {
        "client_g_id": {
          "required": true
        },
        "pool_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "pool_g_id",
        "type": "CognitoUserPool"
      }
    },
    "EcsRdsEdge": {
      "dest": {
        "field": "rds_g_id",
        "type": "RDSPostgres"
      },
      "fields": {
        "ecs_g_id": {
          "required": true
        },
        "policy_doc": {
          "default": null,
          "required": false
        },
        "rds_g_id": {
          "required": true
        },
        "role_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "ecs_g_id",
        "type": "EcsService"
      }
    },
    "EndpointLambdaEdge": {
      "dest": {
        "field": "lambda_node_g_id",
        "type": "LambdaZipFile"
      },
      "fields": {
        "endpoint_node_g_id": {
          "required": true
        },
        "lambda_node_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "endpoint_node_g_id",
        "type": "ApiEndpoint"
      }
    },
    "IAMRoleEcsEdge": {
      "dest": {
        "field": "service_g_id",
        "type": "EcsService"
      },
      "fields": {
        "role_g_id": {
          "required": true
        },
        "service_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "role_g_id",
        "type": "EcsTaskRole"
      }
    },
    "IAMRolePolicyLambdaEdge": {
      "dest": {
        "field": "node_g_id",
        "type": "LambdaZipFile"
      },
      "fields": {
        "node_g_id": {
          "default": null,
          "required": false
        },
        "policy_arn": {
          "default": "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
          "required": false
        },
        "role_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "role_g_id",
        "type": "IAMRole"
      }
    },
    "LambdaDynamoEdge": {
      "dest": {
        "field": "dynamo_node_g_id",
        "type": "DynamoTable"
      },
      "fields": {
        "dynamo_node_g_id": {
          "required": true
        },
        "lambda_node_g_id": {
          "required": true
        },
        "policy_doc": {
          "default": null,
          "required": false
        },
        "role_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "lambda_node_g_id",
        "type": "LambdaZipFile"
      }
    },
    "LambdaSESEdge": {
      "dest": {
        "field": "ses_node_g_id",
        "type": "SESDomainIdentity"
      },
      "fields": {
        "lambda_node_g_id": {
          "required": true
        },
        "policy_doc": {
          "default": null,
          "required": false
        },
        "role_g_id": {
          "required": true
        },
        "ses_node_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "lambda_node_g_id",
        "type": "LambdaZipFile"
      }
    },
    "SESDomainRoute53Edge": {
      "dest": {
        "field": "zone_g_id",
        "type": "HostedZone"
      },
      "fields": {
        "ses_g_id": {
          "required": true
        },
        "zone_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "ses_g_id",
        "type": "SESDomainIdentity"
      }
    },
    "SiteEndpointEdge": {
      "dest": {
        "field": "endpoint_node_g_id",
        "type": "ApiEndpoint"
      },
      "fields": {
        "endpoint_node_g_id": {
          "required": true
        },
        "site_node_g_id": {
          "required": true
        }
      },
      "source": {
        "field": "site_node_g_id",
        "type": "ApiSite"
      }
    }
  },
  "nodes": {
    "ACMCertificate": {
      "fields": {
        "arn": {
          "default": null,
          "required": false
        },
        "domain_name": {
          "required": true
        },
        "region": {
          "default": "us-east-1",
          "required": false
        },
        "status": {
          "default": null,
          "required": false
        },
        "subject_alternative_names": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": null
    },
    "ALB": {
      "fields": {
        "arn": {
          "default": null,
          "required": false
        },
        "canonical_hosted_zone_id": {
          "default": null,
          "required": false
        },
        "dns_name": {
          "default": null,
          "required": false
        },
        "name": {
          "required": true
        },
        "region": {
          "default": "us-east-2",
          "required": false
        },
        "scheme": {
          "default": "internet-facing",
          "required": false
        },
        "security_group_id": {
          "default": null,
          "required": false
        },
        "state": {
          "default": null,
          "required": false
        },
        "subnet_ids": {
          "default": null,
          "required": false
        },
        "vpc_id": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": "name"
    },
    "ApiEndpoint": {
      "fields": {
        "endpoint_name": {
          "required": true
        },
        "method": {
          "required": true
        },
        "path": {
          "required": true
        }
      },
      "isa": null,
      "nameField": "endpoint_name"
    },
    "ApiSite": {
      "fields": {
        "base_path": {
          "default": "/",
          "required": false
        },
        "cors_origins": {
          "default": [],
          "required": false
        },
        "protocol": {
          "default": "HTTP",
          "required": false
        },
        "region": {
          "default": "us-east-2",
          "required": false
        },
        "site_name": {
          "required": true
        },
        "stage": {
          "default": "$default",
          "required": false
        }
      },
      "isa": null,
      "nameField": "site_name"
    },
    "CloudFrontDistribution": {
      "fields": {
        "arn": {
          "default": null,
          "required": false
        },
        "cert_arn": {
          "default": null,
          "required": false
        },
        "default_root_object": {
          "default": "index.html",
          "required": false
        },
        "distribution_domain_name": {
          "default": null,
          "required": false
        },
        "distribution_id": {
          "default": null,
          "required": false
        },
        "domain_name": {
          "required": true
        },
        "oac_id": {
          "default": null,
          "required": false
        },
        "status": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": null
    },
    "CloudFrontFunction": {
      "fields": {
        "comment": {
          "default": "",
          "required": false
        },
        "function_arn": {
          "default": null,
          "required": false
        },
        "function_code": {
          "required": true
        },
        "name": {
          "required": true
        },
        "runtime": {
          "default": "cloudfront-js-2.0",
          "required": false
        }
      },
      "isa": null,
      "nameField": "name"
    },
    "CognitoUserPool": {
      "fields": {
        "admin_only_signup": {
          "default": true,
          "required": false
        },
        "arn": {
          "default": null,
          "required": false
        },
        "password_min_length": {
          "default": 12,
          "required": false
        },
        "pool_id": {
          "default": null,
          "required": false
        },
        "pool_name": {
          "required": true
        },
        "region": {
          "default": "us-east-2",
          "required": false
        }
      },
      "isa": null,
      "nameField": "pool_name"
    },
    "CognitoUserPoolClient": {
      "fields": {
        "callback_urls": {
          "default": [],
          "required": false
        },
        "client_id": {
          "default": null,
          "required": false
        },
        "client_name": {
          "required": true
        },
        "generate_secret": {
          "default": false,
          "required": false
        },
        "logout_urls": {
          "default": [],
          "required": false
        },
        "password_auth": {
          "default": false,
          "required": false
        }
      },
      "isa": null,
      "nameField": "client_name"
    },
    "DeployRole": {
      "fields": {
        "account_id": {
          "default": null,
          "required": false
        },
        "arn": {
          "default": null,
          "required": false
        },
        "inline_policy": {
          "default": null,
          "required": false
        },
        "name": {
          "default": "graphiac-deploy",
          "required": false
        },
        "trust_policy": {
          "default": null,
          "required": false
        }
      },
      "isa": "IAMRole",
      "nameField": "name"
    },
    "DynamoTable": {
      "fields": {
        "billing_mode": {
          "default": "PAY_PER_REQUEST",
          "required": false
        },
        "partition_key": {
          "required": true
        },
        "read_capacity": {
          "default": 0,
          "required": false
        },
        "region": {
          "default": "us-east-2",
          "required": false
        },
        "sort_key": {
          "default": null,
          "required": false
        },
        "table_name": {
          "required": true
        },
        "tags": {
          "default": {},
          "required": false
        },
        "write_capacity": {
          "default": 0,
          "required": false
        }
      },
      "isa": null,
      "nameField": "table_name"
    },
    "EcrRepository": {
      "fields": {
        "immutable_tags": {
          "default": false,
          "required": false
        },
        "name": {
          "required": true
        },
        "region": {
          "default": "us-east-2",
          "required": false
        },
        "scan_on_push": {
          "default": true,
          "required": false
        },
        "uri": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": "name"
    },
    "EcsCluster": {
      "fields": {
        "arn": {
          "default": null,
          "required": false
        },
        "name": {
          "required": true
        },
        "region": {
          "default": "us-east-2",
          "required": false
        },
        "status": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": "name"
    },
    "EcsService": {
      "fields": {
        "assign_public_ip": {
          "default": true,
          "required": false
        },
        "cluster_name": {
          "default": null,
          "required": false
        },
        "container_port": {
          "default": 8000,
          "required": false
        },
        "cpu": {
          "default": "256",
          "required": false
        },
        "desired_count": {
          "default": 1,
          "required": false
        },
        "env": {
          "default": {},
          "required": false
        },
        "image": {
          "required": true
        },
        "log_retention_days": {
          "default": 30,
          "required": false
        },
        "memory": {
          "default": "512",
          "required": false
        },
        "name": {
          "required": true
        },
        "region": {
          "default": "us-east-2",
          "required": false
        },
        "running_count": {
          "default": null,
          "required": false
        },
        "security_group_id": {
          "default": null,
          "required": false
        },
        "service_arn": {
          "default": null,
          "required": false
        },
        "subnet_ids": {
          "default": null,
          "required": false
        },
        "task_definition_arn": {
          "default": null,
          "required": false
        },
        "vpc_id": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": "name"
    },
    "EcsTaskRole": {
      "fields": {
        "arn": {
          "default": null,
          "required": false
        },
        "inline_policy": {
          "default": null,
          "required": false
        },
        "name": {
          "required": true
        },
        "trust_policy": {
          "default": null,
          "required": false
        }
      },
      "isa": "IAMRole",
      "nameField": "name"
    },
    "HostedZone": {
      "fields": {
        "domain_name": {
          "required": true
        },
        "zone_id": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": null
    },
    "IAMRole": {
      "fields": {
        "arn": {
          "default": null,
          "required": false
        },
        "inline_policy": {
          "default": null,
          "required": false
        },
        "name": {
          "required": true
        },
        "trust_policy": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": "name"
    },
    "LambdaZipFile": {
      "fields": {
        "description": {
          "default": "No description",
          "required": false
        },
        "env": {
          "default": {},
          "required": false
        },
        "handler": {
          "required": true
        },
        "memory_size": {
          "default": 128,
          "required": false
        },
        "name": {
          "required": true
        },
        "public_url": {
          "default": false,
          "required": false
        },
        "publish": {
          "default": true,
          "required": false
        },
        "region": {
          "default": "us-east-2",
          "required": false
        },
        "runtime": {
          "required": true
        },
        "timeout": {
          "default": 15,
          "required": false
        },
        "url": {
          "default": null,
          "required": false
        },
        "zip_file_path": {
          "required": true
        }
      },
      "isa": null,
      "nameField": "name"
    },
    "RDSPostgres": {
      "fields": {
        "allocated_storage": {
          "default": 20,
          "required": false
        },
        "arn": {
          "default": null,
          "required": false
        },
        "backup_retention_days": {
          "default": 7,
          "required": false
        },
        "db_name": {
          "default": "app",
          "required": false
        },
        "deletion_protection": {
          "default": false,
          "required": false
        },
        "endpoint": {
          "default": null,
          "required": false
        },
        "engine_version": {
          "default": "16",
          "required": false
        },
        "instance_class": {
          "default": "db.t4g.micro",
          "required": false
        },
        "master_user_secret_arn": {
          "default": null,
          "required": false
        },
        "multi_az": {
          "default": false,
          "required": false
        },
        "name": {
          "required": true
        },
        "port": {
          "default": 5432,
          "required": false
        },
        "publicly_accessible": {
          "default": false,
          "required": false
        },
        "region": {
          "default": "us-east-2",
          "required": false
        },
        "security_group_id": {
          "default": null,
          "required": false
        },
        "skip_final_snapshot": {
          "default": false,
          "required": false
        },
        "status": {
          "default": null,
          "required": false
        },
        "storage_encrypted": {
          "default": true,
          "required": false
        },
        "subnet_ids": {
          "default": null,
          "required": false
        },
        "username": {
          "default": "postgres",
          "required": false
        },
        "vpc_id": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": "name"
    },
    "Route53AliasRecord": {
      "fields": {
        "alias_dns_name": {
          "required": true
        },
        "alias_hosted_zone_id": {
          "default": "Z2FDTNDATAQYW2",
          "required": false
        },
        "domain_name": {
          "required": true
        },
        "hosted_zone_id": {
          "required": true
        }
      },
      "isa": null,
      "nameField": null
    },
    "S3Bucket": {
      "fields": {
        "bucket_name": {
          "required": true
        },
        "region": {
          "default": null,
          "required": false
        },
        "versioning": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": "bucket_name"
    },
    "SESDomainIdentity": {
      "fields": {
        "dkim_tokens": {
          "default": null,
          "required": false
        },
        "domain": {
          "required": true
        },
        "region": {
          "default": "us-east-1",
          "required": false
        },
        "verification_status": {
          "default": null,
          "required": false
        }
      },
      "isa": null,
      "nameField": null
    }
  },
  "predicates": {
    "admin-only-signup": {
      "args": [
        "CognitoUserPool"
      ],
      "doc": "nobody can sign themselves up"
    },
    "authed": {
      "args": [
        "LambdaZipFile"
      ],
      "doc": "if the function has a public URL, Cognito auth is wired into it"
    },
    "cors-locked": {
      "args": [
        "ApiSite"
      ],
      "doc": "the API's CORS allow-list names real origins, never *"
    },
    "db-private": {
      "args": [
        "RDSPostgres"
      ],
      "doc": "the database has no public address and no open-to-the-world ingress"
    },
    "https-only": {
      "args": [
        "CloudFrontDistribution"
      ],
      "doc": "viewers are forced to HTTPS with modern TLS"
    },
    "locked-to": {
      "args": [
        "S3Bucket",
        "CloudFrontDistribution"
      ],
      "doc": "only that distribution can read the bucket"
    },
    "private": {
      "args": [
        "S3Bucket"
      ],
      "doc": "the bucket blocks all public access and its policy grants none"
    }
  },
  "version": "0.1"
};
});
