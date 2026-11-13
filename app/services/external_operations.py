"""Write intent before external side effects; never blindly retry uncertainty."""
import json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.models.entities import ExternalOperation
from app.security.encryption import hash_lookup, encrypt_json_secret, decode_json_secret
from app.infrastructure.providers.toroforge.exceptions import ToroForgeError, ToroForgeValidationError, DefinitiveRejection
from app.services.audit_logs import record_audit


class ReconciliationRequired(ToroForgeError):
    pass


class OutcomeNotKnownYet(ReconciliationRequired):
    """A timed-out call may still show up at the provider; look again shortly."""


class OutcomeNeedsPerson(ReconciliationRequired):
    """The provider's records do not settle what happened; a person must decide."""


def stored_request(operation: ExternalOperation) -> dict | None:
    """The request sent to the provider; None once applied, or for calls made before it was kept."""
    return decode_json_secret(operation.request_encrypted) if operation.request_encrypted else None


def request_fingerprint(request: dict) -> str:
    return hash_lookup(json.dumps(request, sort_keys=True, separators=(",", ":"), default=str))


class ExternalOperations:
    def __init__(self, db):
        self.db = db

    async def get(self, key):
        return await self.db.scalar(select(ExternalOperation).filter_by(operation_key=key).limit(1))

    async def confirmed_result(self, key):
        """The provider's result when the call is known to have happened, else None."""
        operation = await self.get(key)
        if operation and operation.state in {"succeeded", "applied"} and operation.result_encrypted:
            return decode_json_secret(operation.result_encrypted)
        return None

    async def active(self, resource_id, action):
        return await self.db.scalar(select(ExternalOperation).where(
            ExternalOperation.resource_id == resource_id, ExternalOperation.action == action,
            ExternalOperation.state.in_(["in_flight", "uncertain", "succeeded"]),
        ).limit(1))

    async def execute(self, *, key, resource_id, action, request, call, secrets=None):
        fingerprint = request_fingerprint(request)
        operation = await self.get(key)
        if operation:
            if operation.request_hash != fingerprint:
                raise ToroForgeValidationError("Idempotency key was used for a different request")
            if operation.state in {"succeeded", "applied"}:
                return decode_json_secret(operation.result_encrypted)
            raise ReconciliationRequired("Existing external operation requires reconciliation")
        if await self.active(resource_id, action):
            raise ReconciliationRequired("An external operation for this resource requires reconciliation")
        operation = ExternalOperation(id=uuid4(), operation_key=key, resource_id=resource_id,
                                      action=action, request_hash=fingerprint, state="in_flight",
                                      request_encrypted=encrypt_json_secret(json.loads(json.dumps(request, default=str))),
                                      secrets_encrypted=encrypt_json_secret(secrets))
        self.db.add(operation)
        record_audit(self.db,entity_type="external_operation",entity_id=operation.id,action="intent",
                     details={"resource_id":str(resource_id),"operation":action})
        try:
            await self.db.commit()  # durable before the provider can do anything
        except IntegrityError as exc:
            await self.db.rollback()
            raise ReconciliationRequired("Concurrent external operation requires reconciliation") from exc
        try:
            result = await call()
        except BaseException as exc:
            # Cancellation/crashes must not authorize a replay either. A hard crash
            # leaves the already committed in_flight row, also blocked on replay.
            # Note: this rollback expires rows the caller loaded; callers that keep
            # using them afterwards reload them with database.reload_expired().
            await self.db.rollback()
            operation = await self.get(key)
            if operation:
                operation.state = "failed" if isinstance(exc, DefinitiveRejection) else "uncertain"
                operation.failure_code = type(exc).__name__
                record_audit(self.db,entity_type="external_operation",entity_id=operation.id,action=operation.state,
                             status="failure",details={"failure_code":operation.failure_code})
                await self.db.commit()
            raise
        try:
            operation.result_encrypted = encrypt_json_secret(result)
            operation.state = "succeeded"
            record_audit(self.db,entity_type="external_operation",entity_id=operation.id,action="provider_confirmed")
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            raise ReconciliationRequired("Provider result requires reconciliation before retry") from exc
        return result

    async def mark_applied(self, key):
        """Caller commits this change atomically with its own local entity update."""
        operation = await self.get(key)
        if not operation or operation.state not in {"succeeded", "applied"}:
            raise ReconciliationRequired("External operation has no confirmed result")
        operation.state = "applied"
        operation.request_encrypted = None  # nothing left to look up; do not keep patient details
        record_audit(self.db,entity_type="external_operation",entity_id=operation.id,action="local_applied")

    @staticmethod
    def unknown(operation) -> bool:
        """The call was sent but nobody knows yet whether the provider acted on it."""
        return operation is not None and operation.state in {"in_flight", "uncertain"}

    @staticmethod
    def retire(operation, *, failure_code: str) -> None:
        """Closed as not done. Frees the key so the next attempt calls the provider afresh;
        the row stays as evidence. The caller commits."""
        operation.state = "failed"
        operation.failure_code = failure_code
        operation.operation_key = f"{operation.operation_key}#retired-{uuid4().hex[:12]}"

    async def settle_from_provider_read(self, operation, *, result: dict | None, evidence: str) -> None:
        """Internal only: a read of the provider's own records settled an unknown call.
        result: what the call produced, or None when it was not done. Commits."""
        if result is None:
            self.retire(operation, failure_code="checked_not_done")
        else:
            operation.result_encrypted = encrypt_json_secret(result)
            operation.state = "succeeded"
            operation.failure_code = None
        record_audit(self.db, entity_type="external_operation", entity_id=operation.id,
                     action="checked_happened" if result is not None else "checked_not_done",
                     details={"operation": operation.action, "evidence": evidence})
        await self.db.commit()

    async def confirm_from_verified_read(self, key, result):
        """Internal only: caller must establish identity using a provider read."""
        operation = await self.get(key)
        if not operation or operation.state == "failed":
            raise ReconciliationRequired("Operation cannot be reconciled")
        operation.result_encrypted = encrypt_json_secret(result)
        operation.state = "succeeded"
        await self.db.commit()
