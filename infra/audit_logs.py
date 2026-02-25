from core.database import SessionLocal

class Auditlogs():
    def __init__(self, clinic_id : str , db, source : str | None  = None):
        self.db = db 
        self.clinic_id = clinic_id
        self.source = source
    async def log(self, )
