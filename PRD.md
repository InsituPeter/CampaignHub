# CampaignHub Product Requirements Document

**Status:** Discovery draft  
**Market:** Nigeria-first, extensible to other African markets  
**Primary audience:** Nigerian small businesses, initially online retailers  
**Secondary audiences:** Agencies and solo creators  
**Evidence status:** Provisional; requires customer validation

## 1. Product summary

CampaignHub is a Nigeria-first marketing automation platform that unifies email, SMS, and WhatsApp customer journeys in one workspace.

It helps small businesses replace disconnected tools and manual follow-ups with visual automation, AI-assisted content creation, transparent naira pricing, local integrations, and outcome-focused reporting.

## 2. Problem statement

Nigerian small businesses commonly manage customer communication across spreadsheets, email platforms, SMS providers, and WhatsApp. This creates several problems:

- Customer data is fragmented.
- Follow-ups are inconsistent and manual.
- International tools become expensive as contact lists grow.
- Foreign-currency billing is inconvenient and unpredictable.
- Cross-channel journeys are difficult to coordinate.
- Automation tools are either limited or difficult to configure.
- Campaign costs and business results are difficult to measure.
- Local onboarding and support are limited.

## 3. Product vision

Enable small businesses to create effective customer journeys across the channels their customers use, without requiring specialist marketing knowledge or several disconnected products.

## 4. Value proposition

> Run coordinated email, SMS, and WhatsApp customer journeys from one affordable platform, priced in naira and built for Nigerian businesses.

CampaignHub competes on better value, automation quality, ease of use, local relevance, and dependable support—not solely on price.

## 5. Product personality

CampaignHub should feel:

- Approachable
- Dependable
- Locally aware
- Transparent
- Quietly powerful
- Practical rather than gimmicky

The interface should use plain language and clearly explain channel restrictions, consent, and costs.

## 6. Target customers

### Primary customer

The provisional primary customer is an owner-led Nigerian online retailer that:

- Has approximately 500–20,000 customer records
- Sells through an online store, social media, or WhatsApp
- Has a small team and no automation specialist
- Uses Paystack, Flutterwave, or comparable payment services
- Currently relies on manual or disconnected marketing tools
- Wants more completed and repeat purchases
- Needs predictable costs and accessible support

### Primary job to be done

> Help me follow up with customers consistently across the channels they use, without hiring a marketing specialist or juggling several tools.

### Secondary customers

- Agencies managing multiple small-business clients
- Solo creators who need simple broadcasts and audience nurturing

## 7. Product goals

CampaignHub should enable customers to:

- Consolidate customer and consent data.
- Launch campaigns across email, SMS, and WhatsApp.
- Automate repeatable customer journeys.
- Reduce manual marketing work.
- Understand campaign costs before sending.
- Attribute meaningful customer actions and revenue.
- Respect channel-specific consent.
- Begin receiving value within one guided session.

## 8. Non-goals

The initial product is not intended to be:

- A full sales CRM
- A transactional email service
- A social-media publishing suite
- A website or online-store builder
- A customer-support platform
- An enterprise marketing cloud
- An autonomous AI marketing operator
- A native mobile application

## 9. Product principles

CampaignHub should be:

- Outcome-oriented rather than feature-heavy
- Simple for beginners without limiting experienced marketers
- Transparent about costs
- Explicit about consent
- Cross-channel by design
- Helpful without allowing AI to act recklessly
- Locally relevant but globally extensible

## 10. Core user roles

### Owner

Controls billing, workspace settings, channels, team access, data exports, and sensitive actions.

### Marketer

Manages contacts, segments, campaigns, templates, automations, and reports.

### Analyst

Views reports and exports permitted analytics without publishing campaigns.

### Agency manager

Manages isolated client workspaces, reusable templates, client access, reporting, and billing arrangements.

The first release should use preset roles rather than custom permission builders.

## 11. Core product model

CampaignHub revolves around five connected objects:

1. **Contacts:** People a business communicates with.
2. **Segments:** Dynamic groups based on attributes or behavior.
3. **Campaigns:** One-time broadcasts.
4. **Automations:** Ongoing customer journeys triggered by events.
5. **Channels:** Connected email, SMS, and WhatsApp services.

A single customer timeline should show messages, delivery events, clicks, replies, purchases, consent changes, and automation activity.

## 12. Core user journeys

### Initial activation

