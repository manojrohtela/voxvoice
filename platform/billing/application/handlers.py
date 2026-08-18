"""Handler registration (M9.3) — the one wiring table.

Each command/query type maps to exactly one use-case method. The bus refuses a duplicate
registration (a wiring defect, not a runtime condition), and a coverage test asserts that
**every** type in `ALL_COMMANDS` / `ALL_QUERIES` is registered — so a new message cannot be
added without a handler.

Handlers contain no logic: they are the routing table, nothing more.
"""

from __future__ import annotations

from . import commands as c
from . import queries as q
from .dispatcher import CommandBus, QueryBus
from .use_cases import CommandUseCases, QueryUseCases


def register_command_handlers(bus: CommandBus, uc: CommandUseCases) -> None:
    bus.register(c.OpenBillingAccount, uc.open_account)
    bus.register(c.UpdateBillingProfile, uc.update_profile)
    bus.register(c.CloseBillingAccount, uc.close_account)
    bus.register(c.DefineMeter, uc.define_meter)
    bus.register(c.DefinePlan, uc.define_plan)
    bus.register(c.PublishPlan, uc.publish_plan)
    bus.register(c.PublishPriceBook, uc.publish_price_book)
    bus.register(c.StartSubscription, uc.start_subscription)
    bus.register(c.TransitionSubscription, uc.transition_subscription)
    bus.register(c.ChangeSubscriptionPlan, uc.change_plan)
    bus.register(c.GrantEntitlement, uc.grant_entitlement)
    bus.register(c.RevokeEntitlement, uc.revoke_entitlement)
    bus.register(c.PublishEntitlementSnapshot, uc.publish_snapshot)
    bus.register(c.RecordUsage, uc.record_usage)
    bus.register(c.QuarantineUsage, uc.quarantine_usage)
    bus.register(c.GrantCredit, uc.grant_credit)
    bus.register(c.PostLedgerEntry, uc.post_ledger_entry)
    bus.register(c.DraftInvoice, uc.draft_invoice)
    bus.register(c.IssueInvoice, uc.issue_invoice)
    bus.register(c.RequestPayment, uc.request_payment)
    bus.register(c.RequestRefund, uc.request_refund)
    bus.register(c.ApproveRefund, uc.approve_refund)
    bus.register(c.UpsertTaxProfile, uc.upsert_tax_profile)
    bus.register(c.RecordContract, uc.record_contract)
    bus.register(c.IssueLicense, uc.issue_license)
    bus.register(c.RevokeLicense, uc.revoke_license)


def register_query_handlers(bus: QueryBus, uc: QueryUseCases) -> None:
    bus.register(q.GetAccount, uc.get_account)
    bus.register(q.GetAccountHierarchy, uc.get_hierarchy)
    bus.register(q.ListPublishedPlans, uc.list_plans)
    bus.register(q.GetPriceBook, uc.get_price_book)
    bus.register(q.GetSubscription, uc.get_subscription)
    bus.register(q.ListSubscriptions, uc.list_subscriptions)
    bus.register(q.GetEntitlementSnapshot, uc.get_snapshot)
    bus.register(q.GetQuotaVerdict, uc.get_quota_verdict)
    bus.register(q.GetUsageSummary, uc.get_usage_summary)
    bus.register(q.GetLedgerBalance, uc.get_ledger_balance)
    bus.register(q.GetCreditBalance, uc.get_credit_balance)
    bus.register(q.GetInvoice, uc.get_invoice)
    bus.register(q.ListInvoices, uc.list_invoices)
    bus.register(q.ListPayments, uc.list_payments)
    bus.register(q.ListRefunds, uc.list_refunds)
    bus.register(q.GetTaxProfile, uc.get_tax_profile)
    bus.register(q.GetTaxDetermination, uc.get_tax_determination)
    bus.register(q.ListContracts, uc.list_contracts)
    bus.register(q.ListLicenses, uc.list_licenses)
