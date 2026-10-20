# CampaignHub System Design

**Status:** Architecture draft  
**Last updated:** 2026-09-01  
**Scope:** Backend architecture for the private-pilot product  
**Implementation status:** Not implemented  
**Related documents:** `PRD.md`, `DECISIONS_AND_ASSUMPTIONS.md`, `MVP_ACCEPTANCE_CRITERIA.md`

## 1. Purpose

This document describes the proposed backend system design for CampaignHub. It records architectural strategies and invariants discussed during product discovery without presenting unresolved provider, compliance, schema, or deployment choices as settled facts.

The design supports a Nigeria-first marketing automation platform that coordinates email and WhatsApp during the initial pilot, with SMS planned as the next channel. It must preserve a path toward the broader email, SMS, and WhatsApp product vision.

## 2. Design goals

The backend should:

- Protect workspace data isolation.
- Enforce channel-specific consent and suppression.
- Prevent duplicate messages, enrollments, events, and charges.
- Execute campaigns and automations durably across process restarts.
- Make messaging costs transparent and controllable.
- Tolerate provider retries, outages, and out-of-order events.
- Preserve exact published campaign and workflow definitions.
- Produce traceable operational and reporting data.
- Remain simple enough to operate during a private pilot.
- Allow high-pressure components to be extracted later if justified.

## 3. Non-goals

This design does not yet specify:

- The frontend architecture
- Final messaging, payment, or commerce providers
- A production deployment vendor
- Exact database tables and indexes
- Final REST endpoint contracts
- Formal legal or regulatory conclusions
- General-availability scale or service-level objectives
- Transactional messaging
- Enterprise identity and authorization features
- Predictive or autonomous campaign optimization

## 4. Architectural style

CampaignHub should begin as a **modular monolith**.

The system consists of:

- One FastAPI backend codebase
- One PostgreSQL database
- Redis as a task broker and temporary coordination layer
- Separate background-worker processes using the same codebase
- Object storage for imports, exports, and message media
- External providers behind adapters

```text
Future web client or API consumer
               │
               ▼
          FastAPI API
               │
       ┌───────┼────────┐
       ▼       ▼        ▼
 PostgreSQL   Redis   Object storage
       │       │
       │       ▼
       │   Worker processes
       │       │
       └───────┼──────────────────┐
               ▼                  ▼
       Messaging providers   Commerce/payments
```

The modules remain logically separated even though they are deployed together. The initial module boundaries are:

- Identity and access
- Workspaces
- Contacts and identifiers
- Consent and suppression
- Tags and segments
- Campaigns
- Automations
- Messaging
- Integrations
- Billing and usage
- Reporting and attribution
- Audit and administration

Microservices are not justified for the pilot. Likely future extraction points, if required by measured scale or operational isolation, are message dispatch, webhook ingestion, automation scheduling, and analytics.

## 5. Process responsibilities

### 5.1 API process

The API handles short, interactive operations:

- Authenticate users.
- Establish workspace context.
- Authorize actions.
- Validate request data.
- Read and modify durable business state.
- Create outbox records for asynchronous work.
- Return resource identifiers and current state promptly.

The API must not synchronously send large campaigns, wait through automation delays, process large imports, or perform long provider synchronization.

### 5.2 Worker processes

Workers handle slow, retryable, or batch-oriented work:

- Contact imports
- Audience snapshot generation
- Message preparation and dispatch
- Automation enrollment and progression
- Webhook interpretation
- Commerce synchronization
- Aggregate reporting
- Export generation
- Reconciliation and maintenance

Workers receive stable identifiers rather than serialized SQLAlchemy objects. Each worker operation opens its own database session and reloads current state.

### 5.3 Schedulers

Schedulers identify durable work that has become due, including:

- Scheduled campaigns
- Waiting automation enrollments
- Retryable message attempts
- Reconciliation tasks
- Maintenance operations

A scheduler does not hold the only record of future work. PostgreSQL retains the due time and current state.

## 6. Data ownership and source of truth

### 6.1 PostgreSQL

PostgreSQL is authoritative for:

- Users, memberships, and workspaces
- Contacts and channel identifiers
- Consent history and current permission state
- Suppression records
- Tags and segment definitions
- Campaigns, versions, and audience snapshots
- Automations, versions, enrollments, and transitions
- Message attempts and provider events
- Integration connections and canonical events
- Billing ledger entries and balance projections
- Outbox records
- Audit records

