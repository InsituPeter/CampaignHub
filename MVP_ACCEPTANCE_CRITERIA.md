# CampaignHub Pilot MVP Acceptance Criteria

**Status:** Provisional discovery specification  
**Scope:** Private pilot, not the complete launch product  
**Primary audience:** Nigerian online retailers  
**Initial channels:** Email and WhatsApp  
**Deferred channel:** SMS  
**Initial journeys:** Welcome, abandoned-cart, and post-purchase  
**Evidence status:** Based on untested assumptions in `DECISIONS_AND_ASSUMPTIONS.md`

## 1. Purpose

This document defines observable conditions for considering the CampaignHub pilot MVP functionally ready. It clarifies product behavior without selecting an architecture, provider, framework, or implementation schedule.

Passing these criteria would demonstrate readiness for a controlled private pilot. It would not establish general availability, regulatory approval, product-market fit, or commercial viability.

## 2. Interpretation

Requirements use the following terms:

- **Must:** Required before the private pilot can operate safely.
- **Should:** Expected for pilot quality but may be handled through a documented manual process.
- **May:** Useful, but not necessary for pilot readiness.

A criterion passes only when its behavior can be demonstrated with representative data and its important failure paths have also been checked.

## 3. Pilot scope boundary

### Included

- Single-business workspaces
- Owner and marketer roles
- CSV contact import
- WooCommerce as the tentative native commerce integration
- Webhooks as a fallback integration
- Paystack as the tentative local payment integration
- Email and WhatsApp messaging
- Channel-specific consent and suppression
- Segments and tags
- One-time campaigns
- Welcome, abandoned-cart, and post-purchase automations
- Essential delivery, conversion, attributed-revenue, and cost reporting
- Naira billing or a controlled pilot substitute
- Guided onboarding and manually assisted operations
- Essential audit and abuse controls

### Excluded

- SMS execution
- Transactional messaging
- Full agency workspace management
- Custom permission builders
- Predictive or autonomous AI
- Full customer-support inbox
- Native mobile applications
- Social publishing
- Full CRM functionality
- Large integration marketplace
- General-availability scale and service guarantees

## 4. Workspace and access

### AC-WS-001 — Create a workspace

**Given** a new verified user  
**When** the user provides the required business information  
**Then** CampaignHub must create one isolated workspace with the user assigned as owner.

### AC-WS-002 — Workspace settings

An owner must be able to configure:

- Business name
- Business contact details
- Default timezone
- Currency, fixed to or defaulting to NGN for the pilot
- Sender identity information

Required fields must be identified, validated, and preserved between sessions.

### AC-WS-003 — Invite a marketer

**Given** an active owner  
**When** the owner invites a valid email address as a marketer  
**Then** the invited user must be able to join only that workspace and perform marketer-authorized actions.

### AC-WS-004 — Enforce role boundaries

A marketer must not be able to:

- Change workspace ownership
- Manage subscription billing
- Remove the owner
- Export all workspace data unless explicitly permitted
- Delete the workspace

Unauthorized operations must fail without partially applying changes.

### AC-WS-005 — Workspace isolation

**Given** two pilot workspaces  
**When** a user accesses contacts, messages, integrations, automations, reports, or files  
**Then** the user must see only data belonging to an authorized workspace.

This criterion must be tested explicitly and is a pilot blocker if it fails.

## 5. Contact import and management

### AC-CT-001 — Import a valid CSV

**Given** a supported CSV containing contact records  
**When** the user maps required fields and confirms the import  
**Then** CampaignHub must create or update the expected contacts and report imported, updated, skipped, and failed row counts.

### AC-CT-002 — Preview before import

Before committing an import, the user must be able to see:

- Detected columns
- Proposed field mappings
- Sample values
- Validation errors
- Expected new and updated record counts
- Required consent confirmation

### AC-CT-003 — Handle invalid rows

Invalid rows must not prevent valid rows from importing. CampaignHub must provide a downloadable or reviewable error report that identifies the row and reason without exposing the data to another workspace.

### AC-CT-004 — Detect duplicates

The import process must identify contacts using normalized channel identifiers. Re-importing the same file must not create unintended duplicate profiles.

Potential conflicts must follow a documented merge rule and preserve consent history.

### AC-CT-005 — Manage contact profiles

