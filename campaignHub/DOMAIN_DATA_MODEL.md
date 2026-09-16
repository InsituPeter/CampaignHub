# CampaignHub Domain and Data Model

**Status:** Conceptual design draft  
**Last updated:** 2026-09-01  
**Scope:** PostgreSQL and SQLAlchemy domain model for the private-pilot backend  
**Implementation status:** Not implemented  
**Related documents:** `PRD.md`, `MVP_ACCEPTANCE_CRITERIA.md`, `DECISIONS_AND_ASSUMPTIONS.md`, `SYSTEM_DESIGN.md`

## 1. Purpose

This document translates CampaignHub's system-design invariants into a conceptual relational model. It describes entity ownership, keys, relationships, constraints, indexes, lifecycle fields, and concurrency responsibilities.

It does not contain final SQLAlchemy code, Alembic migrations, API schemas, provider-specific payload models, or a fully normalized physical schema. Names and field details may change during implementation review.

## 2. Modeling conventions

### 2.1 Identifiers

- Internal primary keys should be opaque UUIDs or another non-sequential externally safe identifier.
- External provider identifiers remain strings because formats vary.
- API consumers must not infer ordering or tenant identity from internal IDs.
- Stable business idempotency keys are distinct from primary keys.

### 2.2 Tenant ownership

- Every tenant-owned table carries a non-null `workspace_id`.
- Tenant-specific foreign keys and uniqueness rules include `workspace_id` where practical.
- A child record cannot reference a parent from another workspace.
- Repository queries require workspace context.
- Cross-workspace relationship tests are mandatory.

### 2.3 Time

- Persist timestamps in UTC using timezone-aware PostgreSQL timestamps.
- Store the workspace timezone separately for presentation and schedule interpretation.
- Distinguish provider occurrence time from CampaignHub receipt and processing time.
- Use database-generated timestamps for authoritative record creation where appropriate.

### 2.4 Money

- Store money as integer minor units or exact decimal values.
- Store currency explicitly using an ISO currency code.
- Do not use binary floating point.
- Preserve provider pricing inputs used for estimates and settlements.

### 2.5 Mutable and immutable data

- Published campaign and automation versions are immutable.
- Consent, billing, provider, automation-transition, and audit facts are append-only.
- Current-state projections may be updated but must be reproducible from authoritative facts.
- Soft archive is preferred where historical references must remain intact.

### 2.6 Common fields

Most mutable entities include:

```text
id
workspace_id                 # tenant-owned records only
created_at
updated_at
archived_at                  # when applicable
version                      # optimistic concurrency where needed
```

Append-only facts generally use `recorded_at` rather than `updated_at`.

## 3. High-level relationship map

```text
User ──< WorkspaceMembership >── Workspace
                                      │
              ┌───────────────────────┼──────────────────────────┐
              ▼                       ▼                          ▼
           Contact                 Campaign                  Automation
              │                       │                          │
      ┌───────┼────────┐              ▼                          ▼
      ▼       ▼        ▼        CampaignVersion           AutomationVersion
 Identifier Consent Suppression       │                          │
      │                                ▼                          ▼
      └─────── MessageAttempt <── AudienceSnapshot        Enrollment
                        │                                     │
                        ▼                                     ▼
                 ProviderEvent                       EnrollmentTransition
                        │
                        ▼
                  Reporting facts

Workspace ──< ProviderConnection
Workspace ──< IntegrationConnection ──< CanonicalEvent
Workspace ──< LedgerEntry / BalanceProjection
Workspace ──< AuditEvent
Workspace ──< OutboxEvent
```

## 4. Identity and workspace domain

### 4.1 `users`

Represents a human CampaignHub identity.

| Field | Purpose |
|---|---|
| `id` | Primary key |
| `email` | Display or original normalized login email |
| `normalized_email` | Canonical uniqueness value |
| `password_hash` | Password verifier; never returned by APIs |
| `status` | Pending, active, suspended, or disabled |
| `email_verified_at` | Identity verification time |
| `security_version` | Invalidates tokens after sensitive changes |
| `last_login_at` | Operational and security context |
| `created_at`, `updated_at` | Lifecycle timestamps |

Constraints and indexes:

- Unique `normalized_email`
- Index on `status`
- Password hashes and recovery secrets excluded from ordinary serialization and logs

### 4.2 `user_sessions`

Represents refresh-token families or authenticated sessions.