1. Create a workspace.
2. Select a business objective.
3. Import contacts or connect a customer source.
4. Confirm consent and field mapping.
5. Connect at least one messaging channel.
6. Select a starter journey.
7. Customize or generate content.
8. Preview the journey and estimated cost.
9. Test and publish.
10. Review the first meaningful outcome.

The desired first-day outcome is:

> I connected my customers, launched a professional multi-channel journey, and understood exactly what it would cost—without needing a marketing specialist.

### Campaign broadcast

1. Choose a goal.
2. Select a segment.
3. Select one or more channels.
4. Create content.
5. Preview personalized messages.
6. Review consent exclusions and costs.
7. Test, schedule, or publish.
8. Review delivery, engagement, conversion, and cost.

### Automation creation

1. Select a template or describe an objective.
2. Choose an entry trigger.
3. Add delays, conditions, and actions.
4. Define goals and exit conditions.
5. Apply frequency and spending controls.
6. Simulate the workflow.
7. Activate it.
8. Monitor progression and outcomes.

## 13. Initial automation templates

The complete product vision includes:

- Welcome journey
- Abandoned-cart recovery
- Post-purchase follow-up
- Promotional broadcast
- Inactive-customer re-engagement

A smaller private pilot may begin with welcome, abandoned-cart, and post-purchase journeys.

## 14. Functional requirements

### 14.1 Accounts and workspaces

CampaignHub must allow users to:

- Register and create a business workspace.
- Invite and remove team members.
- Assign preset roles.
- Switch between authorized workspaces.
- Configure business identity, timezone, currency, and contact details.
- View security and activity history.
- Export workspace data or request deletion.

Agency accounts must manage isolated client workspaces without mixing contacts, campaigns, billing, or analytics.

### 14.2 Contact management

CampaignHub must support:

- Manual contact creation
- CSV import and export
- Field mapping and validation
- Custom profile fields
- Tags
- Dynamic segments
- Duplicate management
- Contact activity timelines
- Suppression lists
- Search, filtering, archiving, and merging

### 14.3 Consent management

CampaignHub must:

- Track email, SMS, and WhatsApp permission separately.
- Record consent source and timestamp.
- Preserve opt-out history.
- Apply suppression immediately.
- Prevent sends to ineligible contacts.
- Explain why contacts were excluded.
- Include consent records in data exports.

Consent on one channel must not imply consent on another. Imported contacts must require confirmation that the business has permission to contact them.

### 14.4 Segmentation

Users must be able to create dynamic segments using:

- Profile attributes
- Tags
- Location
- Purchase behavior
- Campaign activity
- Channel availability and consent
- Automation participation
- Engagement or inactivity periods

Before sending, CampaignHub should estimate the reachable audience by channel and explain exclusions.

### 14.5 Campaign management

Users must be able to:

- Create channel-specific or coordinated broadcasts.
- Target segments or selected contacts.
- Personalize messages with contact and business fields.
- Save and reuse templates.
- Send tests and preview content.
- Schedule campaigns in the workspace timezone.
- See estimated reach and cost.
- Review warnings before publication.
- Clone, cancel, and archive campaigns where applicable.

Campaign states should include draft, scheduled, sending, completed, paused, cancelled, and failed.

### 14.6 Automation builder

The builder must support:

- Event, date, segment, and manual-entry triggers
- Delays and scheduled sending windows
- Conditional branches
- Email, SMS, and WhatsApp actions
- Tags and contact-field updates
- Webhook actions
- Conversion goals
- Exit conditions
- Frequency limits
- Test and simulation modes
- Draft, active, paused, and archived states
- Safe handling of edits to active workflows

### 14.7 AI assistance

AI should be able to:

- Draft campaign copy and subject lines.
- Adapt content between channels.
- Generate a draft workflow from an objective.
- Suggest audience segments and timing.
- Explain performance patterns.
- Identify obvious content or compliance concerns.

All generated output must remain editable and require human approval before publication. Sensitive customer data should not be inserted into AI prompts unnecessarily.

### 14.8 Forms and lead capture

Users should be able to:

- Create embedded and hosted signup forms.
- Select fields and channel permissions.
- Configure confirmation and success messages.
- Apply tags or enter contacts into automations.
- Prevent obvious bot submissions.
- Track consent sources.
- Apply basic brand styling.

Advanced landing-page creation remains outside the initial scope.

### 14.9 Analytics

CampaignHub must report:

- Attempted, delivered, and failed messages
- Suppressed recipients
- Measured email opens
- Clicks
- Replies where available
- Unsubscribes and complaints
- Conversion events
- Attributed revenue
- Journey progression and drop-off
- Channel and campaign costs
- Cost per conversion
- Performance by channel and segment

