from core.database import SessionLocal
from uuid import UUID

class Auditlogs():
    def __init__(self, clinic_id : UUID , db, source : str | None  = None):
        self.db = db 
        self.clinic_id = clinic_id
        self.source = source
    async def log(self, *args, **kwargs):
        return None