| Field | Purpose |
|---|---|
| `id` | Session identity |
| `user_id` | Owning user |
| `token_family_id` | Refresh rotation family |
| `refresh_token_hash` | Hash, never the raw token |
| `issued_at`, `expires_at` | Validity window |
| `rotated_at`, `revoked_at` | Lifecycle control |
| `revocation_reason` | Security audit context |
| `last_used_at` | Session monitoring |
| `ip_metadata`, `user_agent` | Optional minimized security metadata |

Constraints and indexes:

- Unique active token hash
- Index on `(user_id, revoked_at, expires_at)`
- Raw refresh tokens are never persisted

### 4.3 `workspaces`

Represents one isolated customer business.

| Field | Purpose |
|---|---|
| `id` | Workspace identity |
| `name` | Business-facing name |
| `slug` | Optional human-readable identifier |
| `status` | Pending, active, sending_suspended, or closed |
| `timezone` | Scheduling and reporting timezone |
| `default_currency` | NGN for the initial market |
| `business_metadata` | Limited extensible business attributes |
| `sending_suspended_at` | Safety control |
| `created_at`, `updated_at`, `archived_at` | Lifecycle timestamps |

Constraints and indexes:

- Unique `slug` if exposed globally
- Valid supported timezone and currency
- Index on `status`

### 4.4 `workspace_memberships`

Joins users to workspaces and assigns a preset role.

| Field | Purpose |
|---|---|
| `id` | Membership identity |
| `workspace_id`, `user_id` | Membership endpoints |
| `role` | Owner, marketer, analyst, or later agency_manager |
| `status` | Invited, active, suspended, or removed |
| `invited_by_user_id` | Audit context |
| `accepted_at`, `removed_at` | Lifecycle times |
| `version` | Concurrent role-change protection |

Constraints and indexes:

- Unique `(workspace_id, user_id)`
- Index `(user_id, status)` for workspace selection
- At least one active owner must remain; enforce in a locked service transaction

### 4.5 `workspace_invitations`

Represents pending invitations separately from membership activation.

Important fields:

```text
id
workspace_id
email
normalized_email
role
token_hash
invited_by_user_id
expires_at
accepted_at
revoked_at
created_at
```

Constraints:

- Only one active invitation per workspace and normalized email
- Store invitation-token hashes only

## 5. Contact domain

### 5.1 `contacts`

Represents one workspace-local customer profile.

| Field | Purpose |
|---|---|
| `id`, `workspace_id` | Tenant-owned identity |
| `status` | Active, archived, or merged |
| `first_name`, `last_name` | Basic profile fields |
| `display_name` | Optional resolved display label |
| `locale`, `timezone` | Personalization and sending context |
| `custom_attributes` | Validated workspace-defined values |
| `merged_into_contact_id` | Surviving profile after merge |
| `created_at`, `updated_at`, `archived_at` | Lifecycle timestamps |
| `version` | Optimistic update control |

Constraints and indexes:

- A merged contact points only to a contact in the same workspace
- A contact cannot merge into itself
- Index `(workspace_id, status, created_at)`
- Selected frequently queried custom fields may require generated columns or dedicated attribute tables later

### 5.2 `contact_identifiers`

Represents email addresses, phone numbers, or other channel identifiers separately from a contact.

| Field | Purpose |
|---|---|
| `id`, `workspace_id`, `contact_id` | Ownership |
| `kind` | Email, phone, or supported future kind |
| `normalized_value` | Matching and uniqueness value |
| `display_value` | Original or presentation value |
| `country_code` | Phone normalization context |
| `is_primary` | Primary identifier within its kind |
| `verification_status`, `verified_at` | Destination verification |
| `source` | Form, import, integration, manual, or system |
| `status` | Active, invalid, or archived |
| `created_at`, `updated_at` | Lifecycle timestamps |

Constraints and indexes:

- Unique `(workspace_id, kind, normalized_value)` for active identifiers, subject to final duplicate policy
- At most one primary active identifier per contact and kind
- Composite relationship prevents cross-workspace contact reference
- Index `(workspace_id, contact_id, kind, status)`

### 5.3 `external_identities`

Maps objects in external systems to contacts.

```text
id
workspace_id
integration_connection_id
external_object_type
external_object_id
contact_id
first_seen_at
last_seen_at
source_metadata
```

Constraints:

- Unique `(integration_connection_id, external_object_type, external_object_id)`
- Workspace must agree across connection and contact
- Index `(workspace_id, contact_id)`

