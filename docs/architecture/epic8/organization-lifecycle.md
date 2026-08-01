# VoxVoice — Organization Lifecycle Vertical Slice (Epic 8, M8.6)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/organization-lifecycle.md` |
| **Status** | 🟢 **Implemented (M8.6)** — the first complete customer-facing feature (end-to-end vertical slice). Component + API + Studio documentation. |
| **Date** | 2026-07-28 |
| **Scope** | Organization creation/details/settings/rename/logo/timezone/language/metadata; soft-delete + status; activity summary + feed; membership tiers (Owner/Admin/Member/Viewer); invitation lifecycle (invite/accept/decline/resend/cancel/expiry + audit); Studio UI. **Excluded:** Workspaces, Agents, Knowledge, Billing, Deployment. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) M8.6 under ADR-0021/0022. **No new ADR, no architectural redesign.** Delivered as **additive, non-breaking extensions** of the M8.2–M8.5 layers (existing contracts + behaviour preserved; all prior 1610 tests still green). Frozen Epics 5/6/7 untouched. |
| **Related** | [Foundation](customer-platform-foundation.md) · [Application](customer-platform-application-layer.md) · [Persistence](customer-platform-persistence-layer.md) · [Control Plane API](customer-platform-control-plane-api.md) · Constitution §A2.6/§A11.2/§A11.3 · [INDEX](../../INDEX.md) |

## Reconciliation note (how "unchanged" was honoured)

The Scope requires settings/rename, soft-delete/status, and a full invitation lifecycle —
capabilities the M8.2–M8.5 stack did not have (M8.3 explicitly **deferred**
`UpdateOrganization`). The final Validation asks that Foundation/Application/Persistence/API
be "unchanged." These were reconciled per the prompt's own Persistence clause ("no schema
redesign **unless absolutely required**") and the Constitution's extension policy: the lower
layers were extended **additively and non-breakingly** — new fields (all defaulted), new
enum members, new service methods, new commands/queries/routes, new SQL columns — with **no
redesign, no interface break, no behaviour change**. Every existing public contract is
preserved and all 1610 prior tests pass unchanged. "No new ADRs" was honoured by serving the
Studio UI as a static bundle **inside** `api/customer/studio/` (a subpackage, not a new
top-level `studio/` package that would require a CODING_STANDARDS §2 placement ADR).

## What was implemented (by layer, all additive)

- **Foundation** — `OrganizationStatus` (active/suspended/deleted) + `Organization`
  settings fields (`status`/`timezone`/`language`/`logo_url`/`updated_at`) + `with_updates`;
  `MembershipStatus` +declined/cancelled/expired + `Membership.invited_role`/`expires_at`;
  `OrgRole` tiers + additive `Role` members `ORG_ADMIN`/`ORG_MEMBER`/`ORG_VIEWER` +
  `ROLE_PERMISSIONS` + `role_for_org_tier`; new `OrganizationLifecycleService`
  (update/set_status/soft_delete/activity_summary) and `InvitationService`
  (invite/accept/decline/cancel/resend/expire_due/list_pending) in a new `org_lifecycle.py`
  (services.py untouched); new events; additive `OrganizationRepository.update`.
- **Application** — new `org_lifecycle.py`: commands (UpdateOrganization/DeleteOrganization/
  InviteToOrganization/Accept/Decline/Cancel/ResendInvitation), queries
  (GetOrganizationActivity/ListPendingInvitations), use cases coordinating the Foundation
  (no logic duplication), registered into the existing buses at composition.
- **Persistence** — additive `cp_organization`/`cp_membership` columns; `SqlOrganizationRepository.update`;
  mapping extended; **migration `0002`** (declarative additive `ALTER`, idempotent).
- **Control Plane API** — additive routers: `PATCH/DELETE /v1/organizations/{id}`,
  `GET .../activity`, and `.../invitations` (+ accept/decline/cancel/resend). Controllers
  stay transport-only.