### 6.2 Redis

Redis contains disposable operational state:

- Task-queue messages
- Short-lived coordination locks
- Rate-limit counters
- Cached query results
- Ephemeral tokens where appropriate

Losing Redis may delay processing, but it must not erase consent, campaign state, workflow position, billing information, or any other durable business fact.

Correctness-critical concurrency controls should rely primarily on PostgreSQL constraints, conditional updates, and row locks rather than Redis locks.

### 6.3 Object storage

Object storage holds larger files:

- CSV imports
- Generated exports
- Email and WhatsApp media
- Provider payload archives where justified

PostgreSQL retains ownership, workspace scope, status, metadata, and storage references. Access should use short-lived authorization rather than public object URLs.

## 7. Multi-tenancy

CampaignHub uses shared-schema multi-tenancy. Every tenant-owned record carries a mandatory `workspace_id`.

The backend derives workspace context from authenticated membership. A user must not be permitted to select an arbitrary workspace identifier without authorization.

Required controls include:

- Workspace-scoped repository queries
- `workspace_id` in tenant-specific uniqueness constraints
- Workspace identifiers in jobs and domain events
- Workspace-scoped provider connections and credentials
- Cross-workspace access tests
- Audit records for sensitive tenant operations
- Optional PostgreSQL row-level security as an additional barrier

Platform-wide reference data, such as supported countries or provider definitions, may remain unscoped.

The invariant is:

> No user, worker, integration, or administrator may read or mutate tenant data without an explicitly authorized workspace context.

## 8. SQLAlchemy and transaction management

The backend uses:

- SQLAlchemy 2.x declarative models
- `AsyncEngine` with `asyncpg`
- One `AsyncSession` per request or worker operation
- Alembic for schema migrations
- Explicit service-owned transaction boundaries

Routes and repositories should not commit independently. A service controls the transaction for one coherent business operation.

Conceptually:

```python
async with session.begin():
    # Load workspace-scoped state.
    # Check business invariants.
    # Apply state changes.
    # Insert an outbox record if asynchronous work follows.
```

External network calls occur outside database transactions. Holding a transaction open during a provider request would create long locks and couple database health to provider latency.

Read-only requests should not implicitly commit. SQLAlchemy sessions and ORM objects must not cross process or task boundaries.

## 9. Transactional outbox

Important database changes and their asynchronous events are written in the same PostgreSQL transaction.

```text
BEGIN
  publish immutable campaign version
  update campaign state
  insert CampaignScheduled outbox record
COMMIT
```

An outbox publisher reads unpublished records and submits them to the task broker. If it crashes, the records remain available for retry.

The publisher may deliver an event more than once. Consumers must therefore be idempotent.

The invariant is:

> An asynchronous business action cannot be committed without also committing a recoverable record of the work that must follow.

## 10. Idempotency

Duplicate commands, jobs, and provider events are expected. Stable idempotency keys and database uniqueness constraints protect each retryable business effect.

Example identities:

```text
Campaign publication: publish:{workspace}:{campaign}:{version}
Message dispatch:     send:{message_attempt}
Provider webhook:     {provider}:{provider_event_id}
Commerce event:       {connection}:{external_event_id}
Enrollment:           {workflow_version}:{business_enrollment_key}
Ledger operation:     {operation_type}:{source_record}
```

There are two levels:

1. **Processing idempotency:** CampaignHub applies the same internal command or event once.
2. **External-effect idempotency:** CampaignHub avoids asking a provider to perform the same operation twice.

Provider idempotency features should be used where available. If a provider may have accepted a request but CampaignHub lacks confirmation, the result becomes uncertain and must be reconciled rather than blindly retried.

## 11. Campaign lifecycle and versioning

Campaign identity is separate from immutable published versions.

```text
editable draft
      │ publish
      ▼
immutable version 1
      │ subsequent edits
      ▼
new editable draft
      │ publish
      ▼
immutable version 2
```

A published version contains the audience definition, channel configuration, content, schedule, and other execution-relevant settings. Message attempts reference an exact `campaign_version_id`.

Suggested campaign states are:

```text
draft → scheduled → preparing → sending → completed
                  ↘ cancelled
                  ↘ failed
```

State changes must be explicit and atomic. Partial message failure must not be represented as complete campaign success without qualification.

