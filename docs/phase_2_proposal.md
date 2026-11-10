# Phase 2: core behavior repairs, before moving packages

Status: approved and implemented; validation results are recorded in the learning guide.

Purpose: repair focused defects while existing import paths remain stable. Mixing every fix with a large package move would make regressions harder to diagnose.

Approved scope:

- OpenDental: decrypt stored credentials at request construction, close its lookup session, translate HTTP failures into domain errors, and avoid retrying permanent authentication failures or uncertain non-idempotent writes.
- Appointment workflow: remove the double await, align duration handling, accept normal provider datetime strings, validate appointment time order, exclude an updated appointment from its own conflict check, and resume unfinished communication-log/popup work.
- Pagination and caching: repair clinic cursor grouping, bound/default DSO page limits, align cache keys and invalidation, and invalidate billing views after successful writes.
- Authentication: safely parse malformed bearer headers, reject refresh for inactive accounts, and give login-failure counters a defined lifetime from their first failure. Refresh concurrency and proxy trust require explicit behavior tests and deployment assumptions.
- Disabled clinics: stop new ingestion and queued syncing consistently; document the rejection behavior for frontend display.
- Logging: align operation labels, call the missing health commit, and avoid misleading success messages.

Public compatibility target: preserve method/path combinations, JSON field names, successful response shapes, cookie names/paths, and existing required header names. Deliberate changes include correct client errors for invalid inputs, bounded pagination defaults, and enforcement of disabled-clinic state. Review and document these changes before adjusting any contract snapshot.

Verification plan: add focused regression tests that reproduce each repaired failure, then run the compatibility suite. Runtime API tests need application dependencies; permission to install these will be requested separately. Offline tests cannot prove frontend compatibility.

Excluded from this phase: execution of migrations; data conversion; external OpenDental/ToroForge calls; wallet transfers; deployment. Billing recovery, privacy changes and model constraints need their own reviewed implementation and migration plan. MVC moves follow core repairs, with compatibility imports and queued-job callable paths considered.

Documentation: update the Markdown and DOCX teaching guide with before/after examples, testing evidence and limitations for each completed repair.
