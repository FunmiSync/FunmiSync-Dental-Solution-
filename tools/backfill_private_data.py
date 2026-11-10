"""Plan legacy PHI encryption/blind-index repair. Dry-run unless --apply.

Requires the reviewed Phase 4 schema and original encryption/HMAC keys.
Never prints names, contacts, provider bodies or decrypted values.
"""
import argparse
from collections import Counter
import json
from cryptography.fernet import InvalidToken
from app.security.encryption import decode_secret, decode_json_secret, encrypt_secret, encrypt_json_secret, hash_lookup
from app.security.search_index import name_index
from app.models.entities import Patients, RegisteredClinics, AppointmentSyncLog, InboundEvent, Audit_logs, PaymentTransaction, WalletLedgerEntry


def plaintext(value):
    if value is None:
        return None, False
    try:
        return decode_secret(value), True
    except (InvalidToken, ValueError, UnicodeError):
        if value.startswith("gAAAA"):
            raise ValueError("Unreadable encrypted data: verify the original encryption key") from None
        return value, False


def plan(db):
    changes, counts, seen = [], Counter(), set()
    def fields(row, names):
        updates = {}
        for name in names:
            value, encrypted = plaintext(getattr(row, name))
            if value is not None and not encrypted:
                updates[name] = encrypt_secret(value)
        return updates
    for row in db.query(Patients).yield_per(200):
        updates = fields(row, ["FName", "LName", "Gender", "phone", "email", "contact_id"])
        contact, _ = plaintext(row.contact_id)
        if not contact:
            raise ValueError("Patient with missing contact needs manual review")
        fingerprint = hash_lookup(contact)
        identity = (row.clinic_id, fingerprint)
        if identity in seen:
            raise ValueError("Duplicate clinic/contact identities need manual review; no changes applied")
        seen.add(identity)
        if row.contact_id_hash != fingerprint:
            updates["contact_id_hash"] = fingerprint
        if updates:
            changes.append((row, updates)); counts["patients"] += 1
    for model, names in (
        (RegisteredClinics,["od_developer_key","od_customer_key","crm_api_key","webhook_secret","calendar_id"]),
        (InboundEvent,["contact_id"]), (AppointmentSyncLog,["patient_name","contact_id"]),
    ):
        for row in db.query(model).yield_per(200):
            updates = fields(row,names)
            if isinstance(row, AppointmentSyncLog):
                name, _ = plaintext(row.patient_name)
                expected = name_index(name)
                if row.patient_name_search != expected:
                    updates["patient_name_search"] = expected
            if hasattr(row,"payload") and row.payload is not None:
                payload, encrypted = plaintext(row.payload)
                decoded = json.loads(payload)
                if not isinstance(decoded,dict):
                    raise ValueError("Non-object legacy payload needs manual review")
                if not encrypted:
                    updates["payload"] = encrypt_json_secret(decoded)
            if updates:
                changes.append((row,updates));counts[model.__tablename__] += 1
    for model in (PaymentTransaction, WalletLedgerEntry, Audit_logs):
        for row in db.query(model).yield_per(200):
            details = row.details or {}
            if "encrypted" in details:
                decode_json_secret(details["encrypted"])
            if "provider_response_encrypted" in details:
                decode_json_secret(details["provider_response_encrypted"])
            if isinstance(row, Audit_logs) and "encrypted" not in details:
                changes.append((row,{"details":{"encrypted":encrypt_json_secret(details)}}));counts[model.__tablename__] += 1
            elif "provider_response" in details:
                updated = dict(details)
                value = updated.pop("provider_response")
                updated["provider_response_encrypted"] = encrypt_json_secret(value)
                changes.append((row,{"details":updated}));counts[model.__tablename__] += 1
    return changes, dict(counts)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true",help="Write the reviewed plan; requires explicit operator approval")
    args=parser.parse_args()
    from app.infrastructure.database import SessionLocal
    db=SessionLocal()
    try:
        changes,counts=plan(db)
        print(json.dumps({"mode":"apply" if args.apply else "dry-run", "rows":counts},sort_keys=True))
        if args.apply:
            for row,updates in changes:
                for field,value in updates.items():setattr(row,field,value)
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__=="__main__":main()
