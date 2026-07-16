"""core — the dependency floor (Constitution §A4.1, §A14).

Holds contracts, event types, state definitions, typed errors, ids, and shared
types. `core` imports nothing above it (no `api`, `runtime`, `providers`,
`events`). Everything else may import `core`; `core` imports only the stdlib.

This package contains **no behaviour** — only interfaces, value types, and
definitions. (Established at milestone M1.)
"""
