from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.controllers import health
from app.config import settings
from app.controllers.webhooks import webhook_config
from app.infrastructure import database
from app.controllers.webhooks import webhook_crm
from app.infrastructure.middleware import RateLimitMiddleware
from fastapi.middleware.cors import CORSMiddleware
from app.controllers.workspaces import dso_clinic_page
from app.controllers.auth import login
from app.controllers.auth import logout
from app.controllers.registration import user_registration
from app.controllers.registration import dso_registration
from app.controllers.registration import clinic_registration
from app.controllers.workspaces import invites
import logging
from app.controllers.workspaces import workspace
from app.controllers.workspaces import team_members
from app.controllers.sync_logs import sync_log_dso
from app.controllers.sync_logs import sync_log_clinic
from app.controllers.billing import toroforge_kyc
from app.controllers.billing import toroforge_wallet_transfer
from app.controllers.billing import toroforge_wallet_creation
from app.controllers.billing import toroforge_billing_wallet_read
from app.controllers.billing import toroforge_funding
from app.controllers import notifications
from app.controllers.errors import service_error_handler
from app.services.errors import ServiceError


log = logging.getLogger("uvicorn.error")
logging.basicConfig(
    level=logging.INFO,  # or DEBUG
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

@asynccontextmanager
async def lifespan(application):
    await verify_db_on_start()
    yield


origins = settings.cors_origins
app = FastAPI(lifespan=lifespan)
app.add_exception_handler(ServiceError, service_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Webhook-Secret", "Idempotency-Key"],
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
app.include_router(team_members.router)
app.include_router(sync_log_dso.router)
app.include_router(dso_clinic_page.router)
app.include_router(sync_log_clinic.router)
app.include_router(toroforge_kyc.router)
app.include_router(toroforge_wallet_creation.router)
app.include_router(toroforge_billing_wallet_read.router)
app.include_router(toroforge_funding.router)
app.include_router(toroforge_wallet_transfer.router)
app.include_router(health.router)
app.include_router(notifications.router)


async def verify_db_on_start():
    ok, msg = await database.ping_db()
    if ok:
        log.info(msg)
    else:
        log.error(msg)


@app.get("/")
async def root():
    return {"status": "running", "message": "Welcome to PMS CRM Sync API"}
