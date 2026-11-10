# Phase 4 local validation

Date: 17 October 2026. User authorized local Phase 4 implementation. Live rollout remains unperformed and requires separate approval.

- Full local suite: `.venv/bin/python -B -m unittest discover -s tests` — 98 checks passed on Python 3.14.
- Tests forbid live PostgreSQL, Redis and HTTP access. SQLite fixtures cover journal persistence and local constraints, not PostgreSQL concurrency.
- Added durable external-operation intent/receipt recovery; scoped billing idempotency; finite/bounded/exact-cent money validation; locked double-entry posting; strict configurable settlement evidence; wallet/password recovery.
- Added conservative patient matching, appointment/side-effect receipts, clinic workflow advisory locking and fresh booking availability.
- Added encrypted cache/job payloads, blind name-prefix indexes, supported legacy privacy backfill planning, actual encrypted audit records, atomic registration, invitation locks/active-membership uniqueness, stream access revalidation, readiness/schema checks, explicit CORS configuration and atomic expiring rate counters.
- Corrected documented Open Dental endpoint/authentication and dormant GHL JSON/read-retry behavior.
- Original public API baselines retained: all original routes and declared schemas/cookies preserved, plus one new readiness route (38 routes, 61 schema classes, 3 cookie declarations). Validation, prefix name search and uncertain/pending behavior intentionally change.
- MVC aliases remain identical for all 67 historical modules. Original AST guards retained for unchanged modules; intentional edits listed with reasons in phase_4_changed_modules.json.
- Original metadata baseline retained; only four table declarations override it in phase_4_metadata_changes.json. Model count is 19.
- Prepared revision f4a100000001 (single head, following d64762c7874a); declaration/precondition tests pass. All 22 historical revisions and requirements.txt remain unchanged. New revision and backfill have NOT been run against any database.
- Source backup: docs/backups/pre_phase_4_source.zip. It does not contain a live database/key backup.
- Same master DOCX regenerated with Phase 4 lessons in sections 45–61, actual before/after code, reasoning, tradeoffs, exercises, verification limits and concrete rollout steps. ZIP/XML/source consistency checked; desktop Word rendering not inspected.

Release constraints: verify the actual database revision and historical populated-data transitions; recover lost staff memberships only from trusted evidence; rehearse PostgreSQL/Redis concurrency and RQ; verify merchant settlement response fields before configuring automatic posting; review frontend origins and run browser smoke tests; reconcile uncertain legacy operations. Default settlement configuration blocks treasury transfer posting. Deposit verification was corrected on 18 October to use result=true plus strict transaction matching without a separate settlement-status field. No live provider writes, migrations, data conversion, deployment or frontend browser tests were performed.

All teaching and rollout instructions are consolidated in FumiSync_Junior_Developer_Guide.docx; this file is the concise verification record.

## Deposit verification correction — 18 October 2026

User clarified the existing provider completion contract and authorized removing the extra deposit status requirement. Funding now accepts a boolean result=true with exact stored transaction ID, address, currency and amount; the inner posting method also validates result=true. Row locks, atomic posting, encrypted evidence and duplicate-credit protection remain. Transfer status policy is unchanged. Full local suite: 99 checks passed; real providers and databases were not contacted. The same master DOCX includes the correction in section 62.