The interface must distinguish confirmed results from estimates and attribution assumptions. Opens should not be treated as definitive; clicks, replies, conversions, and attributed revenue are stronger outcome measures.

### 14.10 Billing and usage

CampaignHub must support:

- Naira subscriptions
- Prepaid or metered channel usage
- Invoices and usage history
- Channel-specific credit balances and charges
- Low-balance notifications
- Spending limits
- Campaign cost estimates
- Plan changes
- Payment-failure handling
- Optional automatic credit replenishment after launch

Campaigns and automations must not generate uncontrolled or concealed charges.

### 14.11 Integrations

Initial priorities are:

1. CSV import and export
2. Webhooks and documented API access
3. Paystack and Flutterwave
4. One initial commerce platform
5. Additional Shopify and WooCommerce coverage
6. Forms and a general integration platform

Each integration must expose its connection status, synchronization history, errors, and recovery instructions.

### 14.12 Notifications

CampaignHub should notify appropriate users about:

- Campaign completion or failure
- Automation errors
- Integration failures
- Low credit balances
- Unusual spending
- Sender or domain problems
- WhatsApp template decisions
- High bounce or complaint rates
- Subscription and payment issues

Users may configure routine notifications, but critical security and compliance notices cannot be disabled.

## 15. Channel requirements

### 15.1 Email

- Beginner-friendly visual editor
- Responsive templates
- Subject and preview text
- Personalization
- Plain-text fallback
- Link validation
- Sender-domain authentication guidance
- Unsubscribe controls
- Bounce and complaint handling
- Delivery and engagement reporting

Advanced custom HTML may be deferred or restricted to trusted accounts.

### 15.2 SMS

- Character and message-part counting
- Destination-aware cost estimates
- Sender identity management
- Country and number validation
- Delivery-status reporting
- Opt-out controls
- Link shortening with click measurement
- Transparent multipart-message charges
- Controls for prohibited or risky content

### 15.3 WhatsApp

- Official business-account connection
- Template creation and status tracking
- Approved-template selection
- Variable validation
- Media where supported
- Conversation-window awareness
- Delivery, read, and reply events where available
- Clear provider and platform charges
- Opt-out and suppression controls
- Plain-language explanations of restrictions and provider errors

## 16. Trust, safety, and privacy

CampaignHub must provide:

- Sender verification
- Appropriate domain authentication
- Test sends
- Confirmation for sensitive actions
- Frequency caps
- Spending controls
- Suppression enforcement
- Audit logs
- Risk-based account and campaign review
- Abuse reporting and response
- Workspace data isolation
- Export and deletion controls
- Data minimization for AI features

Internal operators need auditable tools to review risky accounts, suspend sending without destroying customer data, diagnose provider failures, manage complaints, apply limits, and assist with billing disputes.

Formal legal and compliance review remains necessary.

## 17. Nigeria-first requirements

The product should prioritize:

- Naira pricing
- Paystack and Flutterwave billing
- Nigerian business examples and templates
- Local onboarding and support
- Mobile-friendly workflows
- Good performance on slower connections
- Nigeria Data Protection Act alignment
- Local sender-name and messaging-provider realities
- Expansion-ready country and currency models

Nigeria-first describes initial product relevance rather than a permanent geographic boundary.

## 18. Commercial model

CampaignHub should use three revenue components:

1. **Platform subscription:** Contact management, automation, reporting, support, and workspace access.
2. **Message usage:** Separately metered SMS and WhatsApp costs, with a generous bundled email allowance.
3. **Premium capabilities:** Agency workspaces, larger AI allowances, advanced reporting, and priority support.

Provisional plans are:

- **Starter:** Small contact allowance, one workspace, broadcasts, basic automation, and standard reporting.
- **Growth:** Higher allowances, full cross-channel automation, commerce integrations, advanced segmentation, and conversion reporting.
- **Agency:** Multiple client workspaces, reusable templates, client controls, branded reporting, and consolidated administration.

The initial acquisition offer should be a guided 14-day trial rather than a permanently free plan. Exact prices and plan limits remain subject to customer research and provider economics.

## 19. Success metrics

### Primary metric

Businesses with at least one active automation producing a measurable customer action each month.

### Supporting metrics

- Onboarding completion
- Time to first campaign
- Time to first active automation
- Automation retention
- Delivery and failure rates
- Conversions and attributed revenue
- Cost per conversion
- Customer retention
- Plan expansion
- Support effort per customer
- Consent, unsubscribe, and complaint rates

