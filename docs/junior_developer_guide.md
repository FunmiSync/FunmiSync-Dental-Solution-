# FumiSync Backend: Learning Guide and Change Journal

Phase 4 edition | 17 October 2026 | Status: local repairs and migration/backfill preparation implemented; 98 passing checks. Live rollout, data conversion and provider verification await separate permission.

Master document: docs/FumiSync_Junior_Developer_Guide.docx. All completed phases and future teaching updates belong in this same Word file. This Markdown file is its editable source; regenerate the master after each update. Do not create separate phase-specific Word copies.

Learning format for every future change: show the old code and the new code, explain the symptom and cause, explain why this decision fits this system, describe its tradeoffs, show how to verify it, and give you a way to reason through it independently. Start with sections 22-34 for worked code examples and the independent debugging playbook. Code examples preserve indentation in the Word document. No engineering choice is universally best; judge it against the requirements, failure modes and evidence.

LATEST UPDATE (18 October 2026): Section 62 corrects deposit verification following your clarification of provider behavior. Deposits no longer require an extra settlement field; treasury transfers retain that requirement. Earlier Phase 4 descriptions of a deposit status gate record the initial implementation and are superseded by section 62. Phase 4 is covered in sections 45-60, including actual code, decisions, tests, recovery and the rollout plan. Sections 1-44 retain the history and outcomes of earlier phases; their old test counts and pending-work statements refer to those phases. Phase 3 is covered in sections 35-44. The application implementation now lives under app/. Historical file paths in the Phase 1/2 lessons remain usable as compatibility imports, but edit the new canonical modules. In particular, auth/login.py now points to app/controllers/auth/login.py. The old lesson examples describe the code as it stood when that phase was completed; this new chapter explains the relocation and its compatibility tests.

## Historical Phase 2 introduction (read sections 45-60 for the latest work)

This edition includes the completed Phase 2 code changes, not just the original plan. Read sections 12 through 20 for the implementation lessons. This front section was added to make that new material easier to find. The final checked test count is 38. The MVC move has not started.

The changes you can inspect now are: decrypted OpenDental credentials and closed lookup sessions; one await per provider call; five-minute appointment patterns; corrected update/conflict handling; resumed popup/commlog work; fixed cursor pagination; fresh billing/sync caches; safer bearer and refresh handling; expiring login counters; disabled-clinic enforcement; and corrected transient read retries. Each is explained later with its file paths, reason, examples and limits.

If you are currently reading auth/login.py, begin with the walkthrough immediately below. The controller's response fields were preserved. Most authentication repairs live in the helpers it calls, so the controller itself has little visible change. A small diff in login.py does not mean the login workflow was left unexamined.

## Walkthrough: understanding auth/login.py as a junior developer

Step 1: the APIRouter prefix is /login. The empty path on the first POST decorator means POST /login. loginrequest describes the JSON the frontend sends: email and password. loginresponse describes the returned access_token and csrf_token fields. Changing those field names would require coordinating with the frontend.

Step 2: FastAPI injects a database session through Depends(get_db). Injection means the framework supplies a resource instead of the controller constructing it directly. During tests, dependency_overrides supplies a fake session. That lets us exercise HTTP handling without connecting to PostgreSQL.

Step 3: the controller normalizes the email and calls get_client_ip(request), login_attempts(email, ip) and get_redis_attempts(key). These helpers build and read a login-failure counter. Before Phase 2, the counter expired only once the fifth failure was reached. After the fix in auth/login_helper.py, the first failure starts its 30-minute window. A wrong password today no longer leaves an ordinary low-count record forever. Increment and expiry are still separate commands; that limitation is recorded later.

Step 4: the controller queries Users by email and calls verify_password against the stored password hash. A hash is a one-way password verifier; it is different from the reversible encryption used for provider API credentials. The controller checks user.is_active before allowing a normal login. Failed credentials go through handle_failed_login, which raises the appropriate HTTP error and increments the counter.

Step 5: on success, start_login_session in auth/session_helper.py rotates refresh_jti, commits it, creates JWTs and sets cookies. The access token is returned in JSON. The refresh token is an HttpOnly cookie scoped to /login/refresh. HttpOnly prevents JavaScript from reading that cookie; Secure restricts browser transmission to HTTPS. The readable CSRF token is returned so the frontend can send its matching X-CSRF-Token header.

Step 6: POST /login/refresh first checks that the CSRF cookie and header match. It then calls validate_refresh_token in auth/oauth2.py. Before Phase 2, that validator did not reject an inactive user. After the fix, it checks account state as well as token type, token_version and refresh_jti. It also requests a database row lock and reloads current ORM values before validating the JTI. The controller changes the JTI and commits before issuing the next tokens. A real PostgreSQL race test is still pending; a mock test alone does not prove concurrent safety.

Step 7: the rate-limit middleware runs before the controller. Before Phase 2, Authorization: Bearer could trigger an indexing error because there was no second component. After the fix, a header is accepted for parsing only when it has exactly two whitespace-separated components and its scheme equals bearer. A malformed header no longer crashes a public endpoint. Protected endpoints still require valid authentication.

Before/after example: a deactivated user with an old refresh cookie previously reached refresh validation without an active-account check. The repaired validator returns HTTP 401 with Account is deactivated. Successful refresh responses still use access_token and csrf_token, and tests check the three cookie declarations. This is a behavior repair while keeping the frontend's successful response contract stable.

How to trace it yourself: open auth/login.py, follow start_login_session into auth/session_helper.py, then follow validate_refresh_token into auth/oauth2.py and increment_attempts_with_key into auth/login_helper.py. Finally read AuthTests and ApiBehaviorTests in tests/test_phase_2_runtime.py. Compare what the tests assert with what they replace using mocks.

Practice question: explain why the frontend should never receive the stored password hash, why a refresh token differs from an access token, and why a database rollback cannot invalidate a token already sent unless validation also checks persisted state. These are separate responsibilities, even though they appear together in the login workflow.

## 1. What you asked for

You asked us to fix the reviewed defects, reorganize the backend using MVC, preserve frontend compatibility, ask permission before significant steps, and explain the work in a Word document. This guide records completed work separately from future work. It will grow with each approved implementation phase.

## 2. How the application works today

The frontend sends HTTP requests to FastAPI. main.py registers routers from api/ and auth/. Those handlers check authentication and permissions, use SQLAlchemy sessions, and call domain services in infra/ or billing/toroforge/. Some services call OpenDental or ToroForge; Redis stores caches and RQ queues background jobs. PostgreSQL stores the durable records.

Example: a CRM webhook reaches api/routers/webhook/webhook_crm.py. The handler verifies a shared secret, stores an inbound event and sync log, then queues a worker. The worker resolves a patient, books an appointment in OpenDental, and updates the log. Redis publishes a notification so the frontend can reload its data.

A database commit saves a transaction permanently. A rollback cancels its uncommitted database changes. A rollback cannot undo a payment or appointment already created in another company's API. That boundary explains several of the most important defects found in the review.

## 3. MVC explained for a beginner

Model means the application's data and rules. SQLAlchemy entities describe database records, while services implement operations such as booking or transferring funds. Controller means the code that receives a request, validates access, calls a service, and chooses a response. View means the presentation returned to the caller. For this JSON API, response schemas and serializers fill that role; the separately hosted frontend remains the visual interface.

The proposed request flow is: frontend -> controller -> service -> model/repository or provider client -> response schema -> frontend.

MVC does not require business logic to live inside a large model class. Keeping provider calls and business workflows in services makes them easier to test. Repositories should be introduced where they simplify persistence boundaries, rather than as empty wrappers around every database call.

Proposed organization: app/models for database entities; app/controllers for HTTP handlers; app/schemas for input/output definitions; app/services for workflows; app/infrastructure for database, Redis, queues and provider clients; app/security for authentication and authorization. The exact package moves remain planned, not implemented in Phase 1.

## 4. Why the frontend can survive a backend reorganization

The frontend depends on the public API contract, not the Python directory names. A contract includes HTTP method and URL, request fields, response fields, status codes, authentication tokens, cookies, headers, and streaming behavior. Moving a controller into another package can preserve all those things.

Example: POST /login must still accept its existing email/password JSON and return access_token and csrf_token. The refresh_token cookie must retain the expected path and security settings unless we explicitly review a change. A function rename inside Python is usually harmless, although generated OpenAPI operation IDs may change and must be checked later if the frontend uses a generated client.

Fixes can intentionally change broken behavior: a second page should return results, and an invalid amount should return a client validation error rather than a server crash. Such changes must be documented and tested. This is why zero disruption cannot be promised from source inspection alone.

## 5. Completed work: API inventory

Added tools/api_contract.py. It reads Python source with the standard-library ast module, follows the routers mounted by main.py, and records route paths, methods, response declarations, exposed parameter declarations, schemas, authentication dependencies, cookie settings and CORS configuration. It does not import the running application, contact a service, or read real credentials.

AST means abstract syntax tree: Python's structured representation of code. Instead of searching for text that merely looks like a route, we inspect function decorators and their arguments. This makes the inventory less sensitive to formatting and comments.

Added docs/api_contract_baseline.json as a snapshot of the existing source-level API. A baseline is a reference point: later checks compare the current source to this file. It records existing behavior declarations, including known defects; it is not a certificate that the API is correct.

The inventory deliberately omits handler names and most internal dependency names so an internal rename need not look like a frontend change. It records exact annotation expressions, so some harmless internal schema renames may still need review. Future changes to router wiring may require extending the inspector. A runtime OpenAPI comparison will supplement it once dependencies are installed with permission.

## 6. Completed work: compatibility checks

Added tests/test_api_contract.py using unittest, which is included with Python. The checks compare the current inventory with the baseline, reject duplicate method/path combinations, parse maintained Python source, confirm that route/header/type changes are detected, and confirm that an internal handler rename is ignored.

Run from the project root: python3 -m unittest discover -s tests -v

These are guardrails, not full integration tests. They cannot prove the JSON actually returned by a running handler matches its declaration. In particular, untyped response bodies, KYC's tuple response, authentication flow behavior, permission semantics, provider effects and SSE message contents require focused behavior tests in later phases.

Do not overwrite the baseline simply to make a failing test pass. First decide whether a change is accidental or intentional, assess frontend callers, update tests, and only then approve a baseline change. Bug fixes can require deliberate updates to validation constraints while preserving field names and routes.

## 7. Completed work: this Word document

Added tools/build_learning_docx.py to generate docs/FumiSync_Junior_Developer_Guide.docx from this Markdown file. A DOCX is a ZIP archive containing XML parts. The generator writes the required document, style, relationship and content-type parts using Python's standard library. It escapes XML text, adds document headings and paragraph spacing, and checks the resulting archive and XML.

Why this approach: python-docx was not installed when documentation began, and installing dependencies required permission. This creates a real editable Word document without adding application dependencies. The Markdown source remains easy to edit; rerunning the generator updates the same Word file. The renderer supports headings, paragraphs, bullets and fenced code examples. Code examples now retain indentation, blank lines and comments in a shaded monospace style. This remains a small purpose-built renderer rather than a full Markdown implementation.

Regenerate it with: python3 tools/build_learning_docx.py

## 8. Important review findings and their lessons

