# GIT_WORKFLOW.md — Version Control Workflow

**Status:** Active v1.0 (2026-07-16) · **Owner:** Technical Owner · **Authority:** Level 3 · **Scale assumption:** solo/small team today, structured so a larger organization inherits it without redesign. Precondition: the repository is initialized at migration M0 (it currently is not — nothing below applies until then, which is itself the argument for M0).

## 1. Branching strategy — trunk-based

- `main` is the trunk: always green, always releasable. Direct commits to `main` are allowed **only** for the solo developer on changes that fully satisfy DEFINITION_OF_DONE; the moment a second regular contributor exists, `main` becomes PR-only (this sentence is the scaling switch — no other change needed).
- Work branches: `feat/<slug>`, `fix/<slug>`, `docs/<slug>`, `chore/<slug>`, `spike/<slug>` — short-lived (target ≤3 days; a branch older than a week is a design smell: slice smaller).
- `spike/*` branches are never merged (DEVELOPMENT_WORKFLOW §special cases) — learnings → docs, branch deleted.
- No long-lived develop/staging branches; deployment stages are handled by tags + config, not branches.

## 2. Commit conventions

- Conventional Commits: `type(scope): summary` — types `feat|fix|docs|test|perf|refactor|chore|revert`; scope = module (`runtime/turn`, `api`, `providers/stt`, `governance`).
- Commits are atomic (one logical change, builds and passes fast checks — AI_AGENT_RULES #17) and self-contained with their tests and docs.
- Body: the *why* and any governance references (`ADR-0003`, `DL-012`, `Fixes #42`). Breaking public interfaces: `!` + `BREAKING CHANGE:` footer (feeds the changelog and versioning).

## 3. Pull request workflow

- One PR = one requirement. Description contains: requirement, Constitution sections touched (Phase 3 output), test/benchmark evidence, DoD confirmation.
- Review per REVIEW_CHECKLIST (self-review recorded when solo). CI must be green before review, not after.
- Merge method: squash-merge for branch noise, merge-commit for series of deliberately atomic commits — the result on `main` must read as a clean history either way.

## 4. Versioning policy

- SemVer `MAJOR.MINOR.PATCH` for the platform: MAJOR = breaking change to any public interface (REST/events/webhooks/SDK/capability contracts, per A11.4 deprecation flow) · MINOR = features, new events, new plugins · PATCH = fixes.
- Public interface versions (e.g. `/v1`) evolve per Constitution A11.4 and are independent of package version.
- SDK versions independently, same SemVer rules.

## 5. Tagging & releases

- Releases are annotated tags `vX.Y.Z` on `main`, created only when GOVERNANCE §10 release gates pass.
- At tag time: roll `Unreleased` changelog into the version section; write release notes (consumer-visible changes, deprecations, migration steps); record the performance baseline (PERFORMANCE_POLICY §4).
- No release branches at current scale. **Scaling path:** when multiple supported versions exist, add `release/X.Y` branches cut from the tag, receiving only cherry-picked fixes.

## 6. Hotfix workflow

- Branch `fix/hotfix-<slug>` from the release tag (or `main` if unreleased), compressed workflow per DEVELOPMENT_WORKFLOW §special-cases — testing and security phases never waived.
- Merge to `main`, tag `vX.Y.Z+1` immediately; retro-review within 24 h if review was compressed; DECISION_LOG entry describing what was compressed and why.

## 7. Rollback strategy

- First response to a bad release is **rollback, not forward-fix**: redeploy the previous tag.
- Rollback-safety is a DoD item: DB migrations are expand-contract (new code tolerates old schema and vice versa for one release), so N ⇄ N−1 is always safe.
- A rollback gets a `revert:` commit trail on `main` (never history rewriting) and a DECISION_LOG entry; the forward-fix then follows the normal workflow.
- `git push --force` to `main` is prohibited, always. Force-push to own work branches is fine before review.

## 8. Repository hygiene

- Git-ignored, permanently: env files, databases, virtualenvs/toolchains, caches, recordings/artifacts. Verified before first commit (SECURITY_POLICY §1) and re-checked when new artifact types appear.
- Large binaries (models, audio fixtures) go to storage or LFS, never plain git.
- Every merge to `main` leaves the repo in a state where `make dev` (Stage-1 topology) works from a fresh clone.
