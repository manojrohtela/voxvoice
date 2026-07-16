"""runtime.memory — Memory Engine (Constitution §A7).

Layered, token-budgeted context (system / entity / rolling summary / working
window / tool); context snapshots for reconnect and crash recovery. Transcripts
and context are projections of the same event log. Boundary only at M1;
implemented at M5.
"""
