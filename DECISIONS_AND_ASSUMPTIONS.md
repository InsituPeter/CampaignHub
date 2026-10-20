# CampaignHub Decisions and Assumptions Register

**Status:** Discovery draft  
**Last updated:** 2026-08-31  
**Purpose:** Separate deliberate product choices from hypotheses that still require evidence.

## Status definitions

- **Decided:** Current direction unless new evidence justifies revisiting it.
- **Untested:** Plausible working assumption with no direct evidence yet.
- **Supported:** Evidence currently favors the assumption, but it remains reviewable.
- **Disproved:** Evidence contradicts the assumption.
- **Replaced:** A later decision or assumption supersedes it.
- **Open:** A material choice has not yet been made.

## Evidence standard

An assumption does not become supported merely because it appears in the PRD or this register. Status changes should cite customer interviews, observed behavior, pilot data, provider documentation, financial analysis, legal advice, or another identifiable source.

## Product decisions

| ID | Decision | Status | Rationale | Revisit when |
|---|---|---|---|---|
| D-001 | Position CampaignHub as a better-value platform rather than the cheapest option. | Decided | Strong automation, transparency, and local support are more defensible than price alone. | Pricing research shows customers purchase almost entirely on price. |
| D-002 | Launch Nigeria-first while preserving the ability to expand into other African markets. | Decided | Local billing, support, and channel realities provide a focused initial proposition. | Expansion planning begins or local constraints undermine viability. |
| D-003 | Make small businesses the primary audience. | Decided | They offer a useful balance of meaningful needs, willingness to pay, and manageable complexity. | Discovery identifies a stronger initial buyer. |
| D-004 | Treat agencies and solo creators as secondary audiences. | Decided | Both can use the core platform, but designing equally for all three would dilute the initial experience. | Core merchant experience is validated. |
| D-005 | Use online retailers as the provisional entry segment. | Decided | Commerce events make automation outcomes easier to demonstrate and measure. | Customer research or pilot recruitment contradicts the choice. |
| D-006 | Maintain an omnichannel product vision covering email, SMS, and WhatsApp. | Decided | Nigerian businesses and their customers use all three channels for different purposes. | Provider economics or platform restrictions make a channel impractical. |
| D-007 | Use one shared visual workflow across channels. | Decided | Cross-channel coordination is a central product differentiator. | Usability testing shows separate workflows are substantially clearer. |
| D-008 | Keep transactional messaging outside the initial scope. | Decided | It introduces distinct reliability, API, compliance, and operational requirements. | Marketing automation is stable and customers demonstrate demand. |
| D-009 | Require human approval before AI-generated content or workflows are published. | Decided | It reduces brand, cost, compliance, and safety risks. | Governance and product maturity support carefully bounded automation. |
| D-010 | Use preset roles before introducing custom permission builders. | Decided | Preset roles cover initial needs with less product complexity. | Agency or larger-team research demonstrates insufficient control. |
| D-011 | Keep subscription charges separate from variable channel usage. | Decided | SMS and WhatsApp provider costs vary and should remain transparent. | Unit economics support a simpler bundled model. |
| D-012 | Prefer a guided trial over a permanently free plan initially. | Decided | The product requires setup and should test willingness to pay early. | Acquisition research demonstrates a sustainable free-plan advantage. |

## Market assumptions

| ID | Assumption | Status | Validation method | Success signal |
|---|---|---|---|---|
| A-M01 | Nigerian small businesses experience material cost or billing friction with international marketing platforms. | Untested | Customer interviews and current-tool cost review | At least half of suitable interviewees independently identify the issue as important. |
| A-M02 | Online retailers are the strongest initial segment. | Untested | Compare interviews, urgency, data readiness, and willingness to pay across segments | Retailers show the clearest repeated need and strongest pilot participation. |
| A-M03 | Naira pricing materially increases purchase confidence. | Untested | Pricing interviews and offer testing | Prospects identify local pricing as a meaningful purchase factor. |
| A-M04 | Local onboarding and support influence selection and retention. | Untested | Interview questions and pilot support analysis | Customers use and value support enough to affect continuation. |
| A-M05 | The product can expand into other African markets without redesigning its core model. | Untested | Comparative regulatory, currency, provider, and workflow research | Core contact, consent, billing, and channel abstractions remain applicable. |

