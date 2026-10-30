"""Buyer-side estimates and the quote -> engine-input mapping.

Buyer-side numbers (rejection history, risk estimates, order plan...) are NOT in a supplier's quote,
so the LLM never extracts them. The demo values below are the workbook's illustrative samples.
"""
from __future__ import annotations

from typing import Any

from .schema import EvalSetup, QuoteExtraction, SupplierInputs

DEMO_BUYER_ESTIMATES: dict[str, dict[str, float]] = {
    "A": dict(orders_per_year=12, safety_stock_days=5, rfq_cost=15000, qualification_cost=40000, contract_cost=10000,
              transit_insurance_pct=0.001, handling_per_order=1800, other_levies_pct=0, inspection_per_order=2500,
              admin_per_order=1200, bank_charges_per_year=0, rejection_rate=0.008, cost_per_rejected_unit=45,
              late_rate=0.08, cost_per_late_order=8000, supplier_mgmt_per_year=20000, warranty_rate=0.002,
              cost_per_claim=300, disposal_per_unit=0, compliance_per_year=0, disruption_prob=0.05,
              disruption_impact=400000, regulatory_prob=0.02, regulatory_impact=100000, other_risk_prob=0,
              other_risk_impact=0),
    "B": dict(orders_per_year=12, safety_stock_days=7, rfq_cost=15000, qualification_cost=60000, contract_cost=10000,
              transit_insurance_pct=0.001, handling_per_order=1800, other_levies_pct=0, inspection_per_order=2500,
              admin_per_order=1200, bank_charges_per_year=0, rejection_rate=0.004, cost_per_rejected_unit=45,
              late_rate=0.05, cost_per_late_order=8000, supplier_mgmt_per_year=20000, warranty_rate=0.001,
              cost_per_claim=300, disposal_per_unit=0, compliance_per_year=0, disruption_prob=0.04,
              disruption_impact=400000, regulatory_prob=0.02, regulatory_impact=100000, other_risk_prob=0,
              other_risk_impact=0),
    "C": dict(orders_per_year=24, safety_stock_days=14, rfq_cost=15000, qualification_cost=90000, contract_cost=10000,
              transit_insurance_pct=0.001, handling_per_order=2500, other_levies_pct=0, inspection_per_order=6000,
              admin_per_order=1200, bank_charges_per_year=12000, rejection_rate=0.025, cost_per_rejected_unit=45,
              late_rate=0.2, cost_per_late_order=8000, supplier_mgmt_per_year=45000, warranty_rate=0.006,
              cost_per_claim=300, disposal_per_unit=0, compliance_per_year=15000, disruption_prob=0.15,
              disruption_impact=400000, regulatory_prob=0.1, regulatory_impact=100000, other_risk_prob=0,
              other_risk_impact=0),
}
# Neutral buyer-side estimates for a supplier the buyer has no history with (used by the eval scenarios).
GENERIC_BUYER_ESTIMATES = DEMO_BUYER_ESTIMATES["A"]


# Low-stakes fields: a safe default is applied automatically, but it is always listed as an assumption.
SOFT_DEFAULTS = {"tooling_cost", "annual_price_escalation"}


class MissingInputs(Exception):
    """Raised when engine-critical quote fields are missing and the user has not accepted defaults."""

    def __init__(self, fields: list[str], questions: dict[str, str]):
        super().__init__(f"Missing or conflicting quote fields: {', '.join(fields)}")
        self.fields = fields
        self.questions = questions


def _fallbacks(setup: EvalSetup) -> dict[str, tuple[Any, str]]:
    """Default for a missing quote field -> (value, why). Only used if the user accepts defaults."""
    return {
        "price_includes_gst": (False, "GST assumed EXTRA (common B2B practice); GST treatment changes TCO a lot, so confirm"),
        "annual_price_escalation": (setup.general_inflation, "no escalation stated; used the general inflation rate"),
        "payment_terms_days": (30, "no payment terms stated; assumed 30 credit days"),
        "freight_included": (False, "freight assumed NOT included"),
        "freight_per_order": (12000, "no freight amount stated; assumed INR 12,000 per consignment"),
        "packaging_included": (False, "packaging assumed NOT included"),
        "packaging_per_unit": (0.0, "no packaging charge stated; assumed 0"),
        "tooling_cost": (0.0, "no tooling cost stated; assumed 0"),
    }


def build_inputs(extraction: QuoteExtraction, buyer_estimates: dict[str, float], setup: EvalSetup, *,
                 label: str = "Supplier", accept_defaults: bool = False
                 ) -> tuple[SupplierInputs, list[dict]]:
    """Merge extracted quote fields + buyer estimates into engine inputs.

    Returns (inputs, assumptions). `assumptions` lists every value that did NOT come from the quote.
    The price itself can never be defaulted.
    """
    fb = _fallbacks(setup)
    vals: dict[str, Any] = {}
    assumptions: list[dict] = []
    missing: list[str] = []

    price = extraction.value("quoted_unit_price")
    if price is None:
        q = {"quoted_unit_price": extraction.fields["quoted_unit_price"].question or "What is the unit price?"}
        raise MissingInputs(["quoted_unit_price"], q)
    vals["quoted_unit_price"] = float(price)

    freight_inc = extraction.value("freight_included")
    pack_inc = extraction.value("packaging_included")
    for name, (default, why) in fb.items():
        got = extraction.value(name)
        # conditional fields only matter when the "included" flag says the cost is outside the price
        if name == "freight_per_order" and freight_inc is True:
            vals[name] = 0.0
            continue
        if name == "packaging_per_unit" and pack_inc is True:
            vals[name] = 0.0
            continue
        if got is not None:
            vals[name] = got
            if extraction.fields[name].status == "inferred":
                assumptions.append({"supplier": label, "field": name, "value": got, "source": "inferred from quote",
                                    "reason": extraction.fields[name].evidence})
            continue
        vals[name] = default
        assumptions.append({"supplier": label, "field": name, "value": default, "source": "default", "reason": why})
        if name not in SOFT_DEFAULTS:
            missing.append(name)
    if missing and not accept_defaults:
        qs = {m: (extraction.fields[m].question or f"Please confirm: {m.replace('_', ' ')}") for m in missing}
        raise MissingInputs(missing, qs)

    # informational (not engine-critical) fields: use if present
    for name, key in (("supplier_name", "name"), ("location", "location"), ("certification", "certification"),
                      ("moq", "moq"), ("lead_time_days", "lead_time_days")):
        v = extraction.value(name)
        if v is not None:
            vals[key] = v
    vals.setdefault("name", label)
    return SupplierInputs(**{**buyer_estimates, **vals}), assumptions
