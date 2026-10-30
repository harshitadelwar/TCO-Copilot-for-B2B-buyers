"""Deterministic TCO engine: a pure-Python port of the Excel workbook's TCO Calculation sheet.

No LLM is involved here, by design. Every factor mirrors one row of the workbook
(P1, A1-A4, T1-T9, H1-H5, Q1-Q3, R1-R3, S1-S3) and keeps its escalation rule:
  price   -> escalates with the supplier's price escalation
  general -> escalates with general cost inflation
  one_time-> incurred once, no escalation
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .schema import EvalSetup, SupplierInputs

# id, bucket, name, escalation kind
FACTORS: list[tuple[str, str, str, str]] = [
    ("P1", "B1 Purchase price", "Base purchase spend", "price"),
    ("A1", "A Pre-purchase (one-time)", "Supplier search & RFQ evaluation", "one_time"),
    ("A2", "A Pre-purchase (one-time)", "Qualification: audit, samples, trial lots", "one_time"),
    ("A3", "A Pre-purchase (one-time)", "Tooling / dies / set-up paid by buyer", "one_time"),
    ("A4", "A Pre-purchase (one-time)", "Contract, legal & vendor registration", "one_time"),
    ("T1", "T Transaction (recurring)", "Inbound freight", "general"),
    ("T2", "T Transaction (recurring)", "Transit insurance", "price"),
    ("T3", "T Transaction (recurring)", "Loading / unloading & receiving handling", "general"),
    ("T4", "T Transaction (recurring)", "Packaging / returnable packaging", "general"),
    ("T5", "T Transaction (recurring)", "Non-recoverable GST", "price"),
    ("T6", "T Transaction (recurring)", "Other non-recoverable levies", "price"),
    ("T7", "T Transaction (recurring)", "Incoming inspection & testing", "general"),
    ("T8", "T Transaction (recurring)", "Order processing & procurement admin", "general"),
    ("T9", "T Transaction (recurring)", "Bank charges / guarantees / LC fees", "general"),
    ("H1", "H Holding & working capital", "Capital cost of cycle stock", "price"),
    ("H2", "H Holding & working capital", "Capital cost of safety stock", "price"),
    ("H3", "H Holding & working capital", "Storage & warehousing of stock", "price"),
    ("H4", "H Holding & working capital", "Obsolescence, shrinkage & stock insurance", "price"),
    ("H5", "H Holding & working capital", "Payment-terms financing effect", "price"),
    ("Q1", "Q Quality & performance", "Rejection, sorting & rework", "general"),
    ("Q2", "Q Quality & performance", "Late delivery: expediting & disruption", "general"),
    ("Q3", "Q Quality & performance", "Supplier management & monitoring", "general"),
    ("R1", "R After-sales", "Warranty / field claims (net)", "general"),
    ("R2", "R After-sales", "Disposal / recycling / reverse logistics", "general"),
    ("R3", "R After-sales", "Environmental / compliance obligations", "general"),
    ("S1", "S Risk (expected value)", "Supply-disruption risk", "general"),
    ("S2", "S Risk (expected value)", "Regulatory / ITC-mismatch risk", "general"),
    ("S3", "S Risk (expected value)", "Other strategic / ESG risk", "general"),
]
BUCKETS = ["B1 Purchase price", "A Pre-purchase (one-time)", "T Transaction (recurring)",
           "H Holding & working capital", "Q Quality & performance", "R After-sales",
           "S Risk (expected value)"]
ESTIMATE_BUCKETS = ("Q Quality & performance", "R After-sales", "S Risk (expected value)")
FACTOR_NAME = {f[0]: f[2] for f in FACTORS}


def pv_multiplier(escalation: float, n_years: int, discount_rate: float) -> float:
    """Sum over years 1..n of discount_factor(y) * (1+escalation)^(y-1)  (end-of-year discounting)."""
    return sum((1 / (1 + discount_rate) ** y) * (1 + escalation) ** (y - 1) for y in range(1, n_years + 1))


def base_unit_price(setup: EvalSetup, s: SupplierInputs) -> float:
    """Quoted price net of GST (GST is stripped if the quote is inclusive)."""
    return s.quoted_unit_price / (1 + setup.gst_rate) if s.price_includes_gst else s.quoted_unit_price


def year1_factors(setup: EvalSetup, s: SupplierInputs) -> dict[str, float]:
    vol = s.annual_volume if s.annual_volume else setup.annual_demand
    base = base_unit_price(setup, s)
    spend = base * vol
    orders = s.orders_per_year
    days = setup.days_in_year
    cycle_units = (vol / max(orders, 1)) / 2
    safety_units = s.safety_stock_days * vol / days
    held_units = cycle_units + safety_units
    f = {
        "P1": spend,
        "A1": s.rfq_cost, "A2": s.qualification_cost, "A3": s.tooling_cost, "A4": s.contract_cost,
        "T1": 0.0 if s.freight_included else s.freight_per_order * orders,
        "T2": s.transit_insurance_pct * spend,
        "T3": s.handling_per_order * orders,
        "T4": 0.0 if s.packaging_included else s.packaging_per_unit * vol,
        "T5": spend * setup.gst_rate * (1 - setup.itc_recoverable),
        "T6": s.other_levies_pct * spend,
        "T7": s.inspection_per_order * orders,
        "T8": s.admin_per_order * orders,
        "T9": s.bank_charges_per_year,
        "H1": cycle_units * base * setup.cost_of_capital,
        "H2": safety_units * base * setup.cost_of_capital,
        "H3": held_units * base * setup.storage_pct,
        "H4": held_units * base * setup.obsolescence_pct,
        "H5": -(s.payment_terms_days / days) * (spend * (1 + setup.gst_rate * (1 - setup.itc_recoverable))) * setup.cost_of_capital,
        "Q1": s.rejection_rate * vol * s.cost_per_rejected_unit,
        "Q2": s.late_rate * orders * s.cost_per_late_order,
        "Q3": s.supplier_mgmt_per_year,
        "R1": s.warranty_rate * vol * s.cost_per_claim,
        "R2": s.disposal_per_unit * vol,
        "R3": s.compliance_per_year,
        "S1": s.disruption_prob * s.disruption_impact,
        "S2": s.regulatory_prob * s.regulatory_impact,
        "S3": s.other_risk_prob * s.other_risk_impact,
    }
    return f


def _value_over(setup: EvalSetup, s: SupplierInputs, y1: dict[str, float], n_years: int) -> dict[str, float]:
    pm = pv_multiplier(s.annual_price_escalation, n_years, setup.discount_rate)
    gm = pv_multiplier(setup.general_inflation, n_years, setup.discount_rate)
    out = {}
    for fid, _b, _n, kind in FACTORS:
        out[fid] = y1[fid] * (pm if kind == "price" else gm if kind == "general" else 1.0)
    return out


@dataclass
class SupplierResult:
    name: str
    year1: dict[str, float]
    horizon: dict[str, float]
    buckets: dict[str, float]
    total: float
    per_unit: float
    base_price_per_unit: float          # B1 per unit over the horizon (escalated)
    hidden_cost_pct: float              # (TCO - purchase spend) / purchase spend
    cumulative: list[float]             # cumulative TCO, years 1..5
    rank: int = 0


@dataclass
class Comparison:
    setup: EvalSetup
    suppliers: list[SupplierResult]
    qrs_multiplier: float = 1.0
    horizon_years: int = 3
    ranking: list[str] = field(default_factory=list)

    def by_name(self, name: str) -> SupplierResult:
        return next(s for s in self.suppliers if s.name == name)

    @property
    def top_two_gap_pct(self) -> float:
        """Gap between best and second-best TCO per unit, as % of the best."""
        ordered = sorted(self.suppliers, key=lambda s: s.per_unit)
        if len(ordered) < 2:
            return 0.0
        return (ordered[1].per_unit - ordered[0].per_unit) / ordered[0].per_unit * 100

    @property
    def close_call(self) -> bool:
        return len(self.suppliers) > 1 and self.top_two_gap_pct < 1.0


def compute_supplier(setup: EvalSetup, s: SupplierInputs, horizon: int | None = None,
                     qrs_multiplier: float = 1.0) -> SupplierResult:
    h = horizon or setup.horizon_years
    y1 = year1_factors(setup, s)
    hz = _value_over(setup, s, y1, h)
    if qrs_multiplier != 1.0:
        for fid, bucket, _n, _k in FACTORS:
            if bucket in ESTIMATE_BUCKETS:
                hz[fid] *= qrs_multiplier
    buckets = {b: 0.0 for b in BUCKETS}
    for fid, bucket, _n, _k in FACTORS:
        buckets[bucket] += hz[fid]
    total = sum(hz.values())
    vol = s.annual_volume if s.annual_volume else setup.annual_demand
    units = vol * h
    spend = hz["P1"]
    cumulative = []
    for k in range(1, 6):
        c = _value_over(setup, s, y1, k)
        cumulative.append(sum(v * (qrs_multiplier if b in ESTIMATE_BUCKETS else 1.0)
                              for (fid, b, _n, _k), v in zip(FACTORS, (c[f[0]] for f in FACTORS))))
    return SupplierResult(
        name=s.name, year1=y1, horizon=hz, buckets=buckets, total=total, per_unit=total / units,
        base_price_per_unit=spend / units, hidden_cost_pct=(total - spend) / spend if spend else 0.0,
        cumulative=cumulative,
    )


def compare(setup: EvalSetup, suppliers: list[SupplierInputs], horizon: int | None = None,
            qrs_multiplier: float = 1.0) -> Comparison:
    h = horizon or setup.horizon_years
    results = [compute_supplier(setup, s, h, qrs_multiplier) for s in suppliers]
    order = sorted(results, key=lambda r: r.per_unit)
    for i, r in enumerate(order, start=1):
        r.rank = i
    return Comparison(setup=setup, suppliers=results, qrs_multiplier=qrs_multiplier,
                      horizon_years=h, ranking=[r.name for r in order])


def rank_by_horizon(setup: EvalSetup, suppliers: list[SupplierInputs],
                    qrs_multiplier: float = 1.0) -> dict[int, list[str]]:
    """Ranking (best first) if the evaluation horizon were 1..5 years."""
    return {h: compare(setup, suppliers, h, qrs_multiplier).ranking for h in range(1, 6)}


def top_cost_drivers(result: SupplierResult, n: int = 3) -> list[tuple[str, float]]:
    """Largest non-price cost factors over the horizon."""
    rows = [(FACTOR_NAME[fid], v) for fid, v in result.horizon.items() if fid != "P1" and v > 0]
    return sorted(rows, key=lambda r: r[1], reverse=True)[:n]