## Customer assumptions

| ID | Assumption | Status | Validation method | Success signal |
|---|---|---|---|---|
| A-C01 | Target merchants commonly have at least 500 usable customer records. | Untested | Recruiting questionnaire and data audit | Most qualified pilot candidates meet the threshold. |
| A-C02 | Customer data is fragmented across stores, spreadsheets, payment platforms, and messaging tools. | Untested | Workflow interviews and artifact walkthroughs | Repeated evidence of two or more disconnected sources. |
| A-C03 | Merchants regularly perform valuable follow-ups manually or fail to perform them. | Untested | Recent-campaign walkthroughs | Repeated manual work or documented missed follow-ups. |
| A-C04 | Merchants prioritize conversions and repeat purchases over opens or message volume. | Untested | Outcome-ranking exercise | Conversion or retention outcomes consistently rank highest. |
| A-C05 | A meaningful portion of target businesses have incomplete consent records. | Untested | Consent-process interviews and pilot data review | Multiple qualified participants cannot demonstrate complete channel consent. |
| A-C06 | Customers will trust CampaignHub with contact and commerce data. | Untested | Pilot consent, security objections, and connection completion | Most pilot participants connect a real data source. |

## Product assumptions

| ID | Assumption | Status | Validation method | Success signal |
|---|---|---|---|---|
| A-P01 | A unified email, SMS, and WhatsApp workflow is more valuable than separate tools. | Untested | Concept testing and workflow comparison | Participants prefer and use coordinated journeys. |
| A-P02 | Abandoned-cart recovery is the strongest initial use case. | Untested | Rank use cases and observe pilot selection | It is frequently selected and produces measurable outcomes. |
| A-P03 | Email and WhatsApp are the best two channels for the initial pilot. | Untested | Channel-use interviews and provider feasibility research | They provide sufficient reach, demand, and viable economics. |
| A-P04 | SMS is the most valuable third channel. | Untested | Interview ranking and pilot follow-up | Participants request SMS after using the initial channels. |
| A-P05 | Guided templates activate users faster than a blank workflow canvas. | Untested | Prototype usability tests | Most participants publish successfully from a template with limited assistance. |
| A-P06 | Users want AI to prepare drafts but not publish autonomously. | Untested | Interviews and prototype tests | Users accept drafts while retaining review and approval. |
| A-P07 | Assisted onboarding is acceptable during the pilot. | Untested | Recruitment and attendance tracking | Participants attend setup and complete activation. |
| A-P08 | Customers can understand channel consent when it is explained in plain language. | Untested | Onboarding usability tests | Participants correctly configure consent without repeated intervention. |
| A-P09 | Showing maximum estimated cost before publishing reduces usage anxiety. | Untested | Usability interviews and publication behavior | Participants understand charges and proceed without unexpected-cost complaints. |

## Integration assumptions

| ID | Assumption | Status | Validation method | Success signal |
|---|---|---|---|---|
| A-I01 | CSV import and webhooks provide adequate fallback coverage during the pilot. | Untested | Pilot onboarding | Unsupported merchants can still activate a useful journey. |
| A-I02 | WooCommerce should be the first commerce integration. | Untested | Candidate platform survey and integration feasibility study | It covers the largest practical share of pilot candidates at acceptable cost. |
| A-I03 | Shopify should follow WooCommerce. | Untested | Candidate survey and market research | Demand justifies it as the next native integration. |
| A-I04 | Paystack should be the first local billing and payment-data integration. | Untested | Customer usage survey and API feasibility review | It provides the greatest initial coverage and required events. |
| A-I05 | Flutterwave should follow Paystack. | Untested | Customer usage survey | Material customer demand remains after Paystack support. |
| A-I06 | Official messaging providers expose the required templates, status events, opt-outs, and cost information. | Untested | Provider documentation and technical spike | Required operations are feasible with acceptable reliability and economics. |

