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
        "status": {
          "default": null,
          "required": false
        }
      },
      "nameField": null
    },
    "ALB": {
      "fields": {
        "arn": {
          "default": null,
          "required": false
        },
        "desc": {
          "default": "",
          "required": false
        },
        "name": {
          "required": true
        },
        "region": {
          "default": "us-east-1",
          "required": false
        },
        "subnets": {
          "required": true
        }
      },
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
        }
      },
      "nameField": "client_name"
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
      "nameField": "table_name"
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
      "nameField": "name"
    },
    "LambdaZipFile": {
      "fields": {
        "description": {
          "default": "No description",
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
        "zip_file_path": {
          "required": true
        }
      },
      "nameField": "name"
    },
    "Listener": {
      "fields": {
        "arn": {
          "required": true
        }
      },
      "nameField": null
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
        }
      },
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
      "nameField": null
    },
    "SecurityGroup": {
      "fields": {
        "arn": {
          "default": null,
          "required": false
        },
        "desc": {
          "required": true
        },
        "sg_id": {
          "required": true
        },
        "vpc_id": {
          "required": true
        }
      },
      "nameField": null
    }
  },
  "version": "0.1"
};
});
