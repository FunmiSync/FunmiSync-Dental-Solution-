# FumiSync Backend

FumiSync is a healthcare operations backend for clinics and dental service organizations (DSOs). It connects workspace management, CRM/PMS-style workflow sync, audit logs, and ToroForge/Toronet wallet billing into one API.

Live frontend demo: https://fumisync-project.vercel.app/

## What This Backend Does

- Registers users, DSOs, standalone clinics, and DSO-managed clinics.
- Manages workspace and team access with role-based authorization.
- Receives CRM/PMS webhook events and records sync activity.
- Exposes sync logs for clinic and DSO operations teams.
- Provisions ToroForge/Toronet wallets for clinics and DSO treasury accounts.
- Handles wallet KYC submission/status, funding initialization, funding verification, ledger entries, and DSO-to-clinic wallet transfers.
- Supports a wallet-based premium service model where billable actions such as eligibility checks, patient messaging, appointment reminders, and AI-assisted calls can be metered and debited from funded clinic wallets.

## Architecture

```txt
FumiSync API
  |
  |-- FastAPI routers
  |     |-- auth and registration
  |     |-- DSO/clinic workspaces and team access
  |     |-- webhook config and CRM/PMS ingestion
  |     |-- sync log list/detail/stream endpoints
  |     |-- ToroForge wallet, KYC, funding, and transfer endpoints
  |
  |-- Service layer
  |     |-- RBAC and workspace validation
  |     |-- sync log and webhook helpers
  |     |-- ToroForge wallet provisioning
  |     |-- ToroForge funding verification
  |     |-- DSO-to-clinic wallet transfers
  |
  |-- Data layer
        |-- PostgreSQL
        |-- SQLAlchemy models
        |-- Alembic migrations
```

## Technology Stack

- Python 3.11+
- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL
- Redis/RQ-ready infrastructure
- Pydantic v2
- HTTPX async clients
- JWT authentication
- ToroForge/Toronet API integrations

## Repository Structure

```txt
.
|-- main.py                         # FastAPI app entrypoint
|-- config.py                       # Environment-backed settings
|-- requirements.txt                # Python dependencies
|-- alembic/                        # Database migrations
|-- api/                            # HTTP routers
|-- auth/                           # Authentication and security helpers
|-- billing/toroforge/              # ToroForge clients and wallet services
|-- core/                           # Database, models, schemas, middleware
|-- infra/                          # Domain services and sync helpers
|-- workers/                        # Background worker entrypoints
```

## Prerequisites

- Python 3.11 or newer
- PostgreSQL database reachable from the app
- Redis instance for rate limiting/session-related infrastructure
- ToroForge/Toronet credentials and base URLs
- Google OAuth client ID if Google sign-in is enabled

The current database connector uses `sslmode=require`, so a managed PostgreSQL instance with SSL support is recommended for review and deployment.

## Local Setup

Clone the repository:

```powershell
git clone https://github.com/FunmiSync/FunmiSync-Dental-Solution-.git
cd FunmiSync-Dental-Solution-
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create your local environment file:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and fill in the database, Redis, Google, and ToroForge values for your environment.

Generate a Fernet encryption key for `ENCRYPTION_KEY`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run database migrations:

```powershell
alembic upgrade head
```

Start the API:

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open the API docs:

```txt
http://localhost:8000/docs
```

Health check:

```txt
GET http://localhost:8000/
```

Expected response:

```json
{
  "status": "running",
  "message": "Welcome to PMS CRM Sync API"
}
```

## Environment Variables

Use `.env.example` as the source of truth for required configuration. Do not commit real `.env` files.

Core groups:

- Database: `DATABASE_USERNAME`, `DATABASE_PASSWORD`, `DATABASE_HOSTNAME`, `DATABASE_PORTNAME`, `DATABASE_NAME`
- Auth/security: `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`, `ENCRYPTION_KEY`, `HASH_KEY`
- Infrastructure: `REDIS_URL`, `BACKEND_BASE_URL`
- Google auth: `GOOGLE_CLIENT_ID`
- ToroForge/Toronet: `TOROFORGE_NETWORK`, `TOROFORGE_BASE_URL`, `TOROFORGE_CONNECTW_URL`, `TOROFORGE_DEPLOYER_URL`, `TOROFORGE_ADMIN`, `TOROFORGE_ADMINPWD`

## ToroForge / Toronet Usage

FumiSync uses ToroForge/Toronet as the wallet and transaction layer for healthcare operations billing.

Main integration points:

- Wallet provisioning for standalone clinics, DSO clinics, and DSO treasury accounts.
- TNS name checks/registration during wallet creation.
- KYC submission and status checks for wallets.
- Funding initialization for clinic wallet top-ups.
- Deposit verification using ToroForge transaction lookups and wallet balance checks.
- Ledger entries for wallet funding and transfers.
- DSO-to-clinic wallet transfers with cached balance validation.
- Planned premium-service debits for billable events such as eligibility checks, patient messaging, appointment reminders, and AI-assisted calls.

ToroForge clients live in:

```txt
billing/toroforge/toroforge_client/
```

Business services live in:

```txt
billing/toroforge/toroforge_service/
```

API routers live in:

```txt
api/routers/toroforge_endpoint/
```

Important ToroForge endpoints:

```txt
POST /toroforge/clinics/{clinic_id}/wallet
POST /toroforge/dsos/{dso_id}/wallet
POST /toroforge/dsos/{dso_id}/clinics/{clinic_id}/wallet
POST /toroforge/wallets/{wallet_id}/kyc
GET  /toroforge/wallets/{wallet_id}/kyc-status
POST /toroforge/wallets/{wallet_id}/funding
POST /toroforge/wallets/{wallet_id}/funding/{payment_transaction_id}/verify-deposit
POST /toroforge/dsos/{dso_id}/clinics/{clinic_id}/wallet-transfer
```

## Reviewer Flow

A reviewer can evaluate the main wallet flow with these steps:

1. Register a user.
2. Create a DSO or standalone clinic.
3. Create a clinic wallet or DSO treasury wallet.
4. Submit/check wallet KYC.
5. Initialize wallet funding.
6. Verify a deposit after ToroForge confirms the transaction/balance.
7. Create a DSO-to-clinic wallet transfer.
8. Inspect wallet center and ledger responses.

The hosted frontend provides a user-facing demo path:

```txt
https://fumisync-project.vercel.app/
```

## Security Notes

- Never commit real `.env` files or production secrets.
- Rotate any secret that has ever been pushed to a public repository.
- Use separate ToroForge credentials for local, staging, and production environments.
- Use HTTPS-only deployments for cookie-based authentication.
- Keep `ENCRYPTION_KEY` stable per environment; changing it breaks decryption of stored encrypted secrets.
- Store production secrets in the deployment provider's secret manager, not in source control.

## Development Commands

Run the API:

```powershell
uvicorn main:app --reload
```

Run migrations:

```powershell
alembic upgrade head
```

Create a new migration:

```powershell
alembic revision --autogenerate -m "describe change"
```

Check Python syntax quickly:

```powershell
python -m compileall api auth billing core infra workers main.py config.py
```

## Deployment Notes

Recommended production shape:

- FastAPI app deployed behind HTTPS.
- Managed PostgreSQL with SSL enabled.
- Managed Redis.
- Environment secrets stored outside Git.
- CORS restricted to trusted frontend origins.
- Alembic migrations run before application startup.
- Separate staging and production ToroForge/Toronet credentials.

## License

MIT.
