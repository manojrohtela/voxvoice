"""providers.llm — llm capability plugins (implements core.providers.LLMProvider).

Empty at M1; implementations land from M2+ (Constitution §A6, ADR-0007).

- `echo.py` — the deterministic in-process provider (M2C.2); the default profile.
- `completion_stream.py` — the provider-neutral streaming seam (M11.9).
- `groq.py` — the real streaming provider, library-free, built on that seam (M11.9).
- `groq_client.py` — the **only** module that imports the Groq SDK (AR-12).
"""