### 5.4 `contact_merge_events`

Append-only record of an explicit merge.

```text
id
workspace_id
source_contact_id
target_contact_id
initiated_by_user_id
reason
merge_policy
recorded_at
```

Merging must preserve identifiers, consent events, activity, and historical references. It must not silently combine conflicting consent into a grant.

### 5.5 `workspace_contact_fields`

Defines allowed custom contact attributes.

```text
id
workspace_id
key
label
data_type
validation_rules
status
created_at
updated_at
```

Constraints:

- Unique `(workspace_id, key)`
- Existing field type cannot change incompatibly without migration rules

## 6. Tags and segmentation

### 6.1 `tags`

```text
id
workspace_id
name
normalized_name
description
created_at
archived_at
```

Constraint: unique active `(workspace_id, normalized_name)`.

### 6.2 `contact_tags`

```text
workspace_id
contact_id
tag_id
source
applied_by_user_id
applied_at
```

Constraints:

- Unique `(workspace_id, contact_id, tag_id)`
- Contact and tag must belong to the same workspace

### 6.3 `segments`

Stores a reusable dynamic audience rule.

| Field | Purpose |
|---|---|
| `id`, `workspace_id` | Ownership |
| `name`, `description` | User-facing identity |
| `rule_definition` | Validated structured rule tree |
| `status` | Active or archived |
| `rule_schema_version` | Supports safe evolution |
| `last_evaluated_at` | Freshness indicator |
| `created_by_user_id` | Audit context |
| `created_at`, `updated_at`, `version` | Lifecycle and concurrency |

The rule definition cannot contain arbitrary SQL. Application code maps approved fields and operators to workspace-scoped SQLAlchemy expressions.

Indexes depend on supported rule fields. Initial relational fields should receive targeted indexes based on actual segment operations.

## 7. Consent and suppression domain

### 7.1 `consent_events`

Append-only authoritative evidence of channel permission changes.

| Field | Purpose |
|---|---|
| `id`, `workspace_id` | Tenant-owned fact identity |
| `contact_id`, `identifier_id` | Subject and destination |
| `channel` | Email, WhatsApp, or later SMS |
| `status` | Granted, revoked, unsubscribed, or unknown |
| `source` | Form, import, integration, customer action, or manual |
| `occurred_at` | When the source says the change happened |
| `recorded_at` | When CampaignHub stored it |
| `source_event_id` | Deduplication and traceability |
| `disclosure_version` | Consent language or policy reference |
| `actor_type`, `actor_id` | User, contact, integration, or system |
| `evidence_metadata` | Minimized supporting data |

Constraints and indexes:

- Contact and identifier belong to the same workspace
- Channel must be compatible with identifier kind
- Unique source identity where the source provides one
- Index `(workspace_id, identifier_id, channel, occurred_at desc, recorded_at desc)`
- Records are never updated to rewrite history

### 7.2 `contact_channel_permissions`

Mutable projection of the latest effective consent state.

```text
workspace_id
contact_id
identifier_id
channel
current_status
effective_at
latest_consent_event_id
updated_at
version
```

Constraints:

- Unique `(workspace_id, identifier_id, channel)`
- `latest_consent_event_id` points to an event for the same workspace, identifier, and channel
- Older delayed events cannot overwrite a newer effective decision

### 7.3 `suppressions`

Represents an independent sending prohibition.

| Field | Purpose |
|---|---|
| `id`, `workspace_id` | Ownership |
| `contact_id`, `identifier_id` | Suppressed subject or destination |
| `channel` | Specific channel or supported broader scope |
| `reason` | Unsubscribe, complaint, hard bounce, invalid, abuse, or manual |
| `source` | Provider, contact, user, system, or administrator |
| `effective_at` | Suppression start |
| `expires_at` | Optional temporary suppression end |
| `released_at`, `released_by` | Controlled release metadata |
| `source_event_id` | Traceability |
| `created_at` | Record time |

Constraints and indexes:

- Active suppression uniqueness by workspace, identifier, channel, and scope
- Index optimized for final dispatch lookup
- Release does not delete the historical suppression

## 8. Campaign domain

### 8.1 `campaigns`

Mutable campaign identity and draft lifecycle.

```text
id
workspace_id
name
status
current_draft_version_id
latest_published_version_id
created_by_user_id
created_at
updated_at
archived_at
version
```

The campaign record does not hold mutable production content after publication.

### 8.2 `campaign_versions`

