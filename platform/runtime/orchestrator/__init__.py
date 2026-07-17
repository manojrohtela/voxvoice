"""runtime.orchestrator — Conversation Orchestrator (Constitution §A4.2, §A4.4).

Sole writer of conversation state; drives the state machine defined in
`core.states` (via `runtime.conversation.ConversationStateMachine`); enforces
lifecycle policy; resolves provider capabilities from the registry and drives the
RuntimePipeline. Implemented at M2B.2; pipeline wiring at M2C.2.
"""

from .orchestrator import ConversationOrchestrator

__all__ = ["ConversationOrchestrator"]
