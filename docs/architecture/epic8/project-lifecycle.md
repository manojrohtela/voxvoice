# VoxVoice — Project Lifecycle Vertical Slice (Epic 8, M8.8)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/project-lifecycle.md` |
| **Status** | 🟢 **Implemented (M8.8)** — the third complete customer-facing feature (end-to-end vertical slice). Component + API + Studio documentation. |
| **Date** | 2026-07-28 |
| **Scope** | Project create/rename/description/icon/settings/metadata; soft-archive + restore + status; activity summary + feed; project membership (Admin/Member/Viewer) add/remove/list; per-user environment selection (dev/staging/prod) + switching; search; Studio UI. **Excluded:** Agents, Knowledge, Deployment, Monitoring. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) under ADR-0021/0022. **No new ADR, no architectural redesign.** Delivered reuse-first with a **minimal additive Foundation change** (justified below); Application/Persistence/API/Studio extended additively. All prior 1630 tests still green; frozen Epics 5/6/7 untouched. |
| **Related** | [Workspace Lifecycle](workspace-lifecycle.md) · [Organization Lifecycle](organization-lifecycle.md) · [Foundation](customer-platform-foundation.md) · [Application](customer-platform-application-layer.md) · [Persistence](customer-platform-persistence-layer.md) · [Control Plane API](customer-platform-control-plane-api.md) · Constitution §A2.6/§A11.2/§A11.3 · [INDEX](../../INDEX.md) |

## Reuse-first reconciliation (which layers changed, and why)

The M8.8 architecture rule is **prefer reuse; only change Foundation if there is no clean
alternative, and then additively + minimally**. Three deliberate reuse decisions kept the
change surface small:

1. **Project creation is reused, not rebuilt.** Creation stays on the frozen M8.2
   `ProjectService.create_project` / M8.5 `POST …/projects` route (as org/workspace creation
   stayed on their M8.2 services). This slice adds rename/settings/archive/restore/members/
   environments/activity on top.
2. **No new RBAC scope.** Project operations authorize via the **existing workspace-scoped**
   `PROJECT_MANAGE`/`PROJECT_READ` permissions — `rbac.py` is **unchanged**. Adding a
   `PROJECT` `RoleScope` would have meant touching the permission evaluator; it was not
   necessary for the DoD, so it was avoided. The project-tier (Admin/Member/Viewer) is a
   **recorded** membership attribute; PROJECT-granular RBAC enforcement is a named, deferred
   follow-up (honest limitation).
3. **Environments reuse `UserPreferences` + `EnvironmentKind` — no new persistence.** The
   standard dev/staging/prod set is fixed (built from the existing `EnvironmentKind`); the
   per-user selected environment is Consumer-owned state stored in `UserPreferences.metadata`
   (the exact pattern M8.7 used for the workspace switcher). No deployment logic, no new
   Environment table.

**Foundation was changed — minimally and additively — and it was necessary:** the `Project`
aggregate had no settings/status and `ProjectRepository` had no `get`/`update`; rename/archive
cannot be implemented without them, and the aggregate + repo interface are domain-owned. The
change is confined to: additive defaulted `Project` fields + `with_updates`, new
`ProjectStatus`/`ProjectRole`/`ProjectMembership`/`ProjectMembershipStatus`, additive
`ProjectRepository.get`/`update`, a new `ProjectMembershipRepository`, new event types, and a
new `project_lifecycle.py` module (the M8.2 `services.py` is untouched). Verified non-breaking
by all 1630 prior tests passing.

## What was implemented (by layer)

- **Foundation (minimal additive)** — new `project_lifecycle.py`: `ProjectLifecycleService`
  (update/archive/restore/activity_summary/search), `ProjectMembershipService`
  (add/remove/list), `ProjectEnvironmentService` (fixed dev/staging/prod set + per-user
  get/set/selector via `UserPreferences`). Additive `Project` fields
  (`description`/`icon`/`status`/`updated_at`/`metadata` + `with_updates`); new
  `ProjectStatus`/`ProjectRole`/`ProjectMembership`/`ProjectMembershipStatus`; additive
  `ProjectRepository.get`/`update` + new `ProjectMembershipRepository`; new events.
  **`rbac.py`, `services.py` unchanged.**
- **Application (additive)** — new `project_lifecycle.py`: commands (UpdateProject/Archive/
  Restore/Add-/RemoveProjectMember/SwitchProjectEnvironment), queries (ListProjectMembers/
  GetProjectActivity/GetProjectEnvironment/SearchProjects), use cases coordinating the
  Foundation, registered into the existing buses at composition. M8.3 `use_cases.py` untouched.
- **Persistence (additive)** — additive `cp_project` columns + new `cp_project_membership`
  table + `SqlProjectMembershipRepository`; `SqlProjectRepository.get`/`update`; mapping
  extended; **migration `0004`** (declarative additive `ALTER` + `CREATE TABLE`, idempotent).