Immutable published or explicitly versioned campaign definition.

| Field | Purpose |
|---|---|
| `id`, `workspace_id`, `campaign_id` | Ownership |
| `version_number` | Human and operational sequence |
| `lifecycle` | Draft or published |
| `audience_definition` | Segment references or validated rules |
| `channel_configuration` | Enabled channel and routing configuration |
| `schedule_configuration` | Interpreted schedule and timezone |
| `frequency_policy` | Campaign-specific behavior within workspace limits |
| `cost_policy` | Spending limit and reservation behavior |
| `created_by_user_id`, `created_at` | Author context |
| `published_by_user_id`, `published_at` | Publication context |

Constraints:

- Unique `(workspace_id, campaign_id, version_number)`
- Published records are immutable
- Campaign and version must share a workspace

### 8.3 `message_templates`

Reusable workspace-owned content identity.

```text
id
workspace_id
name
channel
status
created_at
updated_at
archived_at
version
```

### 8.4 `message_template_versions`

Immutable content version used for sending.

```text
id
workspace_id
message_template_id
version_number
content_definition
provider_template_reference
provider_approval_status
published_at
created_at
```

WhatsApp provider approval status belongs to a provider-specific template reference or mapping rather than being assumed universal across providers.

### 8.5 `campaign_channel_messages`

Associates a campaign version with its channel content.

```text
id
workspace_id
campaign_version_id
channel
template_version_id
inline_content_snapshot
fallback_policy
position_or_priority
```

Constraint: unique channel/position identity within a campaign version.

### 8.6 `audience_snapshots`

Represents the stable campaign audience calculation.

```text
id
workspace_id
campaign_version_id
status
rule_snapshot
started_at
completed_at
total_candidates
failure_details
```

### 8.7 `audience_snapshot_members`

Records candidate membership before final dispatch eligibility.

```text
workspace_id
audience_snapshot_id
contact_id
selected_identifier_id
channel
selection_reason
created_at
```

Constraints:

- Unique membership appropriate to campaign version, contact, and channel
- Workspace agrees across snapshot, contact, and identifier
- Index supports bounded message-attempt generation

## 9. Message execution domain

### 9.1 `message_attempts`

One durable attempt identity per recipient and channel action.

| Field | Purpose |
|---|---|
| `id`, `workspace_id` | Tenant-owned message identity |
| `source_type`, `source_id` | Campaign version or automation action |
| `contact_id`, `identifier_id` | Recipient and destination |
| `channel` | Email, WhatsApp, or later SMS |
| `template_version_id` | Exact content identity where applicable |
| `content_snapshot` | Required rendered or render-input snapshot |
| `status` | Controlled lifecycle state |
| `idempotency_key` | Duplicate-effect protection |
| `available_at` | Earliest claim time |
| `claimed_at`, `claim_id` | Worker ownership |
| `attempt_count` | Retry count |
| `provider_connection_id` | Selected sender/provider connection |
| `provider_message_id` | Provider-assigned identity |
| `cost_reservation_id` | Funding reference |
| `submitted_at`, `accepted_at`, `delivered_at` | Milestones |
| `last_error_code`, `last_error_class` | Actionable failure context |
| `created_at`, `updated_at`, `version` | Lifecycle and concurrency |

Constraints and indexes:

- Unique `(workspace_id, idempotency_key)`
- Source, contact, identifier, provider connection, and template belong to the same workspace
- Index `(status, available_at, id)` for worker claiming, potentially partition-aware later
- Index `(workspace_id, source_type, source_id, status)` for reporting
- Index on provider message identity within its connection

### 9.2 `message_state_events`

Append-only internal message lifecycle transitions.

```text
id
workspace_id
message_attempt_id
from_status
to_status
reason_code
actor_type
actor_id
metadata
recorded_at
```

This record supports diagnosis and does not replace the current state on `message_attempts`.

### 9.3 `provider_message_events`

Append-only verified provider facts.

```text
id
workspace_id
provider_connection_id
provider_event_id
provider_message_id
message_attempt_id
event_type
provider_occurred_at
received_at
processed_at
processing_status
payload_reference
canonical_metadata
```

Constraints:

- Unique `(provider_connection_id, provider_event_id)`
- Index `(provider_connection_id, provider_message_id)`
- Index `(message_attempt_id, provider_occurred_at, received_at)`

### 9.4 `frequency_reservations`

Atomically reserves a contact's permitted marketing slot across campaigns and automations.

