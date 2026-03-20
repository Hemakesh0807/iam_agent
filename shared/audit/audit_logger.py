import json
import logging
from datetime import datetime
from pathlib import Path

from shared.config import config
from shared.models import AuditLogEntry, FlowName, FlowStatus

logger = logging.getLogger(__name__)

# Local audit log file path (used when Cosmos DB is not configured)
_LOCAL_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "audit.jsonl"


class AuditLogger:
    """
    Writes structured audit log entries to Cosmos DB (production)
    or a local .jsonl file (local dev when COSMOS_DB_ENDPOINT is not set).

    Every action taken by either bot must be logged here regardless of success or failure.

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
        self._use_cosmos = bool(config.cosmos_endpoint and config.cosmos_key)

        if self._use_cosmos:
            from azure.cosmos.aio import CosmosClient
            self._client = CosmosClient(config.cosmos_endpoint, credential=config.cosmos_key)
            self._db = self._client.get_database_client(config.cosmos_database)
            self._container = self._db.get_container_client(self.CONTAINER_NAME)
            logger.info("AuditLogger: using Cosmos DB.")
        else:
            logger.warning(
                "AuditLogger: COSMOS_DB_ENDPOINT not set. "
                "Writing audit logs to local file: %s", _LOCAL_LOG_PATH
            )

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

        if self._use_cosmos:
            await self._write_to_cosmos(entry)
        else:
            self._write_to_file(entry)

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

    async def _write_to_cosmos(self, entry: AuditLogEntry) -> None:
        try:
            from azure.cosmos.exceptions import CosmosHttpResponseError
            await self._container.create_item(body=entry.model_dump(mode="json"))
            logger.info(
                "Audit log -> Cosmos: flow=%s status=%s principal=%s",
                entry.flow_name.value, entry.status.value, entry.principal_id,
            )
        except Exception as exc:
            # Never let audit logging failure break the main flow
            logger.error("Failed to write audit log to Cosmos DB: %s", exc)

    def _write_to_file(self, entry: AuditLogEntry) -> None:
        """Append a single JSON line to the local audit log file."""
        try:
            with open(_LOCAL_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(entry.model_dump_json() + "\n")
            logger.info(
                "Audit log -> file: flow=%s status=%s principal=%s",
                entry.flow_name.value, entry.status.value, entry.principal_id,
            )
        except Exception as exc:
            logger.error("Failed to write audit log to file: %s", exc)