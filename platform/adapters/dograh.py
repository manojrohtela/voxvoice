"""DograhAdapter — primary engine (SPEC §5).

✅ VERIFIED against a live self-hosted Dograh **v1.35.0** (Docker), 2026-06-15.
   Every route below was exercised against the running instance, not assumed.
   The earlier guessed routes (`/api/v1/calls/web`, token+url+room) were WRONG;
   Dograh's real model is workflow/run/embed-token based. Per SPEC §5/§9 we
   follow Dograh's actual API and document the deviations here, while keeping
   our own consumer contract (SPEC §4) as stable as Dograh allows.

REAL MODEL (how our concepts map):
   our Agent    -> a Dograh **workflow**  (POST /workflow/create/definition)
   our Session  -> a Dograh **run**       (POST /workflow/{id}/runs {mode})
   web connect  -> an **embed token**     (POST /workflow/{id}/embed-token)
                   + TURN creds (GET /public/embed/turn-credentials/{token})
                   + the engine's embed/WebRTC client. There is NO room/url
                   like LiveKit — see WEB-CONTRACT DEVIATION below.
   auth         -> `X-API-Key: dgr_…` from POST /user/api-keys (set DOGRAH_API_KEY)

⚠️ DEVIATIONS / OPEN ITEMS (also in README):
 1. WEB-CONTRACT: SPEC §4 promises `web:{token,url,room}`. Dograh gives an
    embed token + embed_script + separate TURN creds over WebRTC. We map
    token<-embed token and put `embed_script`/run id where url/room were. The
    final consumer shape is an OWNER decision (does not block the adapter).
 2. WORKFLOW NODE GRAPH: a real conversational agent needs a node graph
    (startCall -> agentNode -> endCall), authored in Dograh's visual builder or
    via the `dograh-sdk` Workflow builder. `POST /workflow/create/definition`
    accepts our flat config but a *minimal* definition won't actually converse.
    Building the full node graph from our flat Agent is the remaining work —
    marked TODO(nodegraph). We do NOT fake a working conversation.
 3. TRANSCRIPTS: Dograh exposes NO transcript-push webhook (only telephony
    status-callbacks). So `on_transcript` is fed by POLLING the run report
    (poll_transcript) — not an engine push. Our /v1/engine/dograh/events
    endpoint remains for client-forwarded / future-push items.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from .base import (
    EngineAdapter,
    EngineError,
    EngineSession,
    TranscriptHandler,
    WebConnInfo,
)

if TYPE_CHECKING:
    from api.models import Agent, Session

log = logging.getLogger("voxvoice.adapters.dograh")

_API = "/api/v1"  # VERIFIED prefix (health: GET /api/v1/health)


class DograhAdapter(EngineAdapter):
    name = "dograh"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        ui_base_url: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        # base_url = Dograh REST API host root (e.g. http://localhost:8000).
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        # Where the browser loads the embed widget from (the UI host, :3010).
        self._ui_base_url = (ui_base_url or "").rstrip("/")
        self._timeout = timeout
        # per engine_ref count of transcript items already dispatched (de-dupe).
        self._emitted: dict[str, int] = {}

    # ─── HTTP plumbing ───────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        # VERIFIED: Dograh `dgr_…` API keys authenticate via the `X-API-Key`
        # header. (`Authorization: Bearer` is for the login JWT, not API keys.)
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    async def _request(
        self, method: str, path: str, json: dict | None = None
    ) -> dict[str, Any]:
        """One place all Dograh calls funnel through. Wraps every failure in a
        typed EngineError so raw engine errors never reach consumers."""
        url = f"{self._base_url}{_API}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    method, url, headers=self._headers(), json=json
                )
                resp.raise_for_status()
                return resp.json() if resp.content else {}
        except httpx.HTTPError as exc:
            log.error("Dograh %s %s failed: %s", method, path, exc)
            raise EngineError(f"engine call failed: {method} {path}") from exc

    # ─── agent -> workflow node graph ────────────────────────────────────────
    @staticmethod
    def _node(node_id: str, node_type: str, data: dict, y: int) -> dict:
        return {"id": node_id, "type": node_type, "position": {"x": 0, "y": y}, "data": data}

    def _agent_to_workflow_definition(self, agent: Agent) -> dict:
        """Map our flat Agent -> a Dograh workflow_definition (React-Flow graph).

        ✅ VERIFIED: this exact shape passes `POST /workflow/{id}/validate`
        (is_valid=true) and produces a runnable webrtc run on Dograh v1.35.0.
        Node `data` fields + graph constraints come from GET /node-types; edges
        require `data:{label, condition}`.

        Structure (minimal graph that actually holds a conversation):
            globalNode — our system_prompt as the call-wide persona
            startCall  — speaks first_message, opens the conversation
            agentNode  — the conversational step (driven by the global persona)
            endCall    — wraps up and hangs up
        Provider routing (stt/tts/llm, Sarvam) is not a node-graph concern; it
        lives in workflow_configurations and is wired in Phase 2. We honour
        system_prompt, first_message and allow_interruptions here.
        """
        interrupt = agent.allow_interruptions
        greeting = agent.first_message or f"Hello, you're speaking with {agent.name}."
        return {
            "nodes": [
                self._node(
                    "global-1", "globalNode",
                    {"name": "Persona", "prompt": agent.system_prompt}, -150,
                ),
                self._node(
                    "start-1", "startCall",
                    {
                        "name": "Greeting",
                        "prompt": "Greet the caller and find out how you can help.",
                        "greeting_type": "text",
                        "greeting": greeting,
                        "allow_interrupt": interrupt,
                    }, 0,
                ),
                self._node(
                    "agent-1", "agentNode",
                    {
                        "name": "Assist",
                        "prompt": "Assist the caller according to your instructions.",
                        "allow_interrupt": interrupt,
                    }, 150,
                ),
                self._node(
                    "end-1", "endCall",
                    {
                        "name": "Goodbye",
                        "prompt": "Thank the caller and end the call.",
                        "add_global_prompt": False,
                    }, 300,
                ),
            ],
            "edges": [
                {
                    "id": "e1", "source": "start-1", "target": "agent-1",
                    "data": {"label": "engaged", "condition": "The caller has stated their need"},
                },
                {
                    "id": "e2", "source": "agent-1", "target": "end-1",
                    "data": {
                        "label": "done",
                        "condition": "Request handled or the caller says goodbye",
                    },
                },
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }

    async def _ensure_workflow(self, agent: Agent) -> int:
        """Return the Dograh workflow id for this agent, creating one if needed.

        The SPEC EngineAdapter interface has no create_agent hook, so the
        workflow is created lazily on first session and the id is written to
        `agent.engine_workflow_id`. The platform (sessions router) persists the
        agent afterwards, so each agent maps to exactly one Dograh workflow."""
        if agent.engine_workflow_id:
            return int(agent.engine_workflow_id)
        data = await self._request(
            "POST",
            "/workflow/create/definition",
            {
                "name": f"voxvoice::{agent.id}",
                "workflow_definition": self._agent_to_workflow_definition(agent),
            },
        )
        workflow_id = int(data["id"])
        agent.engine_workflow_id = str(workflow_id)  # platform persists this
        return workflow_id

    # ─── interface ───────────────────────────────────────────────────────────
    async def start_session(self, agent: Agent, session: Session) -> EngineSession:
        if session.channel != "web":
            # Phone channels go through /public/agent/* (SPEC Phase 3).
            raise EngineError(
                f"DograhAdapter web sessions only in Phase 1, got {session.channel!r}"
            )

        workflow_id = await self._ensure_workflow(agent)

        # A run is the call instance (our Session). VERIFIED mode "webrtc".
        run = await self._request(
            "POST",
            f"/workflow/{workflow_id}/runs",
            {"mode": "webrtc", "name": session.id},
        )
        run_id = run["id"]

        # Embed token = the browser's web-call credential. VERIFIED shape.
        embed = await self._request(
            "POST", f"/workflow/{workflow_id}/embed-token", {}
        )

        # engine_ref encodes both ids so end_session / polling can address them.
        engine_ref = f"{workflow_id}:{run_id}"

        # WEB-CONTRACT (superset, owner decision 2026-06-15): Dograh has no
        # room/url, so we keep the SPEC §4 fields (token=embed token, url=UI
        # embed URL, room=run id) AND expose Dograh's real handles alongside
        # (embed_script, turn_url, run_id) for the Phase 4 SDK.
        embed_token = embed["token"]
        embed_url = (
            f"{self._ui_base_url}/embed/{embed_token}"
            if self._ui_base_url
            else embed.get("embed_script", "")
        )
        turn_url = f"{self._base_url}{_API}/public/embed/turn-credentials/{embed_token}"
        return EngineSession(
            engine_ref=engine_ref,
            web=WebConnInfo(
                token=embed_token,
                url=embed_url,
                room=str(run_id),
                embed_script=embed.get("embed_script"),
                turn_url=turn_url,
                run_id=str(run_id),
            ),
        )

    async def end_session(self, session: Session) -> None:
        # VERIFIED: there is no documented "end run" REST route; a webrtc run
        # ends when the browser disconnects the peer connection. We mark our
        # Session ended platform-side (done by the router) and no-op here.
        # TODO(dograh): if a run-cancel endpoint exists, call it for early hangup.
        if session.engine_ref:
            log.debug("end_session: webrtc run %s ends on client disconnect", session.engine_ref)
        return None

    async def on_transcript(self, handler: TranscriptHandler) -> None:
        """Register the platform persist+webhook handler.

        Dograh has NO transcript push (deviation item 3): feed this handler via
        `poll_transcript` (run report polling) or via client-forwarded items to
        /v1/engine/dograh/events. We just store the reference here."""
        self._transcript_handler = handler

    # ─── transcript polling (no push available) ──────────────────────────────
    async def fetch_run(self, engine_ref: str) -> dict[str, Any]:
        """GET the Dograh run detail. VERIFIED fields include is_completed,
        transcript_url, transcript_public_url, recording_url."""
        workflow_id, run_id = engine_ref.split(":", 1)
        return await self._request("GET", f"/workflow/{workflow_id}/runs/{run_id}")

    @staticmethod
    def _parse_transcript(payload: Any) -> list[tuple[str, str, float]]:
        """Tolerantly parse a Dograh transcript artifact into (role, text, ts).

        ⚠️ The transcript_url JSON shape is only produced AFTER a real completed
        call, which needs a browser+mic, so it could not be captured here. This
        parser accepts the common shapes (list of items, or {transcript:[...]} /
        {messages:[...]}) and is the one spot to confirm field names on the very
        first real call (TODO(transcript-shape))."""
        items = payload
        if isinstance(payload, dict):
            items = (
                payload.get("transcript")
                or payload.get("messages")
                or payload.get("turns")
                or []
            )
        out: list[tuple[str, str, float]] = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            role = it.get("role") or it.get("speaker") or it.get("source") or "agent"
            role = "user" if str(role).lower() in ("user", "caller", "human") else "agent"
            text = it.get("text") or it.get("content") or it.get("message") or ""
            ts = it.get("ts") or it.get("timestamp") or it.get("start") or 0.0
            if text:
                out.append((role, text, float(ts)))
        return out

    async def poll_transcript(self, engine_ref: str) -> bool:
        """Fetch the run; once it has a transcript artifact, dispatch any new
        items to the registered handler. Returns True when the run is complete
        (so a polling loop knows to stop). De-dupes via a per-run offset."""
        run = await self.fetch_run(engine_ref)
        url = run.get("transcript_public_url") or run.get("transcript_url")
        if url:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    parsed = self._parse_transcript(resp.json())
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("transcript fetch/parse failed for %s: %s", engine_ref, exc)
                parsed = []
            already = self._emitted.get(engine_ref, 0)
            for role, text, ts in parsed[already:]:
                await self.dispatch_transcript(engine_ref, role, text, ts)
            self._emitted[engine_ref] = max(already, len(parsed))
        return bool(run.get("is_completed"))