An authorized user must be able to:

- View a contact profile
- Edit permitted profile fields
- Apply or remove tags
- View channel reachability
- View consent and suppression state
- View a chronological activity history
- Archive the contact

### AC-CT-006 — Export contacts

An authorized user must be able to export workspace contacts, including available consent source, consent timestamp, suppression status, and custom fields.

The export action must be recorded in the audit log.

## 6. Consent and suppression

### AC-CS-001 — Separate channel consent

Each contact must have independently represented consent states for email and WhatsApp. Updating one channel must not modify the other.

### AC-CS-002 — Preserve consent evidence

For each consent change, CampaignHub must retain:

- Channel
- Resulting state
- Source
- Timestamp
- Initiating user, integration, or customer action where known

### AC-CS-003 — Confirm imported permission

Before an imported audience becomes sendable, the importing user must affirm that the business has permission to contact those people through the selected channels.

CampaignHub must not infer WhatsApp consent from the presence of a telephone number or infer email consent from the presence of an email address.

### AC-CS-004 — Enforce suppression

**Given** a contact who is suppressed or lacks the required permission  
**When** a campaign or automation attempts a send  
**Then** CampaignHub must exclude that contact before dispatch and record the exclusion reason.

### AC-CS-005 — Process opt-outs

Supported unsubscribe or opt-out events must update the relevant channel promptly enough to prevent subsequent sends. Existing consent history must remain auditable.

### AC-CS-006 — Explain audience exclusions

Before publication and in final reporting, CampaignHub must show how many contacts are excluded because of missing consent, invalid destination, suppression, duplication, or another known rule.

## 7. Tags and segments

### AC-SG-001 — Apply tags

An authorized user must be able to apply and remove tags manually, during import, through an integration, or from an automation action.

### AC-SG-002 — Create a dynamic segment

Users must be able to define a segment using at least:

- Contact fields
- Tags
- Channel consent or reachability
- Purchase or commerce-event attributes
- Campaign or automation activity

### AC-SG-003 — Recalculate membership

When relevant contact data changes, dynamic-segment membership must update according to documented timing. The interface must indicate when segment membership was last evaluated.

### AC-SG-004 — Preview reachability

Before using a segment, users must be able to see its total membership and currently reachable membership for each enabled channel.

## 8. Channel setup

### AC-CH-001 — Configure email sending

An owner must be able to configure an approved sender identity. CampaignHub must indicate whether required verification or authentication is incomplete and prevent unauthorized sending.

### AC-CH-002 — Configure WhatsApp

An owner must be able to connect the supported official WhatsApp business provider and see:

- Connection status
- Business or sender identity
- Available templates
- Template approval status
- Actionable setup or provider errors

### AC-CH-003 — Detect channel unavailability

If a channel is disconnected, unapproved, suspended, or otherwise unavailable, CampaignHub must prevent affected publication or clearly route the contact through an explicitly configured alternative path.

### AC-CH-004 — Protect credentials

Channel credentials and secrets must not be displayed after entry, exposed in exports, included in ordinary logs, or made available to unauthorized roles.

## 9. Message composition

### AC-MC-001 — Create email content

A marketer must be able to create an email containing:

- Sender identity
- Subject
- Preview text
- Responsive message body
- Links
- Supported personalization fields
- Required unsubscribe mechanism

### AC-MC-002 — Create WhatsApp content

A marketer must be able to select an approved template, supply required variables, and preview the resulting WhatsApp message.

The system must prevent publication when required variables are missing or invalid.

### AC-MC-003 — Resolve personalization

Before sending, CampaignHub must detect missing personalization data. Users must be able to define a safe fallback or exclude affected contacts.

No recipient should receive an unresolved template token.

### AC-MC-004 — Send a test

An authorized user must be able to send or render a representative test without enrolling production contacts or corrupting campaign reporting.

### AC-MC-005 — Preview by channel

The user must be able to preview the material differences between email and WhatsApp content before publication.

## 10. One-time campaigns

### AC-CP-001 — Save a draft

An authorized user must be able to save an incomplete campaign as a draft without making it eligible for sending.

### AC-CP-002 — Complete preflight review

Before publication, CampaignHub must present:

- Selected audience
- Reachable recipients by channel
- Exclusion counts and reasons
- Message previews
- Sender and channel status
- Schedule and timezone
- Estimated maximum charge
- Blocking errors and non-blocking warnings

