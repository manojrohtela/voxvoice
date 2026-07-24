"""Governance observability (M6.1B-2) — basic counters only. No metrics engine,
no dashboards (an explicit non-goal — a full observability engine is M6.1B-3)."""

from __future__ import annotations


class Metric:
    POLICY = "tool.governance.policy"
    APPROVAL = "tool.governance.approval"
    AUTHORIZATION = "tool.governance.authorization"
    RATE_LIMIT = "tool.governance.rate_limit"
    SECRET_REQUEST = "tool.governance.secret_request"