The invariant is:

> Content and execution rules used by scheduled or dispatched messages cannot change underneath them.

## 12. Audience snapshots and segmentation

Segments are validated, structured rule trees rather than user-written SQL. Supported fields and operators are mapped to safe, workspace-scoped queries.

Dynamic segments represent current membership whenever evaluated. Publishing a campaign produces a durable audience snapshot so later contact changes do not make campaign reporting ambiguous.

```text
dynamic segment
       ↓ publication
stable audience snapshot
       ↓ batches
message attempts
```

Snapshot membership is not final permission to send. Consent, suppression, destination validity, frequency, campaign state, channel readiness, and budget are rechecked immediately before dispatch.

Large snapshots and message-attempt sets are created in bounded batches.

## 13. Message-attempt lifecycle

CampaignHub creates one durable message-attempt record per recipient and channel.

Core lifecycle:

```text
pending → eligible → reserved → processing → submitted → accepted → delivered
```

Alternative outcomes include:

```text
suppressed
skipped
insufficient_credit
retryable_failure
failed
rejected
bounced
uncertain
```

Clicks, replies, reads, and conversions are associated events. They do not replace the delivery state.

A message attempt should retain or reference:

- Workspace
- Campaign or automation version
- Contact and channel identifier
- Channel
- Template or rendered-content version
- Eligibility decision
- Cost reservation
- Current status
- Availability and claim timestamps
- Attempt count
- Provider message identity
- Latest error classification

State transitions use conditional updates or row locks so only the expected prior state can advance.

## 14. Final dispatch eligibility

Eligibility is evaluated during audience preparation and again immediately before the provider call.

The final check confirms:

- Current channel consent
- No applicable suppression
- Valid destination
- Active campaign or automation
- Workspace sending permission
- Operational sender and channel
- Frequency-limit availability
- Sufficient reserved funds or budget
- No conversion or exit event that makes the message unnecessary

The check and transition into the claimed dispatch state occur atomically in a short transaction. The provider call follows after commit.

Once a provider has accepted a message, a later opt-out cannot recall it. CampaignHub must record this timing honestly and block subsequent messages.

The invariant is:

> No provider request begins unless the message passes a fresh eligibility check and atomically enters the dispatch state.

## 15. Concurrent worker claiming

Workers claim bounded batches using PostgreSQL row locking, conceptually:

```sql
SELECT id
FROM message_attempts
WHERE status = 'pending'
  AND available_at <= now()
ORDER BY available_at, id
FOR UPDATE SKIP LOCKED
LIMIT :batch_size;
```

Claimed records are changed to `processing`, assigned a claim identifier, timestamped, and committed before external work begins.

Expired claims are recoverable. Recovery must distinguish work that definitely did not reach a provider from work with an uncertain external effect.

Retryable errors use bounded exponential backoff with jitter. Permanent errors are not retried automatically.

## 16. Automation definitions and versions

Automation identity is separate from immutable published versions.

- Existing enrollments remain on the version they entered.
- New enrollments use the newest active version.
- Safety actions such as pause, suppression, or channel suspension can apply across versions.
- Moving active enrollments to another version requires an explicit future migration design.

Current contact consent and suppression are always evaluated live and are not frozen into a workflow version.

## 17. Durable automation scheduling

Automation delays are persisted in PostgreSQL rather than represented by long-running or sleeping queue tasks.

Each enrollment records:

- Workspace
- Contact
- Automation version
- Current step
- Status
- Wake-up time
- Optimistic version
- Last transition time
- Business enrollment key

Suggested enrollment states are:

```text
active
waiting
processing
completed
exited
paused
failed
```

A scheduler claims due enrollments. A worker executes one short, idempotent transition, records an append-only transition event, and persists the next wake-up time or terminal state.

Conditions are evaluated when reached using current data unless a condition explicitly refers to immutable trigger data.

## 18. Trigger and canonical event ingestion

Triggers may originate from contact signup, imports, tags, segments, commerce systems, payments, dates, manual action, or CampaignHub message activity.

External input is verified and normalized into durable canonical events before it can create automation enrollments.

A canonical event includes:

- Internal event ID
- Workspace
- Event type
- Occurrence and receipt times
- Subject or contact reference
- Source and source event ID
- Structured payload

Events are deduplicated by source connection and external event identity. Unmatched or ambiguous contact events are retained for reconciliation rather than attached through risky guessing.