### AC-CP-003 — Require explicit confirmation

No production campaign may be published solely because content was saved, previewed, or generated by AI. An authorized user must explicitly confirm publication after preflight review.

### AC-CP-004 — Schedule a campaign

Users must be able to schedule a campaign for a future time in the workspace timezone. The interface must display the resolved date, time, and timezone before confirmation.

### AC-CP-005 — Prevent duplicate dispatch

Retries, refreshes, or repeated provider callbacks must not cause the same campaign message to be intentionally dispatched more than once to the same recipient and channel unless the campaign explicitly permits repetition.

### AC-CP-006 — Report campaign state

CampaignHub must expose at least these states:

- Draft
- Scheduled
- Sending
- Completed
- Cancelled
- Failed

Partial failures must not be presented as complete success.

## 11. Automation builder and execution

### AC-AU-001 — Build a pilot workflow

An authorized user must be able to create a workflow containing:

- One entry trigger
- One or more delays
- At least one condition or branch
- Email or WhatsApp actions
- A conversion goal
- An exit condition

### AC-AU-002 — Validate before activation

CampaignHub must prevent activation when a workflow has unreachable steps, invalid configuration, disconnected channels, unavailable templates, missing message content, or no valid entry path.

### AC-AU-003 — Simulate a workflow

Before activation, the user should be able to inspect how representative contacts would move through the workflow without dispatching production messages or incurring provider costs.

If full simulation is unavailable for the pilot, a documented staff-assisted verification process must exist.

### AC-AU-004 — Activate explicitly

Saving or testing a workflow must not activate it. Activation requires an authorized user to review expected audience behavior, channel readiness, and cost controls and then confirm activation.

### AC-AU-005 — Enroll eligible contacts

Only contacts satisfying the entry trigger, consent, suppression, and frequency rules may enter or continue through the workflow.

### AC-AU-006 — Pause safely

An authorized user must be able to pause new dispatches from an automation. CampaignHub must explain what happens to contacts already waiting in the workflow.

### AC-AU-007 — Handle retries safely

Temporary failures may be retried according to a documented policy. Retries must not create duplicate intentional sends or bypass current consent and suppression checks.

### AC-AU-008 — Record progression

CampaignHub must record meaningful workflow events, including enrollment, step entry, waiting, dispatch, exclusion, conversion, exit, and failure.

### AC-AU-009 — Protect active workflows from edits

Editing an active workflow must not silently or unpredictably change the treatment of enrolled contacts. The system must either version changes or explicitly explain their effect before applying them.

## 12. Starter journeys

### AC-JR-001 — Welcome journey

The starter welcome journey must:

- Enroll a newly eligible contact from a supported source.
- Verify channel consent before each send.
- Send at least one welcome message.
- Support a configurable delay and follow-up.
- Stop when the contact opts out or reaches the defined goal.

### AC-JR-002 — Abandoned-cart journey

The abandoned-cart journey must:

- Receive a supported cart-abandonment event.
- Associate the event with the correct workspace and contact.
- Avoid enrollment after a qualifying purchase.
- Wait for the configured delay.
- Recheck consent, suppression, purchase, and frequency conditions before sending.
- Include a working cart-recovery destination where supplied.
- Exit after purchase, opt-out, expiration, or the configured final step.

### AC-JR-003 — Post-purchase journey

The post-purchase journey must:

- Receive a supported qualifying purchase event.
- Avoid duplicate enrollment for the same order.
- Support a configurable delay.
- Send only through consented channels.
- Record the order reference without exposing unnecessary payment data.
- Exit after completing its configured steps or upon opt-out.

## 13. Integrations

### AC-IN-001 — WooCommerce connection

The pilot must be able to establish an authorized connection to the supported WooCommerce version and identify the connected store and workspace.

If WooCommerce is replaced before implementation, this criterion must be updated rather than silently reinterpreted.

### AC-IN-002 — Commerce event ingestion

CampaignHub must ingest the minimum events and fields needed for the selected pilot journeys, including stable identifiers, event time, customer reference, order or cart reference, and relevant monetary values.

### AC-IN-003 — Idempotent event processing

Receiving the same integration event more than once must not create duplicate contacts, orders, conversions, or automation enrollment where duplication is not intended.

