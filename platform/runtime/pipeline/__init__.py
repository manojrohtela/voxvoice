"""runtime.pipeline — the first-party streaming conversation pipeline.

VoxVoice owns this pipeline; **no external pipeline substrate is bound**. The
Pipecat binding was retired by **P-ADR-0002** (M11.1,
`docs/adr/P-ADR-0002-implementation-profile-epic11.md`), which exercises
ADR-0002's *"may use a substrate"* in the negative.
**AR-13 is retained, not deleted** — should a substrate ever be adopted, it would
still be confined to this package.

Commodity work is instead rented narrowly, inside the layers already permitted to
hold it: a vendor SDK lives in that vendor's Provider Plugin (AR-12), and sample-rate
conversion lives in `audio/` here (ADR-0044 AD4), dependency-free.
"""