Enrollment uniqueness is journey-specific:

- Welcome: typically once per contact
- Abandoned cart: once per cart
- Post-purchase: once per order
- Re-engagement: repeatable after a cooling-off period

## 19. Webhook ingestion

Webhook endpoints should perform limited synchronous work:

1. Verify provider signature.
2. Enforce size and basic format limits.
3. Record the event and deduplication identity durably.
4. Return an acknowledgement promptly.
5. Interpret and apply the event asynchronously.

Valid provider events are append-only facts. Processing must tolerate duplicates and out-of-order delivery.

State reducers use monotonic precedence rules; for example, a late “accepted” event does not move a delivered message backward.

Unknown event types are retained for investigation. Unverifiable events are rejected or quarantined. Full sensitive payloads are not written indiscriminately to application logs.

## 20. Contact identity

Contacts are separate from identifiers.

Conceptual records are:

```text
contact
contact_identifier: email | phone | external
external_identity: integration object → contact
```

Recommended matching order:

1. Match a known external-system identity.
2. Otherwise match a unique normalized email in the workspace.
3. Otherwise match a unique normalized phone in the workspace.
4. Flag conflicts when identifiers point to different contacts.

Automatic matching remains conservative. Contacts are never merged across workspaces. Consent histories are not combined merely because profiles appear similar. Manual merges are audited and source information is preserved.

Phone numbers should use an international normalized representation when country context is known. Email normalization should be conservative and must not assume provider-specific alias behavior.

## 21. Consent and suppression

Consent consists of append-only evidence plus an efficiently queryable current-state projection.

Consent events record:

- Workspace
- Contact and identifier
- Channel
- Resulting status
- Source
- Occurrence and recording times
- Disclosure or policy version where applicable
- Initiating actor or integration
- Supporting evidence metadata

The derived current-state record identifies the latest effective permission for one contact identifier and channel.

Consent rules include:

- Permission is destination- and channel-specific.
- An address or telephone number does not imply consent.
- Email permission does not grant WhatsApp permission.
- Older delayed events do not override newer effective decisions.
- Imported permission requires source information and importer affirmation.
- Manual changes require an actor and reason.

Suppression is separate and overrides consent. Reasons may include unsubscribe, complaint, hard bounce, invalid destination, abuse action, or workspace decision.

The invariant is:

> A current consent grant is necessary but not sufficient to send; any applicable suppression prevents dispatch.

## 22. Cross-channel frequency control

CampaignHub enforces workspace-level and channel-specific marketing limits across campaigns and automations.

Example policies include:

- Maximum marketing messages per contact within a rolling period
- Maximum messages per channel within a rolling period
- Minimum time between marketing messages
- Separate treatment for future transactional or legally required messages

When a limit is reached, workflow configuration determines whether to defer, skip, or exit. Overrides require an explicitly permitted message category and authorized actor.

The final frequency check and reservation of a contact’s sending slot occur atomically so concurrent workers cannot each consume the same remaining capacity.

## 23. Provider abstraction

Business services depend on channel capabilities rather than vendor SDKs.

Shared messaging concepts include:

- Message identity
- Eligibility
- Dispatch status
- Cost
- Provider events

Channel-specific capabilities remain distinct:

- Email: subjects, HTML, sender domains, bounces, complaints
- WhatsApp: approved templates, variables, session windows, reads, replies
- SMS: character segments, sender IDs, destination pricing, delivery receipts

Provider adapters translate canonical requests and outcomes to and from vendor-specific formats. Canonical outcomes include accepted, temporary failure, permanent rejection, and uncertain.

Provider credentials belong to workspace-scoped connections, are encrypted at rest, and are never returned through ordinary API responses or written to logs.

## 24. Provider failure handling

Provider failures are classified as:

- **Retryable:** Temporary outage, rate limit, or safe pre-acceptance timeout
- **Permanent:** Invalid destination, rejected sender, invalid template, or revoked credential
- **Uncertain:** The provider may have accepted the request but CampaignHub lacks confirmation

Retryable failures use bounded exponential backoff with jitter and provider-specific concurrency controls. Permanent failures stop immediately and expose an actionable reason.

Uncertain outcomes are reconciled through provider idempotency or status APIs where available. They are not automatically resent without evidence that doing so is safe.

