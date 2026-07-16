"""runtime.orchestrator — Conversation Orchestrator (Constitution §A4.2, §A4.4).

Sole writer of conversation state; runs the state machine defined in
`core.states`; enforces lifecycle policy (timeouts, max-duration, idle re-prompt,
goodbye); emits all `session.*` events. Boundary only at M1; implemented at M2.
"""