Appointment workflow: encrypted credentials need decryption only at the provider boundary; a coroutine must be awaited once; duration encoding and decoding must agree; a booking update must exclude itself from availability checks; and concurrent bookings need a deliberate concurrency strategy. Lesson: valid Python syntax does not prove correct runtime behavior.

Billing workflow: scope idempotency keys to the operation and verify the original request; persist enough state to reconcile uncertain provider outcomes; verify settled deposits rather than only a balance; reject non-finite amounts; and keep wallet ownership constraints consistent with multiple clinics. Lesson: local transactions cannot make remote side effects atomic.

Security and privacy: validate refresh-account state, parse authorization headers safely, trust proxy headers only within a defined deployment setup, apply access checks consistently, and minimize sensitive data in caches and queues. Lesson: encryption in one database column does not protect every copy of that data.

Caching and UI: cache keys must match invalidation patterns; writes must invalidate affected wallet views; page cursors must use a correct timestamp/id ordering condition. Lesson: a frontend that refreshes can still show stale data if the backend cache is wrong.

Migrations and operations: populated databases need backfills before adding required columns; existing memberships should be preserved; sessions must close; health checks should distinguish liveness from readiness. Lesson: deploying working source code also requires a safe data transition.

The review findings are hypotheses supported to different degrees. Some have isolated reproductions; others require dependency-backed tests or provider documentation before implementation. In particular, provider endpoint shapes and settlement fields must be confirmed before changing their integration. No production provider calls have been made.

## 9. Planned implementation phases

Phase 2: approved and completed for core appointment, pagination, cache and authentication repairs in their current locations. Dependency installation was separately approved. The exact coverage and remaining limits are recorded below.

Phase 3: approved and completed. Code moved incrementally into the agreed MVC packages, with compatibility imports and RQ callable paths preserved. Public contracts were checked after each layer move. See sections 35-44 for the current layout and rationale.

Phase 4: repair billing transaction recovery, provider reconciliation, privacy boundaries and database constraints. Prepare migration scripts and document rollout/backfill steps before requesting permission to execute them. Determine existing database state before writing data conversions; do not assume all records are plaintext or all are encrypted.

Phase 5: execute the approved local/runtime tests, exercise frontend login, refresh, workspaces, team access, webhooks, pagination, streams and billing screens, and finish the guide with observed results and remaining limitations. External payment tests and deployment require permission.

## 10. Permission and change record

Phase 1 approval covered inspection, documentation and compatibility checks. Phase 1 did not alter application behavior. Phase 2 approval subsequently covered the core behavior repairs, and a separate approval permitted creating .venv and installing the existing requirements. requirements.txt itself remains unchanged. This download does not contain a Git repository, so no commit or branch was created.

A source backup was saved before Phase 2 edits at docs/backups/pre_phase_2_source.zip. It contains the pre-edit source/configuration/documentation files, not credentials, a virtual environment or database data. Keep this archive locally. To compare a file, inspect its archived version alongside the working copy. Do not overwrite current files without reviewing the differences. Before touching live data in a later phase, establish a database-specific backup and rollback plan.

Files added in Phase 1: tools/api_contract.py; tests/test_api_contract.py; docs/api_contract_baseline.json; docs/junior_developer_guide.md; tools/build_learning_docx.py; docs/FumiSync_Junior_Developer_Guide.docx; tests/test_learning_docx.py.

Verification completed on 17 October 2026: all 7 offline unittest checks passed. The captured API contains 37 mounted routes, 61 schema classes and 3 authentication/CSRF cookie declarations. DOCX structure and XML are validated; visual rendering in Microsoft Word or LibreOffice has not been inspected. Runtime API and frontend tests remain pending.

## 11. Small exercises

Exercise 1: open main.py and locate the login router registration. Then find the controller that handles POST /login. Explain which part is controller code and which part belongs to authentication services.

Exercise 2: open docs/api_contract_baseline.json and find the loginresponse schema. Explain why renaming access_token could break the frontend even if the backend still starts.

Exercise 3: explain why rolling back a PostgreSQL transaction cannot reverse a completed ToroForge transfer. Sketch the states pending, uncertain, confirmed and failed, and describe what evidence allows each transition.

Exercise 4: explain what the offline checks prove and name three things they cannot prove. This distinction is essential when reporting that a system is tested.

## 12. Phase 2, Step 1: prepare a reversible working environment

Permission received: fix appointment, authentication, pagination and caching behavior. A separate question requested permission to create .venv and install existing requirements. The sandbox could not resolve PyPI, so installation required network escalation. No application packages were added to requirements.txt. The local Python interpreter selected Python 3.14; production's Python version still needs its own compatibility run.

A virtual environment is a project-specific package directory. Installing into .venv avoids changing system Python. Activate it or run its executable directly. The test command for this phase is: .venv/bin/python -B -m unittest discover -s tests -v. The -B option avoids creating bytecode cache files during these checks.

## 13. Phase 2, Step 2: fix the OpenDental boundary

Files: sdk/opendental_sdk.py, core/utils.py and infra/appointment_service.py. The SDK now loads encrypted credentials, decrypts them into memory, then closes its short-lived database session in a finally block. Finally runs whether the lookup succeeds or fails. Plain credentials are not logged.

Previously, the SDK could send Fernet ciphertext as an authentication header and keep a database session open. Now regression tests assert the plaintext header values and verify session cleanup even when ciphertext is invalid. Each clinic gets its own process-local circuit breaker, rather than sharing failure state across all clinics. A bounded cache holds these breakers; other worker processes still have their own independent breakers.

Read operations retry temporary connection problems and HTTP 408/429/5xx responses. Permanent client failures return immediately. Exhausted retries preserve the original error instead of raising a generic ValueError. Authentication, rejected-data and availability failures are translated into separate domain exceptions, which the worker can classify. An already-open circuit breaker is allowed through the service wrapper so the worker retains its intended handling.

Writes are sent once by the SDK because a timeout can happen after a remote write completed. This does not yet guarantee exactly-once remote creation: an RQ job retry can still replay a workflow. Durable reconciliation of uncertain remote writes remains work for a later phase; there is no claim that appointment concurrency or duplicate patient creation is fully solved here.

The appointment update URL was corrected to /appointments/{AptNum}; patient search now uses encoded query parameters. Provider URL/header deployment settings still need a real-environment review. The five-minute Pattern units, timestamp format, nine-hour maximum and appointment update resource were verified against the official OpenDental appointments documentation: https://www.opendental.com/site/apiappointments.html. No provider request with clinic credentials was made.

## 14. Phase 2, Step 3: repair appointment time and async handling

A coroutine is work that has not yet finished; await waits for its result. Before the fix, appointment lookup awaited the provider call to get a list, then tried to await that list again. The provider coroutine is now passed directly to the common wrapper, which awaits it exactly once. The wrapper also calls db.commit() after successful clinic-health updates; previously it only referenced the commit method.

Time Pattern conversion now uses one character per five minutes. A 09:00 to 09:30 request produces XXXXXX; decoding it produces 09:30. Dates and times are joined with a space and interpreted in the clinic's timezone. Zero/negative durations, partial five-minute units and durations over nine hours are rejected. Ambiguous or nonexistent daylight-saving times, and appointments spanning an offset change, are rejected instead of guessing a timezone offset. Offset-bearing inputs are rejected because this workflow expects clinic-local times.

Generated timestamps no longer contain a trailing space. Reading provider timestamps strips surrounding whitespace, so old stored values remain comparable. Availability checks accept an excluded appointment number: updating appointment 12 ignores appointment 12 but still rejects an overlapping appointment 13. Adjacent appointments remain allowed.

This validates time handling inside the worker; validating bad webhook inputs before returning 202 remains a later schema/controller task. Same-day durations are supported; an end time earlier than the start is rejected rather than assumed to mean the next day.

## 15. Phase 2, Step 4: finish partial appointment work

Files: infra/appointment_service.py and infra/sync_log_service.py. Booking compares the calendar identifier as well as time and status. On an unchanged appointment, it still attempts unfinished communication-log and popup work. Each successful side effect gets its own persisted done flag. Popup creation now sets popups_done, which previously remained false.

Example: appointment creation succeeds, communication logging succeeds, then popup creation fails. The retry sees the existing appointment, skips the completed communication log and retries the popup. After a successful popup, subsequent unchanged retries do neither side effect. A provider success followed by a database commit failure is still an uncertain-result boundary; the flags alone do not solve that remote transaction problem.

Booking results using created/updated now receive the same specific UI labels as create/update. The API field names remain unchanged; the human-readable label becomes more accurate.

## 16. Phase 2, Step 5: fix pagination and review the one API exception

Files: infra/sync_log_service.py, api/routers/synclog/sync_log_dso.py and tests/test_api_contract.py. Pagination sorts by timestamp and ID. The next page condition must be: timestamp earlier than cursor time OR (same timestamp AND ID earlier than cursor ID). Previously the clinic query used AND between those branches, which could never match.

DSO pages now use limit=50 by default and reject values outside 1 through 200. The original None default caused limit + 1 to fail. This is an intentional validation/default change. Existing clients supplying 1-200 keep their behavior; requests above 200 now receive HTTP 422.

The original JSON contract baseline was preserved. The compatibility test explicitly allows only this reviewed limit-default/bounds change, then compares every other captured declaration. This avoids silently accepting unrelated API drift. Backend package and function names remain in their existing locations in this phase.

## 17. Phase 2, Step 6: make optional caches safe and current

Files: caches/resilience.py, all cache modules and the wallet/funding/transfer service commit paths. A small optional_cache decorator catches Redis connection/server errors and malformed cached JSON or Unicode. Read failures behave like misses; failed cache writes/deletes are logged without converting a successful business commit into an error. Programming errors, such as an unserializable application object, are not silently swallowed.

Sync-log key generation and deletion patterns now agree. Invalidation covers all pages and historical date ranges for the affected clinic/DSO, including pre-fix key prefixes. This favors correctness over narrower invalidation and may increase cache misses for large DSOs. Keys for unrelated scopes are retained.

Successful wallet provisioning, funding updates and transfers invalidate clinic and DSO billing views. Independent deletions let the DSO invalidation run even if a clinic deletion fails. A Redis outage can still leave a previously cached response stale until its existing TTL expires; fail-open caching trades freshness for availability during that outage. Durable invalidation delivery is not implemented yet.

Only optional caches fail open. Webhook claiming and background queue submission remain required infrastructure and are not hidden behind this decorator. Monetary transfer atomicity and provider reconciliation are unchanged by these cache repairs. Wallet-creation duplicate-request recovery also now rolls back a failed database transaction before trying to query the winning request.

The ToroForge read-retry predicate now recognizes transient ToroForgeHTTPError instances instead of checking authentication errors for transient status codes. Regression tests check HTTP 408/429/5xx classification and verify that authentication errors are not retried. This adjusts read resilience only; no payment write was executed in testing.

## 18. Phase 2, Step 7: protect authentication boundaries

Files: core/middleware.py, auth/oauth2.py and auth/login_helper.py. Bearer headers are split into exactly two components and the scheme must equal bearer. A bare Bearer or a BearerFake scheme no longer crashes parsing or gets treated as a valid bearer token. Rate-limit identities only accept access-token payloads. The rate-limit warning uses proper logging arguments so reaching the limit does not produce a formatting error.

