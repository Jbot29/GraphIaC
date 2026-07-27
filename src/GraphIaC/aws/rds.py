"""RDS Postgres — the database, and the one arrow that lets your app reach it.

Two things here are worth reading before you copy this into production.

**Nobody types a password.** The instance is created with
`ManageMasterUserPassword`, so RDS generates the master credentials, stores
them in Secrets Manager, owns the rotation, and hands back an ARN. No
password appears in your `.giac` file, your state DB, your shell history, or
your CI logs — because one never exists outside AWS.

**The security group starts empty.** A database with no ingress rules is
unreachable, which is the correct state for one nobody has connected to yet.
`app -> db` (EcsRdsEdge) is what opens port 5432, from exactly one source
group, and grants the task role permission to read exactly that one secret.
Delete the arrow and both go away.
"""

from typing import ClassVar, Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode, VerifyResult

from ..logs import setup_logger
from .ec2.network import (
    NETWORK_ACTIONS,
    delete_security_group,
    ensure_ingress,
    ensure_security_group,
    has_ingress,
    resolve_network,
    revoke_ingress,
)
from .iam_policy import (
    IamPolicyDocument,
    IamPolicyStatement,
    get_inline_policy_for_role,
    put_inline_policy_for_role,
)
from .iam_role import IAMRoleInlinePolicyEdge
from .types import AwsName

logger = setup_logger()


