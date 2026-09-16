# CampaignHub Backend API Contract

**Status:** API design draft  
**Last updated:** 2026-09-01  
**Base path:** `/api/v1`  
**Scope:** Private-pilot backend  
**Implementation status:** Not implemented  
**Related documents:** `PRD.md`, `MVP_ACCEPTANCE_CRITERIA.md`, `SYSTEM_DESIGN.md`, `DOMAIN_DATA_MODEL.md`

## 1. Purpose

This document defines the proposed external HTTP contract for CampaignHub's backend. It describes resources, operations, request conventions, response envelopes, errors, idempotency, asynchronous processing, authorization, and webhook boundaries.

It does not define final SQLAlchemy models, provider-specific SDK calls, frontend behavior, or generated OpenAPI output. The API should express product concepts rather than expose database tables directly.

## 2. API principles

- Use resource-oriented HTTP endpoints.
- Version the public contract under `/api/v1`.
- Derive workspace context from authenticated membership and an explicit authorized workspace selector.
- Require idempotency for retryable commands with material effects.
- Represent long-running work as asynchronous operation resources.
- Use stable machine-readable error codes.
- Return UTC timestamps in ISO 8601 format.
- Represent money as integer minor units plus currency.
- Never return secrets, password hashes, raw refresh-token values, or provider credentials.
- Keep list pagination stable and bounded.
- Make current processing state and eventual consistency explicit.
- Preserve channel-specific capabilities instead of forcing false uniformity.

## 3. Transport and content

- Production traffic uses HTTPS only.
- Request and response bodies use `application/json` unless uploading or downloading a file.
- UTF-8 is required.
- Maximum body and upload sizes are endpoint-specific.
- Unknown request fields should be rejected for command endpoints unless forward compatibility explicitly requires otherwise.
- Responses should include `X-Request-ID`; clients may provide one in the accepted format.

## 4. Authentication

### 4.1 Access tokens

Protected endpoints require:

```http
Authorization: Bearer <access-token>
```

Access tokens are short-lived and identify the user and security/session context. Workspace authorization is resolved from current membership, not trusted solely from token claims.

### 4.2 Authentication endpoints

```text
POST /auth/register
POST /auth/login
POST /auth/refresh
POST /auth/logout
POST /auth/logout-all
POST /auth/email-verifications
POST /auth/email-verifications/confirm
POST /auth/password-resets
POST /auth/password-resets/confirm
GET  /auth/me
```

Example login request:

```json
{
  "email": "owner@example.com",
  "password": "user-supplied-secret"
}
```

Example token response:

```json
{
  "access_token": "opaque-or-jwt-value",
  "token_type": "bearer",
  "expires_in": 1800,
  "refresh_token": "returned-only-at-issuance",
  "user": {
    "id": "usr_...",
    "email": "owner@example.com",
    "email_verified": true
  }
}
```

Refresh-token rotation is required. Reuse of a rotated token may revoke its token family.

## 5. Workspace context

Workspace-scoped endpoints use:

```text
/workspaces/{workspace_id}/...
```

The backend verifies current membership for every request. A path identifier never grants access by itself.

General workspace endpoints:

```text
GET   /workspaces
POST  /workspaces
GET   /workspaces/{workspace_id}
PATCH /workspaces/{workspace_id}
POST  /workspaces/{workspace_id}/archive
```

The owner role controls billing, sensitive workspace settings, integrations, exports, and destructive operations. Marketer and analyst permissions follow the PRD and acceptance criteria.

## 6. Response conventions

### 6.1 Single resource

