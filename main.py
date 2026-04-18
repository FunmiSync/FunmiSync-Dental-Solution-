from fastapi import FastAPI
from api.routers.webhook import webhook_config
from core import database
from api.routers.webhook import webhook_crm
from core.middleware import RateLimitMiddleware
from fastapi.middleware.cors import CORSMiddleware
from api.routers import dso_clinic_page
from auth import login, logout
from api.registration import user_registration, dso_registration, clinic_registration
from api.routers import invites
import logging
from api.routers import workspace
from api.routers.synclog import sync_log_dso
from api.routers.synclog import sync_log_clinic


log = logging.getLogger("uvicorn.error")
logging.basicConfig(
    level=logging.INFO,  # or DEBUG
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

origins = ["https://fumisync-project.vercel.app"]
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Webhook-Secret"],
    expose_headers=["X-Request-ID", "Retry-After"],

)
app.add_middleware(RateLimitMiddleware)
app.include_router(webhook_crm.router)
app.include_router(webhook_config.router)
app.include_router(login.router)
app.include_router(logout.router)
app.include_router(user_registration.router)
app.include_router(dso_registration.router)
app.include_router(clinic_registration.router)
app.include_router(invites.router)
app.include_router(workspace.router)
app.include_router(sync_log_dso.router)
app.include_router(dso_clinic_page.router)
app.include_router(sync_log_clinic.router)



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
