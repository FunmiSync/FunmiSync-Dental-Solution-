"""Short-lived sessions recheck both JWT expiry and current scope permissions."""
from app.infrastructure.database import AsyncSessionLocal
from app.security.oauth2 import get_current_user
from app.security.rbac import require_clinic_access, require_dso_access


async def revalidate_stream(token, *, clinic_id=None, dso_id=None):
    # A stream outlives its request, so each check uses its own short session.
    async with AsyncSessionLocal() as db:
        user = await get_current_user(token, db)
        if clinic_id is not None:
            await require_clinic_access(db=db, user_id=user.id, clinic_id=clinic_id)
        else:
            await require_dso_access(db=db, user_id=user.id, dso_id=dso_id)