```text
id
workspace_id
contact_id
identifier_id
channel
message_attempt_id
policy_key
reserved_at
effective_window_start
effective_window_end
released_at
```

The precise constraint depends on the selected frequency policy. Reservation and final eligibility occur in the same transaction.

## 10. Automation domain

### 10.1 `automations`

Mutable workflow identity.

```text
id
workspace_id
name
status
current_draft_version_id
latest_published_version_id
created_by_user_id
created_at
updated_at
archived_at
version
```

### 10.2 `automation_versions`

Immutable published workflow definition.

```text
id
workspace_id
automation_id
version_number
lifecycle
trigger_definition
workflow_definition
entry_policy
frequency_policy
cost_policy
created_by_user_id
created_at
published_by_user_id
published_at
```

Constraints:

- Unique `(workspace_id, automation_id, version_number)`
- Published definition is immutable

The pilot may store the validated graph as versioned structured data. Frequently queried or independently referenced steps may be normalized into `automation_steps`.

### 10.3 `automation_steps`

Optional normalized representation of versioned steps.

```text
id
workspace_id
automation_version_id
stable_step_key
step_type
configuration
position_metadata
```

Constraint: unique `(automation_version_id, stable_step_key)`.

### 10.4 `automation_enrollments`

Durable position of one contact in one workflow version.

| Field | Purpose |
|---|---|
| `id`, `workspace_id` | Enrollment identity |
| `automation_version_id` | Immutable workflow version |
| `contact_id` | Subject |
| `trigger_event_id` | Originating canonical event |
| `business_enrollment_key` | Journey-specific duplicate protection |
| `current_step_key` | Current graph position |
| `status` | Active, waiting, processing, completed, exited, paused, or failed |
| `wake_at` | Earliest next processing time |
| `claimed_at`, `claim_id` | Scheduler/worker claim |
| `goal_reached_at`, `exit_reason` | Terminal outcome |
| `entered_at`, `last_transition_at`, `completed_at` | Lifecycle times |
| `version` | Optimistic concurrency control |

Constraints and indexes:

- Unique `(workspace_id, automation_version_id, business_enrollment_key)`
- Contact and trigger event share workspace
- Index `(status, wake_at, id)` for scheduler claiming
- Index `(workspace_id, contact_id, status)` for final eligibility and support

### 10.5 `enrollment_transitions`

Append-only workflow execution history.

```text
id
workspace_id
enrollment_id
from_step_key
to_step_key
from_status
to_status
trigger_event_id
message_attempt_id
reason_code
transition_key
recorded_at
metadata
```

Constraint: unique `(workspace_id, enrollment_id, transition_key)` to prevent duplicate step execution.

## 11. Integrations and canonical events

### 11.1 `integration_connections`

Workspace-specific commerce, form, or payment connection.

```text
id
workspace_id
provider_type
display_name
status
external_account_id
credential_reference
configuration
connected_by_user_id
connected_at
last_success_at
last_error_code
updated_at
version
```

Credentials are encrypted through a dedicated mechanism and are not stored as readable JSON configuration.

Constraint: provider-specific uniqueness for external account identity within a workspace.

### 11.2 `inbound_events`

Durably records verified external integration input before interpretation.

```text
id
workspace_id
integration_connection_id
external_event_id
external_event_type
provider_occurred_at
received_at
signature_status
processing_status
payload_reference
last_error
```

Constraint: unique `(integration_connection_id, external_event_id)`.

### 11.3 `canonical_events`

Normalized internal business event used by automations and reporting.

```text
id
workspace_id
event_type
subject_type
subject_id
contact_id
source_type
source_id
source_event_key
occurred_at
recorded_at
payload
schema_version
```

Constraints and indexes:

- Unique `(workspace_id, source_event_key)`
- Index `(workspace_id, event_type, occurred_at)`
- Index `(workspace_id, contact_id, occurred_at)`

### 11.4 `commerce_carts`

Pilot read model for relevant cart state.

```text
id
workspace_id
integration_connection_id
external_cart_id
contact_id
status
currency
amount_minor
recovery_url_reference
created_at_external
updated_at_external
last_event_id
```

Constraint: unique `(integration_connection_id, external_cart_id)`.

Sensitive or expiring recovery URLs may require encryption or short-lived provider retrieval rather than ordinary storage.

### 11.5 `commerce_orders`

Pilot read model for conversion and post-purchase journeys.