### AC-IN-004 — Webhook fallback

An authorized integration must be able to submit a documented pilot event. Invalid authentication, malformed payloads, unknown workspaces, and unsupported event types must be rejected safely and reported actionably.

### AC-IN-005 — Integration health

Authorized users must be able to see:

- Connection status
- Last successful activity
- Most recent relevant error
- Whether intervention is required
- Available recovery action

### AC-IN-006 — Paystack integration

If Paystack supplies purchase or billing events during the pilot, CampaignHub must verify event authenticity, process supported events safely, and avoid duplication.

The exact role of Paystack—CampaignHub billing, merchant conversion data, or both—must be decided before implementation.

## 14. Cost and spending controls

### AC-CO-001 — Estimate campaign cost

Before a campaign is published, CampaignHub must show an estimated maximum cost based on reachable recipients, selected channels, and known provider pricing.

The estimate must identify material uncertainty rather than imply false precision.

### AC-CO-002 — Estimate automation exposure

Before activating an automation, CampaignHub must explain that total future cost depends on enrollment volume and show the configured spending or balance controls.

### AC-CO-003 — Enforce spending limits

When a configured spending or balance limit would be exceeded, CampaignHub must stop or defer affected sends according to a documented policy and notify an appropriate user.

### AC-CO-004 — Never conceal provider charges

Usage history must distinguish platform fees, message usage, credits, adjustments, and known provider-driven charges sufficiently for a pilot participant to reconcile their balance.

### AC-CO-005 — Handle insufficient credit

Insufficient credit must not produce an unexplained workflow failure. CampaignHub must preserve the affected contact state, record the reason, notify an appropriate user, and provide a safe recovery path.

## 15. Reporting and attribution

### AC-RP-001 — Delivery reporting

CampaignHub must report attempted, accepted or sent, delivered where available, failed, and suppressed counts without combining distinct states misleadingly.

### AC-RP-002 — Engagement reporting

CampaignHub must report available clicks, measured opens, WhatsApp reads, and replies while explaining that channel availability and reliability differ.

### AC-RP-003 — Conversion reporting

When a supported conversion event occurs within the documented attribution window, CampaignHub must associate it with eligible campaign or automation activity using a defined, reproducible method.

### AC-RP-004 — Revenue reporting

Attributed revenue must show its currency, attribution rule, and included transactions. Estimated or inferred values must be labeled.

### AC-RP-005 — Cost reporting

Users must be able to review message usage and known cost by campaign, automation, channel, and reporting period.

### AC-RP-006 — Journey reporting

For each automation, users must be able to see at least:

- Enrolled contacts
- Contacts currently waiting or active
- Sends and failures by step
- Exclusions and exits
- Recorded goals or conversions
- Attributed revenue and known cost

## 16. Notifications and recovery

### AC-NT-001 — Notify critical operational failures

Appropriate users must receive an in-product or supported external notification for:

- Disconnected channels
- Integration failures affecting journey execution
- Exhausted credits or enforced spending limits
- Failed campaign execution
- Unavailable WhatsApp templates
- Material bounce or complaint concerns

### AC-NT-002 — Provide actionable errors

An operational error must identify the affected workspace resource, explain the user-visible consequence, and provide a recovery action when one exists. Secrets and unnecessary customer data must not appear in the message.

### AC-NT-003 — Preserve recoverable state

A temporary provider or integration failure must not silently discard a scheduled campaign, enrolled contact, or relevant commerce event.

## 17. Audit, safety, and abuse controls

### AC-SA-001 — Record sensitive actions

CampaignHub must record at least:

- Workspace role changes
- Channel connection changes
- Contact imports and exports
- Campaign publication and cancellation
- Automation activation, pause, and material edits
- Spending-limit changes
- Administrative suspension or restoration

Each record must include time, workspace, action, target, and actor where available.

### AC-SA-002 — Verify senders

Unverified or unauthorized sender identities must not be used for production dispatch.

### AC-SA-003 — Apply frequency controls

The pilot must provide a documented method to prevent a contact from receiving an unreasonable number of marketing messages across active campaigns and automations.

### AC-SA-004 — Suspend sending safely

Authorized internal operators must be able to suspend sending for a risky or compromised workspace without deleting its customer data or falsifying campaign history.