Circuit breakers may temporarily stop calls during persistent failure. Durable pending work remains available for recovery. Reconciliation jobs inspect unresolved records and stuck transitions.

## 25. Billing ledger and cost reservation

Billing uses an append-only, idempotent ledger rather than relying solely on a mutable balance field.

Ledger operations include:

- Credit purchase
- Cost reservation
- Final settlement
- Reservation release
- Refund
- Manual adjustment
- Subscription charge

All monetary values use integer minor units or exact decimals with an explicit currency. Binary floating-point values are prohibited for money.

Before dispatch, a short transaction locks the relevant balance projection, verifies funds, creates a reservation entry, and marks the message as funded. Concurrent workers cannot spend the same available funds.

When the billable amount becomes known, CampaignHub settles the actual charge and releases unused reservation. Retrying the same reservation or settlement cannot create duplicate ledger effects.

Estimated costs are labeled as estimates when provider pricing or final billable outcomes remain uncertain.

## 26. Reporting and attribution

Reporting separates facts from derived conclusions:

```text
verified raw events
        ↓
canonical facts
        ↓
aggregates and attribution
```

Canonical facts may include:

- Message accepted
- Message delivered
- Link clicked
- Contact replied
- Order completed
- Payment confirmed

Aggregates are calculated asynchronously by campaign, automation, step, channel, segment, workspace, and time period.

Attribution is a transparent, replaceable rule rather than historical fact. The pilot model should define:

- Eligible interactions
- Attribution window
- Precedence between interactions
- Conversion deduplication
- Currency treatment
- Confirmed versus estimated revenue

PostgreSQL aggregate tables are sufficient initially. A separate analytical store should be introduced only after measured volume or query pressure justifies it.

## 27. Security architecture

### 27.1 Authentication

- Modern password hashing
- Short-lived access tokens
- Rotated refresh tokens
- Token revocation after security-sensitive changes
- Rate limits on authentication and recovery
- Verified user email ownership
- Multi-factor authentication as a later capability or earlier requirement for privileged roles

JWT and encryption secrets come from secure configuration rather than source code.

### 27.2 Authorization

Every operation establishes actor, workspace, role, permission, and resource ownership. Authorization is enforced in domain services as well as HTTP dependencies because workers and internal processes also invoke business behavior.

### 27.3 Credential management

External credentials are encrypted, redacted, rotatable, workspace-scoped, and decrypted only when required. Restricted provider tokens or OAuth are preferred where available.

### 27.4 Data protection

- Do not store payment-card details.
- Avoid logging message bodies and unnecessary contact information.
- Encrypt network traffic.
- Restrict and audit exports.
- Use expiring access to generated files.
- Minimize data sent to AI providers.
- Establish retention and deletion policies after formal review.

### 27.5 Administrative access

Support access uses separate identities, strong authentication, least privilege, an explicit access reason, and a complete audit record. Administrative access must remain exceptional rather than becoming a routine way to operate customer accounts.

## 28. Observability and audit

Correlation identifiers connect activity across asynchronous boundaries:

- Request ID
- Workspace ID
- Campaign and version IDs
- Automation, version, and enrollment IDs
- Message-attempt ID
- Outbox-event and task IDs
- Provider message and event IDs

Structured logs must redact credentials, tokens, unnecessary personal information, and message content.

Operational metrics include:

- API latency and errors
- Database health
- Outbox publication delay
- Queue depth and oldest-task age
- Worker retries and failures
- Automation scheduler lag
- Provider latency and rejection rates
- Webhook delay and processing failures
- Stuck message and enrollment states
- Credit reservation inconsistencies
- Bounce, complaint, and opt-out rates
- Cross-workspace access failures

Audit records are durable business records, separate from operational logs. They cover role changes, imports and exports, campaign publication, automation activation, integration changes, spending controls, and administrative intervention.

## 29. Reliability and recovery

The system assumes external calls and internal tasks can fail.

Required strategies include:

- Explicit network timeouts
- Bounded retries with backoff and jitter
- Failed-work review
- Provider rate-limit enforcement
- Circuit breaking where justified
- Reconciliation jobs
- Stuck-work detection
- Database backup and restore procedures
- Graceful degradation when one channel is unavailable

The practical processing guarantee is at-least-once delivery with idempotent effects, not an unrealistic claim of exactly-once distributed execution.

