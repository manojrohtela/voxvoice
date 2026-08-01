# VoxVoice — Workspace Lifecycle Vertical Slice (Epic 8, M8.7)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/workspace-lifecycle.md` |
| **Status** | 🟢 **Implemented (M8.7)** — the second complete customer-facing feature (end-to-end vertical slice). Component + API + Studio documentation. |
| **Date** | 2026-07-28 |
| **Scope** | Workspace create/rename/description/icon/color/settings/metadata; soft-archive + restore + status; activity summary + feed; workspace membership (Admin/Member/Viewer) add/remove/list; navigation (switcher/recent/favorite/default/search); Studio UI. **Excluded:** Projects, Agents, Knowledge, Deployment. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) under ADR-0021/0022. **No new ADR, no architectural redesign.** Delivered as **additive, non-breaking extensions** of the M8.2–M8.6 layers (existing contracts + behaviour preserved; all prior 1619 tests still green). Frozen Epics 5/6/7 untouched. |
| **Related** | [Organization Lifecycle](organization-lifecycle.md) · [Foundation](customer-platform-foundation.md) · [Application](customer-platform-application-layer.md) · [Persistence](customer-platform-persistence-layer.md) · [Control Plane API](customer-platform-control-plane-api.md) · Constitution §A2.6/§A11.2/§A11.3 · [INDEX](../../INDEX.md) |

## Reconciliation note (how "additive" was honoured)

The Scope requires workspace settings/rename, soft-archive/restore, workspace membership,
and navigation — capabilities the M8.2–M8.6 stack did not have. The prompt asks to "reuse
existing repositories whenever possible" with "only additive persistence changes" and "no
redesign". These were honoured exactly as M8.6 did for organizations: the lower layers were
extended **additively and non-breakingly** — new fields (all defaulted), new enum members,
new service methods, new commands/queries/routes, new SQL columns, one new SQL table — with
**no redesign, no interface break, no behaviour change**. Every existing public contract is
preserved and all 1619 prior tests pass unchanged. "No new ADRs" was honoured by extending
the existing `api/customer/studio/` static bundle (a subpackage) rather than creating a new
top-level package.

Workspace **creation** intentionally stays on the frozen M8.2 `WorkspaceService.create_workspace`
(and its M8.5 `POST /workspaces` route) — untouched, exactly as organization creation stayed
on `IdentityService.create_organization` in M8.6. This slice adds rename/settings/archive/
restore/membership/navigation on top.

## What was implemented (by layer, all additive)

- **Foundation** — new `workspace_lifecycle.py`: `WorkspaceLifecycleService`
  (update/archive/restore/activity_summary/search), `WorkspaceMembershipService`
  (add/remove/list, granting a WORKSPACE-scoped role binding per tier), and
  `WorkspaceNavigationService` (default/favorite/recent/switcher, backed by the existing
  `UserPreferences`). The M8.2 `services.py` is untouched. Additive `Workspace` fields
  (`description`/`icon`/`color`/`status`/`updated_at` + `with_updates`); new
  `WorkspaceStatus`, `WsRole`, `WorkspaceMembershipStatus`, and the `WorkspaceMembership`
  aggregate; additive `Role.WS_MEMBER`/`WS_VIEWER` (Admin reuses `WORKSPACE_ADMIN`) +
  `ROLE_PERMISSIONS` + `WS_ROLE_BINDING`/`role_for_ws_tier`; new events; additive
  `WorkspaceRepository.update` + new `WorkspaceMembershipRepository`.
- **Application** — new `workspace_lifecycle.py`: commands (UpdateWorkspace/Archive/Restore/
  Add-/RemoveWorkspaceMember/SetDefault/Favorite/RecordRecent), queries
  (ListWorkspaceMembers/GetWorkspaceActivity/GetWorkspaceNavigation/SearchWorkspaces), use
  cases coordinating the Foundation (no logic duplication), registered into the existing
  buses at composition.
- **Persistence** — additive `cp_workspace` columns; new `cp_workspace_membership` table +
  `SqlWorkspaceMembershipRepository`; `SqlWorkspaceRepository.update`; mapping extended;
  **migration `0003`** (declarative additive `ALTER` + `CREATE TABLE`, idempotent).
- **Control Plane API** — additive routers: `PATCH /workspaces/{id}`, `.../archive`,
  `.../restore`, `.../activity`, `.../search`, workspace `.../members` (+ remove), and a
  `workspace-navigation` sub-router (switcher + default/favorite/recent). Controllers stay
  transport-only. Registered **before** `workspaces.router` so the literal `/search` path
  resolves ahead of M8.5's `/{workspace_id}`.
