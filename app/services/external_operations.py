"""Write intent before external side effects; never blindly retry uncertainty."""
import json
from uuid import uuid4
from sqlalchemy.exc import IntegrityError
from app.models.entities import ExternalOperation
from app.security.encryption import hash_lookup, encrypt_json_secret, decode_json_secret
from app.infrastructure.providers.toroforge.exceptions import ToroForgeError, ToroForgeValidationError, DefinitiveRejection
from app.services.audit_logs import record_audit


class ReconciliationRequired(ToroForgeError):
    pass


def request_fingerprint(request: dict) -> str:
    return hash_lookup(json.dumps(request, sort_keys=True, separators=(",", ":"), default=str))


class ExternalOperations:
    def __init__(self, db):
        self.db = db

    def get(self, key):
        return self.db.query(ExternalOperation).filter_by(operation_key=key).first()

    def active(self, resource_id, action):
        return self.db.query(ExternalOperation).filter(
            ExternalOperation.resource_id == resource_id, ExternalOperation.action == action,
            ExternalOperation.state.in_(["in_flight", "uncertain", "succeeded"]),
        ).first()

    async def execute(self, *, key, resource_id, action, request, call, secrets=None):
        fingerprint = request_fingerprint(request)
        operation = self.get(key)
        if operation:
            if operation.request_hash != fingerprint:
                raise ToroForgeValidationError("Idempotency key was used for a different request")
            if operation.state in {"succeeded", "applied"}:
                return decode_json_secret(operation.result_encrypted)
            raise ReconciliationRequired("Existing external operation requires reconciliation")
        if self.active(resource_id, action):
            raise ReconciliationRequired("An external operation for this resource requires reconciliation")
        operation = ExternalOperation(id=uuid4(), operation_key=key, resource_id=resource_id,
                                      action=action, request_hash=fingerprint, state="in_flight",
                                      secrets_encrypted=encrypt_json_secret(secrets))
        self.db.add(operation)
        record_audit(self.db,entity_type="external_operation",entity_id=operation.id,action="intent",
                     details={"resource_id":str(resource_id),"operation":action})
        try:
            self.db.commit()  # durable before the provider can do anything
        except IntegrityError as exc:
            self.db.rollback()
            raise ReconciliationRequired("Concurrent external operation requires reconciliation") from exc
        try:
            result = await call()
        except BaseException as exc:
            # Cancellation/crashes must not authorize a replay either. A hard crash
            # leaves the already committed in_flight row, also blocked on replay.
            self.db.rollback()
            operation = self.get(key)
            if operation:
                operation.state = "failed" if isinstance(exc, DefinitiveRejection) else "uncertain"
                operation.failure_code = type(exc).__name__
                record_audit(self.db,entity_type="external_operation",entity_id=operation.id,action=operation.state,
                             status="failure",details={"failure_code":operation.failure_code})
                self.db.commit()
            raise
        try:
            operation.result_encrypted = encrypt_json_secret(result)
            operation.state = "succeeded"
            record_audit(self.db,entity_type="external_operation",entity_id=operation.id,action="provider_confirmed")
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            raise ReconciliationRequired("Provider result requires reconciliation before retry") from exc
        return result

    def mark_applied(self, key):
        """Caller commits this change atomically with its own local entity update."""
        operation = self.get(key)
        if not operation or operation.state not in {"succeeded", "applied"}:
            raise ReconciliationRequired("External operation has no confirmed result")
        operation.state = "applied"
        record_audit(self.db,entity_type="external_operation",entity_id=operation.id,action="local_applied")

    def confirm_from_verified_read(self, key, result):
        """Internal only: caller must establish identity using a provider read."""
        operation = self.get(key)
        if not operation or operation.state == "failed":
            raise ReconciliationRequired("Operation cannot be reconciled")
        operation.result_encrypted = encrypt_json_secret(result)
        operation.state = "succeeded"
        self.db.commit()
