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
|-- requirements.txt                # Python dependencies
|-- app/
|   |-- config.py                   # Environment-backed settings
|   |-- controllers/                # HTTP routes (auth, registration, workspaces, sync, billing)
|   |-- models/entities.py          # Canonical SQLAlchemy models and enums
|   |-- schemas/api.py              # API input/output schemas (JSON presentation boundary)
|   |-- services/                   # Application workflows and billing services
|   |-- security/                   # JWT, CSRF, encryption, RBAC, webhook verification
|   |-- infrastructure/             # Database, Redis/cache, providers and workers
|-- alembic/                        # Database migrations
|-- tests/                          # Contract, behavior and MVC compatibility checks
|-- tools/                          # Offline contract inspection and DOCX generation
|-- docs/                           # Contract snapshots and the master teaching guide
|-- api/, auth/, billing/, core/, infra/, caches/, sdk/, workers/
                                   # Compatibility modules; edit canonical files in app/
```

The backend uses an MVC-oriented API layout with services: controllers handle HTTP,
models describe persistence, and schemas define the JSON presentation contract.
Each layer has one job, and `tests/test_mvc_architecture.py` enforces the rules:

| Layer | Owns | Must not |
|---|---|---|
| `controllers/` | Route signature, auth guards, calling one service, shaping the response | Query or commit the database session; import another router module |
| `security/` | Request guards that raise `HTTPException` (RBAC, wallet access, CSRF, JWT) | Business workflows |
| `services/` | Business rules, transactions, cache invalidation, provider calls | Raise `HTTPException`; import controllers |
| `models/`, `schemas/` | Tables and JSON shapes | Workflow logic |

Services report expected failures by raising an error from `app/services/errors.py`
(`NotFound`, `PermissionDenied`, `InvalidRequest`, ...). `app/controllers/errors.py`
turns it into the HTTP status and `{"detail": ...}` body, so a route usually looks like:

```python
@router.post("/accept", status_code=status.HTTP_201_CREATED)
async def accept_invite(payload: accept_invite_request, current_user: Users = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    return invite_service.accept_invite(db, token=payload.token, user=current_user)
```

### Async database and I/O

The API and the background worker are fully async:

- **Database:** SQLAlchemy `AsyncSession` on `asyncpg` (`app/infrastructure/database.py`).
  Write queries as `await db.scalar(select(Model).where(...))`; the legacy `db.query()` API
  is not available on an async session, and a test rejects it in `app/`. Alembic migrations
  and `tools/` keep a separate synchronous engine (`psycopg2`).
- **Supabase:** SSL is required, and prepared-statement caching is disabled so the same
  settings work on the direct connection or session pooler (port 5432) and the transaction
  pooler (port 6543).
- **Redis:** caches, rate limits and live-update pub/sub use the async client. RQ has no async
  client, so jobs are enqueued through `run_in_threadpool`.
- **CPU-heavy or blocking libraries:** bcrypt and Google token verification run in
  `run_in_threadpool` so they never freeze the event loop.
- **Background jobs:** each RQ job runs in its own event loop, so it opens a job-scoped session
  with `worker_session()` instead of the shared pool.

Two async rules to remember:

1. **Committed objects keep their values** (`expire_on_commit=False`); call
   `await db.refresh(obj)` when you need fresh database state.
2. **A rollback expires every row loaded in that transaction**, and reading an expired
   attribute raises `MissingGreenlet` instead of silently reloading. `ExternalOperations`
   rolls back when a provider call fails, so code that keeps using a row afterwards calls
   `await reload_expired(db, row)` first, or captures values such as ids beforehand.

### Resolving stuck provider actions

If ToroForge or OpenDental doesn't answer, the app parks that action as "outcome unknown"
and refuses to repeat it, so a retry can never pay or book twice. Check the provider, then
record what really happened (every resolution goes to the audit log):

```bash
.venv/bin/python -m tools.stuck_operations list
.venv/bin/python -m tools.stuck_operations resolve <id> --happened --set TX_ID=<from ToroForge> --by "<your name>"
.venv/bin/python -m tools.stuck_operations resolve <id> --not-done --by "<your name>" --note "<what you checked>"
```

`--happened` reuses the provider's result (a confirmed transfer posts its balances immediately);
`--not-done` closes the action so the next attempt calls the provider again. Actions that started
less than 10 minutes ago may still be running and need `--force`.

Most cases resolve themselves. `auto` asks the providers (read-only) and records the answer
through the same `resolve`, with `automatic check` as the resolver in the audit log:

```bash
.venv/bin/python -m tools.stuck_operations auto --dry-run   # report only
.venv/bin/python -m tools.stuck_operations auto             # run as a Render Cron Job every 15 minutes
```

| Action | Counts as done when | Counts as not done when (after 1 hour) |
| --- | --- | --- |
| DSO→clinic transfer | Toronet shows exactly one unused transfer of that exact amount from the treasury to the clinic since it started | Toronet shows no transfer from the treasury to the clinic since it started |
| Top-up start | ToroForge has exactly one unlinked payment of that amount for the wallet | ToroForge has none |
| Wallet key creation | (never; the address was lost) | always: no one can have paid into an address no one received |
| Wallet name (TNS) | the name points to this wallet | the name is not registered |
| Wallet password change | ToroForge accepts the new password (it is then saved on the wallet) | (never; a person decides) |
| OpenDental patient | the patient row already has a PatNum, or exactly one OpenDental patient has the same name and birth date | OpenDental has no patient with that last name and birth date |
| OpenDental appointment | exactly one appointment for that patient at that time in that operatory | none |

Anything else is left for a person: several matches, a near match (for example a different
amount), a provider error, comm logs and popups, and OpenDental actions started before request
details were kept. Under an hour, "nothing found yet" waits for the next run. Requests are kept
encrypted in `external_operations.request_encrypted` until the result is applied.

`tests/test_mvc_architecture.py` fails if an `async def` never awaits or a session call
(`commit`, `execute`, ...) is not awaited.

Billing controllers share `controllers/billing/dependencies.py` (per-request ToroForge
clients) and `controllers/billing/errors.py` (provider-error mapping); the sync-log
streams share `controllers/sync_logs/streaming.py`.

For example, edit `app/controllers/auth/login.py` for login behavior. The old
`auth/login.py` path aliases that same module for existing imports. Old queued
`workers.workers.process_crm_load_job` references still resolve; new code uses
`app.infrastructure.workers.appointments.process_crm_load_job`. Keep compatibility
modules until callers and queued jobs have been audited. The full mapping is in
`docs/mvc_module_map.json`.

The single continuously updated teaching document is
`docs/FumiSync_Junior_Developer_Guide.docx`, generated from
`docs/junior_developer_guide.md`. It includes before/after examples, decision
tradeoffs, tests and independent debugging exercises.

## Prerequisites

- Python 3.11 or newer
- PostgreSQL database reachable from the app
- Redis instance for rate limiting/session-related infrastructure
- ToroForge/Toronet credentials and base URLs
- Google OAuth client ID if Google sign-in is enabled

The current database connector uses `sslmode=require`, so a managed PostgreSQL instance with SSL support is recommended for review and deployment.

## Required Services

The backend needs these services before a reviewer can run the full wallet flow:

- PostgreSQL with SSL enabled. The app builds a database URL from `DATABASE_USERNAME`, `DATABASE_PASSWORD`, `DATABASE_HOSTNAME`, `DATABASE_PORTNAME`, and `DATABASE_NAME`.
- Redis. `REDIS_URL` is used by the rate-limit middleware and RQ-ready queue setup.
- ToroForge/Toronet credentials and provider URLs. Wallet creation, KYC links, funding, deposit verification, and transfers call ToroForge upstream services.
- Google OAuth client ID only if Google sign-in is being used. Email/password registration and login can be reviewed without using Google OAuth directly.

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

If an existing virtual environment is already present, run the same install command again to sync missing packages:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
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

If `alembic` is not on your shell path, use the virtual-environment executable:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

Start the API:

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Or start it through the virtual environment:

```powershell
.\.venv\Scripts\uvicorn.exe main:app --reload --host 127.0.0.1 --port 8000
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

The first endpoint to open is `GET /`. If that returns the health-check response above, open `/docs` and continue with the authenticated reviewer flow.

## Environment Variables

Use `.env.example` as the source of truth for required configuration. Do not commit real `.env` files.

Core groups:

- Database: `DATABASE_USERNAME`, `DATABASE_PASSWORD`, `DATABASE_HOSTNAME`, `DATABASE_PORTNAME`, `DATABASE_NAME`
- Auth/security: `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`, `INVITE_TTL_HOURS`, `ENCRYPTION_KEY`, `HASH_KEY`
- Infrastructure: `REDIS_URL`, `BACKEND_BASE_URL`
- Google auth: `GOOGLE_CLIENT_ID`
- ToroForge/Toronet: `TOROFORGE_NETWORK`, `TOROFORGE_BASE_URL`, `TOROFORGE_CONNECTW_URL`, `TOROFORGE_DEPLOYER_URL`, `TOROFORGE_ADMIN`, `TOROFORGE_ADMINPWD`

Required values:

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_USERNAME` | Yes | PostgreSQL username |
| `DATABASE_PASSWORD` | Yes | PostgreSQL password |
| `DATABASE_HOSTNAME` | Yes | PostgreSQL host |
| `DATABASE_PORTNAME` | Yes | PostgreSQL port, usually `5432` |
| `DATABASE_NAME` | Yes | PostgreSQL database name |
| `SECRET_KEY` | Yes | JWT signing secret |
| `ALGORITHM` | Yes | JWT signing algorithm, usually `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Yes | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Yes | Refresh token lifetime |
| `INVITE_TTL_HOURS` | Yes | Team invite expiration window |
| `ENCRYPTION_KEY` | Yes | Fernet key for encrypted stored secrets |
| `HASH_KEY` | Yes | Separate HMAC/hash secret |
| `REDIS_URL` | Yes | Redis URL for rate limiting and queue infrastructure |
| `BACKEND_BASE_URL` | Yes | Public or local backend URL used by generated links |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth client ID when Google auth is enabled |
| `TOROFORGE_NETWORK` | Yes | ToroForge/Toronet network name, for example `testnet` |
| `TOROFORGE_BASE_URL` | Yes | ToroForge API base URL |
| `TOROFORGE_CONNECTW_URL` | Yes | ConnectW URL used for KYC links |
| `TOROFORGE_DEPLOYER_URL` | Yes | ToroForge deployer URL |
| `TOROFORGE_ADMIN` | Yes | ToroForge admin credential |
| `TOROFORGE_ADMINPWD` | Yes | ToroForge admin password |

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

A reviewer can evaluate the main wallet flow with the steps below. Protected endpoints require:

```txt
Authorization: Bearer <access_token>
Content-Type: application/json
Idempotency-Key: <unique-key-for-write-operations>
```

First create or sign in as a user, then create a DSO or standalone clinic through the registration endpoints. Use the returned `dso_id`, `clinic_id`, and login token in the examples below.

1. Confirm the app is alive:

```http
GET /
```

Expected response:

```json
{
  "status": "running",
  "message": "Welcome to PMS CRM Sync API"
}
```

2. Create a DSO treasury wallet:

```http
POST /toroforge/dsos/{dso_id}/wallet
Authorization: Bearer <access_token>
Content-Type: application/json
Idempotency-Key: dso-wallet-001

{
  "username": "fumi-dso-treasury"
}
```

Example response:

```json
{
  "wallet_id": "11111111-1111-4111-8111-111111111111",
  "scope_type": "dso",
  "dso_id": "22222222-2222-4222-8222-222222222222",
  "external_wallet_address": "0xabc123...",
  "external_wallet_username": "fumi-dso-treasury",
  "generated_password": "generated-wallet-password"
}
```

3. Create a standalone clinic wallet:

```http
POST /toroforge/clinics/{clinic_id}/wallet
Authorization: Bearer <access_token>
Content-Type: application/json
Idempotency-Key: clinic-wallet-001

{
  "username": "fumi-clinic-main"
}
```

Example response:

```json
{
  "wallet_id": "33333333-3333-4333-8333-333333333333",
  "scope_type": "clinic",
  "clinic_id": "44444444-4444-4444-8444-444444444444",
  "external_wallet_address": "0xdef456...",
  "external_wallet_username": "fumi-clinic-main",
  "generated_password": "generated-wallet-password"
}
```

For a DSO-managed clinic, use this endpoint instead:

```http
POST /toroforge/dsos/{dso_id}/clinics/{clinic_id}/wallet
```

4. Start wallet KYC:

```http
POST /toroforge/wallets/{wallet_id}/kyc
Authorization: Bearer <access_token>
Content-Type: application/json
```

Current response shape:

```json
[
  "https://connectw.example/KYC/project-verify?address=0xabc123...",
  "generated-wallet-password"
]
```

The first item is the ConnectW KYC URL. The second item is the wallet password needed by the provider KYC flow.

5. Check KYC status:

```http
GET /toroforge/wallets/{wallet_id}/kyc-status
Authorization: Bearer <access_token>
```

Example response:

```json
{
  "wallet_id": "11111111-1111-4111-8111-111111111111",
  "verified": true,
  "provider": "toroforge"
}
```

6. Initialize wallet funding:

```http
POST /toroforge/wallets/{wallet_id}/funding
Authorization: Bearer <access_token>
Content-Type: application/json
Idempotency-Key: funding-001

{
  "amount": "100.00",
  "currency": "USD",
  "payment_type": "card",
  "success_url": "https://example.com/funding/success",
  "cancel_url": "https://example.com/funding/cancel",
  "token": "USD",
  "payer_name": "FumiSync Reviewer",
  "payer_address": "123 Demo Street",
  "payer_city": "New York",
  "payer_state": "NY",
  "payer_country": "US",
  "payer_zipcode": "10001",
  "payer_phone": "+15555550100",
  "description": "Reviewer wallet top-up"
}
```

Example response:

```json
{
  "payment_transaction_id": "55555555-5555-4555-8555-555555555555",
  "ledger_entry_id": "66666666-6666-4666-8666-666666666666",
  "status": "pending",
  "external_payment_id": "TORO_TX_123",
  "provider_response": {
    "result": true,
    "TX_ID": "TORO_TX_123"
  },
  "amount_minor": 10000
}
```

7. Verify the deposit after ToroForge confirms the transaction:

```http
POST /toroforge/wallets/{wallet_id}/funding/{payment_transaction_id}/verify-deposit
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "txid": "TORO_TX_123"
}
```

Example response:

```json
{
  "payment_transaction_id": "55555555-5555-4555-8555-555555555555",
  "ledger_entry_id": "66666666-6666-4666-8666-666666666666",
  "wallet_id": "11111111-1111-4111-8111-111111111111",
  "status": "succeeded",
  "currency": "USD",
  "txid": "TORO_TX_123",
  "new_cached_balance_minor": 10000,
  "provider_response": {
    "result": true,
    "data": []
  }
}
```

8. Transfer funds from a DSO treasury wallet to a DSO-managed clinic wallet:

```http
POST /toroforge/dsos/{dso_id}/clinics/{clinic_id}/wallet-transfer
Authorization: Bearer <access_token>
Content-Type: application/json
Idempotency-Key: transfer-001

{
  "amount": "25.00",
  "currency": "USD"
}
```

Example response:

```json
{
  "wallet_transfer_id": "77777777-7777-4777-8777-777777777777",
  "sender_ledger_entry_id": "88888888-8888-4888-8888-888888888888",
  "receiver_ledger_entry_id": "99999999-9999-4999-8999-999999999999",
  "from_wallet_id": "11111111-1111-4111-8111-111111111111",
  "to_wallet_id": "33333333-3333-4333-8333-333333333333",
  "status": "completed",
  "amount_minor": 2500,
  "currency": "USD",
  "external_transaction_id": "TORO_TRANSFER_123",
  "sender_new_cached_balance_minor": 7500,
  "receiver_new_cached_balance_minor": 2500,
  "provider_response": {
    "result": true
  },
  "reused": false
}
```

9. Inspect wallet center and ledger records:

```http
GET /dsos/{dso_id}/billing/wallet_center
GET /clinics/{clinic_id}/billing/wallet_center
```

Original short flow:

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

## Known Limitations

- Automated contract, unit, mocked HTTP and import/metadata/worker compatibility checks are included. They do not replace a real database/provider integration run or browser frontend testing. The manual reviewer flow above requires working service credentials.
- Premium service wallet debits for eligibility checks, patient messaging, appointment reminders, and AI-assisted calls are part of the billing model and roadmap, but the current submitted backend focuses on wallet creation, KYC, funding, ledger entries, wallet-center reads, and DSO-to-clinic transfers.
- Full review of wallet funding and transfers requires valid ToroForge/Toronet credentials and provider-side transactions. Without those credentials, reviewers can still inspect the code, run the API, view `/docs`, and validate local setup/migrations.
- The KYC start endpoint currently returns a two-item response containing the provider KYC URL and wallet password. A future cleanup can wrap this in a named response object.

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
python -m compileall app api auth billing core infra caches sdk workers main.py config.py
```

Run the automated checks from the project root, using the configured virtual environment:

```powershell
python -B -m unittest discover -s tests -v
```

The tests install dummy settings in their process and mock/guard service boundaries;
they do not execute migrations or real provider transactions. On macOS/Linux, use
`.venv/bin/python` explicitly if the environment is not activated.

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

## Phase 4 local changes

The master learning guide is [FumiSync_Junior_Developer_Guide.docx](docs/FumiSync_Junior_Developer_Guide.docx), updated in place; Phase 4 is in sections 45–61. It includes code examples, engineering reasoning, recovery scenarios and the rollout sequence. See [local validation](docs/phase_4_validation.md) for evidence and limitations.

**Migration preparation is not deployment.** These workflows now require revision `f4a100000001`. The migration and `tools/backfill_private_data.py` have been prepared but have not been executed against a database. Review the guide's rollout steps and obtain approval for the actual target before running them. Preserve original encryption/HMAC keys.

`GET /health/ready` checks the expected schema plus PostgreSQL/Redis. Configure `CORS_ORIGINS` as explicit JSON origins. Existing API shapes remain checked, while validation, name-prefix searching and uncertain-operation behavior are intentionally stricter.

Deposit verification makes two independent ToroForge checks: the payment lookup by transaction ID (`getfiattransactions_txid`; `result` must be boolean `true` with an exact transaction ID, destination, currency and amount match), and the wallet's real Toronet balance (`getaddrbalance`), which must already include the deposit. The balance is compared under a row lock, so one arrival cannot credit two deposits. DSO-to-clinic transfers follow the same ToroForge contract: a reply with `result: true` and a `TX_ID` means the money moved, so balances post immediately. Both rules follow the provider behavior confirmed by the project owner; live integration remains untested.