- **Studio UI** — the existing single-file app (`api/customer/studio/index.html`) gains a
  Workspaces section: list/switcher (with favorite/default marks + search), create,
  dashboard/settings (rename/description/icon/color), archive/restore, workspace members
  (add/remove/list), and workspace activity; responsive; loading/empty/error states.

## Membership tiers & isolation model

A **workspace membership is org-and-workspace scoped**: only an **ACTIVE org member** may be
added to a workspace (a cross-org or non-member add raises `OrganizationIsolationError` →
403). Each add grants a **WORKSPACE-scoped** `RoleBinding` for the tier (Admin→`WORKSPACE_ADMIN`,
Member→`WS_MEMBER`, Viewer→`WS_VIEWER`), so RBAC evaluation is unchanged — an ORGANIZATION
binding still spans all workspaces, a WORKSPACE binding covers exactly its own workspace.
Removal flips the membership to `REMOVED` and revokes that workspace's bindings for the user.
Workspace **name uniqueness within an org** is enforced on rename.

## API surface (additive)

| Method & path | Application message |
|---|---|
| `PATCH /v1/organizations/{org_id}/workspaces/{workspace_id}` | `UpdateWorkspace` |
| `POST …/workspaces/{workspace_id}/archive` | `ArchiveWorkspace` (soft-archive) |
| `POST …/workspaces/{workspace_id}/restore` | `RestoreWorkspace` |
| `GET …/workspaces/{workspace_id}/activity` | `GetWorkspaceActivity` |
| `GET …/workspaces/search?q=` | `SearchWorkspaces` |
| `POST …/workspaces/{workspace_id}/members` | `AddWorkspaceMember` |
| `GET …/workspaces/{workspace_id}/members` | `ListWorkspaceMembers` |
| `DELETE …/workspaces/{workspace_id}/members/{user_id}` | `RemoveWorkspaceMember` |
| `GET …/workspace-navigation` | `GetWorkspaceNavigation` |
| `POST …/workspace-navigation/{workspace_id}/default` | `SetDefaultWorkspace` |
| `POST …/workspace-navigation/{workspace_id}/favorite` | `FavoriteWorkspace` |
| `POST …/workspace-navigation/{workspace_id}/recent` | `RecordRecentWorkspace` |

## Audit

The Foundation event stream is the audit trail: `workspace.updated/archived/restored/
settings_changed`, `workspace.member_added/member_removed`. The workspace activity feed reads
these filtered by `org_id` **and** the target `workspace_id` (matching the event `resource`
or its `data.workspace_id`). (In-process for a running server; durable audit persistence is a
later hardening — named, not faked.)

## Studio UI screens

Workspace List/Switcher, Workspace Dashboard, Workspace Settings, Workspace Members,
Workspace Activity, and search + favorite/default/recent controls — responsive, with
loading/empty/error states. Verified served at `/studio`; behaviour exercised through the API
integration/e2e tests (no JS test runner in-repo — a documented limitation; UI logic is thin
over the API).

## Tests

`tests/test_workspace_lifecycle.py` — 11 tests: Foundation unit (settings/archive/restore,
name uniqueness, membership add-grants-binding + remove-revokes, org-member-only isolation,
navigation default/favorite/recent/search), and API+persistence integration — the **full
Definition-of-Done flow** (open org → create multiple workspaces → switch → manage members →
configure settings → view activity), authorization failure (viewer → 403), cross-org and
cross-workspace isolation, archive hides from switcher/search, and Studio served. **11/11
pass; 1630/1630 repository-wide; ruff clean; frozen Epics 5/6/7 untouched; all prior M8.2–M8.6
tests green (additive changes preserved behaviour).**

## Architecture compliance

- No new ADR, no architectural redesign, no ownership change, no dependency inversion.
- Lower layers extended additively; existing behaviour + all prior tests preserved.
- No business logic in controllers or UI (controllers dispatch Application commands/queries;
  the UI calls the API); authorization stays in the Application; org + workspace isolation
  honoured.
- Frozen Epics 5/6/7 byte-for-byte unchanged.

## End-to-end user flow (Definition of Done)

A user can, using only the Studio UI + Control Plane API: **1.** open an organization, **2.**
create multiple workspaces, **3.** switch between workspaces, **4.** manage workspace members,
**5.** configure workspace settings, **6.** view workspace activity. Proven by
`test_definition_of_done_end_to_end`.

## Non-goals / later hardening

Durable audit persistence; workspace-scoped settings inheritance/feature flags; workspace
transfer/ownership; UI automated component tests (needs a JS toolchain); Projects/Agents/
Knowledge/Deployment.
