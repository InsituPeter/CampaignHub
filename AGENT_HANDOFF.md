# CampaignHub — agent handoff

Saved on 2026-10-20 from the project handoff prepared on 2026-10-17.
This is a context snapshot, not a fresh verification report. Inspect the current
working tree before acting; it contains work that may not be committed.

## Product context

CampaignHub is a Nigeria-first, lower-cost alternative to Mailchimp, focused on
stronger automation for small and growing businesses.

The intended product combines:

- Contact management, segmentation, consent, and suppression.
- Email, WhatsApp, and SMS campaigns.
- Automated customer journeys.
- Commerce integrations, including WooCommerce.
- Payments and billing, including Paystack.
- Campaign reporting and operational monitoring.

These are product goals, not all implemented capabilities. No external messaging,
payment, or commerce provider is connected yet. Sentry is deferred.

## Working relationship with the user

The user is building this as a hands-on learning project.

- Work incrementally and explain the purpose of each component.
- When asked to "generate the logic here," provide code in the conversation;
  do not edit files.
- When explicitly asked to write into the codebase, implement the scoped change.
- "Fire away" generally approves the immediately proposed step, not unrestricted
  development.
- Backend first; frontend later.
- Do not silently enable email delivery or choose a provider.
- Preserve existing changes. The repository has substantial modified and
  untracked work.
- Earlier rollbacks mean conversation history alone is not reliable evidence of
  current implementation. Inspect the files.

## Repository and stack

Project directory:

```text
C:\Users\offic\AI_engineering\campaignHub
```

The Git repository root is `C:\Users\offic\AI_engineering\campaignHub`.
It was corrected from the parent directory on 2026-10-20, preserving existing
history with a normal layout-change commit. Run Git commands from the project
directory; the sibling `ML_ENGINEERING` directory is outside this repository.
The branch is `master` and the remote is
`https://github.com/InsituPeter/CampaignHub.git`.

Environment: Windows, PowerShell.

Stack:

- Python 3.13, managed with uv.
- FastAPI and Pydantic Settings.
- Async SQLAlchemy, asyncpg, PostgreSQL.
- Alembic migrations.
- Docker Compose for development PostgreSQL.
- Pytest, Ruff, and strict MyPy.

Architecture: feature-focused modular monolith, with layers inside each feature.

```text
app/
|-- main.py
|-- api/                       # Health endpoints and router composition
|-- core/                      # Config, DB, security, errors, context, logging
|-- shared/                    # Shared model infrastructure
`-- modules/
    |-- identity/
    |   |-- models.py
    |   |-- repository.py
    |   |-- schemas.py
    |   |-- service.py
    |   |-- dependencies.py
    |   |-- http_dependencies.py
    |   `-- router.py
    `-- workspace/
        |-- model.py           # Singular filename in the current structure
        |-- repository.py
        |-- schemas.py
        |-- service.py
        |-- http_dependencies.py
        |-- router.py
        `-- invitation_router.py
```

Routes call services; services coordinate repositories. Services own explicit
transaction boundaries.

## Roadmap position

Working status: Phase 5 — identity and workspaces, still in progress.

The ten-phase roadmap is:

1. Minimal foundation.
2. Configuration.
3. Database.
4. Application framework.
5. Identity and workspaces.
6. Contacts and consent.
7. Messaging and campaigns.
8. Automations.
9. Commerce, billing, and reporting.
10. Pilot hardening.

Phases 1–4 were treated as completed in our working sequence. The README roadmap
still says Phase 4 is in progress and contains other outdated "future module"
wording. Reconcile documentation against code; do not assume Phase 5 is complete.

## Implemented foundations

- Application factory and managed lifespan.
- Validated environment configuration.
- Async database engine, session management, and transaction helpers.
- Alembic setup and feature migrations.
- Liveness and database-readiness endpoints.
- Versioned business API under `/api/v1`.
- Request-ID propagation.
- Standard application exceptions and JSON error handling.
- Structured logging with request correlation.
- Automated foundation tests.
- FastAPI-generated OpenAPI, Swagger UI, and ReDoc.

## Identity implementation

Identity models, repositories, schemas, services, routes, and tests exist for:

- Registration.
- Email verification and verification resend.
- Login.
- Refresh-token rotation.
- Logout and logout-all.
- Password-reset request and confirmation.
- Current-user lookup.

Authentication combines access JWTs with database-backed sessions and opaque
refresh tokens.

Important security rules:

- Protected routes use `get_current_identity`, not JWT decoding alone.
- Authentication checks account status, verification, security version, and
  session validity.
- Refresh rotation is single-use; replay revokes the session family.
- Rotated session rows remain necessary for replay detection and access-token
  checks.
