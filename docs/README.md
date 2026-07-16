# VoxVoice Documentation

All engineering knowledge for VoxVoice lives in this directory. Documentation
is part of the product: every analysis, audit, architecture review, design
decision, and recommendation must be written down here, not left in chat.

**Start at the [documentation index (INDEX.md)](INDEX.md).**

## Structure

```
docs/
├── README.md            ← this file: conventions
├── INDEX.md             ← central index; every document is linked from here
├── SPEC.md              ← the build specification (living document)
├── audits/              ← versioned, immutable audit reports
├── adr/                 ← Architecture Decision Records (opened on first ADR)
├── architecture/        ┐
├── runtime/             │
├── audio/               │
├── conversation-engine/ │
├── memory/              │
├── providers/           │  opened when their first
├── apis/                │  document lands (see INDEX.md)
├── performance/         │
├── security/            │
├── roadmap/             │
├── refactoring/         │
├── testing/             │
├── deployment/          │
└── research/            ┘
```

## Rules

1. **Everything is linked from `INDEX.md`.** A document that isn't in the index
   doesn't exist. Update the index in the same change that adds a document.
2. **Audits are immutable.** When an audit is completed, save the complete
   report as `audits/<phase>-<topic>-audit.md`, mark it Final with a version.
   Never overwrite a previous report — corrections or re-audits get a new
   versioned file (`…-v2.md`) that names what it supersedes.
3. **ADRs for architectural change.** Any recommended or implemented
   architectural change gets an ADR in `adr/` (numbered `ADR-NNNN-slug.md`)
   covering: Problem, Context, Alternatives Considered, Selected Solution,
   Trade-offs, Risks, Future Impact. No major change ships without one.
4. **Living documentation.** When code changes affect architecture, runtime
   behavior, APIs, or decisions: update the related documents, mark obsolete
   ones as **Superseded** (with a link to their replacement), and
   cross-reference related documents. Every engineering decision must be
   traceable through this tree.
5. **Document headers.** Every document starts with a small metadata block:
   status (Draft / Final / Living / Superseded), date, scope, and related
   documents.