```text
id
workspace_id
integration_connection_id
external_order_id
contact_id
status
currency
amount_minor
ordered_at
paid_at
last_event_id
```

Constraint: unique `(integration_connection_id, external_order_id)`.

Only the fields required for journeys and reporting should be retained.

## 12. Provider connections and templates

### 12.1 `provider_connections`

Workspace-specific messaging connection.

```text
id
workspace_id
channel
provider_type
status
display_name
external_account_id
credential_reference
configuration
capabilities
connected_at
last_verified_at
last_error_code
version
```

Constraints:

- Provider-specific uniqueness for external account or sender identity
- Channel compatible with provider type
- Index `(workspace_id, channel, status)`

### 12.2 `sender_identities`

```text
id
workspace_id
provider_connection_id
channel
identity_type
display_value
normalized_value
verification_status
verified_at
provider_reference
configuration
```

Examples include email sender/domain and WhatsApp business number.

### 12.3 `provider_template_mappings`

Maps an internal template version to a provider-approved template.

```text
id
workspace_id
provider_connection_id
message_template_version_id
provider_template_id
provider_status
language
variable_schema
submitted_at
approved_at
rejected_at
rejection_reason
```

Constraint: unique mapping per connection, template version, and supported language.

## 13. Billing and usage domain

### 13.1 `billing_accounts`

```text
id
workspace_id
status
currency
plan_code
billing_provider_customer_id
created_at
updated_at
version
```

One active billing account per pilot workspace unless agency billing later requires a broader owner.

### 13.2 `ledger_entries`

Append-only financial facts.

| Field | Purpose |
|---|---|
| `id`, `workspace_id`, `billing_account_id` | Ownership |
| `entry_type` | Purchase, reservation, settlement, release, refund, adjustment, subscription |
| `direction` | Credit or debit |
| `amount_minor`, `currency` | Exact monetary amount |
| `operation_key` | Idempotency identity |
| `source_type`, `source_id` | Message, payment, subscription, or adjustment source |
| `reservation_id` | Related reservation where applicable |
| `description` | User-facing explanation |
| `effective_at`, `recorded_at` | Financial timing |
| `created_by_actor` | System, user, payment provider, or administrator |
| `metadata` | Minimized pricing context |

Constraints and indexes:

- Unique `(billing_account_id, operation_key)`
- Amount must be non-negative; direction determines effect
- Currency must match the applicable billing context
- Records are never edited or deleted to alter financial history
- Index `(billing_account_id, recorded_at)`

### 13.3 `cost_reservations`

Tracks estimated funds held for messages or campaigns.

```text
id
workspace_id
billing_account_id
source_type
source_id
amount_minor
currency
status
operation_key
reserved_at
settled_at
released_at
expires_at
version
```

Constraints:

- Unique `(billing_account_id, operation_key)`
- Valid state transitions: active to partially_settled, settled, released, or expired
- Ledger entries are authoritative for monetary effects

### 13.4 `balance_projections`

Fast current view derived from the ledger.

```text
billing_account_id
workspace_id
currency
posted_balance_minor
reserved_balance_minor
available_balance_minor
latest_ledger_entry_id
updated_at
version
```

Constraint: unique `(billing_account_id, currency)`.

Balance reservation locks this projection row, validates available balance, appends the ledger operation, updates the projection, and marks the source funded in one transaction.

### 13.5 `usage_records`

Canonical billable usage facts.

```text
id
workspace_id
message_attempt_id
channel
provider_connection_id
usage_type
quantity
unit
provider_cost_minor
customer_charge_minor
currency
pricing_version
provider_event_id
recorded_at
```

Constraint: unique operation identity derived from message and provider billable event.

## 14. Reporting and attribution domain

### 14.1 `conversion_events`

Canonical confirmed or qualified conversion fact.

```text
id
workspace_id
canonical_event_id
contact_id
conversion_type
external_object_id
amount_minor
currency
occurred_at
recorded_at
```

Constraint: unique per source conversion identity.

### 14.2 `attribution_results`

Derived, replaceable attribution output.

```text
id
workspace_id
conversion_event_id
attribution_model_version
message_attempt_id
campaign_version_id
automation_version_id
interaction_type
interaction_at
attribution_weight
attributed_amount_minor
currency
calculated_at
superseded_at
```

Constraints:

- Unique result identity within an attribution model version
- Old results may be superseded, not rewritten as raw fact

### 14.3 Aggregate tables

Initial PostgreSQL aggregate tables may include:

- `campaign_daily_metrics`
- `automation_daily_metrics`
- `channel_daily_metrics`
- `workspace_daily_metrics`

They are derived read models and may be rebuilt from canonical facts. Exact dimensions should follow pilot reporting requirements rather than speculative analytics.

## 15. Audit and administration

### 15.1 `audit_events`

Append-only record of sensitive business and administrative actions.

```text
id
workspace_id                    # nullable only for platform-wide action
actor_type
actor_id
action
target_type
target_id
reason
request_id
ip_metadata
before_summary
after_summary
recorded_at
```

Sensitive values and credentials must not be copied into summaries.

Indexes:

- `(workspace_id, recorded_at)`
- `(actor_type, actor_id, recorded_at)`
- `(target_type, target_id, recorded_at)`

### 15.2 `administrative_actions`

Optional separate record for privileged support and safety operations.

```text
id
workspace_id
administrator_user_id
action_type
reason
approved_by_user_id
started_at
expires_at
completed_at
audit_event_id
```

Useful for temporary access elevation, sending suspension, or data-support procedures.

## 16. Transactional outbox and work recovery

### 16.1 `outbox_events`

```text
id
workspace_id                    # nullable only for platform event
event_type
aggregate_type
aggregate_id
payload
schema_version
idempotency_key
occurred_at
available_at
published_at
publish_attempts
last_error
claimed_at
claim_id
```

Constraints and indexes:

- Unique `idempotency_key` within its defined scope
- Index `(published_at, available_at, id)` for publisher claiming
- Published events retained according to an archival policy

### 16.2 `failed_work_items`

Optional operational table for work requiring inspection after automatic retries are exhausted.

```text
id
workspace_id
work_type
source_type
source_id
failure_class
last_error
attempt_count
first_failed_at
last_failed_at
resolution_status
resolved_by
resolved_at
```

This is an operational aid, not the authoritative business state.

## 17. Imports and exports

### 17.1 `import_jobs`

```text
id
workspace_id
import_type
object_reference
status
mapping_definition
consent_affirmation
total_rows
successful_rows
skipped_rows
failed_rows
requested_by_user_id
created_at
started_at
completed_at
last_error
```

### 17.2 `import_row_results`

Store only the detail necessary to explain invalid or conflicting rows. Large result files may live in object storage with an expiring reference.

### 17.3 `export_jobs`

```text
id
workspace_id
export_type
filters
status
object_reference
requested_by_user_id
requested_at
completed_at
expires_at
audit_event_id
last_error
```

Downloads require authorization and use expiring access.

## 18. Deletion and retention behavior

Final periods require legal and operational review. Structural rules are:

- Workspace deletion is a controlled asynchronous process, not a broad cascading request transaction.
- Historical financial and audit facts follow approved retention policy.
- Suppression evidence may need retention sufficient to prevent accidental re-import and resend.
- Published campaign, message, and provider-event history must remain internally consistent.
- Contact deletion or anonymization must not falsify aggregate reporting or financial records.
- External credentials are revoked and erased through a separate tracked step.
- Object-storage files are removed through idempotent deletion work.

Foreign-key behavior should generally use:

- `RESTRICT` for authoritative referenced facts
- Controlled soft archive for user-managed definitions
- Explicit service-led deletion or anonymization for personal data
- `CASCADE` only for strictly dependent, non-authoritative records where accidental loss is acceptable

## 19. Critical transaction mappings

### 19.1 Publish campaign

One transaction:

1. Lock or version-check the campaign draft.
2. Verify workspace authorization and publishable state.
3. Create immutable campaign version.
4. Update campaign publication pointers and state.
5. Insert audit event.
6. Insert idempotent outbox event.

### 19.2 Record consent

One transaction:

1. Insert deduplicated append-only consent event.
2. Compare event precedence with current projection.
3. Update projection only if the event is effective.
4. Create or update suppression when required by the event type.
5. Insert audit or domain event as appropriate.

### 19.3 Claim and fund message

One short transaction:

1. Claim expected pending message state.
2. Recheck workspace, campaign/automation, consent, suppression, frequency, and channel state.
3. Reserve frequency capacity.
4. Lock balance projection.
5. Create idempotent cost reservation and ledger entry.
6. Mark message funded and processing.

The provider call occurs after commit.

### 19.4 Record provider result

One transaction:

