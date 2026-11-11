# Phase 4: remaining correctness and data-safety repairs

Status: approved by the user with "do phase 4"; local implementation and 98-check validation completed. Live rollout is pending separate permission. See the master guide sections 45-60 and phase_4_validation.md. Work will use canonical `app/` paths and continue updating the same master DOCX.

## First batch: billing correctness

- Validate finite, bounded monetary amounts and supported wallet currency before contacting a provider.
- Bind funding/transfer idempotency to ownership and original request contents; reject mismatched reuse without exposing another scope's operation.
- Persist transfer intent before remote effects and represent uncertain outcomes separately from confirmed failure. Reconcile before permitting another remote write; do not assume an HTTP timeout means no money moved.
- Confirm provider settlement/transaction fields from official documentation before tightening deposit matching. Mock those contracts and prohibit live transactions during automated checks.
- Prepare a reviewed replacement for the DSO wallet uniqueness constraint so multiple clinics can hold wallets without allowing duplicate treasury wallets.
- Address wallet provisioning/password-rotation recovery and document what cannot be made atomic with the provider API.

## Following batches within the broader repair plan

- Patient ambiguity and appointment concurrency: prevent arbitrary patient selection and establish a tested reservation/reconciliation strategy. Avoid claiming that retry flags alone guarantee exactly-once remote effects.
- Schema/data transitions: prepare safe, resumable backfills for existing encrypted/plaintext records, required columns and memberships. Establish actual database state before any conversion. Remove ORM delete cascades toward parent clinic/patient entities.
- Operations and security: finish audit logging, stream permission revalidation, readiness behavior and input validation. Review configured frontend origins before changing CORS. Extract controller workflows into services where transaction boundaries and tests justify it.

## Verification and permission boundaries

Add regression tests before each fix; preserve API paths/field names and review any intentional validation/status changes. Replace migration-only AST fingerprints selectively for approved behavior edits while keeping API and focused regression checks. Database declaration snapshots change only with the corresponding reviewed schema changes.

Approval to begin this phase authorizes local code/test/documentation changes and migration-script preparation. It does not authorize migration execution, production data conversion, provider payment/appointment calls or deployment. Those actions require separate approval after a concrete plan and scripts are ready to review.

Live PostgreSQL/Redis tests need an explicitly identified disposable test environment. Do not infer that a configured URL points to a safe test database. Provider write tests remain mocked unless separately authorized.