Refresh validation now rejects inactive accounts and takes a PostgreSQL row lock before checking/rotating the refresh JTI. populate_existing refreshes ORM values that might already be loaded. The lock lasts until the existing handler commits the new JTI. A second refresh should then see the changed JTI. Local mock tests check the path; a real PostgreSQL concurrency test is still required before claiming proven concurrent replay protection.

The first failed login now starts the counter's expiration window. Later failures do not keep extending it. The increment and expiration are still separate Redis commands, so a process failure between them remains a potential orphan-counter edge case. An atomic script can be evaluated in a later infrastructure pass.

Rate limiting and login throttling now use request.client.host rather than trusting caller-supplied forwarding headers. Behind a reverse proxy, configure the ASGI server to trust only actual proxy addresses using Uvicorn's forwarded-allow-ips setting. Never trust all client addresses on an exposed listener. This prevents a caller from choosing a new identity simply by changing X-Forwarded-For. Deployment configuration itself has not been changed.

## 19. Phase 2, Step 8: honor disabled clinics

Files: api/routers/webhook/webhook_crm.py, workers/workers.py and infra/appointment_sync_log_helper.py. After authenticating the webhook secret, ingestion rejects disabled clinics with HTTP 403 before enqueueing anything. The booking service and SDK also refuse disabled clinics. A queued job checks the flag before resolving patients or calling OpenDental.

An already queued job for a disabled clinic is marked failed in domain logs, explicitly non-billable, and returns a skipped result without asking RQ to retry. The failure helper gained an optional billable override; existing callers keep their prior behavior. The incoming webhook's 202 message now says queued successfully rather than implying that the eventual appointment sync already finished. Its response keys are unchanged.

A worker already inside an external call cannot be cancelled by this flag check. Strong cancellation across in-flight jobs would require a separate coordination design. The existing policy that other customer-configuration failures can count toward usage has not been changed.

## 20. Phase 2, Step 9: testing without real services

Files: tests/runtime_support.py and tests/test_phase_2_runtime.py. Tests install dummy settings in the test process and replace DB/Redis/provider boundaries with mocks. Real database connections, Redis commands and outbound async HTTP sends are guarded to raise an assertion if a test accidentally reaches them. No production credentials are loaded into these fixtures.

A unit test calls one piece directly, such as the time conversion helper. A regression test proves that a formerly failing scenario now works. An API test uses FastAPI's in-process TestClient to send HTTP requests without opening a real server. A SQL compilation test builds the actual SQLAlchemy query and inspects its OR condition without executing SQL against PostgreSQL.

The tests exercise decrypted credential headers and session cleanup, read/write retry behavior, time conversion, update conflicts, side-effect retry flags, malformed tokens, proxy spoofing, refresh status/cookies, billing invalidation, cache outages, disabled webhooks and DSO pagination. They do not replace real database transactions, provider contract tests, Redis integration tests or a browser frontend walkthrough.

Final Phase 2 results: all 38 unittest checks passed under the project's .venv. pip check reported no broken requirements. A fresh OpenAPI document was generated for the original source backup in a temporary directory and compared with the edited application. Exactly three differences were found, all on the DSO sync-log limit schema: default 50, minimum 1, maximum 200. The original runtime contract is saved in docs/api_openapi_baseline.json, and a regression test compares future generated contracts against it with that single reviewed exception.

requirements.txt and every existing Alembic file were checked byte-for-byte against the backup and are unchanged. No migration was run. The application emits pre-existing deprecation warnings about startup events and datetime.utcnow(); they remain for later cleanup. The DOCX archive and XML are validated; visual layout in Microsoft Word/LibreOffice has not been inspected. See docs/phase_2_validation.md for a concise completion record.

## 21. Remaining work after Phase 2

At the end of Phase 2, MVC package migration and the remaining correctness work were outstanding. Phase 3 has now completed the package migration. Cross-process booking/patient concurrency, durable uncertain-payment recovery, deposit settlement verification, money edge-case validation, schema/backfill repairs, sensitive-data retention and encryption migrations, audit logging, full stream revocation, readiness health checks and actual frontend testing remain outstanding. No migration was executed, no remote payment or appointment was created, and nothing was deployed.

Ask for approval before the next phase. Continue expanding this guide with actual file changes and observed verification results; do not describe planned fixes as completed.

## 22. Worked example: debug a double await by tracing types

File: infra/appointment_service.py, get_operatory_appointments_cached. These are shortened excerpts of the actual before/after flow; surrounding cache calls and logging are omitted. The before code belongs to the source backup, not the current implementation.

Before:

```python
appointments = await self._run_od_call(
    await self.od.get_appointments_in_operatory(
        operatory, date_start, date_end,
    ),
    operation="get_operatory_appointments",
)
```

After:

```python
appointments = await self._run_od_call(
    self.od.get_appointments_in_operatory(
        operatory, date_start, date_end,
    ),
    operation="get_operatory_appointments",
)
```

The wrapper contains this line:

```python
result = await od_call
```

What to learn: calling an async function produces a coroutine; awaiting that coroutine produces its result, here a list. The inner await in the old code turns the coroutine into a list before the wrapper receives it. The wrapper then attempts to await a list, which raises TypeError. The repair removes one keyword because the wrapper owns waiting and error classification.

Why this decision: removing await inside the wrapper instead would bypass its intended responsibility for awaiting provider work inside its try/except. Passing the unfinished operation preserves centralized error handling. This is a good fit for the existing service design, not a rule that every wrapper should receive a coroutine.

How to solve it yourself: write the type above each expression on paper. First: coroutine. Next after await: list. Then ask what the receiving function expects. Reproduce a cache miss, because a warm cache bypasses the broken path. Read test_cache_miss_awaits_provider_once_and_commits_health; it asserts that the provider was awaited once and the result was cached. Do not test only cache hits.

## 23. Worked example: use units and round trips to catch time bugs

File: core/utils.py. Before, simplified from the original:

```python
diff = int((end_dt - start_dt).total_seconds() / 60)
pattern = "X" * diff

# Elsewhere, decoding treated each character as five minutes:
duration_minutes = len(pattern) * 5
```

After, excerpt from the current implementation:

```python
seconds = (end_dt - start_dt).total_seconds()
if seconds <= 0 or seconds > 9 * 60 * 60 or seconds % 300:
    raise ValueError(
        "Appointment duration must be positive, "
        "in five-minute increments, and at most nine hours"
    )
pattern = "X" * int(seconds / 300)
```

What to learn: write units beside numbers. total_seconds returns seconds; one provider pattern unit is 300 seconds. For thirty minutes: 1,800 seconds / 300 seconds per character = 6 characters. Old code created thirty characters and the decoder interpreted them as 150 minutes.

Why this decision: rejecting unsupported durations prevents silently shortening or extending clinical bookings. Rounding a seven-minute request to five or ten minutes would be a product decision affecting scheduling, not a harmless implementation detail. The nine-hour limit and five-minute units came from the provider documentation, not memory. Daylight-saving edge cases are rejected because an offset-free provider timestamp cannot express every timezone ambiguity safely.

How to solve it yourself: search the official provider docs for Pattern; compare the encoder with the decoder; then assert decode(encode(start, end)) equals end. Test the smallest valid duration, zero, a negative duration, a non-multiple of five and the maximum. A system can have correct syntax and still be wrong because its units disagree.

Try this without an assistant: calculate the expected pattern for 45 minutes before running anything. Your answer should have nine characters. Then run test_duration_round_trip_and_plain_or_legacy_timestamps and inspect its assertion rather than merely reading OK.

## 24. Worked example: translate pagination into a truth table

File: infra/sync_log_service.py, build_clinic_items. Before, reformatted for readability:

```python
query = query.filter(
    or_(AppointmentSyncLog.started_at < cursor_started_at),
    and_(
        AppointmentSyncLog.started_at == cursor_started_at,
        AppointmentSyncLog.id < cursor_id,
    ),
)
```

After:

```python
query = query.filter(or_(
    AppointmentSyncLog.started_at < cursor_started_at,
    and_(
        AppointmentSyncLog.started_at == cursor_started_at,
        AppointmentSyncLog.id < cursor_id,
    ),
))
```

What to learn: multiple arguments to SQLAlchemy filter are combined with AND. The old query therefore demanded an earlier timestamp AND an equal timestamp. No row can satisfy both. The new query expresses two acceptable branches under one OR, matching the descending timestamp/ID sort order.

Reason through three rows: a row older than the cursor should appear; a row at the same timestamp with a smaller ID should appear; a newer row should not. The ID breaks ties so rows sharing a timestamp have a stable order. If you change the sort direction, reconsider both comparison operators.

Why this decision: cursor pagination fits a changing log stream and the existing timestamp/ID indexes. Offset pagination could be easier to write, but concurrent inserts can shift rows between pages. Cursor pagination still needs tests for equal timestamps and concurrent activity; compiling SQL proves grouping, not actual database execution or performance.

How to solve it yourself: express the desired row rule in plain English, turn it into boolean branches, then inspect the generated SQL. test_clinic_cursor_is_an_or_condition compiles the real query without a database. A later database test should insert tie-timestamp rows and verify no missing or duplicate rows across pages.

## 25. Worked example: parse an authorization header defensively

File: core/middleware.py. Before:

```python
if not auth_header or not auth_header.lower().startswith("bearer"):
    return None
token = auth_header.split(" ", 1)[1]
```

After:

```python
parts = (auth_header or "").split()
if len(parts) != 2 or parts[0].lower() != "bearer":
    return None
token = parts[1]
```

What to learn: external input is not guaranteed to have the structure you expect. Bearer alone passes the old prefix check but has no token component. BearerFake also starts with bearer but is a different scheme. The new code verifies structure before indexing and compares the complete scheme.

Why this decision: middleware should not crash a public route because a caller sent malformed credentials. Returning None here means no authenticated rate-limit identity; it does not grant access to protected endpoints. Those endpoints still perform token validation. The stream-token helper uses the same structural rule.

How to solve it yourself: test an absent header, an empty value, Bearer, Bearer followed by spaces, BearerFake token, a valid two-part form, and an extra third part. For each, decide whether the parser returns a token, rejects the input, or raises a server error. A good test checks those outcomes, not whether a particular split method appears in the source.

## 26. Worked example: distinguish an account from its token

File: auth/oauth2.py, validate_refresh_token. The earlier validator checked token fields but omitted the active-account check. Current excerpts:

```python
user = (
    db.query(Users)
    .filter(Users.id == user_uuid)
    .with_for_update()
    .populate_existing()
    .first()
)

# Other token checks occur between these excerpts.
if not user.is_active:
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Account is deactivated",
    )
```

What to learn: a valid signature proves who issued a token and that its contents were not modified. It does not prove the account still has permission today. Account state, token version and refresh JTI are persisted facts that can change after issuance.

Why the lock: two refresh requests can both read an old JTI before either rotates it. The row lock makes the second request wait for the first transaction to finish. populate_existing tells SQLAlchemy to reload values even if the object is already present in its session identity map. The controller commits the new JTI before issuing the next token pair.

Tradeoff: locking serializes refresh operations for a user and holds a database transaction open. It is suitable for this single-JTI design, but a system supporting many independently revocable devices would likely need a separate session table. A mocked test cannot prove PostgreSQL lock behavior; a real concurrent integration test remains required.

