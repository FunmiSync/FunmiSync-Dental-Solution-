from fastapi import FastAPI
from core import database
from api.routers import webhook_crm
from core.middleware import RateLimitMiddleware
from fastapi.middleware.cors import CORSMiddleware
from auth import login, logout
from api.registration import user_registration, dso_registration, clinic_registration
from api import invites
import logging

log = logging.getLogger("uvicorn.error")
logging.basicConfig(
    level=logging.INFO,  # or DEBUG
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

origins = ["*"]
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],

)
app.add_middleware(RateLimitMiddleware)
app.include_router(webhook_crm.router)
app.include_router(login.router)
app.include_router(logout.router)
app.include_router(user_registration.router)
app.include_router(dso_registration.router)
app.include_router(clinic_registration.router)
app.include_router(invites.router)


@app.on_event("startup")
def verify_db_on_start():
    ok, msg = database.ping_db()
    if ok:
        log.info(msg)
    else:
        log.error(msg)


@app.get("/")
async def root():
    return {"status": "running", "message": "Welcome to OpenDental CRM Sync API"}
