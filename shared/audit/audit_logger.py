import logging
from datetime import datetime

from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError

from shared.config import config
from shared.models import AuditLogEntry, FlowName, FlowStatus

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Writes structured audit log entries to the Cosmos DB 'audit-log' container.
    Every action taken by either bot — successful or failed — must be logged here.

    Usage:
        audit = AuditLogger()
        await audit.log(
            flow_name=FlowName.USER_ONBOARDING,
            status=FlowStatus.COMPLETED,
            principal_id=user["id"],
            requested_by="hr_system",
            details={"upn": "jane@org.com", "groups_assigned": 3},
            graph_operations=["POST /users", "POST /groups/.../members/$ref"],
        )
    """

    CONTAINER_NAME = "audit-log"

    def __init__(self):
        self._client = CosmosClient(config.cosmos_endpoint, credential=config.cosmos_key)
        self._db = self._client.get_database_client(config.cosmos_database)
        self._container = self._db.get_container_client(self.CONTAINER_NAME)

    async def log(
        self,
        flow_name: FlowName,
        status: FlowStatus,
        principal_id: str,
        requested_by: str,
        details: dict | None = None,
        graph_operations: list[str] | None = None,
        error: str | None = None,
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            bot_type=config.bot_type,
            flow_name=flow_name,
            status=status,
            principal_id=principal_id,
            requested_by=requested_by,
            timestamp=datetime.utcnow(),
            details=details or {},
            graph_operations=graph_operations or [],
            error=error,
        )

        try:
            await self._container.create_item(body=entry.model_dump(mode="json"))
            logger.info(
                "Audit log written: flow=%s status=%s principal=%s",
                flow_name.value, status.value, principal_id,
            )
        except CosmosHttpResponseError as exc:
            # Never let audit logging failure break the main flow
            # But always surface it in application logs
            logger.error("Failed to write audit log entry: %s", exc)

        return entry

    async def log_failure(
        self,
        flow_name: FlowName,
        principal_id: str,
        requested_by: str,
        error: Exception,
        details: dict | None = None,
    ) -> AuditLogEntry:
        """Convenience method for logging a failed flow."""
        return await self.log(
            flow_name=flow_name,
            status=FlowStatus.FAILED,
            principal_id=principal_id,
            requested_by=requested_by,
            details=details,
            error=str(error),
        )