How to think independently: draw two columns, request A and request B. Interleave read, compare, write and commit. If both requests can validate the same old state, find a database mechanism that makes the check-and-update atomic. Then test the real database rather than assuming a mock reproduced isolation rules.

## 27. Worked example: make retries match the side effect

File: sdk/opendental_sdk.py. The old request path used retry_with_bak_off for every HTTP method. Current excerpt:

```python
response = (
    await retry_with_bak_off(send)
    if method.upper() == "GET"
    else await send()
)
```

What to learn: a timeout tells you that you did not receive a confirmed answer. It does not tell you that the provider did nothing. Retrying a read usually repeats observation; retrying patient creation may create another patient. The SDK now retries supported transient read failures but sends writes once per SDK invocation.

Why this decision: it removes immediate blind write replay without inventing provider idempotency support. The limitation matters: RQ may still retry the entire workflow, so exactly-once patient or appointment creation has not been achieved. A durable intent record, provider idempotency key or provider reconciliation flow would be the next engineering step, depending on provider capabilities.

How to solve it yourself: classify the operation as read, idempotent write or non-idempotent write. Ask what happens if the response is lost after success. Search the provider's official contract for an idempotency mechanism. If none exists, design how to discover the remote result before repeating a write. test_timed_out_write_is_not_replayed checks only the SDK boundary, and its name deliberately reflects that scope.

## 28. Worked example: close a resource on every path

File: sdk/opendental_sdk.py. Before, the instance kept an open session and copied encrypted values directly. Current flow, shortened:

```python
db = SessionLocal()
try:
    clinic = db.query(RegisteredClinics).filter_by(
        id=clinic_id,
    ).first()
    # Existence/disabled/configuration checks omitted here.
    self.developer_key = decode_secret(clinic.od_developer_key)
    self.customer_key = decode_secret(clinic.od_customer_key)
    self.clinic_num = clinic.clinic_number
finally:
    db.close()
```

What to learn: resource ownership must be explicit. This constructor owns the short-lived session and needs only scalar values from it. It closes the session whether lookup, validation or decryption fails. Provider credentials are decrypted at the outbound boundary because ciphertext cannot authenticate a request.

Why this decision: retaining a session for the SDK object's lifetime wastes pooled connections and obscures who must close it. A context manager would also be reasonable; try/finally fits the existing session factory. Neither approach removes the need to protect plaintext values in memory and logs. Do not print API keys to debug this path.

How to solve it yourself: identify every resource acquisition and write down its owner, lifetime and cleanup path. Inject a decryption failure in a test and assert close still happens. Successful-path tests alone do not detect resource leaks on exceptions.

## 29. Worked example: treat Redis caching as optional, not all Redis work

File: caches/resilience.py. Current excerpt:

```python
def optional_cache(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (RedisError, json.JSONDecodeError, UnicodeDecodeError):
            logger.warning(
                "Optional cache operation failed: %s",
                function.__name__, exc_info=True,
            )
            return None
    return wrapped
```

Before: cache helpers called Redis directly, so a Redis exception could turn a successful database operation into an HTTP failure. After: selected cache helpers use this decorator. Read failure becomes a cache miss; write/delete failure is logged. wraps preserves the original function's metadata for tooling and debugging.

Why this decision: a performance cache should not be the sole authority for whether money moved or an appointment exists. But stale balances during an outage are possible until cached entries expire. That is a tradeoff, not a promise of perfect consistency. The wrapper intentionally does not catch every Exception, which could hide programming mistakes.

Boundary to remember: queue submission, deduplication and authentication counters also use Redis, but they have different correctness roles. Do not put this decorator around everything containing the word Redis. Losing a disposable cache entry is different from silently losing a required job.

How to solve it yourself: ask whether the application can reconstruct the value from an authoritative source. If yes, a miss may be acceptable. If no, define the failure behavior explicitly. Test Redis failure and malformed JSON, and verify that unrelated exceptions still surface. Then monitor logs and latency when cache misses increase.

## 30. Worked example: recover only unfinished appointment work

File: infra/appointment_service.py. The old unchanged-booking path returned before attempting pending popup/commlog work. Popup success also did not persist popups_done. Current excerpts:

```python
if not change:
    await self.finish_side_effects(reserve, req)
    return AppointmentBookingResult(
        apt_num=int(existing_aptnum), action="unchanged",
    )
```

```python
if reserve and not reserve.popups_done and req.pop_up:
    await self.handle_popups(req)
    reserve.popups_done = True
    self.db.commit()
```

What to learn: a workflow has more than one state. Appointment exists does not imply popup exists. Record completion for independently retryable work and check those flags even when the main entity does not need updating.

Why this decision: it reuses existing persisted flags and repairs the retry path without adding a migration. The remaining gap is provider success followed by local commit failure: the next attempt may still duplicate the side effect. Completion flags are useful recovery state, not a distributed transaction guarantee.

How to solve it yourself: list the workflow's steps and imagine a crash after each one. For each retry, say which steps should repeat and which should not. Test a failed popup after a successful communication log, then a retry and another unchanged invocation. The successful popup should be attempted once in the locally confirmed case.

## 31. Worked example: protect the frontend while fixing a default

File: api/routers/synclog/sync_log_dso.py. Before and after:

```python
# Before: downstream code attempted None + 1.
limit: int = Query(default=None)

# After: the same policy as the clinic endpoint.
limit: int = Query(default=50, ge=1, le=200)
```

What to learn: an annotation saying int does not make a None default safe in downstream arithmetic. Define what omission means, and validate limits before querying. The change preserves the endpoint and response fields but intentionally changes validation: out-of-range limits receive HTTP 422.

Why this decision: 50/1-200 reuses an existing application policy, rather than introducing another arbitrary page size. It bounds database work and fixes the omitted-parameter failure. A frontend using larger limits must adapt; compatibility is not the absence of all behavior changes.

How to solve it yourself: record the old API contract first, change the smallest relevant declaration, and compare the generated OpenAPI document. The Phase 2 comparison found only default, minimum and maximum changes at this parameter. Do not regenerate all snapshots just because a test becomes red; review the precise difference and allow only the intended one.

## 32. A repeatable engineering method you can use without me

1. State a concrete symptom. Example: the second clinic log page is empty despite older rows. Avoid starting with a guessed rewrite such as we need MVC.

2. Trace the path from input to output. For this example: frontend cursor -> controller parameters -> service filter -> SQL -> rows -> response schema -> cache. Name the authoritative data and any external side effects.

3. Write the expected behavior as an invariant. Example: rows older than the cursor must be eligible, and rows already returned must not repeat. For appointment patterns: decoding the generated pattern must recover the intended end time.

4. Reproduce the failure with the smallest relevant case. Use two rows or one mocked provider operation before attempting a full live clinic flow. Test the failing path, such as a cold cache, rather than only the happy path.

5. Form a falsifiable hypothesis. Example: filter's two arguments are combined using AND. Predict the SQL before compiling it. If evidence contradicts the hypothesis, revise it; do not keep patching around the original guess.

6. Read authoritative evidence. Inspect the caller, callee, tests and official provider/library documentation. For time rules, write down units. For transactions, find commit boundaries. For authentication, distinguish signed token contents from current persisted permissions.

7. Choose the smallest fix that preserves the invariant. Compare alternatives using correctness, recoverability, frontend compatibility, operational cost and maintainability. A short patch is good only if it solves the actual cause and handles the relevant failure paths.

8. Make the old failure pass and check nearby boundaries. Include invalid input, retry, concurrency, resources and partial success where they matter. Prefer tests about observable behavior over tests that assert the exact implementation text.

9. Report exactly what you verified. A compiled SQL test is not a PostgreSQL execution test. A mocked HTTP call is not a provider integration test. A passing OpenAPI comparison is not a browser walkthrough. State the limits without understating useful evidence.

10. Review the diff and document the decision. Name the changed files, why the approach fits, alternatives rejected, remaining risks and rollback path. Then rerun relevant checks. Escalate scope only when new evidence requires it.

## 33. Practice independently: exercises with success criteria

Exercise A, 15 minutes: trace POST /login/refresh without changing code. Draw cookie/header input, CSRF validation, token validation, locked user lookup, JTI rotation, commit and response. Success: you can explain what would fail if the account were inactive and which module owns each decision.

Exercise B, 20 minutes: manually evaluate a cursor at timestamp T and ID 50 against rows (T-1, 90), (T, 49), (T, 51) and (T+1, 1). Success: only the first two qualify for descending next-page ordering. Explain why parentheses matter before inspecting SQL.

Exercise C, 20 minutes: write a temporary test where a provider succeeds but db.commit raises. Do this with mocks, never a real payment. Success: you can identify the uncertain outcome and explain why retrying automatically may duplicate the remote action. Do not claim that rollback reversed the provider call.

Exercise D, 20 minutes: introduce a Redis connection error into a cache test. Success: a cache read becomes a miss and a warning is recorded, while a programming error is not swallowed. Explain why required queue submission should behave differently.

Exercise E, 30 minutes: propose a safe fix for ambiguous patient matching, which is still outstanding. First write acceptance criteria for zero, one and multiple candidate matches. Success: your plan refuses to silently select an arbitrary person, identifies necessary provider fields and includes a regression test before changing production behavior.

Working rule: spend a focused interval tracing and testing before asking for help. When you do ask, bring the symptom, expected behavior, smallest reproduction, hypothesis, evidence and one alternative. That makes the discussion a review of your reasoning rather than someone else doing all the thinking.

## 34. Documentation improvements made for these examples

Only documentation tooling and document tests changed for this update. The DOCX generator now preserves fenced-code indentation, blank lines, comments and XML-sensitive characters such as < and >. An unterminated code fence fails generation instead of silently flattening an unfinished example. The document is still the same master file.

Why this decision: Python indentation changes meaning. A teaching document that flattens code can teach the wrong program, even when its prose is correct. A small monospace code style fits this guide without introducing a new package dependency. Dedicated renderer tests check indentation and escaping; desktop Word visual inspection remains unperformed.

Examples above are focused excerpts or explicitly labelled simplified snippets. Ellipses in prose mean surrounding checks were omitted for teaching, not that you should paste incomplete authentication code into production. Consult the named source file for the full implementation and the test module for executable verification.

## 35. Phase 3: what MVC means in this JSON backend

MVC separates data, request handling and presentation. Here the frontend is a separate application, so the backend returns JSON rather than HTML templates. SQLAlchemy entities live in app/models, HTTP controllers live in app/controllers, and Pydantic input/output schemas live in app/schemas. Services contain the existing application workflows; infrastructure contains database sessions, Redis, provider clients and worker adapters. Security helpers have their own package.

This is an MVC-oriented package organization with a service layer, not a claim that every controller is now minimal. Registration and invitation controllers still contain existing database/workflow orchestration. Separating that logic into smaller services can be done with dedicated behavior tests later. Changing those workflows during a structural migration would have made failures harder to attribute.

The goal of this phase was a verifiable source reorganization: canonical application modules no longer import the legacy package roots, and non-controller layers do not import controllers. Some existing dependencies between services and infrastructure remain, so this is not a strict onion/hexagonal architecture rewrite.

