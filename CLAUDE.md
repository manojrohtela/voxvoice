# VoxVoice — Project Instructions

> **This file was a byte-for-byte duplicate of [`docs/SPEC.md`](docs/SPEC.md).**
> At milestone **M0** it was replaced with this pointer to remove the duplication
> (Engineering Constitution §A14; the original spec is preserved unchanged in
> `docs/SPEC.md`). No information was lost.

VoxVoice is a **provider-agnostic, production-grade Voice AI Runtime Platform**. The
runtime is the product; applications (VoxHire, sales/support/other agents) are
*consumers* built on top of it.

## Read these before doing any work (in authority order)

1. **Engineering Constitution (highest technical authority)** —
   [`docs/architecture/engineering-constitution-v2.md`](docs/architecture/engineering-constitution-v2.md).
   Part A = timeless architecture; Part B = current Implementation Profile (today's
   vendors/frameworks). Ratified v2.0.
2. **ADRs** — [`docs/adr/`](docs/adr/). Amend the Constitution; binding until superseded.
3. **Governance framework** — [`.voxvoice/`](.voxvoice/README.md). *How* engineering
   is done. **Every coding session must follow [`.voxvoice/AI_AGENT_RULES.md`](.voxvoice/AI_AGENT_RULES.md)**
   and report against [`.voxvoice/DEFINITION_OF_DONE.md`](.voxvoice/DEFINITION_OF_DONE.md).
4. **Documentation index** — [`docs/INDEX.md`](docs/INDEX.md) links everything:
   audits, architecture, ADRs, runtime specs, baseline snapshot.

## Authority hierarchy (from `.voxvoice/README.md`)

Engineering Constitution → ADRs → Governance documents → Technical Design Documents
→ Sprint plans → Implementation tasks → Source code.
A lower artifact may never contradict a higher one; resolve conflicts upward
(stop, log, and raise an ADR) before continuing.

## Original product specification

The full original build spec is retained verbatim in [`docs/SPEC.md`](docs/SPEC.md).
It remains authoritative for product goal, consumer model, org/auth contract, and
the Hinglish acceptance phrases, but is **superseded in part** by the Constitution
(engine-adapter architecture → capability-based providers; Dograh-primary →
first-party runtime; repo layout). See Constitution §22.
