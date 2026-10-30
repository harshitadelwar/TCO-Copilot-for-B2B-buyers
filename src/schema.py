"""Data models: quote-extraction fields and the TCO engine's input schema.

Two families of inputs, on purpose:
  * QUOTE fields  - things a supplier actually writes in a quote (extracted by the LLM).
  * BUYER fields  - things only the buyer knows (rejection history, risk estimates, order plan).
The LLM is only ever asked for QUOTE fields. It never guesses BUYER fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- engine inputs
class EvalSetup(BaseModel):
    """Common basis for all suppliers (sheet: Evaluation Setup)."""
    item: str = "Mild steel fabricated bracket (sample)"
    unit: str = "piece"
    annual_demand: float = Field(120_000, gt=0)
    horizon_years: int = Field(3, ge=1, le=5)
    gst_rate: float = Field(0.18, ge=0, le=1)
    itc_recoverable: float = Field(1.0, ge=0, le=1)
    cost_of_capital: float = Field(0.11, ge=0)
    storage_pct: float = Field(0.04, ge=0)
    obsolescence_pct: float = Field(0.02, ge=0)
    general_inflation: float = Field(0.05, ge=0)
    discount_rate: float = Field(0.0, ge=0)
    days_in_year: int = 365


class SupplierInputs(BaseModel):
    """One supplier column of the sheet 'Supplier Inputs'."""
    name: str = "Supplier"
    location: str = ""
    certification: str = ""
    # B1 price
    quoted_unit_price: float = Field(..., ge=0)
    price_includes_gst: bool = False
    annual_price_escalation: float = 0.0
    annual_volume: Optional[float] = None          # None -> common annual demand
    # order pattern & terms
    orders_per_year: float = 12
    moq: float = 0
    lead_time_days: float = 0
    payment_terms_days: float = 0                  # negative = advance payment
    safety_stock_days: float = 0
    # already inside the quoted price?
    freight_included: bool = False
    packaging_included: bool = False
    # A: one-time
    rfq_cost: float = 0
    qualification_cost: float = 0
    tooling_cost: float = 0
    contract_cost: float = 0
    # T: transaction
    freight_per_order: float = 0
    transit_insurance_pct: float = 0
    handling_per_order: float = 0
    packaging_per_unit: float = 0
    other_levies_pct: float = 0
    inspection_per_order: float = 0
    admin_per_order: float = 0
    bank_charges_per_year: float = 0
    # Q: quality & performance
    rejection_rate: float = 0
    cost_per_rejected_unit: float = 0
    late_rate: float = 0
    cost_per_late_order: float = 0
    supplier_mgmt_per_year: float = 0
    # R: after-sales
    warranty_rate: float = 0
    cost_per_claim: float = 0
    disposal_per_unit: float = 0
    compliance_per_year: float = 0
    # S: risk (expected value = probability x impact)
    disruption_prob: float = 0
    disruption_impact: float = 0
    regulatory_prob: float = 0
    regulatory_impact: float = 0
    other_risk_prob: float = 0
    other_risk_impact: float = 0


# --------------------------------------------------------------------------- extraction schema
@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: Literal["str", "num", "bool", "frac"]
    desc: str


QUOTE_FIELDS: list[FieldSpec] = [
    FieldSpec("supplier_name", "str", "Supplier / company name as written"),
    FieldSpec("location", "str", "City / state of the supplying facility"),
    FieldSpec("certification", "str", "Quality certifications stated (e.g. ISO 9001, IATF 16949)"),
    FieldSpec("price_unit", "str", "Unit the price is quoted per, singular (piece, kg, tonne, metre...)"),
    FieldSpec("quoted_unit_price", "num", "Quoted price in INR per price_unit, exactly as written, no conversion"),
    FieldSpec("price_includes_gst", "bool", "true if the quoted price already includes GST, false if GST is extra"),
    FieldSpec("annual_price_escalation", "frac", "Expected yearly price rise as a fraction (5% -> 0.05)"),
    FieldSpec("moq", "num", "Minimum order quantity per order, in units"),
    FieldSpec("lead_time_days", "num", "Days from order/PO to delivery"),
    FieldSpec("payment_terms_days", "num", "Credit days after invoice/delivery; 0 = pay on delivery; negative = advance payment"),
    FieldSpec("freight_included", "bool", "true if delivery/freight to the buyer's plant is already in the price"),
    FieldSpec("freight_per_order", "num", "INR freight per consignment/truck when freight is NOT included"),
    FieldSpec("packaging_included", "bool", "true if packaging is already in the price"),
    FieldSpec("packaging_per_unit", "num", "INR per unit for packaging when NOT included"),
    FieldSpec("tooling_cost", "num", "One-time tooling/die cost payable by the buyer, INR"),
]
FIELD_BY_NAME = {f.name: f for f in QUOTE_FIELDS}
Status = Literal["stated", "inferred", "missing", "conflict"]


class FieldResult(BaseModel):
    value: Optional[Any] = None
    status: Status = "missing"
    evidence: str = ""            # exact span copied from the quote
    question: str = ""            # clarifying question for missing / conflict
    evidence_verified: Optional[bool] = None

    @field_validator("evidence", "question", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return "" if v is None else str(v)


class QuoteExtraction(BaseModel):
    fields: dict[str, FieldResult]
    notes: str = ""

    def value(self, name: str):
        fr = self.fields.get(name)
        return fr.value if fr and fr.status in ("stated", "inferred") else None

    def flagged(self) -> list[str]:
        return [n for n, fr in self.fields.items() if fr.status in ("missing", "conflict")]


# --------------------------------------------------------------------------- buyer-side fields
BUYER_FIELDS: list[tuple[str, str, str]] = [
    # (SupplierInputs attribute, label, unit)
    ("orders_per_year", "Orders per year", "orders/yr"),
    ("safety_stock_days", "Safety stock held", "days of demand"),
    ("rfq_cost", "Supplier search & RFQ evaluation", "INR one-time"),
    ("qualification_cost", "Qualification: audit, samples, trial lots", "INR one-time"),
    ("contract_cost", "Contract, legal & vendor registration", "INR one-time"),
    ("transit_insurance_pct", "Transit insurance", "% of purchase value (fraction)"),
    ("handling_per_order", "Loading / unloading & receiving", "INR/order"),
    ("other_levies_pct", "Other non-recoverable levies", "% of base price (fraction)"),
    ("inspection_per_order", "Incoming inspection & testing", "INR/order"),
    ("admin_per_order", "Order processing & admin", "INR/order"),
    ("bank_charges_per_year", "Bank charges / guarantees / LC fees", "INR/yr"),
    ("rejection_rate", "Rejection rate", "fraction of units"),
    ("cost_per_rejected_unit", "Handling cost per rejected unit", "INR"),
    ("late_rate", "Late-delivery rate", "fraction of orders"),
    ("cost_per_late_order", "Cost per late order", "INR"),
    ("supplier_mgmt_per_year", "Supplier management & monitoring", "INR/yr"),
    ("warranty_rate", "Warranty / field-claim rate", "fraction of units"),
    ("cost_per_claim", "Net unrecovered cost per claim", "INR"),
    ("disposal_per_unit", "Disposal / reverse logistics", "INR/unit"),
    ("compliance_per_year", "Environmental / compliance obligations", "INR/yr"),
    ("disruption_prob", "Supply-disruption probability", "per year"),
    ("disruption_impact", "Supply-disruption impact", "INR/event"),
    ("regulatory_prob", "Regulatory / ITC-mismatch probability", "per year"),
    ("regulatory_impact", "Regulatory / ITC-mismatch impact", "INR/event"),
    ("other_risk_prob", "Other strategic / ESG risk probability", "per year"),
    ("other_risk_impact", "Other strategic / ESG risk impact", "INR/event"),
]