## 36. Phase 3 code map: where to read and edit

```text
main.py                         stable FastAPI entrypoint
app/
    config.py                   settings
    controllers/
        auth/                   login/logout
        registration/           users, clinics, DSOs
        workspaces/             workspaces, clinics, teams, invites
        sync_logs/              list/detail/SSE endpoints
        webhooks/               ingestion/config endpoints
        billing/                wallet, funding, KYC, transfer endpoints
    models/
        entities.py             SQLAlchemy entities and enums
    schemas/
        api.py                  Pydantic request/response definitions
    services/
        auth/                   session and failed-login workflows
        billing/                money helpers and wallet workflows
        appointment_service.py  booking workflow
        patient_creation.py     patient resolution workflow
        sync_log_service.py     sync-log read/serialization workflow
    security/                   JWT, CSRF, encryption, RBAC
    infrastructure/
        database.py             engine/session/Base
        queue.py                Redis clients and RQ queue
        cache/                  optional cached views
        providers/              OpenDental/GHL/ToroForge clients
        workers/appointments.py queued appointment processing
```

67 executable application modules moved. The exact old-to-new module map is saved in docs/mvc_module_map.json. The historical core/new.py file remains in place: it is commented-out reference code, not an active implementation. Existing migrations remain in alembic/versions. Empty legacy package initializers remain so Python can still resolve older package paths.

Why keep entities and schemas together initially? Splitting every class into a separate file would increase import-cycle risk and make it easier to accidentally register duplicate models. A single canonical entities.py and api.py preserve identities while making ownership clear. Domain-by-domain splitting can follow when there is a concrete maintenance benefit.

## 37. Before and after: follow login through the new layers

Before, main.py mounted the old package:

```python
from auth import login, logout

app.include_router(login.router)
app.include_router(logout.router)
```

After, main.py imports the canonical controllers:

```python
from app.controllers.auth import login
from app.controllers.auth import logout

app.include_router(login.router)
app.include_router(logout.router)
```

The public URL is still POST /login because the APIRouter prefix and route decorators did not change. Moving the Python module does not require renaming an HTTP route. uvicorn main:app is also still valid: the entrypoint module and app variable were preserved.

Inside the relocated controller, imports now express the new responsibilities:

```python
from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import loginrequest, loginresponse
from app.security.oauth2 import verify_password
from app.services.auth.session import start_login_session
```

Read the whole flow yourself: app/controllers/auth/login.py receives JSON; app/schemas/api.py defines its fields; app/security/oauth2.py verifies credentials/tokens; app/services/auth/session.py starts the session; app/infrastructure/database.py supplies the same SessionLocal and Base as before. These imports are actual current paths; the example omits other imported helpers for readability.

What to learn: a directory move is safe only when callers and resource identities still line up. Ask both what external contract remains stable and what internal state must stay unique. Folder names alone do not establish sound architecture.

## 38. Why auth/login.py is now short: compatibility without duplicate implementations

The old auth/login.py file now contains this compatibility mechanism, with a longer explanatory docstring in the actual file:

```python
import sys
from app.controllers.auth import login as _implementation

sys.modules[__name__] = _implementation
```

Python stores imported modules in sys.modules. The old import name is assigned the canonical module object. This is an alias, not a second copy of the controller. Both imports see the same globals and function objects:

```python
from auth.login import login as old_login
from app.controllers.auth.login import login as new_login

assert old_login is new_login
```

Why choose this over copying the old implementation? Two implementations would drift and might create separate routers, engines or model registries. Why choose it over only re-exporting names? A function keeps its defining module's globals. If an older test patches auth.login.start_login_session on a simple re-export module, the function might still call the unpatched global in its canonical module. Aliasing the whole module preserves that patch behavior.

Tradeoff: sys.modules aliasing is less familiar than ordinary imports. The old module's __name__ resolves to the canonical name, and logs using logging.getLogger(__name__) now use app.* namespaces. Monitoring filters targeting old logger names may need updating. Do not execute these legacy files as standalone scripts; the supported interfaces are module imports, uvicorn main:app, and configured RQ workers.

Removal rule: do not delete compatibility shims merely because new code uses app/. First audit downstream imports, deployment commands, saved pickles and queued jobs, then remove aliases in a planned compatibility release. Newly written application code should import canonical paths directly.

## 39. Protect the database from duplicate model registration

Before:

```python
from core.database import Base
from core.models import Users
```

Canonical imports now:

```python
from app.infrastructure.database import Base
from app.models.entities import Users
```

The old imports still resolve to these same objects. The database-identity test checks this property:

```python
from core.database import Base as LegacyBase
from app.infrastructure.database import Base
from core.models import Users as LegacyUsers
from app.models.entities import Users

assert LegacyBase is Base
assert LegacyUsers is Users
assert Users.__table__ is Base.metadata.tables["users"]
```

Why this matters: SQLAlchemy tracks mapped classes and tables on a registry/metadata object. Defining the same model twice can create duplicate mappings or table-registration errors. Copying a source file into app/ while keeping the original executable definitions would not satisfy this identity invariant.

All 18 model table definitions and indexes were captured as PostgreSQL DDL before the move, without connecting to a database. The post-move DDL is compared with docs/database_metadata_baseline.json. Mapper configuration is also exercised. Alembic's env.py now imports Base from app.models.entities and settings from app.config; revision contents are unchanged. No upgrade/downgrade command was executed.

What this proves: declared schema consistency and shared model identity. What it does not prove: the actual live database matches those declarations. Existing schema defects remain for their own reviewed migration phase.

## 40. Keep queued workers callable after changing Python packages

An RQ job can contain a saved Python callable path. Before the move, that path could be:

```text
workers.workers.process_crm_load_job
```

New controllers reference:

```text
app.infrastructure.workers.appointments.process_crm_load_job
```

The compatibility shim keeps the historical path resolvable. The migration test uses RQ itself to serialize/restore a job in memory, without submitting it to Redis. Focused excerpt of the tested behavior:

```python
from rq.utils import import_attribute
from app.infrastructure.workers.appointments import process_crm_load_job

resolved = import_attribute("workers.workers.process_crm_load_job")
assert resolved is process_crm_load_job
```

Why this decision: production might already have jobs waiting when a new version is deployed. An HTTP-only smoke test would miss a worker ImportError that appears minutes later. Old callable compatibility and consistent argument signatures are part of the deployment interface even though the frontend never sees them.

Limits: the tests preserve path resolution and in-memory job serialization. They do not start a real Redis/RQ worker, drain existing queues or prove safe mixed-version deployment. Deploy API and workers from the same compatible release, retaining the old paths until older jobs are audited.

## 41. How the migration was performed and why checks ran between layers

A pre-edit source backup was saved as docs/backups/pre_phase_3_source.zip. Modules were moved in four dependency-aware batches: 6 data/configuration modules; 25 security/cache/provider boundary modules; 16 services; and 20 controller/worker/middleware modules. Each batch was followed by the existing test suite. Canonical modules use rewritten app.* imports; legacy files contain alias shims.

Why data first? Controllers, security and services depend on shared models and database resources. Establishing one canonical Base/engine early reduces duplicate-state risk. Why controllers last? They assemble lower-level pieces and expose the API; moving them after those pieces lets the old entrypoint keep working while earlier layers are checked.

The import rewrite used Python's AST to locate import statements, rather than replacing every occurrence of auth or core in text. Module names can also appear in strings, comments or unrelated identifiers. Only recognized imports were rewritten, preserving aliases and surrounding executable logic. A pre-move fingerprint of each module's AST excluding imports checks that the migration did not silently change workflow bodies.

That fingerprint test is a migration-specific invariant. When a later approved phase intentionally fixes behavior in these modules, review and retire/update the corresponding fingerprints deliberately while retaining focused behavior tests. Never use a snapshot as an excuse to preserve a known bug forever.

## 42. Verify contracts and import order rather than trusting a green start screen

