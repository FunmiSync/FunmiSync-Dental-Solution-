# Phase 2 completion record

Completed: 17 October 2026. Scope approved by the user: core appointment, authentication, pagination and cache repairs. Creating .venv and installing the existing requirements was separately approved.

## Results

- 38 unittest checks pass: `.venv/bin/python -B -m unittest discover -s tests -v`.
- `.venv/bin/python -m pip check` reports no broken requirements.
- Original and edited runtime OpenAPI documents differ only in the DSO sync-log page limit: default 50, minimum 1, maximum 200. The original runtime document is retained as docs/api_openapi_baseline.json.
- Existing method/path pairs, schema shapes, declared response status codes, cookie declarations and CORS settings are retained by the compatibility checks. Behavior fixes intentionally reject disabled clinics, handle malformed tokens, correct labels/messages and apply valid pagination bounds.
- requirements.txt and Alembic files are byte-identical to the pre-edit source backup. No database migration or data conversion was performed.
- Mocked/in-process API and unit tests are guarded against real database, Redis and outbound async HTTP access. No live provider transactions were performed.

## Changed behavior

- OpenDental credentials are decrypted and lookup sessions close; clinic-specific circuit breakers isolate failures. Reads retry transient failures; SDK writes are sent once. Appointment updates use the documented resource URL and patient searches use encoded parameters.
- Appointment lookups await once; health commits execute; patterns use five-minute units; normal/legacy timestamp whitespace is handled; invalid durations and problematic DST times are rejected.
- Updated appointments exclude themselves from conflict checks. Unchanged appointments resume unfinished popup/commlog work and persist the completion flags.
- Clinic pagination uses the correct OR cursor condition. DSO limits are bounded and default correctly. Operation labels understand both historical and current verb forms.
- Sync-log invalidation covers all pages/date ranges in affected scopes. Optional cache errors are logged as misses or best-effort writes; billing commits invalidate affected clinic/DSO views.
- Refresh rejects inactive users and locks/reloads the row before JTI rotation. Malformed bearer headers are handled safely; forwarding headers cannot directly select rate-limit identity; failed-login counters expire from the first failure.
- Disabled clinics cannot enqueue new syncs; queued jobs stop without provider work, automatic retry or billing. Webhook acceptance says queued successfully.
- ToroForge transient read HTTP errors are correctly recognized for retries. Wallet creation's integrity-error recovery rolls back before querying.

## Limits and unfinished work

No browser frontend has been run. No real PostgreSQL concurrency, Redis server, OpenDental or ToroForge integration test has run. Compatibility results concern declarations/generated OpenAPI plus selected mocked HTTP flows; untyped response bodies and all provider behaviors are not comprehensively validated.

Remote-write recovery, concurrent bookings/patient creation, billing settlement verification/idempotency, money edge cases, model constraints, data/privacy migrations, audit logging, long-lived stream revocation and readiness checks remain outstanding. Existing startup/time deprecation warnings remain. A Redis outage may leave cached data stale until TTL expiration. The source backup is not a database backup.

## Artifacts

- docs/backups/pre_phase_2_source.zip: local pre-edit source archive.
- docs/FumiSync_Junior_Developer_Guide.docx: updated editable teaching guide.
- docs/junior_developer_guide.md: editable source of the teaching guide.
- docs/phase_3_proposal.md: proposed MVC migration, awaiting approval.

Regenerate the Word file with `python3 -B tools/build_learning_docx.py`. Document structure/XML are checked; rendering in a desktop Word application has not been visually inspected.