## 30. Transaction boundaries and consistency

One transaction protects one coherent business invariant. Examples include:

- Workspace creation with owner membership
- Campaign publication with its outbox record
- Consent-event recording with current-state projection
- Message claim and eligibility transition
- Credit reservation with ledger entry
- One automation transition with its event
- External-event recording with deduplication identity

Network calls occur after commit. Follow-up transactions record external outcomes.

Eventual consistency is acceptable for:

- Dashboard aggregates
- Provider delivery updates
- Segment counts
- Revenue attribution
- Generated exports

Interfaces must expose processing state and freshness rather than implying immediate consistency.

Concurrency controls are selected by invariant:

- Database uniqueness constraint
- Atomic conditional update
- Row-level lock
- Optimistic version check
- Narrow use of stronger transaction isolation where necessary

## 31. Principal invariants

The implementation must preserve these invariants:

1. Tenant data is never accessed without an authorized workspace context.
2. A message is never intentionally dispatched twice for the same message-attempt identity.
3. Final consent, suppression, frequency, campaign state, channel readiness, and funding are checked before provider dispatch.
4. A published campaign or automation version is immutable.
5. A contact remains enrolled in the exact automation version it entered unless explicitly migrated.
6. The same external event cannot create duplicate business effects.
7. One cart or order cannot create duplicate enrollment when its journey policy forbids it.
8. Billing reservations and settlements cannot be applied twice.
9. Concurrent workers cannot consume the same message claim, frequency slot, or available funds.
10. Provider events do not move message state backward.
11. Redis loss cannot erase durable business state.
12. Sensitive credentials do not appear in ordinary API responses or logs.

## 32. Conceptual backend module structure

The architecture maps provisionally to:

```text
app/
├── main.py
├── api/
│   ├── dependencies.py
│   └── v1/
│       ├── router.py
│       └── endpoints/
├── core/
│   ├── config.py
│   ├── database.py
│   ├── security.py
│   ├── logging.py
│   └── exceptions.py
├── modules/
│   ├── identity/
│   ├── workspaces/
│   ├── contacts/
│   ├── consent/
│   ├── segments/
│   ├── campaigns/
│   ├── automations/
│   ├── messaging/
│   ├── integrations/
│   ├── billing/
│   ├── reporting/
│   └── audit/
├── providers/
│   ├── email/
│   ├── whatsapp/
│   ├── sms/
│   ├── payments/
│   └── commerce/
├── workers/
└── shared/
```

Within a business module, responsibilities may be separated into models, schemas, repositories, services, routes, events, and exceptions.

Dependency direction should remain:

```text
API routes ───────→ module services ───────→ repositories/models
Workers ──────────→ module services
Module services ──→ provider interfaces
Provider adapters → external systems
```

Services own business rules and transactions. Repositories encapsulate persistence queries. Routes translate HTTP contracts. Provider adapters isolate vendor behavior.

## 33. Scale strategy

The pilot should not be engineered for speculative hyperscale. It should preserve scalable patterns through:

- Stateless API processes
- Horizontally scalable workers
- Bounded batch processing
- Indexed workspace-scoped queries
- Provider-specific queues and concurrency limits
- Partition-ready high-volume event tables
- Asynchronous reporting aggregates
- Data archival policies

Scaling decisions should follow observed bottlenecks. Candidate extraction boundaries are dispatch, webhooks, scheduling, and analytics.

## 34. Unresolved decisions

The following require further design, research, or validation:

- Concrete SQLAlchemy models, relationships, and indexes
- PostgreSQL row-level security adoption
- REST resources and request/response contracts
- Authentication and refresh-token details
- First email and WhatsApp providers
- SMS provider for the later channel
- WooCommerce confirmation as the first native commerce integration
- Exact role of Paystack
- Provider idempotency and reconciliation capabilities
- Attribution window and precedence
- Cross-channel frequency limits
- Cost-reservation calculation
- Retention, deletion, and consent evidence requirements
- Object-storage provider
- Deployment platform and network topology
- Capacity estimates and pilot service objectives
- Backup, restore, and incident procedures

## 35. Recommended next design artifact

The next design artifact should be the **domain and data model**. It should define the entities, ownership boundaries, keys, relationships, state fields, uniqueness constraints, indexes, and concurrency controls required to enforce the invariants above.

That artifact should precede detailed API contracts and implementation planning.
