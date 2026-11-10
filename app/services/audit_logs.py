"""Audit entries participate in the caller's transaction; no hidden commit."""
from uuid import UUID, uuid4
from app.models.entities import Audit_logs
from app.security.encryption import encrypt_json_secret


def record_audit(db, *, clinic_id=None, entity_type, entity_id, action,
                 status="success", source="api", details=None):
    row = Audit_logs(id=uuid4(), clinic_id=clinic_id, entity_type=entity_type,
                     entity_id=str(entity_id) if entity_id is not None else None,
                     action=action, status=status, source=source,
                     details={"encrypted":encrypt_json_secret(details or {})})
    db.add(row)
    return row


class Auditlogs:
    def __init__(self, clinic_id: UUID | None, db, source: str | None = None):
        self.db, self.clinic_id, self.source = db, clinic_id, source or "api"

    async def log(self, *, entity_type, entity_id, action, status="success", details=None):
        return record_audit(self.db,clinic_id=self.clinic_id,entity_type=entity_type,
                            entity_id=entity_id,action=action,status=status,source=self.source,details=details)
