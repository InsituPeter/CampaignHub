# Authentication security contract

## Implemented

- Login and refresh JWTs contain a required `sid` identifying their issuing session.
- Protected endpoints must depend on `get_current_identity` from
  `app.modules.identity.dependencies`, not just decode a JWT.
- That dependency checks account status, verification, security version, and the
  issuing session's ownership, version, revocation and expiry in the database.
- Rotation retains old session rows and preserves the family's original expiry.
  Rotated rows continue to authorize unexpired access JWTs until revoked.
- Replay revokes every row in that family, commits, and only then returns an error.
- Single-family logout accepts the existing LogoutRequest refresh-token credential,
  acquires the user lock and revokes only that family. It does not require an access
  JWT, so users can log out after JWT expiry. Unknown tokens and repeat requests
  are no-ops. Rotated/expired tokens can revoke their own family, never another one.
  This revocation-only operation must not use the protected-endpoint dependency that
  would reject already-revoked credentials before an idempotent retry reaches it.
- Logout-all rechecks authorization under the user lock, increments the user's
  security version, and revokes all sessions in the same transaction.
- Session issuance versions prevent old refresh tokens obtaining new JWTs after a
  version increase, even if a future caller forgets explicit refresh revocation.

## Required workflow rules

All password, status and session-changing workflows must acquire the user row lock
before changing session rows. Password reset must update the hash, increment
security_version and revoke all sessions in one transaction. These future workflows
must not bypass the service. Authentication checks do not retroactively cancel a
request already authorized; sensitive writes should recheck under their transaction.

Clients must serialize refresh calls. If a successful refresh response is lost,
retrying its old token revokes the family and requires login. No replay grace period
or retry-response cache is implemented. Do not remove old family rows before family
expiry: they are needed for replay detection and access-token checks.

## Migration

Run `uv run alembic upgrade head` before using the changed model. Revision
`b47a16c93e20` adds session security versions and revokes existing sessions whose
issuance version was not recorded. Users must log in again. Old access JWTs without
`sid` are rejected. Downgrade does not undo revocation.

## Public exposure blockers

Email provider selection is deliberately deferred. Keep production delivery disabled;
do not install a no-op adapter merely to make email-issuing endpoints return success.

Verification resend atomically revokes unused verification tokens and creates a new
one only for an unverified pending account. Its optional ActionTokenDelivery result
is private delivery data, not an HTTP response. Unknown/ineligible accounts return
None internally. The future endpoint must always use the same generic public status
and message, and avoid revealing delivery eligibility through error responses.
Uniform wording alone is not a guarantee against timing-based account discovery.
Delivery is not yet durable: no email or outbox job is created. Integrate reliable
delivery and resend throttling before exposing this operation; repeated resends
invalidate older links and can otherwise be used to disrupt verification.

All ten authentication routes are registered under `/api/v1/auth` and documented in
OpenAPI. They fail closed with 503 until `application.state.identity_rate_limiter`
implements the IdentityRateLimiter protocol. Email-issuing routes additionally need
`application.state.identity_delivery` implementing IdentityDelivery. Neither production
adapter is supplied; the API tests use explicit test-only adapters, never default bypasses.

Before enabling them, implement shared, atomic rate limiting at the gateway or with a shared backend. Apply IP and
account limits before password hashing, endpoint-specific refresh limits, bounded
request sizes and expensive-work concurrency limits. Do not treat an in-process
counter as a multi-worker production limiter. Set trusted proxy rules explicitly.
The existing RateLimitExceededError is an error type, not rate-limit enforcement.

The delivery adapter must handle eligible and ineligible requests consistently and
queue email reliably. Current service commits precede adapter enqueue: a process
crash or enqueue failure can leave an undelivered token. Before production, integrate
a transactional outbox with protected token payloads (or an equivalent durable
recovery design). Do not substitute an in-process background task and call it reliable.

Password reset requests are eligible only for active, verified users. Tokens expire
after one hour by default; requesting one does not revoke sessions. Confirmation
consumes the token, hashes the new password, increments security_version and revokes
all sessions atomically. The account is not automatically logged in afterward.
Pending accounts should complete verification first; suspended/disabled accounts
cannot regain access through password reset. This policy is explicit and adjustable.

`GET /auth/me` returns only public user fields. Sensitive responses and error responses
use Cache-Control: no-store; authentication failures include WWW-Authenticate: Bearer.
Duplicate registration, resend and reset requests use generic public responses.

Browser token transport/storage and CSRF protection must be designed when the
frontend/auth routes are introduced. Never log token responses or verification tokens.

## Verification

SQLite tests cover service behavior, rollback and revocation. PostgreSQL locking
tests are opt-in: set `CAMPAIGNHUB_TEST_DATABASE_URL` to a dedicated asyncpg database
whose name ends in `_test`, then run `uv run pytest tests/test_identity_postgres.py`.
The fixture creates and removes only a randomly named test schema in that database.
It does not validate Alembic migrations; migration validation is a separate check.