- **Control Plane API (additive, preserved architecture)** — additive router:
  `PATCH …/projects/{id}`, `.../archive`, `.../restore`, `.../activity`, `.../search`, project
  `.../members` (+ remove), and `.../environment` (GET selector + POST switch). Controllers
  stay transport-only. Registered **before** `projects.router` so the literal `/search`
  resolves ahead of any `/{project_id}` sub-path.
- **Studio UI (additive)** — the existing `api/customer/studio/index.html` gains a Projects
  section (shown when a workspace is open): list/search, create, dashboard/settings
  (rename/description/icon), archive/restore, environment selector (dev/staging/prod switch),
  members (add/remove/list), and project activity; responsive; loading/empty/error states.

## Membership & isolation model

A **project membership is org/workspace/project scoped**: only an **ACTIVE org member** may be
added to a project (a cross-org or non-member add raises `OrganizationIsolationError` → 403);
the tier (Admin/Member/Viewer) is recorded on the membership. Authorization to manage a project
or its members uses the **existing workspace-scoped `PROJECT_MANAGE`** (org owners and
workspace admins/developers already hold it). Project **name uniqueness within a workspace** is
enforced on rename. A project fetched under the wrong workspace path is not found (workspace
isolation).

## Environment management (no deployment)

Every project exposes the fixed standard set **development / staging / production** (from
`EnvironmentKind`). The **default is development**; a user **switches** their active environment
per project, persisted in `UserPreferences.metadata` (Consumer-owned, ADR-0021). Switching
emits `project.environment_switched`. There is **no deployment logic** — this is selection
state only.

## API surface (additive)

| Method & path | Application message |
|---|---|
| `PATCH …/workspaces/{wid}/projects/{project_id}` | `UpdateProject` |
| `POST …/projects/{project_id}/archive` | `ArchiveProject` (soft-archive) |
| `POST …/projects/{project_id}/restore` | `RestoreProject` |
| `GET …/projects/{project_id}/activity` | `GetProjectActivity` |
| `GET …/projects/search?q=` | `SearchProjects` |
| `POST …/projects/{project_id}/members` | `AddProjectMember` |
| `GET …/projects/{project_id}/members` | `ListProjectMembers` |
| `DELETE …/projects/{project_id}/members/{user_id}` | `RemoveProjectMember` |
| `GET …/projects/{project_id}/environment` | `GetProjectEnvironment` (selector) |
| `POST …/projects/{project_id}/environment` | `SwitchProjectEnvironment` |

## Audit

The Foundation event stream is the audit trail: `project.updated/archived/restored/
settings_changed`, `project.member_added/member_removed`, `project.environment_switched`. The
project activity feed reads these filtered by `org_id` **and** the target `project_id` (event
`resource` or `data.project_id`). (In-process for a running server; durable audit persistence
is a later hardening — named, not faked.)

## Studio UI screens

Project List (with search), Project Dashboard, Project Settings, Project Members, Environment
Selector, Project Activity — responsive, with loading/empty/error states. Verified served at
`/studio`; behaviour exercised through the API integration/e2e tests (no JS test runner in-repo
— a documented limitation; UI logic is thin over the API).

## Tests

`tests/test_project_lifecycle.py` — 11 tests: Foundation unit (settings/archive/restore, name
uniqueness, membership add/remove + org-member-only isolation, environment selection/switching/
validation), and API+persistence integration — the **full Definition-of-Done flow** (open
workspace → create multiple projects → configure settings → manage members → switch
environments → view activity), authorization failure (viewer → 403), cross-org/cross-workspace/
cross-project isolation, archive-hides-from-search, and Studio served. **11/11 pass;
1641/1641 repository-wide; ruff clean; frozen Epics 5/6/7 untouched; all prior M8.2–M8.7 tests
green (additive changes preserved behaviour).**

## Architecture compliance

- No new ADR, no architectural redesign, no ownership change, no dependency inversion.
- Foundation changed only where strictly necessary (additive; `rbac.py`/`services.py`
  unchanged); Application/Persistence/API/Studio extended additively; all prior tests preserved.
- No business logic in controllers or Studio (controllers dispatch Application commands/queries;
  the UI calls the API); authorization stays in the Application; org/workspace/project isolation
  honoured.
- Frozen Epics 5/6/7 byte-for-byte unchanged.

## End-to-end user flow (Definition of Done)

A user can, using only the Studio UI + Control Plane API: **1.** open a workspace, **2.** create
multiple projects, **3.** configure project settings, **4.** manage project members, **5.**
switch environments, **6.** view project activity. Proven by `test_definition_of_done_end_to_end`.

## Non-goals / later hardening

PROJECT-scoped RBAC enforcement (making Viewer read-only at project granularity — would add a
`PROJECT` `RoleScope`); durable audit persistence; per-project persisted environments/config
(deployment concern); UI automated component tests (needs a JS toolchain); Agents/Knowledge/
Deployment/Monitoring.
