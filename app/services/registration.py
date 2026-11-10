from sqlalchemy.exc import IntegrityError
from app.models.entities import Dso, Users, RoleAssignment, ScopeType, RoleType
from app.security.oauth2 import hashpassword
from app.services.audit_logs import record_audit


class RegistrationConflict(ValueError):
    pass


def create_dso(db, *, user_id, name):
    if db.query(Dso).filter(Dso.user_id == user_id).first():
        raise RegistrationConflict("This Dso already exist")
    try:
        dso = Dso(name=name,user_id=user_id)
        db.add(dso)
        db.flush()
        db.add(RoleAssignment(user_id=user_id,scope_type=ScopeType.DSO,dso_id=dso.id,
                              role=RoleType.ADMIN,created_by=user_id))
        record_audit(db,entity_type="dso",entity_id=dso.id,action="create",details={"actor_user_id":str(user_id)})
        db.commit()
        db.refresh(dso)
        return dso
    except IntegrityError as exc:
        db.rollback()
        raise RegistrationConflict("This Dso already exist") from exc
    except Exception:
        db.rollback()
        raise


def create_user(db, *, username, email, password):
    email = email.strip().lower()
    if db.query(Users).filter(Users.email == email).first():
        raise RegistrationConflict("User with this email already exist")
    user = Users(username=username,email=email,password=hashpassword(password))
    try:
        db.add(user)
        db.flush()
        record_audit(db,entity_type="user",entity_id=user.id,action="register")
        db.commit()
        db.refresh(user)
        return user
    except IntegrityError as exc:
        db.rollback()
        raise RegistrationConflict("User with this email already exist") from exc
    except Exception:
        db.rollback()
        raise