```json
{
  "data": {
    "id": "resource-id",
    "type": "contact",
    "attributes": {}
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

The final implementation may use flatter resource objects if consistently reflected in OpenAPI. The key requirement is a stable distinction between resource data and response metadata.

### 6.2 Collection

```json
{
  "data": [],
  "page": {
    "next_cursor": "opaque-cursor-or-null",
    "has_more": false,
    "limit": 50
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

### 6.3 Asynchronous acceptance

Long-running commands return `202 Accepted`:

```json
{
  "data": {
    "id": "op_...",
    "type": "operation",
    "status": "queued",
    "operation_type": "contact_import",
    "resource": {
      "type": "import_job",
      "id": "imp_..."
    },
    "created_at": "2026-09-01T09:00:00Z"
  }
}
```

## 7. Error contract

Errors use a consistent envelope:

```json
{
  "error": {
    "code": "CONTACT_NOT_FOUND",
    "message": "The requested contact was not found.",
    "status": 404,
    "request_id": "req_...",
    "details": [],
    "retryable": false
  }
}
```

Validation details may contain:

```json
{
  "field": "email",
  "code": "INVALID_EMAIL",
  "message": "Enter a valid email address."
}
```

Baseline HTTP behavior:

| Status | Meaning |
|---|---|
| `400` | Malformed or semantically invalid request |
| `401` | Missing or invalid authentication |
| `403` | Authenticated but not authorized |
| `404` | Resource absent within authorized scope |
| `409` | State, uniqueness, idempotency, or version conflict |
| `422` | Structured field validation failure |
| `429` | Rate or usage limit reached |
| `503` | Temporarily unavailable dependency or service |

Cross-workspace resource probing should normally produce `404` rather than reveal resource existence.

## 8. Idempotency

Material command endpoints require or accept:

```http
Idempotency-Key: client-generated-stable-value
```

Required operations include:

- Workspace creation
- Contact imports
- Consent commands
- Campaign publication and scheduling
- Automation activation
- Manual enrollment
- Credit purchases or adjustments
- Integration connection commands where repeated execution is unsafe

The idempotency scope includes authenticated actor or workspace, endpoint, and key. Repeating an identical completed request returns the original logical result. Reusing the key with a different request body returns `409 IDEMPOTENCY_KEY_REUSED`.

Keys have a documented retention period appropriate to the operation. Server-side business idempotency remains necessary even after an HTTP key expires.

## 9. Optimistic concurrency

Mutable resources expose a version or entity tag. Updates provide the expected version:

```http
If-Match: "7"
```

If another operation has changed the resource, return:

```text
409 RESOURCE_VERSION_CONFLICT
```

This applies especially to campaign drafts, automation drafts, workspace settings, role changes, and integration configuration.

Published campaign and automation versions are immutable and reject mutation.

## 10. Pagination, filtering, and sorting

Collections use cursor pagination:

```text
?limit=50&cursor=<opaque>
```

Rules:

- Default and maximum limits are endpoint-specific.
- Cursors are opaque and tied to filter/sort context.
- Supported filters are explicitly documented per endpoint.
- Unsupported filter or sort fields produce validation errors.
- Search is distinct from exact filtering.
- Stable tie-breaking includes a unique field such as ID.

Example:

```text
GET /workspaces/{workspace_id}/contacts?status=active&tag=vip&sort=-created_at&limit=50
```

## 11. Asynchronous operations

Operations provide a common status resource:

```text
GET /workspaces/{workspace_id}/operations/{operation_id}
```

Suggested states:

```text
queued
processing
succeeded
partially_succeeded
failed
cancelled
```

An operation response may include progress, result resource, failure summary, and timestamps. It must not expose internal stack traces or credentials.

Cancellation is available only when the operation can safely stop. A cancellation request does not imply recall of provider-accepted messages.

## 12. Memberships and invitations

```text
GET    /workspaces/{workspace_id}/memberships
PATCH  /workspaces/{workspace_id}/memberships/{membership_id}
DELETE /workspaces/{workspace_id}/memberships/{membership_id}
GET    /workspaces/{workspace_id}/invitations
POST   /workspaces/{workspace_id}/invitations
DELETE /workspaces/{workspace_id}/invitations/{invitation_id}
POST   /invitations/{token}/accept
```

Rules:

- Only authorized roles may invite or change membership.
- The final active owner cannot remove or demote themselves without transferring ownership.
- Invitations expire and tokens are single-use.
- Role and membership changes are audited.

## 13. Contacts

```text
GET    /workspaces/{workspace_id}/contacts
POST   /workspaces/{workspace_id}/contacts
GET    /workspaces/{workspace_id}/contacts/{contact_id}
PATCH  /workspaces/{workspace_id}/contacts/{contact_id}
POST   /workspaces/{workspace_id}/contacts/{contact_id}/archive
GET    /workspaces/{workspace_id}/contacts/{contact_id}/timeline
GET    /workspaces/{workspace_id}/contacts/{contact_id}/identifiers
POST   /workspaces/{workspace_id}/contacts/{contact_id}/identifiers
PATCH  /workspaces/{workspace_id}/contacts/{contact_id}/identifiers/{identifier_id}
POST   /workspaces/{workspace_id}/contacts/merge
```

Example contact creation:

```json
{
  "first_name": "Ada",
  "last_name": "Okafor",
  "identifiers": [
    {
      "kind": "email",
      "value": "ada@example.com",
      "is_primary": true
    },
    {
      "kind": "phone",
      "value": "+2348012345678",
      "is_primary": true
    }
  ],
  "custom_attributes": {
    "customer_tier": "gold"
  }
}
```

Contact creation does not imply messaging consent. Conflicting identifiers return a structured conflict rather than silently merging profiles.

## 14. Contact imports and exports

```text
POST /workspaces/{workspace_id}/contact-imports
GET  /workspaces/{workspace_id}/contact-imports
GET  /workspaces/{workspace_id}/contact-imports/{import_id}
POST /workspaces/{workspace_id}/contact-imports/{import_id}/confirm
POST /workspaces/{workspace_id}/contact-imports/{import_id}/cancel
GET  /workspaces/{workspace_id}/contact-imports/{import_id}/errors

POST /workspaces/{workspace_id}/contact-exports
GET  /workspaces/{workspace_id}/contact-exports/{export_id}
GET  /workspaces/{workspace_id}/contact-exports/{export_id}/download
```

Import flow:

1. Create upload/import resource.
2. Upload or attach the CSV.
3. Backend inspects columns and samples.
4. User submits mapping and consent affirmation.
5. Backend returns a preview.
6. User confirms processing.
7. Worker processes rows and records results.

Download links are short-lived and require current authorization.

## 15. Tags

```text
GET    /workspaces/{workspace_id}/tags
POST   /workspaces/{workspace_id}/tags
PATCH  /workspaces/{workspace_id}/tags/{tag_id}
DELETE /workspaces/{workspace_id}/tags/{tag_id}
POST   /workspaces/{workspace_id}/contacts/{contact_id}/tags/{tag_id}
DELETE /workspaces/{workspace_id}/contacts/{contact_id}/tags/{tag_id}
```

Tag application is idempotent. Removing an absent tag may return success with no change.

## 16. Consent and suppression

```text
GET  /workspaces/{workspace_id}/contacts/{contact_id}/permissions
GET  /workspaces/{workspace_id}/contacts/{contact_id}/consent-events
POST /workspaces/{workspace_id}/contacts/{contact_id}/consent-events
GET  /workspaces/{workspace_id}/suppressions
POST /workspaces/{workspace_id}/suppressions
POST /workspaces/{workspace_id}/suppressions/{suppression_id}/release
```

Example consent event:

```json
{
  "identifier_id": "cid_...",
  "channel": "email",
  "status": "granted",
  "source": "hosted_form",
  "occurred_at": "2026-09-01T08:30:00Z",
  "source_event_id": "form-submission-123",
  "disclosure_version": "email-consent-v1",
  "evidence": {
    "form_id": "form_..."
  }
}
```

Rules:

- Consent records are append-only.
- API clients cannot edit or delete earlier events.
- Delayed older events may be recorded without replacing the effective current state.
- Suppression release requires an allowed reason and authorization.
- Manual consent grants and suppression releases are audited.

## 17. Segments

```text
GET    /workspaces/{workspace_id}/segments
POST   /workspaces/{workspace_id}/segments
GET    /workspaces/{workspace_id}/segments/{segment_id}
PATCH  /workspaces/{workspace_id}/segments/{segment_id}
DELETE /workspaces/{workspace_id}/segments/{segment_id}
POST   /workspaces/{workspace_id}/segments/preview
GET    /workspaces/{workspace_id}/segments/{segment_id}/members
```

Segment rule definitions use a versioned, validated expression format. Arbitrary SQL is prohibited.

Preview response includes candidate count, reachable counts by channel, evaluation time, and potentially a bounded sample. Counts may be asynchronous for large datasets.

## 18. Message templates

```text
GET    /workspaces/{workspace_id}/message-templates
POST   /workspaces/{workspace_id}/message-templates
GET    /workspaces/{workspace_id}/message-templates/{template_id}
PATCH  /workspaces/{workspace_id}/message-templates/{template_id}
POST   /workspaces/{workspace_id}/message-templates/{template_id}/versions
GET    /workspaces/{workspace_id}/message-templates/{template_id}/versions
POST   /workspaces/{workspace_id}/message-templates/{template_id}/test
POST   /workspaces/{workspace_id}/message-templates/{template_id}/provider-submissions
```

WhatsApp submission and approval status are provider-connection-specific. Internal template publication does not imply provider approval.

## 19. Campaigns

```text
GET    /workspaces/{workspace_id}/campaigns
POST   /workspaces/{workspace_id}/campaigns
GET    /workspaces/{workspace_id}/campaigns/{campaign_id}
PATCH  /workspaces/{workspace_id}/campaigns/{campaign_id}
POST   /workspaces/{workspace_id}/campaigns/{campaign_id}/preview
POST   /workspaces/{workspace_id}/campaigns/{campaign_id}/test
POST   /workspaces/{workspace_id}/campaigns/{campaign_id}/preflight
POST   /workspaces/{workspace_id}/campaigns/{campaign_id}/publish
POST   /workspaces/{workspace_id}/campaigns/{campaign_id}/schedule
POST   /workspaces/{workspace_id}/campaigns/{campaign_id}/cancel
POST   /workspaces/{workspace_id}/campaigns/{campaign_id}/clone
GET    /workspaces/{workspace_id}/campaigns/{campaign_id}/versions
GET    /workspaces/{workspace_id}/campaigns/{campaign_id}/versions/{version_id}
GET    /workspaces/{workspace_id}/campaigns/{campaign_id}/audience-snapshots
GET    /workspaces/{workspace_id}/campaigns/{campaign_id}/report
```

Publication and scheduling require idempotency and expected draft version.

Preflight response should include:

```json
{
  "publishable": false,
  "candidate_count": 1200,
  "reachable_by_channel": {
    "email": 1030,
    "whatsapp": 760
  },
  "exclusions": {
    "missing_consent": 120,
    "suppressed": 30,
    "invalid_destination": 20
  },
  "estimated_cost": {
    "amount_minor": 450000,
    "currency": "NGN",
    "is_estimate": true
  },
  "blocking_errors": [],
  "warnings": []
}
```

Published versions cannot be mutated. Campaign cancellation affects undispatched work only and cannot recall provider-accepted messages.

## 20. Message attempts

Message attempts are mostly operational resources. Authorized users may inspect them for reporting and support:

```text
GET /workspaces/{workspace_id}/message-attempts
GET /workspaces/{workspace_id}/message-attempts/{message_attempt_id}
GET /workspaces/{workspace_id}/message-attempts/{message_attempt_id}/events
```

Filters may include source, campaign, automation, contact, channel, status, and time range.

The API does not expose commands to set arbitrary message states. Controlled internal services and verified provider events own transitions.

## 21. Automations

```text
GET    /workspaces/{workspace_id}/automations
POST   /workspaces/{workspace_id}/automations
GET    /workspaces/{workspace_id}/automations/{automation_id}
PATCH  /workspaces/{workspace_id}/automations/{automation_id}
POST   /workspaces/{workspace_id}/automations/{automation_id}/validate
POST   /workspaces/{workspace_id}/automations/{automation_id}/simulate
POST   /workspaces/{workspace_id}/automations/{automation_id}/publish
POST   /workspaces/{workspace_id}/automations/{automation_id}/activate
POST   /workspaces/{workspace_id}/automations/{automation_id}/pause
POST   /workspaces/{workspace_id}/automations/{automation_id}/archive
POST   /workspaces/{workspace_id}/automations/{automation_id}/clone
GET    /workspaces/{workspace_id}/automations/{automation_id}/versions
GET    /workspaces/{workspace_id}/automations/{automation_id}/versions/{version_id}
GET    /workspaces/{workspace_id}/automations/{automation_id}/enrollments
GET    /workspaces/{workspace_id}/automations/{automation_id}/report
```

Activation requires a published immutable version, successful validation, channel readiness, and cost-control configuration.

Existing enrollments remain on their original automation version. The initial API does not provide implicit bulk migration.

## 22. Automation enrollments

```text
POST /workspaces/{workspace_id}/automations/{automation_id}/enrollments
GET  /workspaces/{workspace_id}/automation-enrollments/{enrollment_id}
GET  /workspaces/{workspace_id}/automation-enrollments/{enrollment_id}/transitions
POST /workspaces/{workspace_id}/automation-enrollments/{enrollment_id}/exit
```

Manual enrollment requires a business enrollment key or server-defined equivalent. Repetition returns the existing logical enrollment when idempotent.

Arbitrary step mutation is not exposed. Administrative recovery commands, if later required, must be separately authorized and audited.

## 23. Provider connections

```text
GET    /workspaces/{workspace_id}/provider-connections
POST   /workspaces/{workspace_id}/provider-connections
GET    /workspaces/{workspace_id}/provider-connections/{connection_id}
PATCH  /workspaces/{workspace_id}/provider-connections/{connection_id}
POST   /workspaces/{workspace_id}/provider-connections/{connection_id}/verify
POST   /workspaces/{workspace_id}/provider-connections/{connection_id}/rotate
POST   /workspaces/{workspace_id}/provider-connections/{connection_id}/disconnect
GET    /workspaces/{workspace_id}/provider-connections/{connection_id}/sender-identities
POST   /workspaces/{workspace_id}/provider-connections/{connection_id}/sender-identities
```

Responses contain connection health and capability metadata, never credential values. Credential rotation uses write-only request fields.

Channel-specific connection setup may use provider authorization callbacks outside the generic resource shape.

## 24. Commerce and payment integrations

```text
GET    /workspaces/{workspace_id}/integration-connections
POST   /workspaces/{workspace_id}/integration-connections
GET    /workspaces/{workspace_id}/integration-connections/{connection_id}
PATCH  /workspaces/{workspace_id}/integration-connections/{connection_id}
POST   /workspaces/{workspace_id}/integration-connections/{connection_id}/verify
POST   /workspaces/{workspace_id}/integration-connections/{connection_id}/sync
POST   /workspaces/{workspace_id}/integration-connections/{connection_id}/disconnect
GET    /workspaces/{workspace_id}/integration-connections/{connection_id}/health
```

The exact WooCommerce and Paystack authorization flows remain provider-specific and unresolved.

Integration health reports last successful activity, current status, latest actionable error, and whether user intervention is required.

## 25. Billing and usage

```text
GET  /workspaces/{workspace_id}/billing/account
GET  /workspaces/{workspace_id}/billing/balance
GET  /workspaces/{workspace_id}/billing/ledger
GET  /workspaces/{workspace_id}/billing/usage
GET  /workspaces/{workspace_id}/billing/invoices
POST /workspaces/{workspace_id}/billing/credit-purchases
GET  /workspaces/{workspace_id}/billing/credit-purchases/{purchase_id}
GET  /workspaces/{workspace_id}/billing/spending-limits
PUT  /workspaces/{workspace_id}/billing/spending-limits
```

Example balance response:

```json
{
  "data": {
    "currency": "NGN",
    "posted_balance_minor": 1000000,
    "reserved_balance_minor": 150000,
    "available_balance_minor": 850000,
    "updated_at": "2026-09-01T09:30:00Z"
  }
}
```

Ledger entries are append-only and cannot be edited through ordinary workspace APIs. Credit purchase commands require idempotency.

## 26. Reporting

```text
GET /workspaces/{workspace_id}/reports/overview
GET /workspaces/{workspace_id}/reports/channels
GET /workspaces/{workspace_id}/reports/campaigns/{campaign_id}
GET /workspaces/{workspace_id}/reports/automations/{automation_id}
GET /workspaces/{workspace_id}/reports/conversions
GET /workspaces/{workspace_id}/reports/costs
```

Common report parameters include time range, timezone, channel, and optional segment.

Responses distinguish:

- Confirmed facts
- Provider-reported facts
- Measured but imperfect signals such as opens
- Derived attribution
- Estimated costs or revenue

Report responses include freshness metadata and attribution-model version where applicable.

## 27. Audit

```text
GET /workspaces/{workspace_id}/audit-events
GET /workspaces/{workspace_id}/audit-events/{audit_event_id}
```

Audit access is role-restricted. Filtering may include actor, action, target, and time range. Sensitive before/after values remain redacted.

There is no ordinary API endpoint to create, edit, or delete audit events.

## 28. Webhooks

Provider-facing paths should be separated from authenticated workspace APIs:

```text
POST /webhooks/v1/messaging/{provider}/{connection_token}
POST /webhooks/v1/commerce/{provider}/{connection_token}
POST /webhooks/v1/payments/{provider}/{connection_token}
```

The exact routing token design may change. It must not substitute for signature verification.

Webhook behavior:

- Verify provider signature against the workspace-scoped connection.
- Enforce size and content-type limits.
- Record the external event identity and payload reference durably.
- Return provider-appropriate acknowledgement promptly.
- Process interpretation asynchronously.
- Treat duplicates as successful no-ops after verification.
- Avoid returning internal diagnostic details to providers.

Webhook endpoints do not use end-user bearer authentication.

## 29. Internal endpoints

Internal worker operations should preferably invoke Python services directly rather than loop through public HTTP endpoints within the modular monolith.

If deployment later requires internal HTTP controls, they must use separate authentication, network restrictions, and namespaces. They must not be exposed as undocumented public `/api/v1` routes.

Health endpoints may include:

```text
GET /health/live
GET /health/ready
```

Liveness indicates process health. Readiness verifies only dependencies necessary to accept traffic and must not disclose secrets or sensitive infrastructure details.

## 30. Rate limiting

Rate limits apply by endpoint risk and actor context:

- Authentication attempts by identity and network source
- Password reset and verification requests
- Workspace API use by user and workspace
- Expensive previews and exports
- Test sends
- Provider setup and verification
- Webhooks by connection and provider characteristics

Rate-limit responses use `429` and provide safe retry guidance where possible. Marketing frequency limits are business rules and remain distinct from HTTP rate limits.

## 31. API security requirements

- Validate all workspace-resource relationships server-side.
- Reject cross-workspace identifiers without revealing existence.
- Prevent mass assignment by defining explicit writable fields.
- Redact credentials and secrets from schemas and errors.
- Avoid returning full contact data where summary fields suffice.
- Require authorization and audit for exports and sensitive changes.
- Use short-lived signed download access.
- Protect browser clients through an explicit CORS and token-storage strategy when frontend design begins.
- Document webhook signature verification separately per provider.
- Do not expose raw provider payloads through ordinary workspace APIs.

## 32. Compatibility and deprecation

- Additive response fields may be introduced within `v1`; clients should ignore unknown response fields.
- Removing or changing field meaning requires a new version or documented deprecation period.
- New required request fields are breaking unless guarded by a new operation or version.
- Enum expansion must be considered in client contracts.
- Error codes are stable even when human messages improve.
- Provider-specific capability changes should not silently alter canonical API meaning.

## 33. Open API decisions

- Final response-envelope shape
- Exact access and refresh-token transport
- Whether workspace selection uses only paths or also an active-workspace header for selected endpoints
- Final ID representation and prefixes
- File upload protocol and object-storage handoff
- Segment expression schema
- Campaign and automation draft content schemas
- Operation progress representation
- Provider OAuth callback routes
- WooCommerce connection and webhook setup flow
- Paystack's exact role in merchant data and CampaignHub billing
- Attribution response detail
- Contact timeline event taxonomy
- Batch contact mutation requirements
- Public API keys for customer-built integrations
- Webhook routing-token strategy
- Pilot rate limits
- OpenAPI publication and SDK generation policy

## 34. API readiness criteria

The contract is ready for implementation planning when:

1. Authentication and workspace authorization flows are agreed.
2. Error, pagination, idempotency, and concurrency conventions are fixed.
3. Pilot resource schemas are defined precisely enough for OpenAPI generation.
4. Campaign and automation command state transitions match the domain model.
5. Consent and suppression commands have approved semantics.
6. Provider and integration setup flows are known.
7. Billing commands align with the ledger and payment provider.
8. Webhook verification requirements are documented for selected providers.
9. Acceptance criteria map to one or more API operations or documented worker behavior.
10. No API operation permits bypassing a system invariant.

## 35. Recommended next artifact

The next artifact should be the **concurrency and state-transition matrix**. It should map each critical race to its invariant, database transaction, lock or constraint, idempotency key, allowed transition, retry behavior, and recovery path.

After that, the project can produce a phased backend implementation roadmap without beginning code prematurely.