1. Insert deduplicated provider event.
2. Resolve the message attempt.
3. Apply monotonic state transition if appropriate.
4. Create canonical facts.
5. Create usage/settlement work where applicable.
6. Insert outbox events for downstream reporting or automation.

### 19.5 Advance automation enrollment

One transaction:

1. Claim the expected enrollment state/version.
2. Load immutable workflow version and current relevant contact data.
3. Evaluate one transition.
4. Insert idempotent transition record.
5. Create message attempt or update wake time/terminal state.
6. Insert outbox work if external action follows.

### 19.6 Reserve balance concurrently

One transaction:

1. Lock the balance projection row.
2. Check available funds.
3. Insert unique reservation and ledger operation.
4. Update projection.
5. Associate reservation with the source message or campaign.

## 20. Initial indexing strategy

Indexes should follow measured queries, but pilot-critical patterns include:

- All tenant lookups begin with `workspace_id`.
- Worker queues: `(status, available_at/wake_at, id)`.
- Contact identifiers: `(workspace_id, kind, normalized_value)`.
- Permission lookup: `(workspace_id, identifier_id, channel)`.
- Active suppression lookup: `(workspace_id, identifier_id, channel, effective_at)`.
- Campaign reporting: `(workspace_id, source_id, status)`.
- Provider correlation: `(provider_connection_id, provider_message_id)`.
- Event timelines: `(workspace_id, contact_id, occurred_at)`.
- Ledger history: `(billing_account_id, recorded_at)`.
- Outbox publication: `(published_at, available_at, id)`.

Partial indexes may improve active-only and pending-only lookups. JSON fields should not receive broad GIN indexes until specific rule or query patterns justify them.

## 21. Partitioning and archival posture

Partitioning is not required merely to begin the pilot. High-growth candidates are:

- Message attempts
- Provider message events
- Canonical events
- Audit events
- Ledger entries
- Enrollment transitions

If volume requires it, partitioning should preserve workspace-scoped query performance and manageable retention operations. A time-based strategy is likely for append-only event data, but the exact key requires query and deletion analysis.

Archival must not make active consent, suppression, billing, or unresolved delivery state unavailable.

## 22. SQLAlchemy mapping guidance

- Use typed SQLAlchemy 2.x declarative mappings.
- Prefer explicit relationships and foreign keys over implicit conventions.
- Avoid accidental lazy loading in asynchronous code; choose loading behavior deliberately.
- Use database enums only after considering migration friction; constrained strings or application enums may be preferable for frequently evolving states.
- Use SQLAlchemy version columns for user-editable or concurrently advanced records where optimistic concurrency is appropriate.
- Use explicit `with_for_update(skip_locked=True)` for worker and scheduler claims.
- Express uniqueness, checks, and tenant relationships as database constraints, not only validators.
- Keep API Pydantic schemas separate from ORM models.
- Let services own transactions; repositories should not commit independently.
- Do not serialize ORM instances into Celery tasks.

## 23. Open modeling decisions

- UUID version and generation strategy
- Exact user-session and token-revocation model
- PostgreSQL row-level security adoption
- Whether custom contact attributes remain JSON or become typed rows
- Whether automation steps are normalized or stored only as a validated versioned graph
- Campaign content snapshot versus template-version reference boundaries
- Destination uniqueness when intentional shared phone numbers exist
- Consent-event precedence for conflicting external timestamps
- Scope and release rules for suppressions
- Final frequency-reservation model
- Provider pricing representation
- Exact cost reservation granularity
- Attribution schema and model versioning
- Retention and anonymization behavior
- Initial table partitioning threshold
- Final provider-specific mapping tables

## 24. Model readiness criteria

The data model is ready for implementation planning when:

1. Every tenant-owned relationship has an explicit workspace-isolation rule.
2. Every critical invariant maps to a database constraint, transaction, or documented service lock.
3. Campaign and automation immutability boundaries are agreed.
4. Consent precedence and suppression rules are approved.
5. Message and enrollment state transitions are defined.
6. Provider-event identities and reconciliation capabilities are known.
7. Billing reservation and settlement semantics are agreed.
8. Data-retention and deletion requirements have received appropriate review.
9. Pilot queries have an initial indexing plan.
10. API resource boundaries can be derived without exposing persistence internals.

## 25. Recommended next artifact

The next design artifact should be the **backend API contract**. It should define versioned resources, authentication context, request and response shapes, pagination, idempotency headers, error format, asynchronous operation status, and webhook boundaries.

The API design should reference this domain model without mirroring every database table directly.
