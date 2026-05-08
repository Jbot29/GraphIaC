from typing import Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode

from ..logs import setup_logger

logger = setup_logger()


class HostedZone(BaseNode):
    domain_name: str
    zone_id: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.domain_name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        route53 = session.client("route53")
        try:
            resp = route53.list_hosted_zones()
            for zone in resp["HostedZones"]:
                if zone["Name"] == read_id.rstrip(".") + ".":
                    return HostedZone(g_id=g_id, domain_name=read_id, zone_id=zone["Id"])
        except ClientError as e:
            logger.error(f"Error reading hosted zone {read_id}: {e}")
        return None

    def create(self, session, G):
        raise NotImplementedError(
            "HostedZone should be imported, not created. "
            "Add it to the graph and let plan() detect it as IMPORT."
        )

    def update(self, session, G):
        pass

    def delete(self, session, G):
        route53 = session.client("route53")
        try:
            route53.delete_hosted_zone(Id=self.zone_id)
        except ClientError:
            raise