## Commercial assumptions

| ID | Assumption | Status | Validation method | Success signal |
|---|---|---|---|---|
| A-B01 | Customers will accept a platform subscription plus separately billed messaging usage. | Untested | Package-choice and paid-pilot tests | Prospects understand and accept the model. |
| A-B02 | A guided trial will convert better than a permanent free plan. | Untested | Initial cohort conversion and later offer tests | Guided-trial conversion supports acquisition costs. |
| A-B03 | The Growth plan will become the principal revenue plan. | Untested | Package testing and post-launch plan mix | Most sustainable recurring revenue comes from Growth customers. |
| A-B04 | Agencies will pay for multi-workspace administration after merchant workflows are proven. | Untested | Agency interviews and paid design-partner offers | Agencies commit to pilots or paid continuation. |
| A-B05 | Transparent spending limits reduce resistance to usage billing. | Untested | Usability and pricing interviews | Participants report confidence and fewer cost objections. |
| A-B06 | Provider, infrastructure, payment, AI, onboarding, and support costs leave a healthy gross margin. | Untested | Unit-economics model using negotiated provider costs | A typical active customer remains sustainably profitable. |

## Pilot assumptions

| ID | Assumption | Status | Validation method | Success signal |
|---|---|---|---|---|
| A-T01 | A six-week pilot with 8–12 retailers can reveal the principal activation and value problems. | Untested | Run the pilot and assess evidence saturation | Major issues repeat and new interviews yield diminishing new insights. |
| A-T02 | Welcome, abandoned-cart, and post-purchase journeys are sufficient for the pilot. | Untested | Pilot journey selection and outcome review | Most participants can address a priority need with these templates. |
| A-T03 | A real paid-continuation decision is the strongest early demand signal. | Untested | Present a priced continuation offer | At least 40% of participants accept. |
| A-T04 | Manual operational support is acceptable temporarily but can later be reduced. | Untested | Track staff time by onboarding and support activity | Participants reach routine independence and support time declines. |

## Tentative pilot configuration

Unless discovery evidence changes it, use the following working configuration:

- **Segment:** Nigerian online retailers
- **Channels:** Email and WhatsApp initially; SMS next
- **Journeys:** Welcome, abandoned-cart, and post-purchase
- **Commerce integration:** WooCommerce
- **Fallback ingestion:** CSV import and webhooks
- **Payment integration:** Paystack
- **Onboarding:** Guided and manually assisted
- **Reporting:** Essential delivery, conversion, attributed revenue, and cost metrics
- **Duration:** Six weeks
- **Cohort:** 8–12 businesses

## Open decisions

| ID | Decision needed | Current tentative direction | Evidence required |
|---|---|---|---|
| O-001 | Final product name | CampaignHub as a working name | Domain, trademark, app-store, and competitor checks |
| O-002 | First commerce integration | WooCommerce | Candidate coverage and technical feasibility |
| O-003 | Initial pilot channels | Email and WhatsApp | Customer demand, provider approval, cost, and reliability |
| O-004 | Subscription prices and limits | Starter, Growth, and Agency tiers | Price-sensitivity research and unit economics |
| O-005 | Messaging providers | Undecided | Coverage, APIs, compliance support, deliverability, and cost |
| O-006 | Attribution model | Conservative event-based attribution | Customer expectations and data feasibility |
| O-007 | Campaign approval flow | Owner/marketer permission plus explicit confirmation | Team workflow research and risk review |
| O-008 | Data-retention periods | Undecided | Legal review and operational requirements |
| O-009 | Pilot recruitment channel | Direct outreach and business communities | Access, candidate quality, and recruitment cost |
| O-010 | Reply-management experience | No full inbox initially | Evidence that replies require in-product handling |
| O-011 | Exact pilot MVP boundary | Reduced scope in the PRD | Interviews, provider research, and feasibility assessment |

## Change log

| Date | Change | Basis |
|---|---|---|
| 2026-08-31 | Created initial register from product discovery discussion. | Provisional product reasoning; no customer evidence yet. |