### AC-SA-005 — Protect against accidental sends

Production publication must require a preflight review and explicit confirmation. Test actions must be visually and behaviorally distinct from production actions.

## 18. Data lifecycle

### AC-DL-001 — Export pilot data

An authorized owner must be able to obtain contacts, consent history, and essential campaign or automation results in a usable format through a product or documented assisted process.

### AC-DL-002 — Disconnect integrations

An owner must be able to disconnect a commerce or messaging integration. CampaignHub must explain the effect on active campaigns and automations before applying the change.

### AC-DL-003 — Handle deletion requests

The pilot must have a documented, auditable process for workspace and contact deletion requests, including applicable retention exceptions. Exact retention periods require legal review.

### AC-DL-004 — Minimize sensitive data

CampaignHub must not require storage of full payment credentials or unrelated commerce data to run the selected journeys and reports.

## 19. AI-assisted drafting

AI assistance is optional for pilot readiness. If included, it must meet these criteria.

### AC-AI-001 — Generate editable drafts

AI output must be presented as a draft that the user can inspect and edit.

### AC-AI-002 — Prevent autonomous publication

AI generation must not publish, schedule, activate, or dispatch a production campaign or workflow.

### AC-AI-003 — Minimize submitted data

The generation process must avoid sending full contact lists, unnecessary personal data, credentials, or payment details to an AI provider.

### AC-AI-004 — Communicate limitations

CampaignHub must tell users to review generated claims, prices, links, tone, personalization, and compliance before publication.

## 20. Pilot operational readiness

Before admitting the first external participant, the team must have:

- A named pilot owner
- A participant onboarding checklist
- A channel and sender-approval checklist
- A consent-review procedure
- A support and escalation path
- A provider-incident response procedure
- A spending and credit recovery procedure
- A data-export and deletion procedure
- An abuse-reporting process
- A method for recording onboarding and support time
- A method for collecting baseline and outcome metrics

Manual procedures are acceptable where explicitly documented, secure, repeatable, and visible to the pilot team.

## 21. Pilot entry gate

A business may enter the live pilot only when:

- Its owner or authorized representative has accepted the pilot terms.
- The business satisfies the participant criteria.
- Its customer-data source has been reviewed.
- The required channel permission and consent process has been reviewed.
- Sender identities and required WhatsApp templates are approved.
- At least one supported integration or import path works.
- A starter journey passes a test or assisted simulation.
- Cost and spending controls are configured.
- A CampaignHub operator has confirmed operational readiness.

## 22. Pilot exit measures

At the end of the six-week pilot, report:

- Setup completion rate
- Automation activation rate
- Median time to activation
- Four-week automation retention
- Measurable outcomes by journey and participant
- Paid-continuation acceptance
- Support and onboarding time per participant
- Provider and infrastructure cost by participant
- Consent, unsubscribe, complaint, and delivery issues
- Critical defects and manual interventions
- Assumptions supported, disproved, or still untested

The product decision should be recorded as one of:

- **Proceed:** Evidence supports the segment, proposition, and economic direction.
- **Narrow:** Evidence supports a smaller segment, channel set, or use case.
- **Pivot:** A materially different proposition has stronger evidence.
- **Stop:** Evidence does not support continued investment in the current direction.

## 23. Unresolved acceptance dependencies

These items must be resolved before implementation can be considered fully specified:

- Final pilot channels
- Messaging providers and their supported event semantics
- Whether WooCommerce remains the first native commerce integration
- Exact use of Paystack
- Attribution window and precedence rules
- Consent language and retention requirements following legal review
- Pilot pricing and usage-credit model
- Cross-channel frequency policy
- Expected provider retry behavior
- Required pilot uptime and support hours

## 24. Definition of pilot-ready

CampaignHub is pilot-ready only when:

1. Every applicable **must** criterion passes in a representative staging environment.
2. Any unmet **should** criterion has an approved, documented manual procedure.
3. No unresolved defect risks workspace isolation, consent enforcement, duplicate dispatch, uncontrolled spending, credential exposure, or misleading reporting.
4. External channel and commerce connections have been tested with real provider sandbox or approved test facilities.
5. Pilot operations, support, data handling, and incident procedures are documented.
6. An authorized product owner explicitly approves admitting the first participant.
