# Membership service contract

Implemented in `WorkspaceService`, with workspace and membership HTTP routes in
`app/modules/workspace/router.py`. Durable audit storage and all identity
lifecycle controls are not yet implemented.

## Operations

| Method | Authority | Effect |
| --- | --- | --- |
| `update_membership_role` | Active owner | Changes an active member's role; owner promotion requires an active, verified account. |
| `suspend_membership` | Active owner | Active → suspended, preserving role and acceptance history. |
| `restore_membership` | Active owner | Suspended → active, preserving role; requires an active, verified account and recorded acceptance. |
| `remove_membership` | Active owner | Active/suspended/legacy invited → removed; records removal time. |
| `leave_workspace` | Own active/suspended membership | Removes only the authenticated user's membership. |
| `list_memberships` | Active member | Cursor-paginated directory; owners can see/filter all states and see emails. Other roles see only active members, without emails. |
| `get_membership` | Active member | Same visibility rules as the directory; inaccessible targets return not found. |

Every operation requires a current authenticated, active, verified user account.
Missing, archived, or closed workspaces are inaccessible. Pending and
sending-suspended workspaces still allow team management.

## Versioning and transitions

- Mutation commands require a strict positive integer `expected_version`.
- A successful change increments the membership version. A same-state/same-role
  request with the current version returns unchanged data. A stale version
  conflicts, including for no-ops.
- Self-leave cannot be repeated after access has been removed; it returns not
  found. Self-suspension likewise prevents the actor from managing the workspace.
- Removed members cannot be restored directly. A fresh, matching verified-email
  invitation is required; acceptance reuses the membership row with the new role.
- Removal/leave revokes outstanding recipient invitations in that workspace.
- Demotion, suspension, removal, and leave invalidate the affected sender's
  outstanding invitations. Restoring ownership does not revive those tokens.
- Suspension does not disable the global user account or revoke sessions in other
  workspaces. Authorization must consult current membership on every request.

## Ownership and transactions

Removing active owner eligibility must leave another active membership with owner
role and an active, verified user account. Ineligible co-owners do not satisfy the
guard. Changes and invitation revocations commit or roll back together.

Affected users are locked in UUID order before the workspace and membership locks.
The workspace lock serializes membership mutations; SQLAlchemy version checks
also catch stale updates. Authentication expiry is rechecked after lock waits.
All membership writers must follow this locking protocol. Future account-level
suspension/disable/delete workflows need an ownership guard across affected
workspaces; the membership service cannot protect against arbitrary direct SQL.

## Directory contract

- Ordered by `(created_at, id)`, with that tuple as the next-page boundary.
- Default limit 50; allowed range 1–100.
- Optional role/status filters never widen workspace authorization.
- Owners receive `OwnerMembershipResponse`; others receive `MembershipResponse`,
  which has no email field. Password hashes and session/token data are never
  returned. Future HTTP schemas must preserve this distinction.
- Pagination is not a historical snapshot across requests. Authorization is
  rechecked on every page.
- The user model has no display-name field yet. The directory currently exposes
  user IDs and roles; names require a later profile/model change. Do not substitute
  emails for names in non-owner views.

## Verification and remaining integration

SQLite tests exercise authorization, transitions, visibility, versioning, and
rollback. Opt-in PostgreSQL tests use `CAMPAIGNHUB_TEST_DATABASE_URL`, restricted to
a database name ending in `_test`, and isolated randomly named schemas. They cover
competing owner departures and stale concurrent membership updates; skipped tests
are not evidence of verified PostgreSQL behavior.

Workspace and membership routes are registered under `/api/v1/workspaces`.
HTTP tests exercise real authentication, service behavior, pagination, validation,
and owner-only email serialization using isolated SQLite storage. Pagination uses
the paired `after_created_at` and `after_id` query parameters; the timestamp must
include a timezone offset. Removal takes `expected_version` as a query parameter;
role changes, suspension, restoration, and leaving take versioned JSON commands.

Invitation list/revoke routes are registered under the workspace; acceptance uses
`POST /api/v1/invitations/accept` with the token in JSON. Creation and resend
perform a read-only owner authorization check, then return 503 without issuing,
rotating, or revoking tokens. They are intentionally disabled until durable
transactional delivery is implemented, not controlled by an enable flag.

Remaining integrations: invitation delivery, durable role/membership audit
events, display names, and account-level ownership safeguards. Email delivery remains disabled.
No schema migration is needed for these service methods.