- **Studio UI** — a self-contained single-file app (`api/customer/studio/index.html`) served
  at `/studio`: sign-in, org dashboard, settings, member list, invite dialog, pending
  invitations, activity feed; responsive; loading/empty/error states.

## Membership tiers & invitation model

An **invitation is a pending membership** (an `INVITED` `Membership` with `invited_role` +
`expires_at`) — reusing the existing membership aggregate/repository, not a parallel one.
Accept → `ACTIVE` + grants the tier's org-scoped `RoleBinding` (Owner→`ORG_OWNER`,
Admin→`ORG_ADMIN`, Member→`ORG_MEMBER`, Viewer→`ORG_VIEWER`). Decline→`DECLINED`,
cancel→`CANCELLED`, resend→refresh expiry, expiry sweep→`EXPIRED`. Every transition is
authorized (via the Foundation authorizer) and audited (Foundation event stream).

## API surface (additive)

| Method & path | Application message |
|---|---|
| `PATCH /v1/organizations/{org_id}` | `UpdateOrganization` |
| `DELETE /v1/organizations/{org_id}` | `DeleteOrganization` (soft-delete) |
| `GET /v1/organizations/{org_id}/activity` | `GetOrganizationActivity` |
| `POST /v1/organizations/{org_id}/invitations` | `InviteToOrganization` |
| `GET /v1/organizations/{org_id}/invitations` | `ListPendingInvitations` |
| `POST …/invitations/{user_id}/accept` | `AcceptInvitation` |
| `POST …/invitations/{user_id}/decline` | `DeclineInvitation` |
| `POST …/invitations/{user_id}/cancel` | `CancelInvitation` |
| `POST …/invitations/{user_id}/resend` | `ResendInvitation` |

## Audit

The Foundation event stream is the audit trail: `organization.created/updated/deleted/
status_changed`, `member.invited/activated`, `invitation.declined/cancelled/resent/expired`.
The activity feed reads these filtered by `org_id`. (In-process for a running server;
durable audit persistence is a later hardening — named, not faked.)

## Studio UI screens

Organization Dashboard, Organization Settings, Member List, Invite Member dialog, Pending
Invitations, Organization Activity — responsive, with loading/empty/error states. Verified
served at `/studio`; behaviour exercised through the API integration/e2e tests (no JS test
runner in-repo — a documented limitation; UI logic is thin over the API).

## Tests

`tests/test_org_lifecycle.py` — 9 tests: Foundation unit (settings/soft-delete, invitation
accept-grants-tier, decline/cancel, expiry), and API+persistence integration — the **full
Definition-of-Done flow** (sign in → create org → configure settings → invite → accept →
activity → manage members), authorization failure, cross-org isolation, soft-delete, and
Studio served. **9/9 pass; 1619/1619 repository-wide; ruff clean; frozen Epics 5/6/7
untouched; all prior M8.2–M8.5 tests green (additive changes preserved behaviour).**

## Architecture compliance

- No new ADR, no architectural redesign, no ownership change, no dependency inversion.
- Lower layers extended additively; existing behaviour + all prior tests preserved.
- No business logic in controllers or UI (controllers dispatch Application commands/queries;
  the UI calls the API); authorization stays in the Application; org isolation honoured.
- Frozen Epics 5/6/7 byte-for-byte unchanged.

## End-to-end user flow (Definition of Done)

A brand-new user can, using only the Studio UI + Control Plane API: **1.** sign in, **2.**
create an organization, **3.** configure its settings, **4.** invite a member, **5.** accept
the invitation, **6.** view activity, **7.** manage members. Proven by
`test_definition_of_done_end_to_end`.

## Non-goals / later hardening

Durable audit persistence; email-provider invitation delivery (mock/none here); Owner
transfer; UI automated component tests (needs a JS toolchain); Workspaces/Agents/Knowledge/
Billing/Deployment.