## 20. Private pilot

### Objective

Determine whether Nigerian online retailers can activate and benefit from cross-channel automation and whether they will pay to continue using it.

### Participants

Recruit 8–12 businesses that:

- Sell physical or digital products online.
- Have at least 500 usable customer records.
- Generate enough orders to produce measurable events.
- Currently use email, SMS, or WhatsApp manually.
- Can identify how customer consent was obtained.
- Have someone responsible for marketing.
- Will attend onboarding and feedback sessions.

### Duration

Run the pilot for six weeks:

1. **Week 1:** Baseline interview, data review, channel setup, and onboarding.
2. **Week 2:** Activate the first workflow.
3. **Weeks 3–5:** Observe performance and conduct weekly check-ins.
4. **Week 6:** Review outcomes, test pricing, and decide whether to continue.

### Pilot success criteria

Promising evidence would include:

- At least 75% complete setup.
- At least 60% activate an automation.
- Median activation time remains below one working day, excluding external provider approval.
- Most active participants keep an automation running for four weeks.
- At least half of active participants produce an agreed measurable outcome.
- At least 40% accept a real paid-continuation offer.
- Routine campaigns do not require continuous CampaignHub staff intervention.

These thresholds are provisional learning criteria, not forecasts.

## 21. MVP prioritization

### Launch-critical

- Accounts and single-business workspaces
- Owner and marketer roles
- CSV contact import
- Contact profiles, tags, segments, and suppression
- Channel-specific consent records
- Email, SMS, and WhatsApp broadcasts
- Basic templates and personalization
- Test sends, scheduling, previews, and cost confirmation
- Visual automation with triggers, delays, branches, and message actions
- Initial automation templates
- One payment integration
- Webhooks and one commerce integration
- Basic delivery, engagement, conversion, and cost reporting
- Naira subscriptions and message credits
- Spending limits and low-balance warnings
- Sender verification, audit logs, and abuse controls

### Shortly after launch

- Full agency workspace management
- Additional commerce and form integrations
- AI-generated campaign and workflow drafts
- Reusable templates across workspaces
- Advanced segmentation
- Improved revenue attribution
- Branded agency reports
- Automatic credit replenishment
- Workflow simulation and version management

### Future scope

- Transactional messaging
- Predictive optimization
- Autonomous campaign management
- Custom permission builders
- Enterprise single sign-on
- Native mobile applications
- Large integration marketplace
- Full CRM functionality
- Social-media publishing
- Customer-service inbox

### Recommended pilot reduction

Even the launch-critical scope is ambitious. A safer private pilot should support:

- One primary customer segment
- One commerce integration
- Email and WhatsApp initially
- SMS as the first additional channel
- Three automation templates
- Essential reporting
- Manually assisted onboarding

## 22. Principal risks

- Businesses may lack valid consent records.
- WhatsApp restrictions or costs may weaken the value proposition.
- Supporting three channels may create excessive complexity.
- Messaging margins may not support local pricing.
- Deliverability and sender reputation may be difficult to maintain.
- Customers may value broadcasts but not automation.
- Integrations may require disproportionate support.
- Assisted onboarding may prevent scalable economics.
- Attribution may overstate campaign value.
- The CampaignHub name may be unavailable or insufficiently distinctive.

## 23. Assumptions requiring validation

- Nigerian small businesses find current tools too expensive or fragmented.
- They want email, SMS, and WhatsApp in one workflow.
- They will pay for automation rather than continue with manual processes.
- Online retailers are the strongest initial segment.
- Naira billing and local support materially influence purchasing.
- Businesses can supply properly consented contact data.
- WhatsApp automation provides enough value despite its restrictions and costs.

## 24. Open decisions

- Final product name
- First commerce integration
- Initial pilot channel combination
- Exact pricing and plan limits
- Messaging and infrastructure providers
- Attribution methodology
- Required campaign approval workflows
- Data-retention periods
- Pilot recruitment source
- Geographic expansion order
- Whether replies require an inbox experience
- Precise MVP boundary after customer validation

## 25. Readiness assessment

This PRD establishes a coherent product hypothesis, but it is not yet build-ready. Advancement should require:

- Customer interviews
- Messaging-provider and cost research
- Consent and regulatory review
- Pilot partner identification
- Pricing tests
- Technical feasibility assessment
- Acceptance criteria for the selected pilot scope

System design, implementation estimates, and technology decisions should follow validation and explicit approval to proceed.
