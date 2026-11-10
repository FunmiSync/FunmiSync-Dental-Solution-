# Phase 3: MVC organization with compatibility preserved

Status: approved; implementation and compatibility verification completed in Phase 3.

Purpose: make the backend easier to navigate and maintain, without combining the file moves with database/data conversions or provider behavior changes.

Proposed packages:

- app/models: SQLAlchemy entities and enums, initially keeping one canonical model module to avoid circular imports and duplicate table registration.
- app/controllers: existing FastAPI routers grouped by authentication, registration, workspaces, clinics, sync logs and billing.
- app/schemas: existing Pydantic input/output schemas, serving the API presentation boundary.
- app/services: appointment, patient, sync-log, workspace/team and billing workflows.
- app/infrastructure: database sessions, Redis/cache implementations, provider clients and worker entrypoints.
- app/security: credential encryption, JWT, CSRF and access-control helpers.
- main.py: stable app entrypoint so uvicorn main:app remains valid.

Migration approach: move one layer at a time, update canonical imports, and keep lightweight compatibility modules at old import locations. Existing tests and queued RQ jobs can still resolve historical callable paths. Alembic must load the same Base/metadata and model classes. Keep class/function identities stable through re-exports where practical.

Frontend target: preserve all 37 existing method/path combinations, operation IDs, response/request schemas, authentication cookies, CORS settings and required headers. Compare against both original source and runtime OpenAPI snapshots after each move. Only Phase 2's documented DSO limit change is currently allowed.

Validation: preserve the 40 checks present at the start of Phase 3 (38 Phase 2 checks plus two later documentation-rendering checks), and add import identity/metadata/worker compatibility checks. Continue using dummy settings and guarded service boundaries; no live database, Redis or provider calls. Update the same master Word guide with package maps and a request-flow walkthrough. Final results are recorded in phase_3_validation.md.

Excluded: migration execution, live backfills, provider transactions, deployment and remaining Phase 4 behavioral fixes. The backend has unresolved reviewed defects; a clean MVC layout alone does not repair them. Those fixes will follow in a separate approved phase.