class RDSPostgres(BaseNode):
    deploy_actions: ClassVar[list] = [
        "rds:CreateDBInstance",
        "rds:DescribeDBInstances",
        "rds:ModifyDBInstance",
        "rds:DeleteDBInstance",
        "rds:CreateDBSubnetGroup",
        "rds:DescribeDBSubnetGroups",
        "rds:DeleteDBSubnetGroup",
        "rds:AddTagsToResource",
        "rds:ListTagsForResource",
        "secretsmanager:DescribeSecret",
        "iam:CreateServiceLinkedRole",
    ] + NETWORK_ACTIONS

    name: AwsName
    region: str = "us-east-2"

    engine_version: str = "16"
    instance_class: str = "db.t4g.micro"
    allocated_storage: int = 20
    db_name: str = "app"
    username: str = "postgres"
    port: int = 5432

    # Defaults chosen for a company that does not yet have a DBA.
    publicly_accessible: bool = False
    storage_encrypted: bool = True
    multi_az: bool = False
    backup_retention_days: int = 7
    deletion_protection: bool = False
    skip_final_snapshot: bool = False

    vpc_id: Optional[str] = None
    subnet_ids: Optional[list] = None

    # populated by AWS
    endpoint: Optional[str] = None
    status: Optional[str] = None
    arn: Optional[str] = None
    security_group_id: Optional[str] = None
    master_user_secret_arn: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    def ready(self) -> bool:
        """Creating a Postgres instance takes five to ten minutes. Anything
        that references db.endpoint stays BLOCKED until it's available —
        which is exactly the ECS service, and exactly right."""
        return self.status == "available"

    @property
    def sg_name(self) -> str:
        return f"{self.name}-db-sg"

    @property
    def subnet_group_name(self) -> str:
        return f"{self.name}-subnets"

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        node = G.nodes.get(g_id, {}).get("data")
        region = kwargs.get("region") or getattr(node, "region", None) or "us-east-2"
        rds = session.client("rds", region_name=region)
        try:
            instances = rds.describe_db_instances(DBInstanceIdentifier=read_id)["DBInstances"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "DBInstanceNotFound":
                return None
            raise
        if not instances:
            return None
        db = instances[0]
        sgs = [g["VpcSecurityGroupId"] for g in db.get("VpcSecurityGroups", [])]
        return cls(
            g_id=g_id,
            name=read_id,
            region=region,
            engine_version=db.get("EngineVersion", "16").split(".")[0],
            instance_class=db.get("DBInstanceClass", "db.t4g.micro"),
            allocated_storage=db.get("AllocatedStorage", 20),
            db_name=db.get("DBName") or "app",
            username=db.get("MasterUsername", "postgres"),
            port=(db.get("Endpoint") or {}).get("Port", 5432),
            publicly_accessible=db.get("PubliclyAccessible", False),
            storage_encrypted=db.get("StorageEncrypted", False),
            multi_az=db.get("MultiAZ", False),
            backup_retention_days=db.get("BackupRetentionPeriod", 0),
            deletion_protection=db.get("DeletionProtection", False),
            vpc_id=(db.get("DBSubnetGroup") or {}).get("VpcId"),
            endpoint=(db.get("Endpoint") or {}).get("Address"),
            status=db.get("DBInstanceStatus"),
            arn=db.get("DBInstanceArn"),
            security_group_id=sgs[0] if sgs else None,
            master_user_secret_arn=(db.get("MasterUserSecret") or {}).get("SecretArn"),
        )

    def _ensure_subnet_group(self, session, subnet_ids):
        rds = session.client("rds", region_name=self.region)
        try:
            rds.create_db_subnet_group(
                DBSubnetGroupName=self.subnet_group_name,
                DBSubnetGroupDescription=f"GraphIaC: subnets for {self.name}",
                SubnetIds=subnet_ids,
            )
            logger.info(f"Created DB subnet group {self.subnet_group_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] not in (
                "DBSubnetGroupAlreadyExists", "DBSubnetGroupAlreadyExistsFault"
            ):
                raise

    def create(self, session, G):
        vpc_id, subnet_ids = resolve_network(session, self.region, self.vpc_id, self.subnet_ids)
        if not vpc_id or len(subnet_ids) < 2:
            logger.error(f"RDS {self.name} needs subnets in at least two availability zones")
            return False
        self.vpc_id, self.subnet_ids = vpc_id, subnet_ids
        self._ensure_subnet_group(session, subnet_ids)

        # Deliberately empty. An arrow opens it; nothing else does.
        self.security_group_id = ensure_security_group(
            session, self.region, self.sg_name,
            f"GraphIaC: database access for {self.name}", vpc_id,
        )

        rds = session.client("rds", region_name=self.region)
        resp = rds.create_db_instance(
            DBInstanceIdentifier=self.name,
            Engine="postgres",
            EngineVersion=self.engine_version,
            DBInstanceClass=self.instance_class,
            AllocatedStorage=self.allocated_storage,
            DBName=self.db_name,
            MasterUsername=self.username,
            # RDS generates, stores, and rotates it. We never see it.
            ManageMasterUserPassword=True,
            Port=self.port,
            DBSubnetGroupName=self.subnet_group_name,
            VpcSecurityGroupIds=[self.security_group_id],
            PubliclyAccessible=self.publicly_accessible,
            StorageEncrypted=self.storage_encrypted,
            MultiAZ=self.multi_az,
            BackupRetentionPeriod=self.backup_retention_days,
            DeletionProtection=self.deletion_protection,
            Tags=[{"Key": "ManagedBy", "Value": "GraphIaC"}],
        )
        db = resp["DBInstance"]
        self.status = db.get("DBInstanceStatus")
        self.arn = db.get("DBInstanceArn")
        self.endpoint = (db.get("Endpoint") or {}).get("Address")
        self.master_user_secret_arn = (db.get("MasterUserSecret") or {}).get("SecretArn")
        logger.info(
            f"Creating Postgres {self.name} ({self.instance_class}) — this takes "
            f"5-10 minutes. Anything referencing db.endpoint stays BLOCKED until it's up."
        )
        return True

    def update(self, session, G, diff=None):
        rds = session.client("rds", region_name=self.region)
        rds.modify_db_instance(
            DBInstanceIdentifier=self.name,
            DBInstanceClass=self.instance_class,
            AllocatedStorage=self.allocated_storage,
            BackupRetentionPeriod=self.backup_retention_days,
            MultiAZ=self.multi_az,
            DeletionProtection=self.deletion_protection,
            ApplyImmediately=True,
        )
        logger.info(f"Modified {self.name}")

    def delete(self, session, G):
        rds = session.client("rds", region_name=self.region)
        kwargs = {"DBInstanceIdentifier": self.name, "SkipFinalSnapshot": self.skip_final_snapshot}
        if not self.skip_final_snapshot:
            kwargs["FinalDBSnapshotIdentifier"] = f"{self.name}-final"
        try:
            rds.delete_db_instance(**kwargs)
            logger.info(f"Deleting {self.name}"
                        + ("" if self.skip_final_snapshot else f" (final snapshot {self.name}-final)"))
        except ClientError as e:
            logger.error(f"Could not delete {self.name}: {e}")
            return
        try:
            rds.delete_db_subnet_group(DBSubnetGroupName=self.subnet_group_name)
        except ClientError as e:
            logger.warning(f"Could not delete subnet group {self.subnet_group_name}: {e}")
        if self.security_group_id:
            delete_security_group(session, self.region, self.security_group_id)

    def verify(self, session, G) -> list:
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        if not live:
            return [VerifyResult(name=f"db:{self.name}", passed=False,
                                 message="instance does not exist")]

        results = [
            VerifyResult(name=f"db:{self.name}:not-public",
                         passed=not live.publicly_accessible,
                         message="reachable from the internet" if live.publicly_accessible
                                 else "not publicly accessible"),
            VerifyResult(name=f"db:{self.name}:encrypted",
                         passed=live.storage_encrypted,
                         message="storage encrypted at rest" if live.storage_encrypted
                                 else "storage is NOT encrypted"),
            VerifyResult(name=f"db:{self.name}:backups",
                         passed=live.backup_retention_days > 0,
                         message=f"{live.backup_retention_days} day backup retention"),
        ]

        # The check that matters most: has anything opened the database to
        # the whole internet? Read the group directly, not the node's state.
        if live.security_group_id:
            open_to_world = has_ingress(session, self.region, live.security_group_id,
                                        live.port, cidr="0.0.0.0/0")
            results.append(VerifyResult(
                name=f"db:{self.name}:no-world-ingress", passed=not open_to_world,
                message="PORT OPEN TO 0.0.0.0/0" if open_to_world
                        else "ingress limited to named security groups",
            ))
        return results


class EcsRdsEdge(IAMRoleInlinePolicyEdge):
    """`app -> db` — the arrow this whole chapter exists for.

    Three things, none of which appear in your config:

      1. a security group rule opening the database's port to exactly the
         service's group (not a CIDR, not the VPC — the group)
      2. `secretsmanager:GetSecretValue` on exactly the RDS-managed secret,
         granted to the task execution role
      3. the secret handed to the container as DATABASE_SECRET — collected
         by EcsService.create() from this edge, so it's in the first task
         definition revision rather than a redeploy

    In Terraform, (1) alone is an aws_security_group_rule with a
    source_security_group_id pointing at another resource's id, and it is
    the single most-Googled part of this pattern.
    """

    deploy_actions: ClassVar[list] = [
        "iam:PutRolePolicy",
        "iam:GetRolePolicy",
        "iam:DeleteRolePolicy",
        "rds:DescribeDBInstances",
    ] + NETWORK_ACTIONS

    role_g_id: str
    ecs_g_id: str
    rds_g_id: str
    policy_doc: Optional[IamPolicyDocument] = None

    @property
    def source_g_id(self) -> str:
        return self.ecs_g_id

    @property
    def destination_g_id(self) -> str:
        return self.rds_g_id

    def secret_arn(self, G) -> Optional[str]:
        """The RDS-managed secret, for EcsService to inject as a container
        secret at task-definition time."""
        return G.nodes[self.rds_g_id]["data"].master_user_secret_arn

    def _policy(self, secret_arn):
        return IamPolicyDocument(Statement=[IamPolicyStatement(
            Sid="ReadDatabaseCredentials",
            Effect="Allow",
            Action=["secretsmanager:GetSecretValue"],
            # Secrets Manager appends a random 6-character suffix to the ARN.
            Resource=[secret_arn, f"{secret_arn}-*"],
        )])

    def read(self, session, G):
        db = G.nodes[self.rds_g_id]["data"]
        svc = G.nodes[self.ecs_g_id]["data"]
        if not (db.security_group_id and svc.security_group_id):
            return None
        if not has_ingress(session, db.region, db.security_group_id, db.port,
                           source_sg_id=svc.security_group_id):
            return None
        role_name = G.nodes[self.role_g_id]["data"].read_id
        doc = get_inline_policy_for_role(session, role_name, self.policy_name)
        if not doc:
            return None
        return EcsRdsEdge(role_g_id=self.role_g_id, ecs_g_id=self.ecs_g_id,
                          rds_g_id=self.rds_g_id, policy_doc=doc)

    def create(self, session, G):
        db = G.nodes[self.rds_g_id]["data"]
        svc = G.nodes[self.ecs_g_id]["data"]
        if not (db.security_group_id and svc.security_group_id):
            logger.warning("security groups not available yet; skipping app -> db wiring")
            return False

        ensure_ingress(
            session, db.region, db.security_group_id, db.port,
            source_sg_id=svc.security_group_id,
            description=f"GraphIaC: {svc.name} -> {db.name}",
        )

        if db.master_user_secret_arn:
            role_name = G.nodes[self.role_g_id]["data"].read_id
            self.policy_doc = self._policy(db.master_user_secret_arn)
            put_inline_policy_for_role(session, role_name, self.policy_name, self.policy_doc)
        else:
            logger.warning(f"{db.name} has no managed secret yet; credentials not granted")
        return True

    def update(self, session, G, diff=None):
        return self.create(session, G)

    def delete(self, session, G):
        db = G.nodes[self.rds_g_id]["data"]
        svc = G.nodes[self.ecs_g_id]["data"]
        if db.security_group_id and svc.security_group_id:
            revoke_ingress(session, db.region, db.security_group_id, db.port,
                           source_sg_id=svc.security_group_id)

    def verify(self, session, G) -> list:
        db = G.nodes[self.rds_g_id]["data"]
        svc = G.nodes[self.ecs_g_id]["data"]
        if not (db.security_group_id and svc.security_group_id):
            return []
        allowed = has_ingress(session, db.region, db.security_group_id, db.port,
                              source_sg_id=svc.security_group_id)
        return [VerifyResult(
            name=f"{svc.name}->{db.name}", passed=allowed,
            message=f"tcp/{db.port} open from {svc.security_group_id}" if allowed
                    else "the service cannot reach the database",
        )]