The offline contract inspector originally read core/schemas.py and auth/*.py directly. After relocation those files are small shims; scanning them without following the aliases could miss the real schema/cookie declarations. tools/api_contract.py now follows the explicit _implementation import statically and rejects circular alias chains. It still does not import the application or read credentials.

The original contract baselines were kept. The existing runtime OpenAPI test still allows only Phase 2's DSO page-limit default/bounds change. Moving controllers did not rename operation IDs, methods, URLs, request/response shapes, required headers or cookie declarations. A test also checks that the inventory still contains 37 routes, 61 schema classes and three cookie declarations, rather than accepting an accidentally empty scan.

New import tests run in fresh Python interpreters in both directions: legacy first and canonical first. A test process that has already imported everything can hide circular imports or duplicate identity bugs. Fresh-process tests configure mappers and generate OpenAPI while guarding live database access.

Another test patches an old controller path and checks the actual canonical function's globals. Tests also assert that app/ does not import legacy packages and that non-controller layers do not import controllers. These guards make the architectural direction reviewable instead of relying solely on a naming convention.

## 43. Think through a package migration yourself

Before moving code, answer five questions. Which external entrypoints must stay valid? Which internal singletons must remain unique? Which saved data contains Python import paths? Which imports can create cycles? Which checks would detect those failures?

For this codebase: main:app is an entrypoint; Base, engine, settings and model classes are shared state; RQ jobs can contain callable paths; authentication, schemas and database imports can form cycles; OpenAPI, identity, cold-import, metadata and job-resolution tests cover different parts of the risk.

Independent exercise: pick PatientService and trace its constructor dependencies, provider client and entity imports. Locate its canonical module using docs/mvc_module_map.json. Import both old and new paths and prove they are identical. Then explain why the module can move without changing an HTTP URL. Success means you can reason about both Python compatibility and public API compatibility, not just find a file.

Second exercise: explain why creating app/controllers does not automatically remove business rules from controllers. Identify one invitation workflow that could become a service, define its input/output and transaction ownership, and propose behavior tests before extraction. Do not change it solely to satisfy a folder diagram.

## 44. Phase 3 completion, tradeoffs and next work

The MVC-oriented package move is complete. All 51 checks pass: the 40 existing checks plus 11 migration-specific checks. Canonical implementation is under app/, while 67 historical module paths remain compatible. All 18 model tables/index declarations match the pre-move snapshot. The original API baselines still allow only Phase 2's page-limit exception; no additional API exception was introduced in Phase 3. requirements.txt and all 22 existing migration revisions remain unchanged.

The same master DOCX has been expanded with the examples above. README.md points to the new structure and explains how to run checks. The migration map and metadata/AST fingerprints are machine-readable verification artifacts, not additional Word guides. The completion record is docs/phase_3_validation.md; the proposed next work is docs/phase_4_proposal.md. Document archive/XML and source consistency are verified, while visual rendering in desktop Word/LibreOffice remains uninspected.

Remaining limits: some controllers still contain workflow orchestration; the commented legacy core/new.py remains; logs now use canonical module namespaces; compatibility shims need an eventual audited retirement plan. Existing runtime/database/provider/browser limitations and reviewed correctness defects are unchanged. A better structure makes future fixes easier to reason about but does not fix payment recovery, PHI migration or concurrency by itself.

No dependency was added in Phase 3, no revision was changed or executed, no live provider/Redis/database call was made, and nothing was deployed. The Phase 3 validation report records the final passing count and the scope of evidence. Phase 4 needs a concrete billing/data/concurrency plan and permission before implementation or any live migration execution.

## 45. Phase 4: what changed and where to look

Phase 4's local implementation repairs billing recovery, patient identity selection, appointment synchronization, privacy boundaries and operational checks. It builds on Phase 3's MVC structure. Edit app/ files. The old auth/, billing/, infra/ and sdk/ files remain compatibility imports. The pre-edit source archive is docs/backups/pre_phase_4_source.zip; this is a source backup, not a backup of your database or encryption keys.

Read this chapter alongside tests/test_phase_4_billing.py and tests/test_phase_4_workflows.py. These are executable examples with fake providers and an in-memory SQLite database. The database helper copies PostgreSQL partial-index predicates for local constraint checks; it does not reproduce PostgreSQL row locks or transaction isolation. Network guards reject PostgreSQL, Redis and HTTP calls during these checks.

The work is ready for local review. It is not a deployment or a claim that the live database has been upgraded. The new journal requires migration f4a100000001 before these workflows can run against an existing database. Migration execution, legacy data conversion and live provider checks require your separate permission.

Learning objective: become able to identify the failure boundary, define the invariant, choose a transaction owner, test an interruption, and explain the remaining uncertainty. Knowing where a function lives is only the beginning.

## 46. Money: validate meaning before converting representation

File: app/services/billing/money.py. Previously the conversion path quantized amounts with ROUND_HALF_UP, so an amount such as 1.005 could become 1.01. Decimal also accepts special values such as NaN and Infinity. Successfully constructing a Decimal is therefore not enough validation for a payment.

The repair checks finiteness, sign, the signed 64-bit storage bound, and fractional minor units. An account balance may be zero; a new payment must be positive. Relevant code:

```python
MAX_MINOR = 2**63 - 1

if not value.is_finite() or value < 0 or (value == 0 and not allow_zero):
    raise ValueError
if value > Decimal(MAX_MINOR) / 100:
    raise ValueError

normalized = amount.quantize(quantizer, rounding=ROUND_HALF_UP)
if normalized != amount:
    raise ToroForgeValidationError("Amount has unsupported fractional minor units")
```

For USD, 10.00 becomes 1000 minor units. 1.005 is rejected rather than silently choosing which cent the caller meant. Funding also rejects a currency that differs from the wallet or has no supported verification path before contacting the provider. Treasury transfers currently accept USD only.

Why this fits: the ledger stores integer minor units and the supported currencies use two decimal places. Introducing float arithmetic would add binary rounding problems. This implementation is not a universal money library: adding a zero-decimal currency requires revisiting both precision and the bound calculation.

Verify: MoneySafetyTests covers nonfinite values, oversized amounts, negative and zero payments, fractional cents and valid zero balances. Independent exercise: calculate the minor units for 25.10, then explain why validating after a provider call is too late even if the local transaction rolls back.

## 47. Idempotency means same owner, same key, same request

Files: app/services/billing/funding_service.py, wallet_transfer_service.py, wallet_service.py and app/services/external_operations.py. Previously a raw global key could collide across workspaces, and some reuse paths did not compare the original amount or destination. A retry must not accidentally return someone else's operation or accept changed instructions.

Funding now constructs a wallet-scoped key and a keyed request fingerprint:

```python
idempotency_key = f"funding:{wallet_id}:{hash_lookup(request_id)}"
fingerprint = request_fingerprint(dict(
    wallet_id=wallet_id, amount_minor=amount_minor,
    currency=normalized_currency, token=normalized_token,
    payment_type=normalized_payment_type,
    success_url=success_url, cancel_url=cancel_url,
    payer_name=payer_name, payer_address=payer_address,
    payer_city=payer_city, payer_state=payer_state,
    payer_country=payer_country, payer_zipcode=payer_zipcode,
    payer_phone=payer_phone, description=description,
))
```

The query constrains wallet ownership before considering either a new key or a historical key. Reuse compares the fingerprint, amount and currency. Transfers compare sender, receiver, amount and currency. A mismatched request fails rather than returning or mutating the old operation. Historical requests without a trustworthy fingerprint require reconciliation instead of guessing their original input.

Why HMAC rather than plain text? hash_lookup uses a secret lookup key, making predictable private inputs harder to enumerate from a stolen index alone. Keep that key stable; changing it changes request and identity fingerprints. A fingerprint is not reversible encryption and cannot reconstruct an original request.

Funding initialization persists payment and pending credit-ledger intent before making the provider request. If initialization times out without a payment reference, the pending operation blocks a different key for that wallet until reconciled. Returning the existing pending request does not repeat the provider write. This sacrifices automatic progress in an uncertain case to avoid duplicate external effects.

Verify: FundingPersistenceTests reuses the same request, rejects a changed amount, allows another wallet to use the same caller key, and checks that a timeout cannot be bypassed with a new key. Exercise: define a request fingerprint for an invoice payment. List every field whose change would make it a different instruction before writing code.

## 48. The operation journal: a database rollback cannot undo an API call

New file: app/services/external_operations.py. New table: external_operations. The earlier transfer pattern created local rows, called the provider, then committed. If the provider succeeded but the process died before commit, the database could have no evidence of the write. Retrying could send money again.

The journal splits the workflow into durable boundaries:

```python
self.db.add(operation)
self.db.commit()  # durable before the provider can do anything
result = await call()
operation.result_encrypted = encrypt_json_secret(result)
operation.state = "succeeded"
self.db.commit()
```

The real implementation also validates fingerprints, prevents a second active action for the same resource with a partial unique index, records audits and handles failures. State meanings:

- in_flight: intent was committed; the provider outcome is not yet durably recorded.
- uncertain: an exception or cancellation occurred after intent; a write may have happened.
- failed: the provider explicitly rejected the operation in a recognized response.
- succeeded: a usable provider response is stored; local application is still pending. Each domain must still validate what that response proves.
- applied: the domain update and journal receipt were committed together.

A transport timeout is uncertain. Only DefinitiveRejection authorizes the failed state. A hard crash may leave in_flight rather than uncertain, but both block blind replay. Reusing succeeded/applied reads the encrypted receipt instead of calling the provider again. mark_applied does not commit; its caller owns the transaction containing the domain update.

Why this decision: there is no shared transaction between PostgreSQL and these remote APIs. A journal preserves evidence across interruptions. It is not a distributed transaction or an exactly-once guarantee. Manual reconciliation remains necessary when a provider offers no reliable lookup or idempotency key. The generic state name succeeded must never be confused with payment settlement.

Verify: ExternalOperationTests checks intent before the call, encrypted secrets/results, changed requests, timeouts, different-key blocking and explicit rejection. Exercise: draw a crash immediately before and after each commit above. For each crash, state what the next request can safely conclude. Never delete an uncertain journal row merely to unblock a retry.

## 49. Settlement and double-entry posting

Funding no longer credits a deposit based on the wallet's aggregate balance. A balance may have changed because of another payment. It also no longer treats a successful lookup or missing transaction amount as proof of this payment.

Files: app/services/billing/settlement.py and funding_service.py. A matching deposit needs the stored transaction ID, requested ID, address, currency, exact amount and an explicitly configured settled status. Missing evidence rejects the match. Relevant policy code:

```python
field = settings.toroforge_settlement_field
allowed = settings.toroforge_settlement_success_values
if not field or not allowed:
    return False
```

TOROFORGE_SETTLEMENT_FIELD and TOROFORGE_SETTLEMENT_SUCCESS_VALUES are deliberately empty by default in .env.example. The public Payments_API.pdf documents transaction lookup operations but does not establish a verified settlement response schema for this merchant. Do not copy the tests' test_status field into a real configuration. Obtain a redacted real response or written provider confirmation, establish which exact value proves settlement and test pending/rejected/reversed responses first. If funding and transfer responses use different schemas, add separate verified adapters before release.

Treasury transfers also require the configured settlement evidence before posting local balances. result=true plus a TX_ID is only an acknowledgement. When that is all the provider returns, the receipt is retained, the transfer stays pending, balances remain unchanged, and another outgoing transfer is blocked pending reconciliation. No live payment was sent to determine response fields.

Posting reloads and locks the transfer, both wallets in stable ID order, and both ledger entries. It verifies the debit/credit wallet pair, matching amount/currency/group, pending state and balance bounds. Funding similarly locks and reloads payment, wallet and ledger before crediting. populate_existing matters because a Session might already hold an older object.

```python
wallets = self.db.query(Wallet).filter(
    Wallet.id.in_([transfer.from_wallet_id, transfer.to_wallet_id])
).order_by(Wallet.id).with_for_update().populate_existing().all()
```

Both balance changes, both posted entries and the applied journal state commit together. Repeating a completed transfer or credited payment does not post again. A confirmed receipt followed by a local application failure can resume without sending another transfer. This protects accounting state; it does not establish blockchain finality by itself.

Verify: DurableTransferTests and FundingPersistenceTests check posting once, local failure recovery, pending quarantine, and acknowledgement without settlement. Independent exercise: begin with balances 10000 and 0; move 2500 and show why the two changes must share one transaction. Explain what evidence you need before treating a timed-out transfer as failed.

Provider sources consulted: https://toronet.org/explorer/Downloads/Payments_API.pdf and https://toronet.org/explorer/Downloads/API_Manuals_Release.pdf. These support the documented operations, not a claim that an undocumented response field means settled.

## 50. Wallet provisioning and password recovery

File: app/services/billing/wallet_service.py. Wallet provisioning contains several external steps: create a key, save its address, register a name, verify the credential. Previously a failure between steps could lose recovery information or generate another key on retry.

Credentials are now encrypted and committed before the keystore write. That write has a stable wallet-keystore:{wallet.id} journal key. Once its response is available, the address is saved before attempting TNS registration. A timeout in naming can be reconciled by reading the name and checking that it resolves to the expected address. It does not call set_name a second time after that proof.

```python
resolved = await self.tns_client.get_address_for_name(username=normalized_username)
if (resolved or "").lower() != external_address.lower():
    raise ReconciliationRequired("Wallet naming requires reconciliation")
self.operations.confirm_from_verified_read(
    name_key, {"address": external_address, "username": normalized_username}
)
```

A keystore timeout with no known address has no safe automatic recovery here. The stored intent and password remain; retry blocks rather than minting another key. Legacy incomplete wallets without recoverable credentials also require manual review. A failed creation-request key remains failed; recovery of its underlying wallet may use a reviewed new request, with the journal still blocking repeated remote writes.

Password rotation saves an encrypted candidate before the remote update. If the update times out, only verifying the NEW credential can confirm recovery. The old password working is insufficient: a delayed write could still change it later. After proof, the candidate becomes the wallet's credential and the journal is applied in the same commit.

Why this fits: the external provider's state cannot be rolled back locally, and losing the only usable password is worse than pausing for reconciliation. The tradeoff is a more explicit state machine and operational work for ambiguous legacy cases. Public password-returning API behavior was not redesigned in this phase; credential access and retention still deserve a separate product/security review.

Verify: WalletRecoveryTests simulates naming timeout, unknown keystore outcome and password rotation timeout. It asserts one write, retained credentials, read-based recovery, and rejection until the new credential is proven. Exercise: add a failure after saving the provider receipt but before applying the wallet update; predict which state makes recovery possible without another provider call.

## 51. Patient identity: never pick the first plausible person

File: app/services/patient_creation.py. A search result is a set of candidates, not proof of identity. The earlier workflow could fall back to a partial name match or first result. That could attach an appointment to the wrong patient.

The repair requires first name, last name and valid birth date before reserving or searching. A candidate must exactly match normalized first name, last name and birth date. Multiple exact matches or partial-only results require manual review. A PatNum already linked to another contact also raises a conflict instead of silently returning that patient's row.

Patient creation uses a stable patient-create:{row.id} operation receipt. A successful create response is reduced to its PatNum for recovery. A timeout does not authorize another create. Patient names, contact values, gender and phone are encrypted in new rows; lookup uses a keyed contact hash.

Why this fits: false positive identity matching can be more damaging than a paused sync. This rule does not magically solve renamed patients, twins with identical demographics, spelling changes or missing historical birth dates. Those need a reviewed matching policy and a human reconciliation workflow.

Verify: PatientMatchingTests covers incomplete identity, ambiguous/partial results, successful receipt reuse, encrypted gender and unknown creates without replay. Exercise: design a candidate list with two equally matching patients; explain why adding ORDER BY or choosing the lowest PatNum does not resolve the ambiguity.

## 52. Appointment concurrency and side-effect receipts

Files: app/infrastructure/clinic_lock.py, app/infrastructure/workers/appointments.py and app/services/appointment_service.py. A Python lock protects only one process. RQ workers may run in separate processes, so patient and appointment reservations need a shared synchronization boundary.

The worker now holds a PostgreSQL advisory session lock on a separate connection for the clinic workflow. It uses pg_try_advisory_lock and reports a busy clinic rather than blocking indefinitely. The key is a stable signed integer derived from clinic identity. The lock survives service-level commits; a transaction advisory lock would be released too early.

```python
acquired = bool(connection.execute(
    text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
).scalar_one())
connection.commit()
if not acquired:
    raise ClinicBusyError("Clinic synchronization is already processing another event")
```

Finally releases the lock and closes its connection. If unlocking fails, the connection is invalidated so a possibly locked session does not return to the pool. The cost is an extra database connection per active clinic workflow and serialization within a clinic. Calls that bypass the worker must use the same lock policy; another external scheduling system can still race this app. This is not a provider-side atomic appointment reservation.

Availability checks bypass cached operatory bookings before creating an appointment. Create, popup and commlog writes have stable journal keys. Their local completion flags are committed with mark_applied. Thus a stored provider receipt can recover from local failure without repeating the write, while an unknown provider create remains blocked.

Webhook and log deduplication now include full event/payload identity. An identical delivery can be recognized, while a changed note or status is not discarded merely because event ID and time stayed the same. The key is an HMAC of canonical JSON, rather than raw patient fields.

Verify: tests cover busy-lock cleanup, unlock failure invalidation, fresh availability and receipt reuse. Real concurrent PostgreSQL workers and external booking races remain untested. Exercise: explain why a lock on the main Session would end at an internal commit, and why a dedicated connection solves only the local cross-process part of the problem.

## 53. Privacy across the database, cache and queue

Files: app/security/search_index.py, app/infrastructure/cache/resilience.py, app/controllers/webhooks/webhook_crm.py and app/services/sync_log_service.py. Encrypting a database column is incomplete if the same patient name appears in a search index, Redis value, job payload or diagnostic message.

Cache helpers now store encrypted JSON. Legacy plaintext cache entries are treated as misses. Query terms in cache keys are hashed. New RQ jobs carry an encrypted_payload envelope; workers decrypt it before processing and still accept legacy jobs. queue_redis uses binary responses because RQ stores binary serialized jobs; decoded Redis clients remain separate for string-oriented caches and pubsub.

```python
# Cache encoding boundary
return encrypt_json_secret(value)

# Blind name search boundary
return "|" + hash_lookup(term) + "|" if 2 <= len(term) <= 255 else None
```

The name index contains HMAC prefixes of normalized full names and words, separated by delimiters. A query is transformed into a token and SQL contains searches that token. This supports word/full-name prefixes of at least two characters. It intentionally changes the old arbitrary substring name-search behavior. Event/reason text search remains separate. The index leaks equality and prefix-pattern information, consumes storage and needs a stable secret; it is not equivalent to making searchable data invisible.

New logs avoid plaintext search terms. Raw provider response bodies are removed from provider exception messages and stored as encrypted details where needed for recovery. Existing Redis keys/jobs and historical log files are not erased by changing source code. Their retention/purge and encryption-key management require an approved operational plan.

Verify: tests check that plaintext patient names are absent from blind indexes, encrypted cache round trips work and legacy plaintext is rejected. The prepared backfill handles supported database records; it does not clean Redis snapshots or external monitoring. Exercise: trace one patient's name through request, SQL, Redis and logs. Mark each location that needs protection and explain why hashing an entire JSON object cannot support name-prefix search.

## 54. Atomic registration, membership constraints and audit records

Files: app/services/registration.py, app/controllers/workspaces/invites.py, app/models/entities.py and app/services/audit_logs.py. DSO creation previously risked leaving a DSO without its owner membership if separate commits were used. The service now flushes for the ID, adds the ADMIN role and audit, then commits once.

```python
db.add(dso)
db.flush()
db.add(RoleAssignment(
    user_id=user_id, scope_type=ScopeType.DSO,
    dso_id=dso.id, role=RoleType.ADMIN, created_by=user_id,
))
record_audit(db, entity_type="dso", entity_id=dso.id, action="create")
db.commit()
```

The excerpt omits audit context and error handling; the actual service rolls back on failure. flush sends SQL within the transaction and obtains generated values. It is not commit. A test simulates failure and proves the DSO, role and audit are rolled back together.

Invitation acceptance locks and reloads the invite before validating it. Partial unique indexes prevent duplicate active clinic or DSO membership for the same user/scope even if separate invitations race. Inactive historical assignments remain allowed. A concurrent conflicting invitation may still receive the generic database error path; the invariant is protected, but a friendlier conflict response is a future refinement.

record_audit adds an Audit_logs row with encrypted JSON details and leaves commit to its caller. Audit entries now accompany registration, invitations, disable actions, billing and external-operation transitions. clinic_id becomes nullable so user/DSO operations do not invent a clinic. This is application audit history, not a tamper-proof compliance ledger or a guarantee that every existing read endpoint is audited.

Deleting an appointment no longer has ORM delete cascade toward its clinic or patient. Cascades from parent to dependent children remain separate decisions. Verify: DatabaseInvariantTests checks membership/treasury constraints and the appointment relationship configuration. Exercise: explain why a uniqueness check in Python is insufficient when two requests can both see no existing row.

## 55. Long-lived authorization, readiness and configured origins

Files: app/security/stream_access.py, both sync-log stream controllers, app/controllers/health.py, app/config.py and main.py. A stream can outlive its original access token or membership. Both SSE loops now revalidate the token and scope using a fresh short-lived Session after waiting for Redis and before delivering an event. Revocation sends access_revoked and ends the stream. Pubsub cleanup closes the connection even when unsubscribe fails.

This reduces the revocation window but does not remove the tiny race between an authorization check and delivery. Tests revoke access while a private event is queued and verify that the event is not emitted.

The root endpoint remains liveness. New GET /health/ready checks PostgreSQL connectivity, the operation-journal table, the expected Alembic revision and Redis. It returns 503 when required infrastructure is unavailable. It never calls a payment or appointment provider. Database and Redis connection timeouts are bounded. Readiness is an operational signal, not evidence that every provider credential or business workflow works.

CORS origins now come from CORS_ORIGINS as a JSON list of explicit HTTP(S) origins. The existing https://www.fumisync.com default is preserved. Wildcards, credentials, paths and malformed origins are rejected. The README's demo frontend origin is different; the actual allowed frontend origins must be reviewed before rollout rather than guessed from a demo link.

main.py now uses FastAPI lifespan for its startup probe. Database URLs use SQLAlchemy URL.create so special characters in credentials are encoded correctly; URLs containing passwords are never intentionally printed. Login and global counters use a single Redis Lua increment/TTL operation, including repair of legacy no-expiry counters. Login identity keys are HMACs rather than email/IP text. Redis failures retain the existing fail-open rate-limit policy, so a Redis outage weakens rate limiting and must be operationally visible.

Exercise: explain why an HTTP 200 root response can coexist with a 503 readiness response. Then explain why revalidating only when the stream initially connects cannot enforce later membership removal.

## 56. Provider adapters: verify protocol assumptions against primary sources

File: app/infrastructure/providers/opendental.py. The old client used https://api.opendental.io/api/v1 and separate OD-Developer-Key/OD-Customer-Key headers. Official Open Dental documentation specifies the .com remote endpoint and the combined Authorization format. The repaired initialization is:

```python
self.base_url = "https://api.opendental.com/api/v1"
self.headers = {
    "Authorization": f"ODFHIR {self.developer_key}/{self.customer_key}",
    "Content-Type": "application/json",
}
```

Sources: https://www.opendental.com/site/apiimplementation.html and https://www.opendental.com/site/apisetup.html. The SDK test checks this exact endpoint and header with fake keys; no clinic request was sent. This correction may change previously failing integration behavior, so verify a read against an explicitly approved test clinic before any write.

The dormant GHL adapter also returned response.json as a method object instead of calling response.json(). It now returns decoded JSON, sends contact search parameters through HTTPX params, and retries reads only. A timeout on a write is not automatically replayed. Tests verify decoded output and one send per unknown write. Existing provider payload mappings and product-specific behavior still need sandbox contract testing before enabling dormant flows.

How to think independently: write down the provider's documented URL, authentication, request body and response evidence separately. Compare each against the client. A familiar variable name is not proof that an integration protocol is correct. Never use live patient or payment writes as a convenient way to discover undocumented behavior.

## 57. The prepared schema transition and data backfill

New revision: alembic/versions/f4a100000001_external_operations_and_wallet_scope.py, following d64762c7874a. It creates the journal, permits audits without a clinic, replaces the broad DSO/wallet-type uniqueness with treasury-only uniqueness, adds active-membership indexes and restores only missing owner ADMIN memberships. Multiple clinic wallets can now belong to one DSO while only one treasury is allowed.

```python
Index("uq_wallet_dso_treasury", "dso_id", unique=True,
      postgresql_where=text("wallet_type = 'DSO_TREASURY'"))
```

The revision aborts on invalid treasury ownership or duplicate active memberships; it does not choose a winning role or discard rows. Its downgrade deliberately raises an error because dropping the journal would destroy evidence needed to prevent duplicate operations. A rollback requires a reviewed forward-compatible recovery plan or coordinated database restore, not casual alembic downgrade.

The 22 historical revisions remain byte-for-byte unchanged. That preserves deployed migration history but does not repair an older populated database automatically. Historical required-column additions can fail before reaching this revision. The historical userclinic drop cannot be reversed into real memberships without a trusted backup/export. The new owner backfill is deliberately limited; it cannot reconstruct lost staff roles. Before any upgrade, establish the actual revision/schema and restore legacy membership data into a reviewed staging manifest if needed. Do not grant everyone ADMIN to make missing access disappear.

Prepared tool: tools/backfill_private_data.py. Its default is a dry-run plan. It recognizes supported plaintext versus encrypted fields, encrypts legacy values, repairs contact/name indexes, encrypts legacy payload/provider/audit details and validates existing encrypted details. It aborts on suspected unreadable ciphertext or duplicate clinic/contact identities. It prints aggregate counts without patient values. Applying uses one transaction; rerunning after success plans no further changes in covered test cases.

```text
# These commands are prepared instructions, not commands already executed.
.venv/bin/python -B -m tools.backfill_private_data
.venv/bin/python -B -m tools.backfill_private_data --apply
```

Both commands connect to the configured database. Even the dry-run needs permission for the intended target. The apply option additionally writes data. The planner holds its change plan in memory, so review database size and use a maintenance window; a large production conversion should be staged/batched after measuring. Preserve original Fernet and HMAC keys. Never rotate keys during this backfill or treat decryption failures as permission to overwrite ciphertext.

Why this decision: schema, business meaning and existing data must agree. Inventing missing calendar IDs, timezones, roles or patient identity fields would create plausible but incorrect records. Safe automation stops where the business value cannot be inferred.

## 58. Rollout sequence to review before requesting execution permission

These steps are prepared, not performed:

1. Identify a disposable PostgreSQL/Redis test environment and the production revision separately. Back up database, keys and legacy membership exports; verify restore. Do not expose connection strings or patient data in logs.
2. Rehearse the existing migration chain on a sanitized populated clone. If it is behind historical required-column/drop revisions, prepare and review the specific staged backfills/export restoration needed before crossing them. Stop rather than invent missing values or blindly stamp a revision.
3. Pause webhook workers and billing writes during the reviewed transition. Resolve invalid ownership, duplicate roles and uncertain legacy payments with evidence. Archive unresolved operations for reconciliation without deleting them.
4. Apply the new revision only after the clone succeeds. Review backfill dry-run counts, approve application separately, run it with original keys, and confirm that a second dry-run plans no changes. Coordinate cache/job retention so old plaintext is not mistaken for newly protected data.
5. Verify a redacted merchant settlement response and configure the correct status mapping/adapters. Rehearse pending, settled, failed, wrong amount/address and reversed cases. Without this evidence, local money posting remains blocked.
6. Run PostgreSQL concurrency tests for wallet rows, active-operation indexes, invitations, refresh rotation and clinic advisory locks. Run the Lua script against disposable Redis, verify binary RQ serialization and encrypted job processing. Local SQLite/mocks do not substitute for these checks.
7. Start matching API/worker versions, check readiness, and run browser login/refresh, lists, streams and allowed-origin smoke tests. Verify approved provider reads before authorizing sandbox writes. Deploy only after explicit permission.

Frontend contract: all 37 original routes and declared request/response shapes/cookies remain covered by baselines; readiness is the one new route. Stricter validation, prefix name search and conservative pending/reconciliation behavior are intentional behavior changes. The frontend may need clearer validation or pending messages. No browser smoke test has established compatibility, and this backend phase did not edit frontend source.

## 59. What the passing checks establish

The complete local suite currently has 98 passing checks. It includes API/OpenAPI compatibility, 67 legacy import identities, fresh import orders, architectural boundaries, journal state transitions, billing posting/recovery, identity selection, private indexes/caches, atomic registration, revoked streams, validation, advisory-lock cleanup, migration declarations/guards and master DOCX consistency.

Original API, metadata and Phase 3 AST baselines are retained. docs/phase_4_changed_modules.json records explicit reasons for retiring migration-only AST checks on intentionally changed modules. Unchanged modules remain checked. docs/phase_4_metadata_changes.json overrides only wallets, role_assignments, audit_logs and the new external_operations table. Tests require precisely that set. A new snapshot is evidence of declared changes, not proof that those declarations were executed correctly on PostgreSQL.

The migration tests inspect the single revision head, journal column declarations, partial indexes and invalid-data aborts using fake Alembic operations. They do not execute DDL. The 98-check result proves local regressions within those fixtures; it does not prove production concurrency, provider finality, real Redis behavior, historical migration safety or browser behavior. Runtime was the existing project .venv on Python 3.14. Production Python needs its own run. requirements.txt was not changed and no package was added in Phase 4.

To rerun locally without services:

```sh
.venv/bin/python -B -m unittest discover -s tests
.venv/bin/python -B tools/build_learning_docx.py
```

The DOCX is generated from this source and its ZIP/XML and source consistency are checked. Its visual pagination in desktop Word has not been inspected. If Word is open, close and reopen docs/FumiSync_Junior_Developer_Guide.docx to load the saved update. The Word temporary lock file is not the guide.

## 60. Learn to perform this work without an assistant

Use this repeatable sequence. First trace one request from controller to service to database/provider. Write down which component owns each commit. State an invariant in one sentence: one credited deposit, one active membership, no patient selected from ambiguous results, or no event delivered after a failed permission check.

Next construct the smallest counterexample. For a money bug, use two requests with one key and different amounts. For identity, use two plausible patients. For recovery, interrupt immediately after provider success. For privacy, inspect the cache/search/job representation, not just the encrypted SQL column. Write a test for the invariant before broad refactoring.

Then list alternatives and their costs. A local flag is simpler than a journal but loses remote evidence across a commit failure. A clinic advisory lock costs a connection and reduces concurrency but crosses worker processes and internal commits. A blind prefix index limits search and leaks patterns but avoids storing plain names. These are context-dependent decisions, not universal best engineering moves.

Implement the smallest complete boundary, including error and retry paths. Verify what happens before a remote call, after an unknown result, after a stored receipt and after local application. If your test mocks the exact function being repaired, it may prove nothing about that function. Inspect assertions and failure injection points rather than relying on a green test count.

Finally report evidence and limits separately. A passing in-memory test does not prove PostgreSQL locking. Official documentation of a lookup operation does not prove its result means settlement. A source move does not prove the frontend works. Ask for permission when the next action changes external state or uses a live environment, with a concrete target and reviewed plan.

Practice project: implement a simulated invoice payment using a fake provider that succeeds but times out once. Persist intent, block blind replay, reconcile by verified reference, and apply the invoice credit once. Add a changed-request test, a cross-owner key test and a local commit failure test. Explain every transaction boundary to another junior developer. You understand this phase when you can predict those outcomes before running the tests.

## 61. Three before-and-after code exercises from the actual Phase 4 backup

These before examples come from docs/backups/pre_phase_4_source.zip. They help you compare the failure mechanism with the repaired boundary rather than just memorize the final code.

Money normalization before, in app/services/billing/money.py:

```python
return amount.quantize(
    quantizer,
    rounding=ROUND_HALF_UP,
)
```

After, following finite/range validation:

```python
normalized = amount.quantize(quantizer, rounding=ROUND_HALF_UP)
if normalized != amount:
    raise ToroForgeValidationError("Amount has unsupported fractional minor units")
return normalized
```

Reasoning: the old expression answers how to round, but the API first needs to decide whether rounding is permitted. This API requires exact supported minor units, so conversion must not silently reinterpret an instruction. Test 1.005 and 1.00. When working alone, separate the business policy from the numeric operation before choosing a Decimal method.

Login counter before, in app/services/auth/login_attempts.py:

```python
attempts = await async_redis.incr(key)
if attempts == 1:
    await async_redis.expire(key, LOCK_WINDOWS_SECONDS)
return int(attempts)
```

After, the helper delegates to one Redis script:

```python
return await increment_window(async_redis, key, LOCK_WINDOWS_SECONDS)
```

The script's critical part:

```lua
local count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
```

Reasoning: a process can stop between two awaited commands, leaving an incremented key without expiry. A Redis script runs those steps atomically on Redis. Checking TTL rather than count also repairs old immortal keys without extending a healthy fixed window on every attempt. The local test checks script invocation and arguments; executing the script and measuring TTL requires the approved disposable Redis rehearsal. Do not confuse a mocked eval call with proof of real Redis atomicity.

GHL response handling before, in app/infrastructure/providers/ghl.py:

```python
response = await retry_with_bak_off(send)
self.cb.success()
return response.json
```

After:

```python
response = await retry_with_bak_off(send) if method.upper() == "GET" else await send()
self.cb.success()
return response.json()
```

Reasoning: response.json is a callable method; response.json() is its decoded result. The retry change is a separate correctness issue: a GET can be repeated under the reviewed read retry policy, while a timed-out POST may already have created a record. The tests assert the returned object and count remote sends. To diagnose this independently, inspect the returned type first, then trace which operations the retry wrapper can replay. Small punctuation and a one-line conditional can both represent important engineering decisions, but they solve different problems.


## 62. Deposit verification correction: respect the existing provider contract

Date: 18 October 2026. You clarified that the transaction lookup supplies successful payment evidence after completion, with result=true, and explicitly asked to remove the additional deposit settlement-status gate. This is project-owner clarification of the provider contract, not a new live-provider verification. The earlier Phase 4 requirement was an extra assumption I introduced; it blocked the existing verification path when unconfigured. Sections 49 and 58 describe that initial policy; this correction supersedes it for deposits only.

File: app/services/billing/funding_service.py. Before this correction:

```python
if not isinstance(record, dict) or not is_settled(record):
    continue
```

After:

```python
if not isinstance(record, dict):
    continue
```

This removes the dependency on an unspecified settlement field for deposits. It does not remove the existing provider success check:

```python
if provider_response.get("result") is not True:
    message = str(provider_response.get("message") or "").strip()
    raise ToroForgeValidationError(message or "ToroForge transaction query failed")
```

The response must contain boolean true, not the string "true". The matching record must contain the exact stored transaction ID, correct destination address, correct currency and exact amount. Missing fields or mismatches fail verification. A successful result with no matching record cannot credit a wallet. The internal record_verified_funding_success method now also requires result=true alongside the matching record, protecting that transaction boundary when called directly.

The earlier aggregate-wallet-balance inference remains removed: another deposit could explain a higher balance. Row locking and reloading, pending-ledger validation, amount limits, atomic balance/ledger/payment commit, encrypted evidence and protection against crediting twice remain. Funding initialization uncertainty blocking also remains. The treasury transfer policy is separate and unchanged; removing the deposit gate does not establish the completion semantics of a different provider operation.

Why this decision fits: a provider adapter should implement the actual completion contract. Requiring an invented additional status field can disable valid payments. Conversely, result=true only proves payment completion if that is what this specific provider endpoint means; it is not a universal rule for all APIs. Record the source of that knowledge, test the agreed contract and confirm it in an approved integration environment before release.

Verification: FundingPersistenceTests now exercises the public verification flow with result=true and a matching record, without a settlement field or configuration, twice. The wallet receives one credit. Another test supplies false, missing/null and string success flags with otherwise matching data; both public verification and direct posting reject them without changing the balance. Matching tests retain missing/wrong ID, address, currency and amount checks. All 99 local tests pass. No live database/provider call, migration or deployment was performed.

Think independently: distinguish three questions. Did the provider return success according to this endpoint's contract? Does this record belong to the expected payment? Has our database already applied it? Each needs its own check. When changing a mature integration, trace the existing success path and confirm the protocol meaning before adding a condition that prevents all completion. A test invented around an invented requirement can pass while still breaking the product.
