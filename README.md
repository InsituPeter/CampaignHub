# CampaignHub

CampaignHub is a Nigeria-first marketing automation platform designed as a lower-cost,
automation-focused alternative to Mailchimp for small and growing businesses. The product is
intended to unify contact management, consent, segmentation, campaigns, and automated customer
journeys across email, WhatsApp, and SMS.

> **Project status:** Active backend development. The shared application and database foundations
> are implemented; customer-facing feature modules and external provider integrations are still
> under development. This project is not production-ready.

## Product goals

- Give Nigerian businesses one place to manage contacts and multichannel campaigns.
- Support practical revenue workflows such as abandoned-cart recovery, follow-ups, promotions,
  and customer re-engagement.
- Provide stronger automation at a lower operating cost than established global platforms.
- Integrate with local commerce, payment, and messaging ecosystems without coupling business
  logic to a single vendor.
- Let AI assist with drafts and setup while keeping publishing decisions under user control.

## Current capabilities

- FastAPI application factory with managed startup and shutdown lifecycle.
- Typed, environment-based configuration using Pydantic Settings.
- Asynchronous PostgreSQL access using SQLAlchemy 2 and asyncpg.
- Connection pooling, request-scoped sessions, and explicit transaction helpers.
- Alembic configured for asynchronous database migrations.
- Docker Compose PostgreSQL development service with health checks.
- Separate liveness and database-readiness endpoints.
- Versioned business API composition under `/api/v1`.
- Request-ID generation, validation, propagation, and response headers.
- Standard application exceptions and consistent JSON error responses.
- Structured JSON logging foundation with request correlation.
- Automated tests for configuration, database helpers, liveness, and readiness.

## Planned product capabilities

Identity service workflows and all ten `/api/v1/auth` routes are now implemented,
including registration, verification/resend, login, refresh, logout/logout-all,
password reset, and current-user lookup. Routes intentionally return 503 until a
shared rate-limiter adapter is configured. Email-issuing routes also require a
delivery adapter; no email provider is connected. See [AUTH_SECURITY.md](AUTH_SECURITY.md)
for the required setup, session migration, and remaining production safeguards.

- User identity, authentication, workspaces, memberships, and role-based authorization.
- Contact imports, profiles, tags, segments, consent, and suppression management.
- Email, WhatsApp, and SMS campaign creation, scheduling, delivery, and reporting.
- Event-driven automation journeys with versioned definitions and recoverable execution.
- Usage metering, credit management, append-only billing ledger, and reporting.
- Provider reconciliation, retries, audit trails, and operational monitoring.

## Third-party integration roadmap

The provider layer will use canonical interfaces and vendor-specific adapters. This keeps campaign
and automation logic independent of individual SDKs.

| Area | Intended integration | Status |
|---|---|---|
| Email | Transactional and bulk email provider | Planned |
| WhatsApp | WhatsApp Business messaging provider | Planned |
| SMS | Nigerian-capable SMS provider | Planned |
| Commerce | WooCommerce events and customer/order synchronization | Planned |
| Payments | Paystack payments, webhooks, and reconciliation | Planned |
| Object storage | Imports, exports, and generated files | Planned |
| Observability | Sentry error tracking and distributed tracing | Deferred |

No external SaaS provider is connected in the current implementation. PostgreSQL is the only
external runtime service presently configured.

## Architecture

CampaignHub is designed as a feature-focused modular monolith. Shared technical infrastructure
lives under `app/core`, while business capabilities will be grouped under independent feature
modules.

```text
app/
├── api/                 # Health checks and versioned API composition
├── core/                # Configuration, database, errors, middleware, and logging
├── modules/             # Future business modules grouped by feature
└── main.py              # Application factory and integration point
```

Within a business module, routes call services, services coordinate repositories, and repositories
use SQLAlchemy. Database transactions remain explicit at the service boundary.

The broader design includes transactional outbox processing, idempotent consumers, optimistic
concurrency, bounded retries, provider reconciliation, and recoverable background jobs.

## Technology stack

- Python 3.13
- FastAPI and Uvicorn
- PostgreSQL 16
- SQLAlchemy 2 with asyncpg
- Alembic
- Pydantic Settings
- Docker Compose
- uv
- Pytest and pytest-asyncio
- Ruff
- MyPy

## Prerequisites

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop or another Docker Compose-compatible runtime

## Local setup

From the `campaignHub` directory, install the locked dependencies:

```powershell
uv sync
```

Create the local environment file:

```powershell
Copy-Item .env.example .env
```

Before using authentication, generate a private JWT signing key:

```powershell
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

Store the result in `.env` as `CAMPAIGNHUB_JWT_SECRET_KEY`. Never commit this value.
JWT issuer, audience, and access-token lifetime are documented in `.env.example`.
The lifetime defaults to 900 seconds. Production configuration requires a signing key;
development health checks can run without one, but authentication cannot.

Start PostgreSQL:

```powershell
docker compose up -d postgres
docker compose ps
```

The development database is exposed at `localhost:5435`. The credentials in `.env.example` are
for local development only and must not be reused in production.

Apply available migrations:

```powershell
uv run alembic upgrade head
```

Start the API:

```powershell
uv run uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

## API documentation and health checks

FastAPI generates interactive API documentation automatically:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`
- Liveness: `GET http://127.0.0.1:8000/health/live`
- Readiness: `GET http://127.0.0.1:8000/health/ready`

Business endpoints will be mounted under `/api/v1`. Health endpoints remain unversioned because
they are infrastructure probes rather than part of the public business contract.

## Error contract and request correlation

Clients may provide an `X-Request-ID` header. CampaignHub validates it or generates a new request
ID, returns it in the response headers, and includes it in error responses.

```json
{
  "error": {
    "code": "CONTACT_NOT_FOUND",
    "message": "The requested contact was not found.",
    "status": 404,
    "request_id": "req_abc123",
    "details": [],
    "retryable": false
  }
}
```

Request IDs support debugging and log correlation. They do not provide authorization and are not
a substitute for idempotency keys.

## Development checks

Run the test suite:

```powershell
uv run pytest
```

Run all local quality checks:

```powershell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests migrations
uv run pytest
docker compose config --quiet
```

Apply Ruff's safe automatic fixes:

```powershell
uv run ruff check --fix .
uv run ruff format .
```

MyPy and Pytest failures require targeted code changes rather than a general automatic-fix command.

## Project documentation

- [Product requirements](PRD.md)
- [System design](SYSTEM_DESIGN.md)
- [Domain and data model](DOMAIN_DATA_MODEL.md)
- [Backend API contract](BACKEND_API_CONTRACT.md)
- [MVP acceptance criteria](MVP_ACCEPTANCE_CRITERIA.md)
- [Decisions and assumptions](DECISIONS_AND_ASSUMPTIONS.md)

## Development roadmap

1. Minimal foundation — complete
2. Configuration — complete
3. Database — complete
4. Application framework — in progress
5. Identity and workspaces
6. Contacts and consent
7. Messaging and campaigns
8. Automations
9. Commerce, billing, and reporting
10. Pilot hardening

## Security notes

- Never commit `.env`, provider credentials, access tokens, or production database secrets.
- Do not log message bodies, contact details, authorization headers, or provider credentials.
- Treat all webhook payloads and client-provided headers as untrusted input.
- Use HTTPS, secret management, credential rotation, and restricted database access in production.