- Password reset changes the password, increments `security_version`, and revokes
  sessions atomically.
- Security version is an authentication invalidation mechanism; do not confuse
  it with membership optimistic-locking versions.
- Tokens, hashes, passwords, and private delivery results must not appear in
  public responses or logs.

Intentional exposure blockers: authentication routes return 503 until a shared
rate-limiter adapter is configured. Email-issuing routes additionally require
delivery integration. No production adapters are supplied.

Existing identity delivery integration points are not yet transactionally
durable: service commits precede adapter enqueue. Production enablement requires
a transactional outbox or equivalent recovery design.

## Workspace and membership implementation

Implemented services and HTTP routes cover:

- Workspace creation, retrieval, listing, and updating.
- Membership listing and retrieval.
- Role changes.
- Suspension and restoration.
- Removal and self-leave.

Key invariants:

- Membership mutations require a positive `expected_version`.
- Stale versions conflict, including otherwise no-op requests.
- The final eligible owner cannot be removed, demoted, suspended, or allowed
  to leave.
- An eligible owner needs an active owner membership and an active, verified
  account.
- Affected users are locked in UUID order before workspace and membership locks.
- Sensitive operations recheck authentication after lock waits.
- Owners can see member emails; nonowners cannot.
- Removed members require a fresh invitation, not direct restoration.
- Removing owner eligibility invalidates that sender's outstanding invitations.
- Membership suspension does not disable the global account.

Account-level suspension/deletion still needs ownership safeguards across
affected workspaces.

## Latest implementation work: invitation API

The following routes exist and are registered:

```text
GET    /api/v1/workspaces/{workspace_id}/invitations
POST   /api/v1/workspaces/{workspace_id}/invitations
POST   /api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/resend
DELETE /api/v1/workspaces/{workspace_id}/invitations/{invitation_id}
POST   /api/v1/invitations/accept
```

Behavior:

- Listing and revocation enforce workspace authorization.
- Acceptance requires a signed-in, verified account whose email matches the
  invitation.
- Acceptance tokens go in the JSON body, not the URL.
- Expired, revoked, or consumed tokens cannot grant membership.
- Public invitation responses contain neither raw tokens nor token hashes.
- Create and resend perform authorization checks, then deliberately return 503
  without issuing, rotating, or revoking tokens.
- Setting an arbitrary delivery object or flag must not bypass this restriction.

Internal invitation issuance services exist, but HTTP issuance stays disabled
until reliable delivery is integrated.

Latest changes involve:

```text
app/modules/workspace/invitation_router.py
app/modules/workspace/service.py
app/api/router.py
tests/test_workspace_api.py
BACKEND_API_CONTRACT.md
MEMBERSHIP_SERVICES.md
```

## Verification status

The last recorded baseline before the latest invitation changes was:

```text
805 passed, 4 skipped
```

That is historical, not a verified result for the current tree.

Invitation API tests were subsequently added. The verification run's result was
not recovered. Do not claim the latest changes pass or Phase 5 is finished.

The four previously skipped tests were opt-in PostgreSQL tests. SQLite tests do
not prove PostgreSQL concurrency behavior.

PostgreSQL tests require `CAMPAIGNHUB_TEST_DATABASE_URL` pointing to a dedicated
database whose name ends in `_test`. Their isolated-schema tests are not
substitutes for migration verification.

## Immediate next development task

Verify the current invitation work before adding functionality. Run from the
`campaignHub` project directory:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests migrations
uv run pytest tests/test_workspace_api.py -q
uv run pytest
```

Inspect and address failures within the approved invitation scope. Do not reset
the repository, delete migrations, modify real database state, expose `.env`, or
enable delivery merely to make checks pass.

## Documentation to read

All are in the project directory:

- `PRD.md` — product requirements.
- `SYSTEM_DESIGN.md` — architectural direction.
- `DOMAIN_DATA_MODEL.md` — domain design.
- `BACKEND_API_CONTRACT.md` — API contract.
- `MVP_ACCEPTANCE_CRITERIA.md` — acceptance targets.
- `DECISIONS_AND_ASSUMPTIONS.md` — decisions and tentative assumptions.
- `AUTH_SECURITY.md` — implemented authentication rules and exposure blockers.
- `MEMBERSHIP_SERVICES.md` — membership invariants and remaining integration.
- `README.md` — setup and roadmap; some status wording is stale.

Remaining gaps include durable delivery, shared rate limiting, durable membership
audit events, display names, and account-level ownership safeguards. The platform
is not production-ready.

The implementation and documentation described here are included in the
repository-root correction commit. The verification caveats above still apply;
committing these files does not establish that all checks pass.
